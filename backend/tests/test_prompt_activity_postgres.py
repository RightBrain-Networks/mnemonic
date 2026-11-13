"""Prompt activity records observed hash changes without exposing prompt text."""

from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.postgres


def prompt_changes(engine):
    with engine.connect() as connection:
        return connection.execute(text(
            "SELECT changed_fields, before_state, after_state FROM project_changes "
            "WHERE operation = 'prompt_updated' ORDER BY sequence"
        )).all()


def test_prompt_edit_captures_hash_once_and_noop_does_not(api, project, postgres_engine):
    base = f"/api/v1/projects/{project['id']}/prompts/warm-code-review"
    before = api.get(base).json()
    assert prompt_changes(postgres_engine) == []
    content = "Private replacement instructions for this project's warm review."
    result = api.put(base, json={"content": content, "expected_revision": before["revision"]})
    assert result.status_code == 200, result.text
    changes = prompt_changes(postgres_engine)
    assert len(changes) == 1
    fields, old, new = changes[0]
    assert fields == ["prompts.warm-code-review"]
    assert old["sha256"] == before["revision"]
    assert new["sha256"] == result.json()["revision"]
    assert content not in str(changes)
    repeated = api.put(base, json={
        "content": content, "expected_revision": result.json()["revision"],
    })
    assert repeated.status_code == 200, repeated.text
    assert len(prompt_changes(postgres_engine)) == 1


def test_external_prompt_edit_is_recorded_when_observed(api, project, postgres_engine):
    base = f"/api/v1/projects/{project['id']}/prompts"
    assert api.get(base).status_code == 200
    root = Path(api.app.state.settings.prompt_root)
    path = root / "projects" / project["id"] / "resume-work.md"
    path.write_text("Externally edited prompt.\n")
    assert prompt_changes(postgres_engine) == []
    assert api.get(base + "/resume-work").status_code == 200
    changes = prompt_changes(postgres_engine)
    assert len(changes) == 1
    assert changes[0][0] == ["prompts.resume-work"]
    assert api.get(base).status_code == 200
    assert len(prompt_changes(postgres_engine)) == 1


def test_agent_render_observes_external_prompt_changes(api, project, postgres_engine):
    base = f"/api/v1/projects/{project['id']}/prompts/warm-code-review/render"
    assert api.post(base, json={}).status_code == 200
    root = Path(api.app.state.settings.prompt_root)
    path = root / "projects" / project["id"] / "warm-code-review.md"
    path.write_text("Externally edited review guidance.\n")
    rendered = api.post(base, json={})
    assert rendered.status_code == 200, rendered.text
    assert rendered.json()["content"] == "Externally edited review guidance.\n"
    assert prompt_changes(postgres_engine)[0][0] == ["prompts.warm-code-review"]
    assert api.post(base, json={}).status_code == 200
    assert len(prompt_changes(postgres_engine)) == 1
