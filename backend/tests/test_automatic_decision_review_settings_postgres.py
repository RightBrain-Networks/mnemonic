"""Persist opt-in review policy and editable investigation prompts per project."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mnemonic_api.prompt_storage import default_prompt

from .test_artifact_extraction_migration_postgres import migrate
from .test_project_reports_postgres import create
from .test_prompt_library_postgres import save_prompt

pytestmark = pytest.mark.postgres


def settings_path(project):
    return f"/api/v1/projects/{project['id']}/settings"


def test_defaults_policy_round_trip_revision_and_project_isolation(api, project):
    endpoint = settings_path(project)
    initial = api.get(endpoint).json()
    assert initial["automatic_decision_reviews"] is False
    assert initial["decision_review_priority"] == 40
    changes = {"automatic_decision_reviews": True, "decision_review_priority": 41}
    response = api.patch(endpoint, json={"expected_revision": initial["revision"], **changes})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert all(saved[key] == value for key, value in changes.items())
    assert int(saved["revision"]) == int(initial["revision"]) + 1
    assert api.get(endpoint).json() == saved
    assert api.patch(endpoint, json={
        "expected_revision": saved["revision"], **changes,
    }).json() == saved
    assert api.patch(endpoint, json={
        "expected_revision": initial["revision"], "automatic_decision_reviews": False,
    }).status_code == 409
    disabled = api.patch(endpoint, json={
        "expected_revision": saved["revision"], "automatic_decision_reviews": False,
    }).json()
    assert disabled["automatic_decision_reviews"] is False
    assert disabled["decision_review_priority"] == 41
    other = api.post("/api/v1/projects", json={"name": "Independent review policy"}).json()
    other_settings = api.get(settings_path(other)).json()
    assert other_settings["automatic_decision_reviews"] is False
    assert other_settings["decision_review_priority"] == 40


@pytest.mark.parametrize("assignment", [
    "automatic_decision_reviews=true",
    "decision_review_priority=41",
    "decision_review_priority=-1,revision=revision+1",
    "decision_review_priority=101,revision=revision+1",
])
def test_database_requires_valid_policy_and_exact_revision(postgres_engine, project, assignment):
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text(
            f"UPDATE project_settings SET {assignment} WHERE project_id=:id"
        ), {"id": project["id"]})


def test_migration_defaults_existing_projects_and_preserves_saved_policy(
    api, project, postgres_engine,
):
    migrate(postgres_engine, "0056_product_decisions", downgrade=True)
    migrate(postgres_engine, "head")
    settings = api.get(settings_path(project)).json()
    assert settings["automatic_decision_reviews"] is False
    assert settings["decision_review_priority"] == 40
    changed = api.patch(settings_path(project), json={
        "expected_revision": settings["revision"], "automatic_decision_reviews": True,
    })
    assert changed.status_code == 200, changed.text
    migrate(postgres_engine, "head")
    assert api.get(settings_path(project)).json() == changed.json()
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0056_product_decisions", downgrade=True)
    assert api.get(settings_path(project)).json() == changed.json()


def test_decision_review_prompt_is_editable_isolated_and_renders_work(api, project, work_payload):
    base = f"/api/v1/projects/{project['id']}"
    initial = api.get(base + "/prompts/decision-impact-review")
    assert initial.status_code == 200, initial.text
    assert initial.json()["content"] == default_prompt("decision-impact-review")
    library_ids = {entry["id"] for entry in api.get(base + "/prompts").json()["items"]}
    assert "decision-impact-review" in library_ids
    work = create(api, project, work_payload)
    saved = save_prompt(
        api, base, "decision-impact-review", "Investigate $WORK_ITEM_ID in $PROJECT_NAME.",
    )
    rendered = api.post(
        base + "/prompts/decision-impact-review/render", json={"work_item_id": work["id"]},
    )
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["content"] == f"Investigate {work['id']} in {project['name']}."
    assert rendered.json()["revision"] == saved["revision"]
    assert rendered.json()["prompt_revision"] is None
    other = api.post("/api/v1/projects", json={"name": "Independent decision prompt"}).json()
    other_prompt = api.get(f"/api/v1/projects/{other['id']}/prompts/decision-impact-review")
    assert other_prompt.json()["content"] == initial.json()["content"]
