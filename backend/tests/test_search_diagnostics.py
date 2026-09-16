"""Defaults follow the same analyzed terms as literal content search."""

import pytest

from mnemonic_api.search_schemas import SearchRequest


@pytest.mark.parametrize("query, transcripts", [
    ("", True), ("   ", True), ("needle", True), ("needle needle", True),
    ("café CAFE", True), ("needle absent", False), ("needle-absent", False),
    ("SESSIONS_PER_REQUEST", False), ("needle " + "x" * 201, True),
])
def test_default_sources_follow_distinct_normalized_terms(query, transcripts):
    request = SearchRequest(q=query)
    assert ("transcripts" in request.facets) is transcripts
    assert "facets" not in request.model_fields_set
    explicit = SearchRequest(q=query, facets=["transcripts"])
    assert explicit.facets == ["transcripts"]
    assert "facets" in explicit.model_fields_set


def test_transcript_filters_do_not_opt_into_a_conjunctive_source():
    request = SearchRequest(q="needle absent", filters={"transcripts": {"client": "claude_code"}})
    assert request.facets == ["work_items", "artifacts"]
    with pytest.raises(ValueError):
        SearchRequest(q="needle absent", facet_order=[{"facet": "transcripts"}])
