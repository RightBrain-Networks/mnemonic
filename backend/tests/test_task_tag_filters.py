"""Review labels filter review episodes without changing implementation lifecycle."""

import pytest

from tests.code_review_fixtures import close, create, mandatory
from tests.test_manual_review_requests_postgres import request_review

pytestmark = pytest.mark.postgres


def test_review_tag_keeps_episode_identity_and_completion_state(
    api, project, work_payload, checkpoint_fields,
):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    create(api, project, {**work_payload, "title": "Other pending work"})
    base = f"/api/v1/projects/{project['id']}"
    response = api.get(base + "/tasks", params={
        "status": "pending", "tags": "code-review", "include_summary": "true",
    })
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1
    task = page["items"][0]
    assert task["id"] == review["id"]
    assert task["kind"] == "code_review"
    assert task["status"] == "pending"
    assert task["system_tags"] == ["code-review"]
    assert task["work_summary"]["work_item"]["status"] == "done"
    assert task["work_summary"]["work_item"]["id"] == review["work_item_id"]
    assert task["tags"] is None
    assert task["created_at"]
    assert "lease_token" not in response.text
    response = api.get(base + "/tasks", params=[
        ("status", "all"), ("tags", "code-review"), ("tags", "remediation"),
        ("tag_mode", "and"),
    ])
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    response = api.get(base + "/tasks", params=[
        ("status", "all"), ("tags", "code-review"), ("tags", "remediation"),
        ("tag_mode", "or"),
    ])
    assert response.json()["total"] == 1
    assert api.get(base + "/tasks", params={
        "status": "all", "q": "no-such-title", "tags": "code-review",
    }).json()["total"] == 0


@pytest.mark.parametrize("manual", [False, True])
def test_review_queue_uses_episode_priority_after_implementation_priority_changes(
    api, project, work_payload, checkpoint_fields, manual,
):
    if manual:
        work = create(api, project, {**work_payload, "priority": 30})
        response, _ = close(api, project, work, checkpoint_fields)
        assert response.status_code == 200, response.text
        response, _ = request_review(api, project, response.json()["work_item"])
        assert response.status_code == 200, response.text
        work = response.json()
    else:
        completed, _ = mandatory(api, project, {**work_payload, "priority": 30}, checkpoint_fields)
        work = completed["work_item"]
    base = f"/api/v1/projects/{project['id']}"
    response = api.patch(f"{base}/work-items/{work['id']}", json={
        "expected_version": work["version"], "priority": 90,
    })
    assert response.status_code == 200, response.text
    pending = create(api, project, {**work_payload, "priority": 50})
    response = api.get(base + "/tasks", params={
        "status": "pending", "sort": "priority", "limit": 1,
    })
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == pending["id"]
    response = api.get(base + "/tasks", params={
        "status": "pending", "sort": "priority", "limit": 1, "offset": 1,
    })
    assert response.status_code == 200, response.text
    review = response.json()["items"][0]
    assert review["kind"] == "code_review"
    assert review["work_item_id"] == work["id"]
    assert review["priority"] == 30
