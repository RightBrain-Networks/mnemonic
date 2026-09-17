"""Native MCP query intent, match evidence and ranking boundaries."""

import copy
import json
from uuid import UUID

import httpx
import pytest
from conftest import CHECKPOINT_ID, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking, semantic_disposition
from test_compact_search import native_call, response_page
from test_duplicate_suggestions import required_arguments, suggestion_page

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)
from mnemonic_mcp.server import build_server

MODELS = {"work_items": WorkAppliedFilters, "artifacts": ArtifactAppliedFilters,
          "transcripts": TranscriptAppliedFilters}
TOOLS = ["search_work", "search", "search_artifact_contents", "search_transcript_contents"]


def exact_page(tool, summary, mode="literal", query="lease_token_mismatch"):
    page = response_page(tool, summary, q=query)
    scopes = page["search_scope"]["searched_facets"] if tool == "search" else [
        {"search_work": "work_items", "search_artifact_contents": "artifacts",
         "search_transcript_contents": "transcripts"}[tool]]
    page.update(search_disclosure(UUID(PROJECT_ID), query, query_mode=mode,
                                 **{scope: MODELS[scope]() for scope in scopes}).model_dump(mode="json"))
    if "match_mode" in page: page["match_mode"] = mode
    for key in ("score_type", "total_kind", "facet_score_types", "facet_total_kinds"):
        page.pop(key, None)
    for hit in page["items"]:
        row = hit[{"work_items": "work_item", "artifacts": "artifact", "transcripts": "transcript"}[hit["facet"]]] if tool == "search" else hit
        row.pop("score_type", None)
        hit.pop("score_type", None)
    return add_ranking(page, "search" if tool == "search" else scopes[0], query, mode)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("mode", ["phrase", "literal"])
async def test_exact_intent_reaches_every_search_door_and_survives_empty_pages(settings, work_summary, tool, mode):
    page = exact_page(tool, work_summary, mode)
    actual, requests = await native_call(settings, tool, {"q": "lease_token_mismatch", "query_mode": mode}, page)
    assert actual == page
    request = requests[0]
    payload = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
    assert payload["query_mode"] == mode
    empty = copy.deepcopy(page)
    empty.update(items=[], total=0)
    if tool == "search": empty["facet_totals"] = dict.fromkeys(MODELS, 0)
    assert (await native_call(settings, tool, {"q": "lease_token_mismatch", "query_mode": mode}, empty))[0] == empty
    empty["query_interpretation"]["query_mode"] = "terms"
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, tool, {"q": "lease_token_mismatch", "query_mode": mode}, empty)


@pytest.mark.parametrize("tool", TOOLS)
async def test_quoted_terms_are_honored_without_ignored_operator_warning(settings, work_summary, tool):
    query = 'lease "admission cookie"'
    page = exact_page(tool, work_summary, "terms", query)
    if "match_mode" in page: page["match_mode"] = "phrase"
    actual, _ = await native_call(settings, tool, {"q": query}, page)
    assert actual["warnings"] == []
    with pytest.raises(ToolError, match="unclosed_query_phrase") as caught:
        await native_call(settings, tool, {"q": 'PRIVATE-QUERY "'}, page)
    assert "PRIVATE-QUERY" not in str(caught.value)


async def test_work_evidence_preserves_checkpoint_and_winning_alias_identity(settings, work_summary):
    page = exact_page("search_work", work_summary)
    item = page["items"][0]
    item.update(matched_fields=["checkpoint"], excerpts_truncated=False,
                matched_member={"id": OTHER_WORK_ID, "title": "Alias", "status": "done"},
                excerpts=[{"field": "checkpoint", "text": "lease_token_mismatch",
                           "matched_member_id": OTHER_WORK_ID, "checkpoint_id": CHECKPOINT_ID,
                           "match_type": "literal"}])
    args = {"q": "lease_token_mismatch", "query_mode": "literal", "work_fields": ["checkpoint"]}
    page["applied_filters"]["work_items"]["work_fields"] = ["checkpoint"]
    page["query_interpretation"]["work_items"]["fields"] = ["checkpoint"]
    actual, requests = await native_call(settings, "search_work", args, page)
    assert actual == page and requests[0].url.params.get_list("work_fields") == ["checkpoint"]
    for patch in ({"matched_member_id": WORK_ID}, {"checkpoint_id": None}, {"text": "x" * 321}, {"match_type": "lexical"}):
        wrong = copy.deepcopy(page)
        wrong["items"][0]["excerpts"][0].update(patch)
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, "search_work", args, wrong)


@pytest.mark.parametrize("args,code", [
    ({"query_mode": "literal", "q": " "}, "exact_query_requires_text"),
    ({"query_mode": "phrase", "q": "!!!"}, "query_phrase_requires_terms"),
    ({"semantic": True, "q": '"cookie"'}, "semantic_requires_unconstrained_work_query"),
    ({"semantic": True, "q": "cookie", "work_fields": ["title"]}, "semantic_requires_all_work_fields"),
])
async def test_query_refusals_are_static_and_local(settings, work_summary, args, code):
    with pytest.raises(ToolError, match=code):
        await native_call(settings, "search_work", args, {})


@pytest.mark.parametrize("tool", ["search_work", "search"])
async def test_ranked_corpus_and_cache_failure_are_not_reported_as_lexical_matches(settings, work_summary, tool):
    page = response_page(tool, work_summary)
    args = {"q": "needle", "semantic": True} if tool == "search_work" else {"q": "needle", "filters": {"work_items": {"semantic": True}}}
    page["semantic"] = semantic_disposition("completed", scope="full_scope", cache="failed", partial=True)
    page["query_interpretation"]["work_items"]["match_mode"] = "hybrid_lexical_semantic"
    page["total_kind"] = "ranked_candidates" if tool == "search_work" else "mixed"
    work = page["items"][0] if tool == "search_work" else page["items"][0]["work_item"]
    work["score_type"] = "hybrid_reciprocal_rank"
    if tool == "search_work": page["score_type"] = "hybrid_reciprocal_rank"
    else:
        page["facet_score_types"]["work_items"] = "hybrid_reciprocal_rank"
        page["facet_total_kinds"]["work_items"] = "ranked_candidates"
    assert (await native_call(settings, tool, args, page))[0] == page
    page["semantic"]["comparison_incomplete"] = False
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, tool, args, page)


async def test_semantic_error_keeps_bounded_retry_and_never_reflects_provider_text(settings):
    disposition = semantic_disposition("unavailable")
    async def run(context):
        api = MnemonicAPI(settings, httpx.MockTransport(lambda request: httpx.Response(503, json={
            "detail": {"code": "semantic_unavailable", "message": "PRIVATE-PROVIDER", "context": context}})))
        with pytest.raises(ToolError) as caught:
            await build_server(settings, api).call_tool("search_work", {"project_id": PROJECT_ID, "q": "query", "semantic": True})
        assert "Retry once after one second" in str(caught.value)
        assert "PRIVATE" not in str(caught.value)
    await run({"semantic": disposition})
    disposition["inference"]["reason"] = "PRIVATE-PROVIDER"
    await run({"semantic": disposition})


async def test_duplicate_fallback_keeps_incomplete_disposition(settings):
    page = suggestion_page(mode="lexical", semantic_available=False, semantic_scope="unavailable")
    page["items"][0]["signals"] = ["exact_title", "lexical"]
    api = MnemonicAPI(settings, httpx.MockTransport(lambda request: httpx.Response(200, json=page)))
    result = await build_server(settings, api).call_tool("suggest_duplicate_work", required_arguments())
    result = result[1] if isinstance(result, tuple) else result
    assert result["semantic"]["comparison_incomplete"] is True
    assert result["semantic"]["retry"] == {"max_attempts": 1, "after_seconds": 1}


@pytest.mark.parametrize("tool", ["search", "search_transcript_contents"])
async def test_exact_transcript_span_can_omit_excerpt_but_keep_pinned_segment(settings, work_summary, tool):
    from test_transcript_segments import REVISION, segment

    page = exact_page(tool, work_summary)
    if tool == "search":
        row = page["items"][2]["transcript"]
        page["query_interpretation"]["artifacts"]["fulltext"] = True
        page["query_interpretation"]["artifacts"]["fields"] = ["metadata", "content"]
    else:
        row = page["items"][0]
    page["query_interpretation"]["transcripts"].update(fulltext=True, fields=["metadata", "content"])
    row.update(matched_fields=["content"], segment_id=segment().segment_id,
               content_kind="human_text", normalized_revision=REVISION, snippet=None,
               snippet_omission_reason="matched_span_exceeds_budget")
    args = {"q": "lease_token_mismatch", "query_mode": "literal", "fulltext": True}
    assert (await native_call(settings, tool, args, page))[0] == page
    for patch in ({"snippet_omission_reason": None}, {"segment_id": None, "content_kind": None},
                  {"snippet": "unrelated"}, {"matched_fields": ["metadata"]}):
        wrong = copy.deepcopy(page)
        target = wrong["items"][2]["transcript"] if tool == "search" else wrong["items"][0]
        target.update(patch)
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, tool, args, wrong)


@pytest.mark.parametrize("mutation", ["global_rank", "source_rank", "score_type", "unsearched_kind", "incomplete", "missing_semantic", "boolean_rank"])
async def test_unified_rank_and_coverage_lies_fail_closed(settings, work_summary, mutation):
    page = response_page("search", work_summary)
    if mutation == "global_rank": page["items"][0]["rank"] = 2
    elif mutation == "source_rank": page["items"][0]["work_item"]["rank"] = 2
    elif mutation == "score_type": page["items"][0]["work_item"]["score_type"] = "cosine_similarity"
    elif mutation == "unsearched_kind": page["facet_total_kinds"]["work_items"] = None
    elif mutation == "incomplete": page["coverage"]["transcripts"]["unsegmented_content_omitted"] = 1
    elif mutation == "missing_semantic": del page["semantic"]
    else: page["items"][0]["rank"] = True
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search", {"q": "needle"}, page)


async def test_unified_validation_never_echoes_query_or_nested_filter_values(settings, work_summary, caplog):
    args = {"q": "PRIVATE-QUERY", "filters": {"transcripts": {"content_kinds": ["human_text"]}}}
    with pytest.raises(ToolError, match="content_kinds_requires_fulltext") as caught:
        await native_call(settings, "search", args, {})
    assert "PRIVATE-QUERY" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("missing", ["semantic", "rank", "score", "score_type", "cache_refresh", "partial_vectors"])
async def test_work_ranking_cannot_synthesize_missing_disclosure(settings, work_summary, missing):
    page = response_page("search_work", work_summary, q="")
    target = page if missing == "semantic" else page["semantic"] if missing in {"cache_refresh", "partial_vectors"} else page["items"][0]
    del target[missing]
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search_work", {}, page)
