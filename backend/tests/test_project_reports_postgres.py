"""Public closeout, inbox and durable-cursor behavior against PostgreSQL."""

from copy import deepcopy
from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


def actor(checkpoint):
    return {
        "actor_client": checkpoint["source_client"],
        "actor_session_id": checkpoint["source_session_id"],
        "actor_model": checkpoint.get("source_model"),
    }


def create(api, project, work_payload):
    response = api.post(f"/api/v1/projects/{project['id']}/work-items", json=work_payload)
    assert response.status_code == 201, response.text
    return response.json()["work_item"]


def close(api, project, work, checkpoint_fields, status="done", **overrides):
    report = {
        "summary": "The cache refreshes after switching branches and preserves existing entries.",
        "fyi_items": ["The cache keeps the existing retention period; it can be changed later."],
        "prompt_revision": "1",
    }
    base = f"/api/v1/projects/{project['id']}/work-items/{work['id']}"
    payload = {
        "expected_version": work["version"],
        "client_operation_id": str(uuid4()),
        "job_completion_report": report,
    }
    if status == "done":
        payload["checkpoint"] = {**checkpoint_fields, "affected_paths": ["src/cache.py"]}
        payload.update(overrides)
        response = api.post(base + "/complete", json=payload)
    else:
        payload.update(status=status, actor=actor(checkpoint_fields))
        payload.update(overrides)
        response = api.patch(base, json=payload)
    return response, payload


@pytest.mark.parametrize("status", ["done", "wont-do", "promoted"])
def test_closeout_report_is_atomic_replayable_and_independent(
    api,
    project,
    work_payload,
    checkpoint_fields,
    status,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    bootstrap = api.get(base + "/activity", params={"start": "now"}).json()
    response, payload = close(api, project, work, checkpoint_fields, status)
    assert response.status_code == 200, response.text
    report = response.json()["job_completion_report"]
    assert report["closeout_status"] == status
    assert report["work_item_id"] == work["id"]
    assert (report["completion_checkpoint_id"] is not None) == (status == "done")
    endpoint = base + f"/work-items/{work['id']}"
    replay = (
        api.post(endpoint + "/complete", json=payload)
        if status == "done"
        else api.patch(endpoint, json=payload)
    )
    assert replay.json() == response.json()
    activity = api.get(base + "/activity", params={"after": bootstrap["next_cursor"]}).json()
    assert [entry["kind"] for entry in activity["items"]] == [
        "work_event",
        "job_completion_report_created",
    ]
    assert len(api.get(base + "/job-completion-reports").json()["items"]) == 1
    assert api.get(base + "/job-completion-reports/count").json()["undismissed_count"] == "1"
    detail = api.get(base + f"/job-completion-reports/{report['id']}").json()
    assert "only LLM output" in detail["report"]["authoring_prompt"]
    assert "authoring_prompt" not in report
    reopened = api.patch(
        endpoint,
        json={
            "expected_version": work["version"] + 1,
            "status": "pending",
            "actor": actor(checkpoint_fields),
        },
    )
    assert reopened.status_code == 200, reopened.text
    reread = api.get(base + f"/job-completion-reports/{report['id']}").json()
    assert reread["source_work_state"]["status"] == "pending"
    assert reread["report"] == detail["report"]


def test_fresh_closeout_requires_report_key_and_current_prompt(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    _, payload = close(
        api,
        project,
        work,
        checkpoint_fields,
        job_completion_report={"summary": "Done.", "fyi_items": [], "prompt_revision": "2"},
    )
    bad = api.post(base + f"/work-items/{work['id']}/complete", json=payload)
    assert bad.status_code == 409 and bad.json()["detail"]["code"] == "job_report_prompt_changed"
    no_report = deepcopy(payload)
    del no_report["job_completion_report"]
    bad = api.post(base + f"/work-items/{work['id']}/complete", json=no_report)
    assert (
        bad.status_code == 422 and bad.json()["detail"]["code"] == "job_completion_report_required"
    )
    payload["job_completion_report"]["prompt_revision"] = "1"
    unkeyed = deepcopy(payload)
    del unkeyed["client_operation_id"]
    bad = api.post(base + f"/work-items/{work['id']}/complete", json=unkeyed)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "client_operation_id_required"
    assert api.get(base + "/job-completion-reports/count").json()["undismissed_count"] == "0"
    assert api.get(base + f"/work-items/{work['id']}").json()["work_item"]["version"] == 1
    successful = api.post(base + f"/work-items/{work['id']}/complete", json=payload)
    assert successful.status_code == 200, successful.text


def test_dismiss_and_follow_up_keep_provenance_and_exact_retry(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    work = create(api, project, work_payload)
    response, _ = close(api, project, work, checkpoint_fields)
    assert response.status_code == 200, response.text
    report_id = response.json()["job_completion_report"]["id"]
    base = f"/api/v1/projects/{project['id']}"
    report_url = base + f"/job-completion-reports/{report_id}"
    payload = {
        "client_operation_id": str(uuid4()),
        "actor": actor(checkpoint_fields),
        "title": "Change the font to Comic Sans",
        "summary": "Replace Arial with Comic Sans.",
        "initial_checkpoint": checkpoint_fields,
    }
    follow = api.post(report_url + "/follow-ups", json=payload)
    assert follow.status_code == 201, follow.text
    assert api.post(report_url + "/follow-ups", json=payload).json() == follow.json()
    result = follow.json()
    new_id = result["work_item"]["id"]
    assert result["work_item"]["status"] == "pending"
    assert result["follow_up"]["source_work_item_id"] == work["id"]
    assert result["follow_up"]["report_id"] == report_id
    assert api.get(report_url).json()["follow_up_count"] == "1"
    origin = api.get(
        base + f"/work-items/{new_id}/report-follow-ups", params={"direction": "origin"}
    )
    assert origin.json()["items"] == [result["follow_up"]]
    assert api.get(report_url + "/follow-ups").json()["items"] == [result["follow_up"]]
    dismissal = {"client_operation_id": str(uuid4()), "actor": actor(checkpoint_fields)}
    first = api.post(report_url + "/dismiss", json=dismissal)
    assert first.status_code == 200 and first.json()["dismissed"] is True, first.text
    assert api.post(report_url + "/dismiss", json=dismissal).json() == first.json()
    dismissal["client_operation_id"] = str(uuid4())
    again = api.post(report_url + "/dismiss", json=dismissal)
    assert again.json()["dismissed"] is False
    assert again.json()["human_dismissal"] == first.json()["human_dismissal"]
    assert api.get(base + "/job-completion-reports").json()["items"] == []
    assert api.get(base + "/job-completion-reports/count").json()["undismissed_count"] == "0"
    assert api.get(report_url).json()["human_dismissed"] is True


def test_settings_are_independent_revisioned_and_noop_is_quiet(api, project):
    base = f"/api/v1/projects/{project['id']}"
    initial = api.get(base + "/settings").json()
    assert initial["revision"] == "1" and initial["recall_pointer_template"] is None
    assert "multitasking" in initial["job_completion_report_prompt"]
    saved = api.patch(
        base + "/settings",
        json={"expected_revision": "1", "recall_pointer_template": "Recall the work context."},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == "2"
    assert saved.json()["job_completion_report_prompt"] == initial["job_completion_report_prompt"]
    cursor = api.get(base + "/activity", params={"start": "now"}).json()["next_cursor"]
    noop = api.patch(
        base + "/settings", json={"expected_revision": "2", "job_completion_report_prompt": None}
    )
    assert noop.json()["revision"] == "2"
    assert api.get(base + "/activity", params={"after": cursor}).json()["items"] == []
    stale = api.patch(
        base + "/settings", json={"expected_revision": "1", "recall_pointer_template": None}
    )
    assert stale.status_code == 409


def test_cursor_bounds_query_guards_and_dense_resume(api, project, work_payload):
    base = f"/api/v1/projects/{project['id']}"
    create(api, project, work_payload)
    first = api.get(base + "/activity", params={"limit": 1})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["has_more"] is True
    second = api.get(base + "/activity", params={"after": body["next_cursor"], "limit": 1}).json()
    assert int(second["items"][0]["sequence"]) == int(body["items"][0]["sequence"]) + 1
    for query in (
        {"after": "bad"},
        {"after": body["next_cursor"], "start": "now"},
        {"surprise": "private"},
        {"limit": 101},
    ):
        assert api.get(base + "/activity", params=query).status_code == 422
    assert api.get(base + "/job-completion-reports/count", params={"limit": 1}).status_code == 422


@pytest.mark.parametrize("suffix", ["/activity", "/job-completion-reports", "/settings"])
def test_new_project_reads_reject_bodies_and_ambiguous_queries(api, project, suffix):
    route = f"/api/v1/projects/{project['id']}" + suffix
    assert api.request("GET", route, content=b"{}").status_code == 422
    assert api.get(route, params=[("limit", "1"), ("limit", "2")]).status_code == 422
    assert api.get(route, params={"unexpected": "value"}).status_code == 422
