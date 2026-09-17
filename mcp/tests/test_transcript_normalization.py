"""Exercise real MCP defaults and request-bound normalized conversation retrieval."""

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from conftest import NOW, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking
from test_compact_search import native_call
from test_transcript_segments import REVISION, segment
from test_transcripts import TRANSCRIPT_ID, transcript
from test_unified_search import page

from mnemonic_mcp.search_disclosure import TranscriptAppliedFilters, search_disclosure
from mnemonic_mcp.transcript_models import CompactTranscriptRead


def search_result(tool, detail, kinds):
    record = transcript(segment_id=segment().segment_id, content_kind="human_text",
                        normalized_revision=REVISION, snippet="needle in a human message")
    if detail == "compact":
        record = {key: value for key, value in record.items()
                  if key in CompactTranscriptRead.model_fields}
    if tool == "search":
        result = page([{"facet": "transcripts", "id": TRANSCRIPT_ID, "project_id": PROJECT_ID, "created_at": NOW,
                       "updated_at": NOW, "score": 0.01, "transcript": record}], limit=20)
        result["search_scope"]["searched_facets"] = ["transcripts"]
        result["work_rank_scope"] = "work_items"
        result["tag_counts"] = None
        result["project_coverage"] = [{
            "project_id": PROJECT_ID, "project_name": "Test project", "project_slug": "test-project",
            "facet_totals": result["facet_totals"], "coverage": result["coverage"],
            "indexing_incomplete": result["indexing_incomplete"],
        }]
    else:
        result = {"items": [record], "total": 1, "limit": 20, "offset": 0,
                  "term_diagnostics": [], "indexing_incomplete": False}
    add_ranking(result, "search" if tool == "search" else "transcripts", "needle")
    return {**result, "detail": detail, **search_disclosure(UUID(PROJECT_ID), "needle",
        fulltext=True, transcripts=TranscriptAppliedFilters(content_kinds=kinds)).model_dump(
            mode="json")}


@pytest.mark.parametrize("tool", ["search", "search_transcript_contents"])
@pytest.mark.parametrize("detail", ["compact", "full"])
async def test_content_kind_scope_and_normalized_locators_survive_both_search_front_doors(
    settings, tool, detail,
):
    kinds = ["human_text", "assistant_text"]
    args = {"q": "needle", "fulltext": True, "detail": detail}
    args.update({"facets": ["transcripts"], "filters": {"transcripts": {"content_kinds": kinds}}}
                if tool == "search" else {"content_kinds": kinds})
    response = search_result(tool, detail, kinds)
    actual, requests = await native_call(settings, tool, args, response)
    assert actual == response
    body = json.loads(requests[0].content)
    assert (body["filters"]["transcripts"] if tool == "search" else body)["content_kinds"] == kinds
    for failure in ("echo", "kind", "revision", "coverage"):
        wrong = copy.deepcopy(response)
        record = wrong["items"][0]["transcript"] if tool == "search" else wrong["items"][0]
        if failure == "echo":
            wrong["applied_filters"]["transcripts"]["content_kinds"] = ["tool_result"]
        elif failure == "kind":
            record["content_kind"] = "tool_result"
        elif failure == "revision":
            record["normalized_revision"] = None
        else:
            record["normalization_incomplete"] = True
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, tool, args, wrong)


@pytest.mark.parametrize("tool", ["search", "search_transcript_contents"])
async def test_content_kind_without_fulltext_names_the_reviewed_rule(settings, tool):
    args = {"q": "needle"}
    args.update({"filters": {"transcripts": {"content_kinds": ["human_text"]}}}
                if tool == "search" else {"content_kinds": ["human_text"]})
    with pytest.raises(ToolError, match="content_kinds requires fulltext=true"):
        await native_call(settings, tool, args, {})


def text_page(*, truncated=False, offset=0):
    first = segment(text_truncated=truncated, text_offset=offset).model_dump(mode="json")
    return {"project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID, "text_sha256": None,
            "text": first["text"], "total_chars": 99 if truncated else len(first["text"]),
            "offset": offset, "limit": 20_000, "next_offset": None, "status": "pending",
            "truncated": False, "normalized_revision": REVISION, "segments": [first],
            "segment_window": {"anchor_segment_id": first["segment_id"], "anchor_ordinal": 0,
                               "first_ordinal": 0, "last_ordinal": 0},
            "next_segment_id": first["segment_id"] if truncated else None,
            "next_segment_offset": offset + len(first["text"]) if truncated else None,
            "next_segment_after": 0 if truncated else None}


@pytest.mark.parametrize("offset", [0, 8_000_001])
async def test_structured_text_before_indexing_is_revision_pinned_and_continues_large_blocks(
    settings, offset,
):
    response = text_page(truncated=True, offset=offset)
    args = {"transcript_id": TRANSCRIPT_ID, "segment_id": segment().segment_id,
            "expected_normalized_revision": REVISION, "offset": offset}
    actual, requests = await native_call(settings, "get_transcript_text", args, response)
    assert actual == response
    assert dict(requests[0].url.params) == {"segment_id": segment().segment_id,
        "expected_normalized_revision": REVISION, "offset": str(offset), "limit": "20000",
        "before": "0", "after": "0"}
    assert "expected_sha256" not in requests[0].url.params
    for patch in [{"normalized_revision": "b" * 64}, {"next_segment_offset": offset},
                  {"next_segment_after": 1}, {"segment_window": None}, {"text": "forged"}]:
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, "get_transcript_text", args, {**response, **patch})


@pytest.mark.parametrize("patch", [{"source_record": 2}, {"ordinal": 1}, {"payload": "x" * 20000},
                                   {"text_offset": 1}, {"private": "private-marker"}])
async def test_structured_tool_rejects_substituted_or_oversized_segment_content(settings, patch):
    response = text_page()
    response["segments"][0].update(patch)
    args = {"transcript_id": TRANSCRIPT_ID, "segment_id": segment().segment_id,
            "expected_normalized_revision": REVISION}
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await native_call(settings, "get_transcript_text", args, response)
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize("args", [
    {}, {"segment_id": "a" * 24}, {"expected_normalized_revision": REVISION},
    {"segment_id": "a" * 24, "expected_normalized_revision": REVISION, "before": 20, "after": 1},
    {"segment_id": "a" * 24, "expected_normalized_revision": REVISION, "before": 1, "offset": 1},
])
async def test_text_mode_constraints_fail_before_transport(settings, args):
    with pytest.raises(ToolError, match="Mnemonic rejected the input"):
        await native_call(settings, "get_transcript_text", {"transcript_id": TRANSCRIPT_ID, **args}, {})


def test_normalized_public_models_match_committed_openapi_shape():
    from mnemonic_mcp import transcript_models, transcript_segments

    components = json.loads((Path(__file__).parents[2] / "docs/openapi.json").read_text())[
        "components"]["schemas"]
    for module, name, published in (
        (transcript_models, "TranscriptRead", "TranscriptRead"),
        (transcript_models, "CompactTranscriptRead", "CompactTranscriptRead"),
        (transcript_models, "TranscriptTextPage", "TranscriptText"),
        (transcript_segments, "SegmentRead", "SegmentRead"),
        (transcript_segments, "SegmentWindow", "SegmentWindow"),
    ):
        schema = getattr(module, name).model_json_schema()
        assert set(schema["properties"]) == set(components[published]["properties"]), name
        assert set(schema.get("required", [])) == set(components[published].get("required", [])), name


async def test_maximum_escaped_unicode_segment_window_fits_transport_budget(settings):
    response = text_page()
    response["text"] = response["segments"][0]["text"] = "🌲" * 20000
    response["total_chars"] = 20000
    assert len(json.dumps(response)) > 256 * 1024
    args = {"transcript_id": TRANSCRIPT_ID, "segment_id": segment().segment_id,
            "expected_normalized_revision": REVISION}
    actual, _ = await native_call(settings, "get_transcript_text", args, response)
    assert actual == response
