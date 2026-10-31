"""Change scope is durable, source-owned, and cannot be silently rewritten."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.decision_change_integrity import DECISION_CHANGE_FINDINGS
from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_impact_postgres import changed, decision
from .test_decision_review_backups_postgres import review
from .test_decision_reviews_postgres import finish, settings
from .test_duplicate_handling_postgres import merge_work
from .test_knowledge_postgres import domain
from .test_product_decision_migration_postgres import migrate
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_work_item_moves_postgres import _move_payload
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def _change(engine, project, work_id=None, **fields):
    identifier = uuid4()
    values = {"id": identifier, "project_id": project["id"], "review_work_item_id": work_id,
              **fields}
    names = ",".join(values)
    parameters = ",".join(":" + name for name in values)
    with engine.begin() as connection:
        connection.execute(text(f"INSERT INTO decision_changes ({names}) VALUES ({parameters})"),
                           values)
        if work_id is not None:
            connection.execute(text("""
                INSERT INTO decision_change_work (work_item_id,change_id) VALUES (:work,:change)
            """), {"work": work_id, "change": identifier})
    return identifier


def _findings(engine):
    with engine.connect() as connection:
        return {name: count for name, sql in DECISION_CHANGE_FINDINGS.items()
                if (count := connection.scalar(text(sql)))}


@pytest.mark.parametrize("mutation", [
    "UPDATE decision_changes SET scope_version=scope_version+1 WHERE id=:id",
    "UPDATE decision_changes SET project_id=:foreign WHERE id=:id",
    "UPDATE decision_changes SET admission_actor_session_id='invented' WHERE id=:id",
    "DELETE FROM decision_changes WHERE id=:id",
    "DELETE FROM decision_change_work WHERE change_id=:id",
    "UPDATE decision_change_work SET created_at=clock_timestamp() WHERE change_id=:id",
    "TRUNCATE decision_change_work",
])
def test_change_guards_reject_lost_scope_and_rewritten_identity(
    api, project, work_payload, postgres_engine, mutation,
):
    work = create_work(api, project, work_payload)["work_item"]
    identifier = _change(postgres_engine, project, work["id"])
    other = api.post("/api/v1/projects", json={"name": "Other change owner"}).json()
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(mutation), {"id": identifier, "foreign": other["id"]})
    assert _findings(postgres_engine) == {}


def test_scope_and_acknowledgment_are_monotone_and_episode_counted(api, project, postgres_engine):
    target = domain(api, project)
    before = decision(api, project, target, "Before scope counting")
    changed(api, project, target, before, "After scope counting")
    identifier = _change(postgres_engine, project)
    with postgres_engine.begin() as connection:
        episode = connection.scalar(text("SELECT id FROM decision_impact_assessments LIMIT 1"))
        connection.execute(text("""
            INSERT INTO decision_change_assessments (change_id,assessment_id) VALUES (:id,:episode)
        """), {"id": identifier, "episode": episode})
        connection.execute(text("""
            UPDATE decision_changes SET scope_version=1,acknowledged_scope_version=1 WHERE id=:id
        """), {"id": identifier})
    for field in ("scope_version", "acknowledged_scope_version"):
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text(f"UPDATE decision_changes SET {field}=0 WHERE id=:id"),
                               {"id": identifier})
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM decision_change_assessments WHERE change_id=:id"),
                           {"id": identifier})
    assert _findings(postgres_engine) == {}


def test_successor_requires_system_or_historical_dashboard_provenance(
    api, project, postgres_engine,
):
    parent = _change(postgres_engine, project)
    for identity in ({}, {"admission_actor_client": "codex",
                          "admission_actor_session_id": "native-agent-session"}):
        with pytest.raises(DBAPIError):
            _change(postgres_engine, project, parent_change_id=parent, **identity)
    child = _change(postgres_engine, project, parent_change_id=parent,
                    admission_actor_client="mnemonic",
                    admission_actor_session_id=f"decision-change:{parent}")
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("UPDATE decision_changes SET parent_change_id=NULL WHERE id=:id"),
                           {"id": child})
    assert _findings(postgres_engine) == {}


def test_cross_project_episode_membership_is_rejected(api, project, postgres_engine):
    target = domain(api, project)
    before = decision(api, project, target, "Before source ownership")
    changed(api, project, target, before)
    other = api.post("/api/v1/projects", json={"name": "Foreign change owner"}).json()
    foreign = _change(postgres_engine, other)
    with pytest.raises(DBAPIError, match="source project"), postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO decision_change_assessments (change_id,assessment_id)
            SELECT :id,id FROM decision_impact_assessments LIMIT 1
        """), {"id": foreign})


def _remove_only_change_metadata(engine):
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        # The pre-0058 immutable origin snapshot had no change-group field.
        connection.execute(text("""
            UPDATE checkpoints SET source_metadata=source_metadata-'decision_change_id'
            WHERE source_metadata->'automatic_decision_review'='true'::jsonb
        """))
        for table in ("decision_change_work", "decision_change_assessments",
                      "decision_revision_changes", "decision_changes"):
            connection.execute(text(f"DELETE FROM {table}"))


@pytest.mark.parametrize("retirement", ["wont-do", "merged"])
def test_migration_backfills_exact_legacy_reviews_without_reclassifying_history(
    api, project, postgres_engine, checkpoint_fields, work_payload, monkeypatch, retirement,
):
    initial = review(api, project)
    child = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": initial["work_item"]["id"],
    }, title="Historical review remediation")["work_item"]
    grandchild = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": child["id"],
    }, title="Historical remediation investigation")["work_item"]
    destination = api.post("/api/v1/projects", json={"name": "Historical descendant move"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{grandchild['id']}/move",
                     json=_move_payload(destination, grandchild["version"]))
    assert moved.status_code == 200, moved.text
    if retirement == "wont-do":
        retired = finish(api, project, initial["work_item"], checkpoint_fields, status="wont-do")
        assert retired.status_code == 200, retired.text
    else:
        ordinary = create_work(api, project, work_payload, title="Historical canonical work")
        # The old lifecycle allowed reviews to merge into unmarked ordinary work.
        with monkeypatch.context() as legacy:
            legacy.setattr("mnemonic_api.services.decision_changes.require_merge_scope_adopted",
                           lambda *_: None)
            merge_work(api, project, initial["work_item"], ordinary["work_item"])
    target = domain(api, project, "BDR", "Another historic decision domain")
    before = decision(api, project, target, "Independent old decision")
    changed(api, project, target, before)
    # Keep domain policy reversible so this exercises the older change backfill.
    settings(api, project, automatic_decision_reviews=False, decision_review_priority=40)
    _remove_only_change_metadata(postgres_engine)
    preserved = _snapshot(postgres_engine, project)
    migrate(postgres_engine, "0057_automatic_decision_reviews", downgrade=True)
    migrate(postgres_engine, "head")
    restored = _snapshot(postgres_engine, project)
    for table in ("work_items", "checkpoints", "decision_revisions", "decision_impact_assessments",
                  "decision_impact_dispositions"):
        assert restored[table] == preserved[table]
    assert restored["decision_revision_changes"] == []
    drivers = {row["id"]: row for row in restored["work_items"]
               if row["decision_review_revision_id"] is not None}
    assert len(restored["decision_changes"]) == len(drivers) == 2
    origin = initial["work_item"]["decision_review_revision_id"]
    assert {row["work_item_id"] for row in restored["decision_change_work"]
            if row["change_id"] == origin} == {
        initial["work_item"]["id"], child["id"], grandchild["id"],
    }
    for change in restored["decision_changes"]:
        work = drivers[change["review_work_item_id"]]
        assert change["id"] == change["origin_revision_id"] == work["decision_review_revision_id"]
        expected = {row["id"] for row in restored["decision_impact_assessments"]
                    if row["replacement_revision_id"] == change["origin_revision_id"]}
        actual = {row["assessment_id"] for row in restored["decision_change_assessments"]
                  if row["change_id"] == change["id"]}
        assert actual == expected
        assert change["scope_version"] == len(expected)
        assert change["acknowledged_scope_version"] == (
            len(expected) if work["id"] == initial["work_item"]["id"] else 0
        )
    assert _findings(postgres_engine) == {}
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0057_automatic_decision_reviews", downgrade=True)


@pytest.mark.parametrize("damage", [
    "scope", "driver-membership", "source-project", "descendant-lineage",
])
def test_restore_rejects_changed_scope_or_source_ownership(
    api, project, work_payload, postgres_engine, damage,
):
    work = create_work(api, project, work_payload)["work_item"]
    identifier = str(_change(postgres_engine, project, work["id"]))
    child = create_work(api, project, {
        **work_payload, "discovered_from_work_item_id": work["id"],
    }, title="Restore lineage preservation")["work_item"]
    before = _snapshot(postgres_engine, project)
    backup = _export(postgres_engine, project)

    def corrupt(_header, rows):
        change = next(row for row in rows["decision_changes"] if row["id"] == identifier)
        if damage == "scope":
            change["scope_version"] += 1
        elif damage == "driver-membership":
            rows["decision_change_work"] = []
        elif damage == "descendant-lineage":
            rows["decision_change_work"] = [
                row for row in rows["decision_change_work"] if row["work_item_id"] != child["id"]
            ]
        else:
            change["project_id"] = str(uuid4())

    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert error.value.code in {"backup_integrity_conflict", "invalid_backup"}
    assert _snapshot(postgres_engine, project) == before


def test_adopted_driver_moves_without_reassigning_change_ownership(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    identifier = str(_change(postgres_engine, project, work["id"]))
    target = api.post("/api/v1/projects", json={"name": "Adopted driver destination"}).json()
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
                     json=_move_payload(target, work["version"]))
    assert moved.status_code == 200, moved.text
    source = _snapshot(postgres_engine, project)
    assert source["decision_changes"][0]["id"] == identifier
    assert source["decision_change_work"][0]["work_item_id"] == work["id"]
    assert _snapshot(postgres_engine, target)["decision_change_work"] == []
    detail = api.get(f"/api/v1/projects/{target['id']}/work-items/{work['id']}/context").json()
    assert detail["work_item"]["decision_review_project_id"] == project["id"]
    assert detail["work_item"]["decision_change_projects"] == {identifier: project["id"]}
    restore_project(postgres_engine, UUID(target["id"]),
                    io.BytesIO(_export(postgres_engine, target)))
    assert _snapshot(postgres_engine, project) == source
    assert _findings(postgres_engine) == {}


def test_adopted_ordinary_driver_and_lineage_are_visible_on_every_work_surface(
    api, project, work_payload, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    identifier = str(_change(postgres_engine, project, work["id"]))
    base = f"/api/v1/projects/{project['id']}"
    pointers = []
    for view in ("full", "roots"):
        for detail in ("compact", "full"):
            page = api.get(base + "/work-items", params={"view": view, "detail": detail})
            assert page.status_code == 200, page.text
            row = page.json()["items"][0]
            pointers.append(row if detail == "compact" else row["summary"]["work_item"])
    for suffix in (f"/work-items/{work['id']}", f"/work-items/{work['id']}/context"):
        exact = api.get(base + suffix)
        assert exact.status_code == 200, exact.text
        payload = exact.json()
        pointer = payload.get("work_item", payload)
        assert pointer["decision_change_projects"] == {identifier: project["id"]}
        pointers.append(pointer)
    ready = api.get(base + "/ready-work")
    assert ready.status_code == 200, ready.text
    pointers.append(ready.json()["items"][0]["work_item"])
    tasks = api.get(base + "/tasks", params={"status": "all"})
    assert tasks.status_code == 200, tasks.text
    pointers.append(tasks.json()["items"][0])
    search = api.post(base + "/search", json={"q": work["title"], "facets": ["work_items"]})
    assert search.status_code == 200, search.text
    pointers.append(search.json()["items"][0]["work_item"])
    for pointer in pointers:
        assert pointer["decision_review_change_id"] == identifier
        assert pointer["decision_review_project_id"] == project["id"]
        assert pointer["decision_change_ids"] == [identifier]
        assert pointer.get("decision_review_revision_id") is None
