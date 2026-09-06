"""Review history cannot move projects, while unrelated identity-preserving moves still work."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .code_review_database_fixtures import close_work, create_work, finish_review, policy

pytestmark = pytest.mark.postgres


def _destination(api):
    response = api.post("/api/v1/projects", json={"name": f"Review destination {uuid4()}"})
    assert response.status_code == 201, response.text
    return response.json()


def _protected_work(api, project, work_payload, checkpoint_fields, history):
    work = create_work(api, project, work_payload)
    policy(
        api,
        project,
        required=100 if history in ("disabled", "reopened", "question") else 0,
        optional=0 if history == "question" else 100,
    )
    closed = close_work(
        api,
        project,
        work,
        checkpoint_fields,
        review=history not in ("disabled", "reopened", "question"),
    )
    work = closed["work_item"]
    if history == "remediation":
        result = finish_review(api, project, work, closed["code_review_request"])
        return result["remediation_work"]["work_item"]
    if history == "reopened":
        response = api.patch(
            f"/api/v1/projects/{project['id']}/work-items/{work['id']}",
            json={
                "expected_version": work["version"],
                "status": "pending",
                "actor": {"actor_client": "database-test", "actor_session_id": "reopen"},
                "client_operation_id": str(uuid4()),
            },
        )
        assert response.status_code == 200, response.text
        return response.json()
    return work


@pytest.mark.parametrize(
    "history", ["requested", "question", "disabled", "reopened", "remediation"]
)
def test_direct_sql_cannot_move_review_history(
    api, project, work_payload, checkpoint_fields, postgres_engine, history
):
    work = _protected_work(api, project, work_payload, checkpoint_fields, history)
    target = _destination(api)
    for mutation in (
        "INSERT INTO work_item_moves(work_item_id,source_project_id,target_project_id,"
        "source_work_version,resulting_work_version,preserved_status,actor_kind) "
        "SELECT id,project_id,CAST(:target AS uuid),version,version+1,status,'unattributed' "
        "FROM work_items WHERE id=CAST(:work AS uuid)",
        "UPDATE work_items SET project_id=CAST(:target AS uuid),version=version+1,"
        "updated_at=clock_timestamp() WHERE id=CAST(:work AS uuid)",
    ):
        with pytest.raises(DBAPIError, match="review history cannot change projects"):
            with postgres_engine.begin() as connection:
                connection.execute(text(mutation), {"work": work["id"], "target": target["id"]})
    with postgres_engine.connect() as connection:
        assert (
            str(
                connection.scalar(
                    text("SELECT project_id FROM work_items WHERE id=:id"), {"id": work["id"]}
                )
            )
            == project["id"]
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM work_item_moves WHERE work_item_id=:id"),
                {"id": work["id"]},
            )
            == 0
        )


def test_clean_pending_move_preserves_move_metadata_and_review_sparse_contract(
    api, project, work_payload, postgres_engine
):
    work = create_work(api, project, work_payload)
    target = _destination(api)
    response = api.post(
        f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
        json={
            "target_project_id": target["id"],
            "expected_version": work["version"],
            "actor": {"actor_client": "database-test", "actor_session_id": "move"},
            "client_operation_id": str(uuid4()),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["work_item"]["id"] == work["id"]
    assert response.json()["work_item"]["project_id"] == target["id"]
    with postgres_engine.connect() as connection:
        events = connection.execute(
            text(
                "SELECT metadata->>'role',code_review_id,work_follow_up_id,"
                "work_follow_up_answer_id,code_review_result_id FROM work_events "
                "WHERE work_item_id=:id AND event_type='work_moved' ORDER BY metadata->>'role'"
            ),
            {"id": work["id"]},
        ).all()
    assert events == [("source", None, None, None, None), ("target", None, None, None, None)]
