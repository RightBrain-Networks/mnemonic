"""Lifecycle events must seal back to one exact resource; review leases require a mode."""

import importlib.util
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import WorkFollowUpResponseRequest, WorkItemPatch
from mnemonic_api.services import code_reviews
from mnemonic_api.services.work_items import update_work_record

from .code_review_fixtures import claim_review, close, configure, create, handoff, mandatory
from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


@pytest.fixture(params=["review", "question"])
def obligation(request, api, project, work_payload, checkpoint_fields):
    if request.param == "review":
        completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
        return completion, completion["code_review_request"], "code_reviews", "code_review_id"
    configure(api, project, code_review_optional_min_priority=0)
    work = create(api, project, work_payload)
    response, _ = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    completion = response.json()
    return (
        completion,
        completion["agent_follow_ups"][0],
        "work_agent_follow_ups",
        "work_follow_up_id",
    )


def _supersession_event(connection, obligation):
    completion, resource, table, ref = obligation
    kind = "code_review_superseded" if table == "code_reviews" else "work_follow_up_superseded"
    return connection.scalar(
        text(
            f"INSERT INTO work_events(project_id,work_item_id,event_type,actor_kind,actor_client,"
            f"actor_session_id,origin,{ref},metadata) "
            f"VALUES(:project,:work,:kind,'client','database-test','supersede','live',:resource,"
            f"jsonb_build_object('{ref}',CAST(CAST(:resource AS uuid) AS text))) RETURNING id"
        ),
        {
            "project": resource["project_id"],
            "work": completion["work_item"]["id"],
            "kind": kind,
            "resource": resource["id"],
        },
    )


def _reopen_payload(completion, resource, table):
    review = table == "code_reviews"
    return WorkItemPatch.model_validate(
        {
            "client_operation_id": str(uuid4()),
            "status": "pending",
            "expected_version": completion["work_item"]["version"],
            "actor": {"actor_client": "database-test", "actor_session_id": "supersede"},
            "supersede_code_review_id" if review else "supersede_follow_up_id": resource["id"],
            "expected_code_review_version" if review else "expected_follow_up_version": 1,
        }
    )


def test_event_only_supersession_rejects_at_commit(postgres_engine, obligation):
    staged = False
    with pytest.raises(DBAPIError, match="exact resource witness"):
        with postgres_engine.begin() as connection:
            assert _supersession_event(connection, obligation) is not None
            staged = True
    assert staged, "The event must be admitted for staging and rejected only at commit"
    _, resource, table, _ = obligation
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text(f"SELECT version FROM {table} WHERE id=:id"), {"id": resource["id"]}
            )
            == 1
        )


def test_duplicate_supersession_cannot_hide_behind_valid_transition(
    postgres_engine, obligation, monkeypatch
):
    completion, resource, table, _ = obligation
    original = code_reviews.stage_review_event
    staged = []

    def duplicate(database, work, kind, actor, **refs):
        if kind.endswith("_superseded"):
            staged.append(original(database, work, kind, actor, **refs).id)
        return original(database, work, kind, actor, **refs)

    monkeypatch.setattr(code_reviews, "stage_review_event", duplicate)
    with pytest.raises(DBAPIError, match="exact resource witness"):
        with Session(postgres_engine) as database, database.begin():
            work = database.get(WorkItem, UUID(completion["work_item"]["id"]))
            assert work is not None
            update_work_record(database, work, _reopen_payload(completion, resource, table))
            assert work.status == "pending"
    assert len(staged) == 1


def test_answer_request_and_supersession_may_share_one_transaction(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    configure(api, project, code_review_optional_min_priority=0)
    created = create(api, project, work_payload)
    response, _ = close(api, project, created, checkpoint_fields)
    assert response.status_code == 200, response.text
    completion = response.json()
    question = completion["agent_follow_ups"][0]
    payload = WorkFollowUpResponseRequest.model_validate(
        {
            "expected_follow_up_version": 1,
            "actor": {
                "actor_client": question["origin_client"],
                "actor_session_id": question["origin_session_id"],
            },
            "answer": {
                "kind": "code_review_recommendation",
                "recommend_review": True,
                "rationale": "Review the intertwined changes.",
                "code_review_handoff": handoff(),
            },
        }
    )
    with Session(postgres_engine) as database, database.begin():
        work = database.get(WorkItem, UUID(created["id"]))
        assert work is not None
        answered = code_reviews.answer_follow_up(database, work, UUID(question["id"]), payload)
        assert answered.code_review_request is not None
        review = answered.code_review_request.model_dump(mode="json")
        update_work_record(database, work, _reopen_payload(completion, review, "code_reviews"))
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT state FROM code_reviews WHERE id=:id"), {"id": review["id"]}
            )
            == "superseded"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT bool_and(mnemonic_code_review_event_is_sealed(id)) FROM work_events "
                    "WHERE work_item_id=:work"
                ),
                {"work": created["id"]},
            )
            is True
        )


def _insert_null_mode_lease(connection, review):
    connection.execute(
        text(
            "INSERT INTO work_leases(work_item_id,holder_client,holder_session_id,claim_request_id,"
            "lease_token,purpose,code_review_id,mode,acquired_at,renewed_at,expires_at) "
            "SELECT :work,'database-test','review-session','null-mode','test-only-token',"
            "'code_review',"
            ":review,NULL,instant,instant,instant+interval '10 minutes' "
            "FROM (SELECT clock_timestamp() instant) clock"
        ),
        {"work": review["work_item_id"], "review": review["id"]},
    )


@pytest.mark.parametrize("mutation", ["insert", "update"])
def test_review_lease_mode_null_fails_check_constraint(
    api, project, work_payload, checkpoint_fields, postgres_engine, mutation
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    if mutation == "update":
        claim_review(api, project, completion, checkpoint_fields)
    with pytest.raises(DBAPIError, match="ck_work_leases_purpose_valid"):
        with postgres_engine.begin() as connection:
            if mutation == "insert":
                _insert_null_mode_lease(connection, completion["code_review_request"])
            else:
                connection.execute(
                    text(
                        "UPDATE work_leases SET mode=NULL,lease_generation_id=:generation "
                        "WHERE work_item_id=:work"
                    ),
                    {"generation": uuid4(), "work": completion["work_item"]["id"]},
                )


def _audit(connection):
    path = BACKEND_DIR.parent / "scripts/audit_code_reviews.py"
    spec = importlib.util.spec_from_file_location("witness_audit", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit(connection)


def test_audit_detects_preexisting_event_without_resource_transition(postgres_engine, obligation):
    with postgres_engine.begin() as connection:
        connection.execute(text("ALTER TABLE work_events DISABLE TRIGGER code_review_event_sealed"))
        _supersession_event(connection, obligation)
        connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        connection.execute(text("ALTER TABLE work_events ENABLE TRIGGER code_review_event_sealed"))
    with postgres_engine.connect() as connection:
        report = _audit(connection)
        assert not report["ok"]
        assert report["findings"]["lifecycle_event_witness_mismatch"] == 1


def test_audit_detects_preexisting_null_review_mode(
    api, project, work_payload, checkpoint_fields, postgres_engine
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    with postgres_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE work_leases DROP CONSTRAINT ck_work_leases_purpose_valid")
        )
        _insert_null_mode_lease(connection, completion["code_review_request"])
    with postgres_engine.connect() as connection:
        report = _audit(connection)
        assert not report["ok"]
        assert report["findings"]["review_lease_mismatch"] == 1
