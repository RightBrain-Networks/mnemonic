"""Verify Unicode version boundaries against real PostgreSQL exact/lexical results."""

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from mnemonic_api.external_duplicate_schemas import duplicate_title_key

from .test_duplicate_suggestions_postgres import FailingEmbedder

pytestmark = pytest.mark.postgres
FIXTURE = json.loads((Path(__file__).resolve().parents[2]
                      / "tests/fixtures/external-record-contract-v1.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["title_key_cases"])
def test_unicode_boundary_keys_match_postgresql_17(postgres_engine, case):
    with postgres_engine.connect() as connection:
        actual = connection.scalar(text("SELECT mnemonic_duplicate_title_key_v1(:title)"),
                                   {"title": case["value"]})
    assert actual == case["key"] == duplicate_title_key(case["value"])


@pytest.mark.parametrize("draft,candidate,exact", [
    ("A", "\U0001ccd6", False), ("\U0001ccd6", "\U0001ccd6", True),
    ("S", "\ua7f1", False), ("\ua7f1", "\ua7f1", True),
])
def test_external_response_keeps_sql_exact_result_without_unicode_version_503(
    api, project, draft, candidate, exact,
):
    api.app.state.semantic_embedder = FailingEmbedder()
    response = api.post(f"/api/v1/projects/{project['id']}/duplicate-suggestions", json={
        "title": draft, "summary": "the", "initial_prompt": "and",
        "external_candidates": [{"url": "https://example.com/1", "title": candidate,
                                  "state": "open", "body": ""}],
    })
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["external_scope"] == "lexical"
    if exact:
        assert page["external_items"][0]["reference"]["title"] == candidate
        assert "exact_title" in page["external_items"][0]["signals"]
    else:
        assert page["external_items"] == []
