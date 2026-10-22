"""Agent prompt retrieval preserves project context and closeout revision semantics."""

import hashlib
from pathlib import Path

import pytest

from .test_project_reports_postgres import close, create
from .test_prompt_library_postgres import save_prompt

pytestmark = pytest.mark.postgres


def test_human_review_instructions_are_editable_isolated_and_expanded_once(
    api, project, work_payload,
):
    work = create(api, project, {**work_payload, "title": "$PROJECT_NAME is literal work text"})
    base = f"/api/v1/projects/{project['id']}"
    saved = save_prompt(api, base, "request-human-review",
                        "$PROJECT_NAME / $WORK_ITEM_TITLE / $WORK_ITEM_ID / $UNKNOWN")
    endpoint = base + "/prompts/request-human-review/render"
    response = api.post(endpoint, json={"work_item_id": work["id"]})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "project_id": project["id"], "prompt_id": "request-human-review",
        "work_item_id": work["id"], "code_review_id": None,
        "content": f"{project['name']} / {work['title']} / {work['id']} / $UNKNOWN",
        "revision": saved["revision"], "prompt_revision": None,
    }
    without_work = api.post(endpoint, json={}).json()
    assert "$WORK_ITEM_TITLE" in without_work["content"]
    other = api.post("/api/v1/projects", json={"name": "Other prompt owner"}).json()
    original = api.get(f"/api/v1/projects/{other['id']}/prompts/request-human-review").json()
    assert original["content"] != saved["content"]
    cross_project = api.post(
        f"/api/v1/projects/{other['id']}/prompts/request-human-review/render",
        json={"work_item_id": work["id"]},
    )
    assert cross_project.status_code == 404
    # A current read never caches earlier project instructions.
    changed = save_prompt(api, base, "request-human-review", "Changed $WORK_ITEM_TITLE")
    current = api.post(endpoint, json={"work_item_id": work["id"]}).json()
    assert current["content"] == f"Changed {work['title']}"
    assert current["revision"] == changed["revision"] != saved["revision"]


def test_priority_prompt_uses_saved_workspace_rubric_and_macro_renderer(api, project, work_payload):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    rubric = api.get(base + "/priority-rubric").json()
    content = "# $PROJECT_NAME\nConsider $WORK_ITEM_TITLE. Keep $UNKNOWN."
    saved = api.patch(base + "/priority-rubric", json={
        "expected_revision": rubric["revision"], "content": content,
    })
    assert saved.status_code == 200, saved.text
    response = api.post(base + "/prompts/priority-rubric/render", json={"work_item_id": work["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["content"] == (
        f"# {project['name']}\nConsider {work['title']}. Keep $UNKNOWN."
    )
    assert response.json()["revision"] == hashlib.sha256(content.encode()).hexdigest()
    assert response.json()["prompt_revision"] is None
    assert api.get(base + "/priority-rubric").json()["content"] == content


def test_report_read_revision_closes_work_and_preserves_receipt_after_template_edit(
    api, project, work_payload, checkpoint_fields,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    save_prompt(api, base, "job-completion-report", "Explain $WORK_ITEM_TITLE in $PROJECT_NAME.")
    endpoint = base + "/prompts/job-completion-report/render"
    prompt = api.post(endpoint, json={"work_item_id": work["id"]}).json()
    response, payload = close(api, project, work, checkpoint_fields, job_completion_report={
        "summary": "Completed the requested work.", "fyi_items": [],
        "prompt_revision": prompt["prompt_revision"],
        "decision_provenance": {
            "disposition": "not-code", "reason": "Synthetic prompt fixture; no code produced.",
        },
    })
    assert response.status_code == 200, response.text
    report = response.json()["job_completion_report"]
    save_prompt(api, base, "job-completion-report", "New instructions.")
    replay = api.post(base + f"/work-items/{work['id']}/complete", json=payload)
    assert replay.json() == response.json()
    detail = api.get(base + f"/job-completion-reports/{report['id']}").json()
    assert detail["report"]["authoring_prompt"] == prompt["content"]


def test_report_read_synchronizes_external_edits_and_stale_revision_is_rejected(
    api, project, work_payload, checkpoint_fields,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    endpoint = base + "/prompts/job-completion-report/render"
    before = api.post(endpoint, json={"work_item_id": work["id"]}).json()
    path = Path(api.app.state.settings.prompt_root) / "projects" / project["id"]
    (path / "job-completion-report.md").write_text("Externally changed $WORK_ITEM_TITLE")
    after = api.post(endpoint, json={"work_item_id": work["id"]}).json()
    assert int(after["prompt_revision"]) == int(before["prompt_revision"]) + 1
    assert after["revision"] != before["revision"]
    assert after["content"] == f"Externally changed {work['title']}"
    response, _ = close(api, project, work, checkpoint_fields, job_completion_report={
        "summary": "Completed the work.", "fyi_items": [],
        "prompt_revision": before["prompt_revision"],
        "decision_provenance": {
            "disposition": "not-code", "reason": "Synthetic prompt fixture; no code produced.",
        },
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "job_report_prompt_changed"


def test_unrelated_missing_report_does_not_hide_human_review_instructions(api, project):
    base = f"/api/v1/projects/{project['id']}"
    api.get(base + "/prompts/job-completion-report")
    path = Path(api.app.state.settings.prompt_root) / "projects" / project["id"]
    (path / "job-completion-report.md").unlink()
    response = api.post(base + "/prompts/request-human-review/render", json={})
    assert response.status_code == 200, response.text
    assert "Needs Attention" in response.json()["content"]
    assert api.post(base + "/prompts/job-completion-report/render", json={}).status_code == 503


def test_prompt_read_checks_exact_review_work_pair(api, project, work_payload, checkpoint_fields):
    from .code_review_fixtures import mandatory

    completed, _ = mandatory(api, project, work_payload, checkpoint_fields)
    other_work = create(api, project, {**work_payload, "title": "Unrelated"})
    response = api.post(f"/api/v1/projects/{project['id']}/prompts/warm-code-review/render", json={
        "work_item_id": other_work["id"], "code_review_id": completed["code_review_request"]["id"],
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "code_review_changed"
