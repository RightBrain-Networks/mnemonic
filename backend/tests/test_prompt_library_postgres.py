"""Project isolation, external-edit invalidation, rendering, and immutable report history."""

from pathlib import Path

import pytest
from sqlalchemy import text

from .test_project_reports_postgres import close, create

pytestmark = pytest.mark.postgres


def save_prompt(api, base, prompt_id, content):
    before = api.get(base + "/prompts/" + prompt_id)
    assert before.status_code == 200, before.text
    response = api.put(
        base + "/prompts/" + prompt_id,
        json={
            "content": content,
            "expected_revision": before.json()["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_library_metadata_conflicts_and_project_isolation(api, project):
    base = f"/api/v1/projects/{project['id']}"
    library = api.get(base + "/prompts")
    assert library.status_code == 200, library.text
    assert len(library.json()["items"]) == 7
    assert len(library.json()["macros"]) >= 8
    initial = api.get(base + "/prompts/recall-pointer").json()
    saved = save_prompt(api, base, "recall-pointer", "Recall $WORK_ITEM_ID 🧭")
    assert saved["size_bytes"] == len(saved["content"].encode())
    assert saved["created_at"] == initial["created_at"]
    assert (
        api.put(
            base + "/prompts/recall-pointer",
            json={
                "content": "Stale overwrite",
                "expected_revision": initial["revision"],
            },
        ).status_code
        == 409
    )
    other = api.post("/api/v1/projects", json={"name": "Other", "slug": "other"}).json()
    assert (
        api.get(f"/api/v1/projects/{other['id']}/prompts/recall-pointer").json()["content"]
        == (initial["content"])
    )


def test_report_macros_freeze_before_closeout_and_receipt_replay_survives_external_edit(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    save_prompt(
        api, base, "job-completion-report", "$PROJECT_NAME / $WORK_ITEM_STATUS / $WORK_ITEM_VERSION"
    )
    settings_response = api.get(base + "/settings", params={"work_item_id": work["id"]})
    assert settings_response.status_code == 200, settings_response.text
    settings = settings_response.json()
    expected = f"{project['name']} / pending / {work['version']}"
    assert settings["job_completion_report_prompt"] == expected
    response, payload = close(
        api,
        project,
        work,
        checkpoint_fields,
        job_completion_report={
            "summary": "Implemented the requested changes.",
            "fyi_items": [],
            "prompt_revision": settings["revision"],
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()["job_completion_report"]
    path = (
        Path(api.app.state.settings.prompt_root)
        / "projects"
        / project["id"]
        / "job-completion-report.md"
    )
    path.write_text("External new instructions")
    replay = api.post(base + f"/work-items/{work['id']}/complete", json=payload)
    assert replay.json() == response.json()
    detail = api.get(base + f"/job-completion-reports/{report['id']}")
    assert detail.json()["report"]["authoring_prompt"] == expected
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT prompt_text FROM job_completion_reports")) == expected
        columns = (
            connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='project_settings'"
                )
            )
            .scalars()
            .all()
        )
        assert "job_completion_report_prompt" not in columns
        assert "recall_pointer_template" not in columns


def test_external_template_and_project_changes_invalidate_fresh_closeouts(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    settings = api.get(base + "/settings").json()
    path = (
        Path(api.app.state.settings.prompt_root)
        / "projects"
        / project["id"]
        / "job-completion-report.md"
    )
    path.write_text("Externally customized closeout instructions")
    failed, _ = close(api, project, work, checkpoint_fields)
    assert (
        failed.status_code == 409 and failed.json()["detail"]["code"] == "job_report_prompt_changed"
    )
    current = api.get(base + "/settings").json()
    assert int(current["revision"]) == int(settings["revision"]) + 1
    assert api.patch(base, json={"name": "Renamed project"}).status_code == 200
    assert int(api.get(base + "/settings").json()["revision"]) == int(current["revision"]) + 1


def test_rendering_requires_project_owned_work_and_preserves_unknown_macros(
    api,
    project,
    work_payload,
):
    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    save_prompt(api, base, "resume-work", "$PROJECT_NAME / $WORK_ITEM_TITLE / $UNKNOWN")
    response = api.post(base + "/prompts/resume-work/render", json={"work_item_id": work["id"]})
    assert response.json() == {"content": f"{project['name']} / {work['title']} / $UNKNOWN"}
    other = api.post("/api/v1/projects", json={"name": "Other", "slug": "other"}).json()
    assert (
        api.post(
            f"/api/v1/projects/{other['id']}/prompts/resume-work/render",
            json={
                "work_item_id": work["id"],
            },
        ).status_code
        == 404
    )


def test_review_templates_render_scope_and_explicit_work_macros(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    from .code_review_fixtures import mandatory

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    review = completion["code_review_request"]
    base = f"/api/v1/projects/{project['id']}"
    payload = {"work_item_id": review["work_item_id"], "code_review_id": review["id"]}
    cold = api.post(base + "/prompts/cold-code-review/render", json=payload)
    assert cold.status_code == 200, cold.text
    assert review["id"] in cold.json()["content"]
    assert review["scope_sha256"] in cold.json()["content"]
    assert work_payload["title"] not in cold.json()["content"]
    assert work_payload["summary"] not in cold.json()["content"]
    save_prompt(api, base, "cold-code-review", "$PROJECT_NAME / $WORK_ITEM_TITLE / $CODE_REVIEW_ID")
    customized = api.post(
        base + "/prompts/cold-code-review/render", json={"code_review_id": review["id"]}
    )
    assert customized.json() == {
        "content": f"{project['name']} / {work_payload['title']} / {review['id']}"
    }
    other = api.post("/api/v1/projects", json={"name": "Other", "slug": "other"}).json()
    assert (
        api.post(
            f"/api/v1/projects/{other['id']}/prompts/cold-code-review/render", json=payload
        ).status_code
        == 409
    )


def test_remediation_uses_custom_instructions_and_retains_all_finding_witnesses(
    api,
    project,
    work_payload,
    checkpoint_fields,
):
    from .code_review_fixtures import claim_review, finding, mandatory, result_payload, result_url

    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    base = f"/api/v1/projects/{project['id']}"
    save_prompt(
        api, base, "review-remediation", "Custom remediation $CODE_REVIEW_ID for $WORK_ITEM_TITLE"
    )
    lease = claim_review(api, project, completion, checkpoint_fields)
    payload = result_payload(completion, lease, findings=[finding(), finding("F002")])
    response = api.post(result_url(project, completion), json=payload)
    assert response.status_code == 200, response.text
    created = response.json()["remediation_work"]
    checkpoint = created["initial_checkpoint"]["prompt"]
    assert checkpoint.startswith("Custom remediation " + completion["code_review_request"]["id"])
    assert "F001" in checkpoint and "F002" in checkpoint
    assert response.json()["result"]["id"] in checkpoint


def test_expanded_report_history_is_retained_and_prevents_lossy_downgrade(
    api,
    project,
    work_payload,
    checkpoint_fields,
    postgres_engine,
):
    from alembic import command
    from alembic.config import Config

    work = create(api, project, work_payload)
    base = f"/api/v1/projects/{project['id']}"
    save_prompt(api, base, "job-completion-report", "$WORK_ITEM_TITLE" * 500)
    settings_response = api.get(base + "/settings", params={"work_item_id": work["id"]})
    assert settings_response.status_code == 200, settings_response.text
    settings = settings_response.json()
    assert len(settings["job_completion_report_prompt"]) > 8000
    response, _ = close(
        api,
        project,
        work,
        checkpoint_fields,
        job_completion_report={
            "summary": "Implemented the requested changes.",
            "fyi_items": [],
            "prompt_revision": settings["revision"],
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()["job_completion_report"]
    detail = api.get(base + f"/job-completion-reports/{report['id']}")
    assert detail.json()["report"]["authoring_prompt"] == settings["job_completion_report_prompt"]
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(RuntimeError, match="Expanded report history"):
        with postgres_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0034_variable_work_leases")
    with postgres_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0035_prompt_library"
        )


def test_legacy_settings_patch_cannot_overwrite_an_external_change_after_revision_check(
    api,
    project,
    monkeypatch,
):
    from mnemonic_api.prompt_storage import PromptStorage

    base = f"/api/v1/projects/{project['id']}"
    settings = api.get(base + "/settings").json()
    original_write = PromptStorage.write

    def external_edit_before_write(storage, project_id, prompt_id, content, revision):
        path = storage.root / "projects" / str(project_id) / f"{prompt_id}.md"
        path.write_text("An external editor's newer instructions", encoding="utf-8")
        return original_write(storage, project_id, prompt_id, content, revision)

    monkeypatch.setattr(PromptStorage, "write", external_edit_before_write)
    response = api.patch(
        base + "/settings",
        json={
            "expected_revision": settings["revision"],
            "recall_pointer_template": "Stale editor draft",
        },
    )
    assert response.status_code == 409, response.text
    assert api.get(base + "/prompts/recall-pointer").json()["content"] == (
        "An external editor's newer instructions"
    )


@pytest.mark.parametrize("prompt_id", ["resume-work", "job-completion-report"])
def test_blank_macro_expansion_returns_a_controlled_prompt_error(api, project, prompt_id):
    base = f"/api/v1/projects/{project['id']}"
    assert api.patch(base, json={"description": ""}).status_code == 200
    save_prompt(api, base, prompt_id, "$PROJECT_DESCRIPTION")
    response = api.post(base + f"/prompts/{prompt_id}/render", json={})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "invalid_prompt"
    if prompt_id == "job-completion-report":
        settings = api.get(base + "/settings")
        assert settings.status_code == 422, settings.text
        assert settings.json()["detail"]["code"] == "invalid_prompt"
