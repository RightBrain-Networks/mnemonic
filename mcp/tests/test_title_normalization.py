"""Cold-review regression: newer Unicode cannot forge or reject PostgreSQL title equality."""

import json
from pathlib import Path

import pytest

from mnemonic_mcp.external_records import ExternalDuplicateSuggestion
from mnemonic_mcp.models import DuplicateSuggestionPage, DuplicateSuggestionRequest
from mnemonic_mcp.server import _duplicate_title_key, _suggestion_matches_request

FIXTURE = json.loads((Path(__file__).resolve().parents[2]
                      / "tests/fixtures/external-record-contract-v1.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["title_key_cases"])
def test_mcp_keys_use_postgres_unicode_15_1(case):
    assert _duplicate_title_key(case["value"]) == case["key"]


@pytest.mark.parametrize("title,candidate_title,exact", [
    ("A", "\U0001ccd6", False), ("\U0001ccd6", "\U0001ccd6", True),
    ("S", "\ua7f1", False), ("\ua7f1", "\ua7f1", True),
])
def test_sql_compatible_external_page_survives_request_correspondence(title, candidate_title, exact):
    reference = {"url": "https://example.com/1", "title": candidate_title, "state": "open"}
    request = DuplicateSuggestionRequest(
        title=title, summary="the", initial_prompt="and",
        external_candidates=[{**reference, "body": ""}],
    )
    page = DuplicateSuggestionPage.model_validate({
        "items": [], "limit": 5, "mode": "lexical", "semantic_available": False,
        "semantic_scope": "unavailable", "composition_version": "duplicate-suggestion-v1",
        "exact_title_group_total": 0, "omitted_exact_title_group_count": 0,
        "external_candidate_count": 1, "external_scope": "lexical",
        "external_items": [{"rank": 1, "signals": ["exact_title"],
                            "reference": reference}] if exact else [],
    })
    assert _suggestion_matches_request(page, request)
    if not exact:
        forged = page.model_copy(update={"external_items": [ExternalDuplicateSuggestion(
            rank=1, signals=["exact_title"], reference=reference,
        )]})
        assert not _suggestion_matches_request(forged, request)
