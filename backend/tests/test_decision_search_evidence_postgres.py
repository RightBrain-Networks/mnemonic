"""Decision evidence names only fields that contribute to the accepted match."""

import pytest

from .test_decision_search_postgres import domain, put
from .test_unified_search_postgres import search

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("query,model_id,content,fields", [
    ("and the", "and", "the rest of this body", ["metadata", "content"]),
    ("AND THE", "and", "the rest of this body", ["metadata", "content"]),
    ("transport the", "transport", "the rest of this body", ["metadata", "content"]),
    ("and the", "and the", "unrelated body", ["metadata"]),
    ("and the", "unrelated-model", "and the rest of this body", ["content"]),
    ("and", "and", "unrelated body", ["metadata"]),
    ("", "and", "the rest of this body", []),
])
def test_substring_evidence_retains_both_sides_only_for_boundary_matches(
    api, project, query, model_id, content, fields,
):
    put(api, project, domain(api, project), title="Queue design", content=content,
        author_kind="agent", agent_session_id="search-evidence-session", model_id=model_id)
    page = search(api, project, q=query, facets=["decisions"], fulltext=True)
    assert page["total"] == 1
    assert page["items"][0]["decision"]["matched_fields"] == fields


@pytest.mark.parametrize("query_mode,fulltext,content", [
    ("terms", False, "the rest of this body"),
    ("literal", True, "the rest of this body"),
    ("phrase", True, "the rest of this body"),
    ("terms", True, "unrelated body"),
])
def test_boundary_evidence_does_not_expand_the_matching_scope(
    api, project, query_mode, fulltext, content,
):
    put(api, project, domain(api, project), title="Queue design", content=content,
        author_kind="agent", agent_session_id="search-evidence-session", model_id="and")
    page = search(api, project, q="and the", facets=["decisions"], fulltext=fulltext,
                  query_mode=query_mode)
    assert page["total"] == 0
    assert page["items"] == []
