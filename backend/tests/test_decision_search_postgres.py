"""Unified decision search preserves current/history scope and project isolation."""

from uuid import uuid4

import pytest

from .test_unified_search_postgres import search

pytestmark = pytest.mark.postgres


def domain(api, project, prefix="ADR"):
    response = api.post(f"/api/v1/projects/{project['id']}/knowledge/domains", json={
        "name": prefix + " decisions", "prefix": prefix, "client_operation_id": str(uuid4()),
    })
    assert response.status_code in (200, 201), response.text
    return response.json()


def put(api, project, selected_domain, **changes):
    payload = {"domain_id": selected_domain["id"], "title": "Storage transport",
               "content": "Historical durable provenance.\nA second line.",
               "media_type": "text/markdown", "state": "accepted", "author_kind": "human",
               "client_operation_id": str(uuid4()), **changes}
    response = api.put(f"/api/v1/projects/{project['id']}/knowledge/decisions", json=payload)
    assert response.status_code in (200, 201), response.text
    return response.json()["decision"]


def test_current_and_all_revision_search_are_distinct_pinned_hits(api, project):
    selected = domain(api, project)
    original = put(api, project, selected)
    put(api, project, selected, decision_id=original["decision_id"], expected_revision=0,
        content="Replacement durable provenance", state="proposed")
    options = {"facets": ["decisions"], "fulltext": True}
    assert search(api, project, q="Historical", **options)["total"] == 0
    history = search(api, project, q="Historical", filters={"decisions": {
        "all_revisions": True, "domain_ids": [selected["id"]],
    }}, **options)
    assert history["total"] == 1
    match = history["items"][0]["decision"]
    assert match["reference"] == "ADR-00001-0"
    assert match["revision"] == 0 and match["sha256"] == original["sha256"]
    assert "Historical" in match["snippet"]
    assert history["items"][0]["id"] == match["id"]
    page = search(api, project, filters={"decisions": {"all_revisions": True}}, **options)
    assert len({hit["id"] for hit in page["items"]}) == 2
    assert {hit["decision"]["revision"] for hit in page["items"]} == {0, 1}
    assert page["facet_totals"]["decisions"] == 2
    assert page["applied_filters"]["decisions"]["all_revisions"] is True
    assert page["facet_score_types"]["decisions"] == "none"


def test_decision_search_content_is_opt_in_domain_state_and_range_filters_apply(api, project):
    selected = domain(api, project)
    put(api, project, selected)
    second = domain(api, project, "ODR")
    put(api, project, second, title="Owner decision", state="rejected", content="durable result")
    assert search(api, project, q="durable", facets=["decisions"])["total"] == 0
    options = {"facets": ["decisions"], "q": "durable", "fulltext": True}
    page = search(api, project, filters={"decisions": {"domain_ids": [selected["id"]]}}, **options)
    assert page["total"] == 1 and page["items"][0]["decision"]["domain_id"] == selected["id"]
    assert page["facet_score_types"]["decisions"] == "postgresql_lexical"
    assert page["query_interpretation"]["decisions"]["fields"] == ["metadata", "content"]
    rejected = search(api, project, filters={"decisions": {"state": "rejected"}}, **options)
    assert rejected["total"] == 1
    assert rejected["items"][0]["decision"]["decision_id"] == "ODR-00001"
    empty = search(api, project, filters={"decisions": {
        "created_after": "2100-01-01T00:00:00Z",
    }}, **options)
    assert empty["total"] == 0
    assert empty["term_diagnostics"][0]["matches"]["decisions"] == 0


def test_default_discovery_includes_decisions_and_compact_metadata(api, project):
    put(api, project, domain(api, project))
    page = search(api, project, q="Storage transport", detail="compact")
    assert "decisions" in page["search_scope"]["searched_facets"]
    assert page["search_scope"]["transcripts"] == "omitted_by_default"
    assert page["total"] == 1 and "content" not in page["items"][0]["decision"]
    assert page["items"][0]["decision"]["author_kind"] == "human"


def test_literal_phrase_search_and_diagnostics_preserve_scoped_counts(api, project):
    selected = domain(api, project)
    put(api, project, selected, content="A literal % underscore_ token. alpha beta gamma")
    options = {"facets": ["decisions"], "fulltext": True}
    assert search(api, project, q="% underscore_", query_mode="literal", **options)["total"] == 1
    assert search(api, project, q="beta alpha", query_mode="phrase", **options)["total"] == 0
    assert search(api, project, q='"alpha beta"', **options)["total"] == 1
    missing = search(api, project, q="alpha missing", **options)
    assert missing["total"] == 0
    assert [row["matches"]["decisions"] for row in missing["term_diagnostics"]] == [1, 0]
    assert all(row["matches"]["work_items"] is None for row in missing["term_diagnostics"])


def test_all_revision_pages_and_mult_project_identity(api, project):
    selected = domain(api, project)
    original = put(api, project, selected)
    put(api, project, selected, decision_id=original["decision_id"], expected_revision=0,
        content="A new historical version")
    other_response = api.post("/api/v1/projects", json={"name": "Other decision project"})
    assert other_response.status_code == 201, other_response.text
    other = other_response.json()
    put(api, other, domain(api, other), content="Other project history")
    options = {"facets": ["decisions"], "filters": {"decisions": {"all_revisions": True}},
               "limit": 1}
    first = search(api, project, **options)
    second = search(api, project, offset=first["next_offset"], **options)
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["id"] != second["items"][0]["id"]
    response = api.post("/api/v1/search", json={**options, "limit": 10,
                        "project_ids": [project["id"], other["id"]]})
    assert response.status_code == 200, response.text
    both = response.json()
    assert both["total"] == 3
    assert {hit["project_id"] for hit in both["items"]} == {project["id"], other["id"]}


def test_large_valid_document_search_avoids_postgres_vector_limit(api, project):
    content = " ".join(str(index) for index in range(130_000)) + " late uniqueword"
    assert len(content.encode()) < 1_048_576
    put(api, project, domain(api, project), content=content)
    options = {"facets": ["decisions"], "fulltext": True}
    page = search(api, project, q="uniqueword", **options)
    assert page["total"] == 1
    assert "uniqueword" in page["items"][0]["decision"]["snippet"]
    assert search(api, project, q="late uniqueword", query_mode="phrase", **options)["total"] == 1


def test_search_reports_terms_contributed_by_both_title_and_body(api, project):
    put(api, project, domain(api, project))
    page = search(api, project, q="storage durable", facets=["decisions"], fulltext=True)
    assert page["total"] == 1
    assert page["items"][0]["decision"]["matched_fields"] == ["metadata", "content"]


@pytest.mark.parametrize("padding", ["", "unrelated " * 7000], ids=["short", "large"])
def test_long_repeated_phrase_retains_stemming_order_and_word_boundaries(api, project, padding):
    phrase = " ".join(["x"] * 260) + " running dog"
    content = padding + " ".join(["x"] * 260) + " runs dogs tail"
    put(api, project, domain(api, project), content=content)
    options = {"facets": ["decisions"], "fulltext": True, "query_mode": "phrase"}
    assert search(api, project, q=phrase, **options)["total"] == 1
    reversed_query = phrase.replace("running dog", "dog running")
    assert search(api, project, q=reversed_query, **options)["total"] == 0
    assert search(api, project, q=phrase.replace("running", "runaway"), **options)["total"] == 0
    assert search(api, project, q=" ".join(["x"] * 261) + " running dog", **options)["total"] == 0


@pytest.mark.parametrize("suffix,split", [("foo-bar", "foo bar"),
                                        ("example.org/path", "example.org /path")])
def test_long_phrase_preserves_native_compound_and_url_positions(api, project, suffix, split):
    prefix = " ".join(["x"] * 260) + " "
    selected = domain(api, project)
    original = put(api, project, selected, content=prefix + split)
    options = {"facets": ["decisions"], "fulltext": True, "query_mode": "phrase"}
    assert search(api, project, q=prefix + suffix, **options)["total"] == 0
    put(api, project, selected, decision_id=original["decision_id"], expected_revision=0,
        content=prefix + suffix)
    assert search(api, project, q=prefix + suffix, **options)["total"] == 1
