"""Independent task counts, review queues and implementation status filters."""

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.code_review_fixtures import claim_review, create, mandatory
from tests.test_review_decisions_postgres import decide

pytestmark = pytest.mark.postgres


def test_tasks_separate_completed_work_from_active_review(
    api, project, work_payload, checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    pending = create(api, project, {**work_payload, "title": "Pending implementation"})
    active = create(api, project, {**work_payload, "title": "Active implementation"})
    base = f"/api/v1/projects/{project['id']}"
    response = api.post(f"{base}/work-items/{active['id']}/claim", json={
        "holder_client": "test", "holder_session_id": "implementation",
        "claim_request_id": str(uuid4()), "session_transcript": None,
    })
    assert response.status_code == 200, response.text
    page = api.get(base + "/tasks").json()
    assert page["work_items"] == {"active": 1, "pending": 1}
    assert page["code_reviews"] == {"active": 0, "pending": 1}
    assert [item["id"] for item in page["items"]] == [active["id"]]
    assert page["next_lease_expires_at"]

    claim_review(api, project, completion, checkpoint_fields)
    response = api.get(base + "/tasks")
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["work_items"] == {"active": 1, "pending": 1}
    assert page["code_reviews"] == {"active": 1, "pending": 0}
    assert {item["kind"] for item in page["items"]} == {"work_item", "code_review"}
    assert {item["id"] for item in page["items"]} == {active["id"], review["id"]}
    assert "lease_token" not in response.text
    assert api.get(base + "/tasks", params={"kind": "work_item", "status": "pending"}).json()[
        "items"
    ][0]["id"] == pending["id"]
    for view in ("full", "roots"):
        response = api.get(base + "/work-items", params={
            "view": view, "detail": "full", "status": "done", "status_scope": "work_item",
        })
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["summary"]["work_item"]["id"] == review["work_item_id"]
    response = api.post(base + "/search", json={
        "facets": ["work_items"], "filters": {"work_items": {
            "status": "done", "status_scope": "work_item",
        }},
    })
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


def test_task_counts_ignore_pagination_and_review_leases_expire(
    api, project, work_payload, checkpoint_fields, postgres_engine,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    claim_review(api, project, completion, checkpoint_fields)
    for index in range(3):
        create(api, project, {**work_payload, "title": f"Backlog {index}"})
    base = f"/api/v1/projects/{project['id']}"
    page = api.get(base + "/tasks", params={"status": "all", "limit": 1, "offset": 1}).json()
    assert page["total"] == 5  # Four implementation tasks and one review task.
    assert len(page["items"]) == 1
    assert page["work_items"]["pending"] == 3
    assert page["code_reviews"]["active"] == 1
    exact = api.get(base + "/tasks", params={"status": "all", "task_id": review["id"]}).json()
    assert exact["total"] == 1
    assert exact["items"][0]["work_item_id"] == review["work_item_id"]
    with postgres_engine.begin() as connection:
        connection.execute(text("""
            UPDATE work_leases SET acquired_at = clock_timestamp() - interval '20 minutes',
                renewed_at = clock_timestamp() - interval '20 minutes',
                expires_at = clock_timestamp() - interval '1 second'
            WHERE work_item_id = :id
        """), {"id": review["work_item_id"]})
    expired = api.get(base + "/tasks").json()
    assert expired["code_reviews"] == {"active": 0, "pending": 1}
    assert expired["total"] == 0
    assert expired["next_lease_expires_at"] is None
    assert api.get(f"/api/v1/projects/{uuid4()}/tasks").status_code == 404


def test_review_dispositions_change_only_the_review_task(
    api, project, work_payload, checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    work, review = completion["work_item"], completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}"
    for version, status in enumerate(["deferred", "done", "wont-do", "promoted", "to-review"]):
        response, _ = decide(api, project, work, review, version, status)
        assert response.status_code == 200, response.text
        work = response.json()
        page = api.get(base + "/tasks", params={"status": "all"}).json()
        states = {row["kind"]: row["status"] for row in page["items"]}
        assert states == {"work_item": "done", "code_review": (
            "pending" if status == "to-review" else status
        )}
        assert page["work_items"] == {"active": 0, "pending": 0}
        assert page["code_reviews"] == {"active": 0, "pending": int(status == "to-review")}
