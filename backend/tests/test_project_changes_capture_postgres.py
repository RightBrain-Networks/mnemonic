"""Transactional capture covers mutable settings and retains useful source facts."""

import json
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.models import ProjectChange, ProjectTag, WorkEvent
from mnemonic_api.services.work_events import database_now, stage_work_changed

from .conftest import BACKEND_DIR
from .test_artifacts_postgres import collection, headers, upload
from .test_knowledge_management_postgres import HEADERS, browser_path, deletion_body, preview
from .test_knowledge_postgres import domain, path, put, put_body
from .test_leases_postgres import expire_lease
from .test_phase6_migration_postgres import (
    empty_phase6_migration_engine as empty_phase6_migration_engine,
)
from .test_phase12_database_postgres import _close, _project, _work
from .test_transcript_indexing_postgres import register, run
from .test_work_events_postgres import actor, create_work, item_path

pytestmark = pytest.mark.postgres


def _changes(engine, project_id, resource_type=None):
    with Session(engine) as database:
        query = select(ProjectChange).where(ProjectChange.project_id == UUID(str(project_id)))
        if resource_type:
            query = query.where(ProjectChange.resource_type == resource_type)
        return database.scalars(query.order_by(ProjectChange.sequence)).all()


@pytest.mark.usefixtures("pristine_postgres_engine")
def test_capture_is_atomic_noop_aware_and_keeps_sequence_contiguous(postgres_engine):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
    baseline = _changes(postgres_engine, project_id)
    with Session(postgres_engine) as database:
        database.execute(text("UPDATE projects SET name='Rolled back' WHERE id=:p"),
                         {"p": project_id})
        database.rollback()
    assert len(_changes(postgres_engine, project_id)) == len(baseline)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE projects SET name=name WHERE id=:p"), {"p": project_id})
    assert len(_changes(postgres_engine, project_id)) == len(baseline)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE projects SET description='private body' WHERE id=:p"),
                           {"p": project_id})
    changes = _changes(postgres_engine, project_id)
    assert [row.sequence for row in changes] == list(range(1, len(changes) + 1))
    last = changes[-1]
    assert last.changed_fields == ["description"]
    assert "private body" not in json.dumps([last.before_state, last.after_state])
    assert last.recorded_at >= last.occurred_at


@pytest.mark.usefixtures("pristine_postgres_engine")
def test_work_values_survive_subsequent_edits_without_copying_bodies(postgres_engine):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        work = _work(database, project_id)
        before = {"status": work.status, "title": work.title, "summary": work.summary}
        work.title = "A second title"
        work.summary = "Do not copy this prose"
        work.version += 1
        stage_work_changed(database, work, before=before, requested_fields=["title", "summary"],
                           actor=None, created_at=database_now(database))
    rows = _changes(postgres_engine, project_id, "work_items")
    assert [row.operation for row in rows] == ["work_created", "work_updated"]
    assert rows[-1].before_state["title"] == "Use the selected font"
    assert rows[-1].after_state["title"] == "A second title"
    assert rows[-1].after_state["status"] == "pending"
    assert "summary" in rows[-1].changed_fields
    assert "summary" not in rows[-1].after_state


@pytest.mark.usefixtures("pristine_postgres_engine")
def test_tag_definition_update_and_deletion_keep_old_scalars(postgres_engine):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
        tag = ProjectTag(project_id=project_id, name="capture-test", description="private prose",
                         color="#aabbcc", menu_order=99)
        database.add(tag)
        database.flush()
        tag.color = "#ddeeff"
        database.flush()
        database.delete(tag)
    rows = [row for row in _changes(postgres_engine, project_id, "tags")
            if row.title == "capture-test"]
    assert [row.operation for row in rows] == ["project_tags_insert", "project_tags_update",
                                             "project_tags_delete"]
    assert rows[1].before_state["color"] == "#aabbcc"
    assert rows[1].after_state["color"] == "#ddeeff"
    assert rows[2].after_state == {}
    assert all("description" not in row.after_state for row in rows)


@pytest.mark.parametrize("statement", [
    "DELETE FROM project_changes WHERE project_id=:p",
    "UPDATE project_changes SET title='forged' WHERE project_id=:p",
    "UPDATE project_change_heads SET last_sequence=last_sequence+1 WHERE project_id=:p",
    "TRUNCATE project_changes",
])
@pytest.mark.usefixtures("pristine_postgres_engine")
def test_capture_journal_is_source_managed(postgres_engine, statement):
    with Session(postgres_engine) as database, database.begin():
        project_id = _project(database)
    with pytest.raises(DBAPIError, match="source managed"), postgres_engine.begin() as connection:
        connection.execute(text(statement), {"p": project_id})


def test_artifact_download_is_not_a_change_and_delete_is_retained(
    api, project, postgres_engine, tmp_path,
):
    api.app.state.artifact_storage = ArtifactStorage(tmp_path / "capture-artifacts", max_bytes=1024)
    api.app.state.settings.artifact_max_bytes = 1024
    artifact = upload(api, project, filename="example.txt", body=b"plain text",
                      metadata={"description": "private prose"})
    target = collection(project) + "/" + artifact["id"]
    assert api.get(target + "/content", params={"expected_revision": 1}).status_code == 200
    response = api.delete(target, headers=headers(revision=1))
    assert response.status_code == 200, response.text
    rows = _changes(postgres_engine, project["id"], "artifacts")
    assert [row.operation for row in rows] == ["uploaded", "deleted"]
    assert rows[-1].after_state == {}
    assert all("description" not in row.after_state for row in rows)


def test_decision_revisions_capture_exact_domain_and_revision_without_content(
    api, project, postgres_engine,
):
    adr = domain(api, project)
    first = put(api, project, put_body(adr, "private first body"))
    second = put(api, project, put_body(adr, "private second body",
        decision_id=first["decision"]["decision_id"], expected_revision=0))
    rows = _changes(postgres_engine, project["id"], "decisions")
    assert [row.operation for row in rows] == ["decision_created", "decision_revised"]
    assert [row.revision for row in rows] == ["0", "1"]
    assert all(row.domain_id == UUID(adr["id"]) and row.domain_prefix == "ADR" for row in rows)
    assert rows[-1].after_state["sha256"] == second["decision"]["sha256"]
    assert "content" in rows[-1].changed_fields
    assert "content" not in rows[-1].after_state


def test_upgrade_imports_retained_work_occurrences(empty_phase6_migration_engine: Engine):
    engine = empty_phase6_migration_engine
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0072_force_claim_gates")
    with Session(engine) as database, database.begin():
        project_id = _project(database)
        work = _work(database, project_id)
        occurred = work.created_at
        for metadata in ({"changes": "notes"}, {"changes": []}, {
            "initial": {"title": "Forged"}, "to_status": "done",
            "changes": {"status": {"before": "pending", "after": "done"}},
        }):
            database.add(WorkEvent(project_id=project_id, work_item_id=work.id,
                event_type="progress", actor_kind="client", actor_client="pytest",
                actor_session_id="history", body="Progress prose", event_metadata=metadata))
        database.flush()
        previous_title = work.title
        work.title = "Title retained at migration"
        work.version += 1
        stage_work_changed(database, work, before={"status": work.status, "title": previous_title},
                           requested_fields=["title"], actor=None,
                           created_at=database_now(database))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0073_project_changes")
    rows = _changes(engine, project_id, "work_items")
    assert len(rows) == 5 and all(row.origin == "history_import" for row in rows)
    progress = [row for row in rows if row.operation == "progress"]
    assert len(progress) == 3
    assert all(row.title == "Work item" and row.work_status is None and
               row.before_state == {} and row.after_state == {} for row in progress)
    assert rows[-1].before_state["title"] == previous_title
    assert rows[-1].after_state["title"] == "Title retained at migration"
    assert rows[0].occurred_at == occurred
    assert rows[0].recorded_at >= occurred
    with Session(engine) as database, database.begin():
        database.execute(text("UPDATE projects SET description='new' WHERE id=:p"),
                         {"p": project_id})
    rows = _changes(engine, project_id)
    assert rows[-1].origin == "live" and rows[-1].sequence == 6
    with pytest.raises(RuntimeError, match="Project change history exists"):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0072_force_claim_gates")
    assert len(_changes(engine, project_id)) == 6


def test_decision_scope_and_domain_deletion_retain_change_facts(api, project, postgres_engine):
    adr = domain(api, project)
    decision = put(api, project, put_body(adr))["decision"]
    response = api.post(path(project) + "/decisions/" + decision["decision_id"] + "/scope", json={
        "revision": 0, "commit_hash": "a" * 40, "repository": "owner/repo",
        "client_operation_id": str(uuid4()),
    })
    assert response.status_code == 200, response.text
    suffix = "/domains/" + adr["id"]
    snapshot = preview(api, project, suffix)
    response = api.post(browser_path(project) + suffix + "/delete", headers=HEADERS,
                        json=deletion_body(snapshot))
    assert response.status_code == 200, response.text
    decisions = _changes(postgres_engine, project["id"], "decisions")
    assert [row.operation for row in decisions] == ["decision_created", "scope_added",
                                                  "decision_deleted"]
    assert decisions[1].after_state["commit_hash"] == "a" * 40
    assert decisions[-1].resource_id == decisions[0].resource_id
    domains = _changes(postgres_engine, project["id"], "knowledge_domains")
    assert domains[-1].operation == "knowledge_domains_delete"
    assert domains[-1].domain_id == UUID(adr["id"])


def test_transcript_capture_excludes_index_maintenance(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from mnemonic_api.services.transcripts import rebuild_transcripts
    from mnemonic_api.transcript_indexing import index_next_transcript

    work, _, _, _ = register(api, project, work_payload, tmp_path)
    assert [row.operation for row in _changes(postgres_engine, project["id"], "transcripts")] == [
        "transcript_registered"]
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    rows = _changes(postgres_engine, project["id"], "transcripts")
    assert [row.operation for row in rows] == [
        "transcript_registered", "transcript_content_updated"]
    assert all("source_path" not in row.after_state for row in rows)
    factory = api.app.state.session_factory
    with factory() as database:
        rebuild_transcripts(database, UUID(project["id"]), uuid4())
        database.commit()
    assert index_next_transcript(factory, api.app.state.settings)
    assert len(_changes(postgres_engine, project["id"], "transcripts")) == 2


@pytest.mark.parametrize("metadata", [
    {"changes": "notes"}, {"changes": []}, {"initial": ["notes"]},
    {"initial": {"title": "Forged title"}, "to_status": "done", "work_version": 999,
     "changes": {"status": {"before": "pending", "after": "done"}}},
])
def test_progress_metadata_cannot_forge_system_facts(
    api, project, work_payload, postgres_engine, metadata,
):
    work = create_work(api, project, work_payload)["work_item"]
    response = api.post(item_path(project, work) + "/events", json={
        "event_type": "progress", "body": "Retain as authored context", "metadata": metadata,
        "actor": actor(),
    })
    assert response.status_code == 201, response.text
    row = _changes(postgres_engine, project["id"], "work_items")[-1]
    assert row.operation == "progress" and row.changed_fields == []
    assert row.title == work["title"] and row.revision == "1" and row.work_status == "pending"
    assert row.after_state["status"] == "pending"
    assert row.before_state["status"] == "pending"


@pytest.mark.parametrize("historical", [False, True])
def test_report_human_actions_are_changes_to_original_work(
    empty_phase6_migration_engine, historical,
):
    engine = empty_phase6_migration_engine
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0072_force_claim_gates" if historical else "head")
    with Session(engine) as database, database.begin():
        project_id = _project(database)
        source = _work(database, project_id)
        report = _close(database, source)
        source_id, report_id = source.id, report.id
        followup = _work(database, project_id)
        followup_id = followup.id
        association = uuid4()
        database.execute(text("""
            INSERT INTO job_completion_report_follow_ups
            (id,project_id,report_id,source_work_item_id,follow_up_work_item_id,
             actor_client,actor_session_id)
            VALUES(:id,:p,:r,:source,:followup,'dashboard','phase12-proof')
        """), {"id": association, "p": project_id, "r": report_id,
               "source": source_id, "followup": followup_id})
        database.execute(text("""
            UPDATE job_completion_report_reviews SET dismissal_id=:dismissal,
            dismissal_actor_client='dashboard',dismissal_actor_session_id='change-test'
            WHERE report_id=:report
        """), {"dismissal": uuid4(), "report": report_id})
    if historical:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    changes = [row for row in _changes(engine, project_id, "work_items")
               if row.operation.startswith("job_completion_report_")]
    assert [row.operation for row in changes] == ["job_completion_report_follow_up_created",
                                                 "job_completion_report_dismissed"]
    assert all(row.resource_id == source_id for row in changes)
    assert changes[0].after_state["follow_up_work_item_id"] == str(followup_id)
    assert changes[1].after_state["report_id"] == str(report_id)
    assert all(row.origin == ("history_import" if historical else "live") for row in changes)
