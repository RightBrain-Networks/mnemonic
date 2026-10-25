"""Future origin-edge opt-outs preserve provenance and every other inheritance path."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.models import WorkChangeInheritanceExclusion
from mnemonic_api.schemas import WorkItemCreate
from mnemonic_api.services.client_operations import (
    prepare_client_operation,
    reserve_client_operation,
)
from mnemonic_api.services.decision_changes import bind_work
from mnemonic_api.services.work_items import create_work_records
from mnemonic_api.work_inheritance_db import INHERITANCE_FINDINGS
from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_changes_postgres import root_review, update
from .test_decision_reviews_postgres import context
from .test_knowledge_postgres import path
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_relationships_postgres import add_relationship, relationship_payload
from .test_work_item_moves_postgres import _move_payload, _project
from .test_work_lineage_postgres import collection, create

pytestmark = pytest.mark.postgres
REASON = "Separate implementation; retain the discovery evidence."


def excluded(api, project, work_payload, origin, **changes):
    intent = {**work_payload, "discovered_from_work_item_id": origin["id"],
              "inherit_decision_change": False, "decision_change_inheritance_reason": REASON,
              "client_operation_id": str(uuid4()), **changes}
    return create(api, project, intent), intent


def work(api, project, identifier):
    return context(api, project, identifier)["work_item"]


def assert_integrity(engine):
    with engine.connect() as connection:
        findings = {name: connection.scalar(text(query))
                    for name, query in INHERITANCE_FINDINGS.items()}
    assert not any(findings.values()), findings


def test_excludes_origin_but_retains_edge_and_exact_receipt(
    api, project, work_payload, postgres_engine,
):
    _, _, revision, origin = root_review(api, project)
    creation, intent = excluded(api, project, work_payload, origin)
    child = creation["work_item"]
    assert "decision_change_ids" not in child
    edge, = creation["initial_relationships"]
    assert edge["relationship_type"] == "discovered-from"
    assert edge["target_work_item_id"] == origin["id"]
    assert child["decision_change_inheritance_exclusion"] == {
        "origin_work_item_id": origin["id"],
        "origin_context_checkpoint_id": origin["initial_checkpoint_id"],
        "reason": REASON, "created_at": child["created_at"],
    }
    ordinary = create(api, project, work_payload,
                      discovered_from_work_item_id=origin["id"])["work_item"]
    assert ordinary["decision_change_ids"] == [revision["change"]["change_id"]]
    assert "decision_change_inheritance_exclusion" not in ordinary
    assert create(api, project, intent) == creation
    changed = api.post(collection(project), json={**intent,
                       "decision_change_inheritance_reason": "Changed intent"})
    assert changed.status_code == 409
    assert_integrity(postgres_engine)


def test_later_propagation_skips_only_origin_and_explicit_adoption_still_propagates(
    api, project, work_payload, postgres_engine,
):
    _, _, revision, driver = root_review(api, project)
    origin = create(api, project, work_payload)["work_item"]
    creation, _ = excluded(api, project, work_payload, origin)
    child = creation["work_item"]
    grandchild = create(api, project, work_payload,
                       discovered_from_work_item_id=child["id"])["work_item"]
    add_relationship(api, project, relationship_payload(
        origin, driver, "discovered-from", context_checkpoint_id=driver["initial_checkpoint_id"],
    ))
    assert work(api, project, origin["id"])["decision_change_ids"]
    assert "decision_change_ids" not in work(api, project, child["id"])
    assert "decision_change_ids" not in work(api, project, grandchild["id"])
    episode = api.get(path(project) + "/assessments").json()["items"][0]
    adopted, _ = update(api, project, "adopt", work_item_id=child["id"],
                        expected_work_version=1, assessment_ids=[episode["id"]])
    assert adopted.status_code == 200, adopted.text
    change_id = adopted.json()["id"]
    assert change_id in work(api, project, child["id"])["decision_change_ids"]
    assert work(api, project, grandchild["id"])["decision_change_ids"] == \
        work(api, project, child["id"])["decision_change_ids"]
    assert revision["change"]["change_id"] != change_id
    assert_integrity(postgres_engine)


def test_alternate_initial_path_retains_inheritance_and_readded_origin_stays_excluded(
    api, project, work_payload, postgres_engine,
):
    _, _, revision, origin = root_review(api, project)
    alternate = create(api, project, work_payload,
                       discovered_from_work_item_id=origin["id"])["work_item"]
    creation, _ = excluded(api, project, work_payload, origin, initial_relationships=[{
        "type": "discovered-from", "direction": "outgoing",
        "other_work_item_id": alternate["id"],
        "context_checkpoint_id": alternate["initial_checkpoint_id"],
    }])
    assert creation["work_item"]["decision_change_ids"] == [revision["change"]["change_id"]]
    isolated, _ = excluded(api, project, work_payload, origin)
    edge, = isolated["initial_relationships"]
    removed = api.request("DELETE", f"/api/v1/projects/{project['id']}/relationships/{edge['id']}",
                          json={"actor": {"actor_client": "codex", "actor_session_id": "test"}})
    assert removed.status_code == 200, removed.text
    assert_integrity(postgres_engine)
    add_relationship(api, project, relationship_payload(
        isolated["work_item"], origin, "discovered-from",
        context_checkpoint_id=origin["initial_checkpoint_id"],
    ))
    assert "decision_change_ids" not in work(api, project, isolated["work_item"]["id"])
    # A new explicit bind at the child itself remains valid, including old descendants.
    with Session(postgres_engine) as database:
        bind_work(database, UUID(isolated["work_item"]["id"]),
                  UUID(revision["change"]["change_id"]))
        database.commit()
    assert work(api, project, isolated["work_item"]["id"])["decision_change_ids"]
    assert_integrity(postgres_engine)


def test_supplied_origin_checkpoint_is_retained(api, project, work_payload, checkpoint_fields):
    origin = create(api, project, work_payload)["work_item"]
    checkpoint = api.post(collection(project) + f"/{origin['id']}/checkpoints",
                          json={**checkpoint_fields, "kind": "context"})
    assert checkpoint.status_code == 201, checkpoint.text
    selected = checkpoint.json()["id"]
    creation, _ = excluded(api, project, work_payload, origin, initial_relationships=[{
        "type": "discovered-from", "direction": "outgoing", "other_work_item_id": origin["id"],
        "context_checkpoint_id": selected,
    }])
    assert creation["work_item"]["decision_change_inheritance_exclusion"][
        "origin_context_checkpoint_id"] == selected


@pytest.mark.parametrize("changes", [
    {"discovered_from_work_item_id": None}, {"decision_change_inheritance_reason": None},
    {"decision_change_inheritance_reason": " "}, {"decision_change_inheritance_reason": "x" * 4001},
    {"decision_change_inheritance_reason": "bad\u0000text"},
    {"inherit_decision_change": True}, {"inherit_decision_change": 0},
    {"client_operation_id": None},
])
def test_invalid_fresh_assertions_roll_back(api, project, work_payload, postgres_engine, changes):
    origin = create(api, project, work_payload)["work_item"]
    response = api.post(collection(project), json={
        **work_payload, "discovered_from_work_item_id": origin["id"],
        "inherit_decision_change": False, "decision_change_inheritance_reason": REASON,
        "client_operation_id": str(uuid4()), **changes,
    })
    assert response.status_code == 422, response.text
    with Session(postgres_engine) as database:
        assert database.scalar(select(WorkChangeInheritanceExclusion.work_item_id)) is None


def test_database_refuses_late_insertion_and_mutation(api, project, work_payload, postgres_engine):
    origin = create(api, project, work_payload)["work_item"]
    creation, _ = excluded(api, project, work_payload, origin)
    child = creation["work_item"]["id"]
    for statement in [
        "UPDATE work_change_inheritance_exclusions SET reason='rewrite' WHERE work_item_id=:id",
        "DELETE FROM work_change_inheritance_exclusions WHERE work_item_id=:id",
        "TRUNCATE work_change_inheritance_exclusions",
        "INSERT INTO work_change_inheritance_exclusions SELECT * "
        "FROM work_change_inheritance_exclusions WHERE work_item_id=:id",
    ]:
        with postgres_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text(statement), {"id": child})
            connection.commit()


def test_deferred_seal_rejects_uncompleted_creation_receipt(
    api, project, work_payload, postgres_engine,
):
    origin = create(api, project, work_payload)["work_item"]
    intent = {**work_payload, "discovered_from_work_item_id": origin["id"],
              "inherit_decision_change": False, "decision_change_inheritance_reason": REASON,
              "client_operation_id": str(uuid4())}
    with Session(postgres_engine) as database:
        prepared = prepare_client_operation("create_work", UUID(project["id"]), {},
                                            WorkItemCreate.model_validate(intent))
        reserved = reserve_client_operation(database, prepared, wait_seconds=1)
        database.info["client_operation_receipt_id"] = reserved.receipt_id
        create_work_records(database, UUID(project["id"]), reserved.domain_payload)
        with pytest.raises(DBAPIError, match="sealed creation receipt"):
            database.execute(text("SET CONSTRAINTS work_inheritance_seal IMMEDIATE"))
        database.rollback()
    # The entire work/exclusion/receipt transaction rolled back; exact intent can execute now.
    assert create(api, project, intent)["work_item"]["decision_change_inheritance_exclusion"]
    assert_integrity(postgres_engine)


def test_cross_project_origin_and_work_move_preserve_original_exclusion(
    api, project, work_payload, postgres_engine,
):
    elsewhere = _project(api, "Origin project")
    _, _, _, origin = root_review(api, elsewhere)
    creation, intent = excluded(api, project, work_payload, origin)
    child = creation["work_item"]
    assert "decision_change_ids" not in child
    destination = _project(api, "Moved independent work")
    moved = api.post(collection(project) + f"/{child['id']}/move",
                     json=_move_payload(destination, 1))
    assert moved.status_code == 200, moved.text
    assert work(api, destination, child["id"])["decision_change_inheritance_exclusion"] == \
        child["decision_change_inheritance_exclusion"]
    assert create(api, project, intent) == creation
    backup = _export(postgres_engine, destination)
    restore_project(postgres_engine, UUID(destination["id"]), io.BytesIO(backup))
    assert_integrity(postgres_engine)


def test_backup_roundtrip_and_corrupt_exclusion_rejection(
    api, project, work_payload, postgres_engine,
):
    origin = create(api, project, work_payload)["work_item"]
    creation, intent = excluded(api, project, work_payload, origin)
    backup = _export(postgres_engine, project)
    snapshot = _snapshot(postgres_engine, project)
    assert len(snapshot["work_change_inheritance_exclusions"]) == 1
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup))
    assert create(api, project, intent) == creation
    assert_integrity(postgres_engine)

    def corrupt(_header, rows):
        rows["work_change_inheritance_exclusions"][0]["reason"] = "Forged rationale"

    with pytest.raises(BackupError, match="integrity"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(backup, corrupt)))
    assert work(api, project, creation["work_item"]["id"])[
        "decision_change_inheritance_exclusion"]["reason"] == REASON
