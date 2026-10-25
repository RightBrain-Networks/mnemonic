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


def test_project_policy_is_absent_and_updates_are_rejected(api, project):
    endpoint = settings_path(project)
    initial = api.get(endpoint).json()
    assert "automatic_decision_reviews" not in initial
    assert "decision_review_priority" not in initial
    for changes in ({"automatic_decision_reviews": True}, {"decision_review_priority": 41}):
        response = api.patch(endpoint, json={"expected_revision": initial["revision"], **changes})
        assert response.status_code == 422, response.text
    assert api.get(endpoint).json() == initial


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


def test_migration_defaults_existing_domains_to_off(api, project, postgres_engine):
    migrate(postgres_engine, "0056_product_decisions", downgrade=True)
    migrate(postgres_engine, "head")
    domains = api.get(f"/api/v1/projects/{project['id']}/knowledge/domains").json()["items"]
    assert all(row["automatic_decision_reviews"] == "off" for row in domains)
    assert all(row["decision_review_priority"] == 40 for row in domains)


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
