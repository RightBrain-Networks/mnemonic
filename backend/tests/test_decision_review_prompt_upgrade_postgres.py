"""An updated shipped prompt affects future investigations, never retained snapshots."""

import hashlib
from pathlib import Path

import pytest

from mnemonic_api import prompt_storage

from .test_decision_impact_postgres import changed, decision
from .test_decision_reviews_postgres import context, reviews, settings
from .test_knowledge_postgres import domain

pytestmark = pytest.mark.postgres


def test_untouched_prompt_upgrade_preserves_generated_review_history(api, project, monkeypatch):
    settings(api, project, automatic_decision_reviews=True)
    endpoint = f"/api/v1/projects/{project['id']}/prompts/decision-impact-review"
    response = api.get(endpoint)
    assert response.status_code == 200, response.text
    old_content = (
        Path(__file__).parent / "fixtures" / "decision-impact-review-v1.md"
    ).read_bytes()
    path = (
        Path(api.app.state.settings.prompt_root)
        / "projects" / project["id"] / "decision-impact-review.md"
    )
    path.write_bytes(old_content)
    target = domain(api, project)
    first = decision(api, project, target, "Original policy")
    with monkeypatch.context() as original_release:
        original_release.setattr(prompt_storage, "_REPLACED_DEFAULTS", {})
        second = changed(api, project, target, first, "First policy update")
        earlier, = reviews(api, project)
        old_snapshot = context(api, project, earlier["work_item_id"])["initial_checkpoint"]
    assert old_snapshot["source_metadata"]["prompt_revision"] == hashlib.sha256(
        old_content
    ).hexdigest()

    changed(api, project, target, second, "Second policy update")
    later, = [item for item in reviews(api, project)
              if item["work_item_id"] != earlier["work_item_id"]]
    new_snapshot = context(api, project, later["work_item_id"])["initial_checkpoint"]
    new_hash = hashlib.sha256(prompt_storage.default_prompt("decision-impact-review").encode())
    assert new_snapshot["source_metadata"]["prompt_revision"] == new_hash.hexdigest()
    assert context(api, project, earlier["work_item_id"])["initial_checkpoint"] == old_snapshot
    assert api.get(endpoint).json()["created_at"] == response.json()["created_at"]
