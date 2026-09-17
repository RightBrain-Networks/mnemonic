"""Native compact defaults retain scope and identity without full-summary payloads."""

import copy
import json
from uuid import UUID

import httpx
import pytest
from conftest import NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking
from test_artifacts import artifact
from test_transcripts import transcript
from test_unified_search import page as unified_page

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.artifact_models import ArtifactRead
from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)
from mnemonic_mcp.server import build_server

TOOLS = ["search_work", "search", "search_artifact_contents", "search_transcript_contents",
         "list_transcripts"]


def work_pointer(summary, rank=1):
    item = summary["work_item"]
    return {key: item[key] for key in ("id", "project_id", "title", "status", "priority", "updated_at")} | {
        "rank": rank, "canonical_work_item_id": item["id"], "search_status": item["status"],
        "display_state": item["status"], "ancestor_path": [], "ancestor_path_truncated": False,
    }


def artifact_pointer():
    value = ArtifactRead.model_validate(artifact()).model_dump(mode="json")
    selected = {key: value[key] for key in (
        "id", "project_id", "filename", "revision", "sensitive", "deleted_at", "content_available",
    )}
    selected["extraction"] = {key: value["extraction"][key] for key in (
        "status", "truncated", "error_code",
    )}
    return {"artifact": selected, "score": 1.0, "snippet": None, "matched_fields": ["metadata"]}


def transcript_pointer():
    value = transcript()
    return {key: value[key] for key in (
        "id", "project_id", "work_item_id", "client", "session_id", "filename", "kind",
        "status", "index_status", "copy_status", "truncated", "snippet", "score",
        "normalization_status", "normalization_error_code", "normalized_revision",
        "normalized_sha256", "normalization_schema_version", "normalizer_version",
        "normalized_size_bytes", "segment_count", "normalization_incomplete", "segment_id",
        "content_kind", "matched_fields", "snippet_omission_reason", "rank", "score_type",
        "last_updated_at", "index_created_at", "session_ids", "models",
    )}


def response_page(tool, summary, *, q="needle", detail="compact"):
    work = work_pointer(summary)
    file = artifact_pointer()
    session = transcript_pointer()
    if tool == "search":
        items = [{"facet": facet, "project_id": PROJECT_ID,
                  "id": value["artifact"]["id"] if facet == "artifacts"
                  else value["id"], "created_at": NOW, "updated_at": NOW, "score": 1 / 61,
                  key: value} for facet, key, value in (
                      ("work_items", "work_item", work), ("artifacts", "artifact", file),
                      ("transcripts", "transcript", session),
                  )]
        result = unified_page(items, limit=20)
        result["coverage"]["artifacts"]["indexing"].update(pending=1, ready=0)
        result["indexing_incomplete"] = True
        result["project_coverage"] = [{
            "project_id": PROJECT_ID, "project_name": "Test project", "project_slug": "test-project",
            "facet_totals": result["facet_totals"], "coverage": result["coverage"],
            "indexing_incomplete": result["indexing_incomplete"],
        }]
        scopes = {"work_items": WorkAppliedFilters(), "artifacts": ArtifactAppliedFilters(),
                  "transcripts": TranscriptAppliedFilters()}
    elif tool == "search_work":
        result = {"items": [work], "total": 1, "limit": 20, "offset": 0}
        scopes = {"work_items": WorkAppliedFilters()}
    elif tool == "search_artifact_contents":
        result = {"items": [file], "total": 1, "limit": 20, "offset": 0, "fulltext": False,
                  "match_mode": "all_terms", "term_diagnostics": [], "indexing": {
                      "ready": 0, "pending": 1, "failed": 0, "truncated": 0,
                  }, "sensitive_content_withheld": 0}
        scopes = {"artifacts": ArtifactAppliedFilters()}
    else:
        result = {"items": [session], "total": 1, "limit": 20, "offset": 0,
                  "term_diagnostics": [], "indexing_incomplete": False}
        scopes = {"transcripts": TranscriptAppliedFilters()}
    if tool == "search":
        result["tag_counts"] = None
    if tool == "search_work":
        result["term_diagnostics"] = []
    if tool in {"search", "search_work"}:
        result["work_rank_scope"] = "work_items"
    add_ranking(result, {"search_work": "work_items", "search_artifact_contents": "artifacts",
                         "search_transcript_contents": "transcripts", "list_transcripts": "transcripts"}.get(tool, "search"), q)
    return {**result, "detail": detail,
            **search_disclosure(UUID(PROJECT_ID), q, **scopes).model_dump(mode="json")}


async def native_call(settings, tool, arguments, response):
    """No fixture defaults or response enrichment: exercise the actual tool defaults."""
    requests = []

    def handler(request):
        if request.url.path == "/api/v1/artifacts/status":
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps({
                "enabled": True, "max_bytes": 67108864, "message": "API policy",
            }).encode()), headers={"Content-Type": "application/json"})
        requests.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(response).encode()),
                              headers={"Content-Type": "application/json"})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = await server.call_tool(tool, {"project_id": PROJECT_ID, **arguments})
    return (result[1] if isinstance(result, tuple) else result), requests


@pytest.mark.parametrize("tool", TOOLS)
async def test_native_defaults_request_compact_twenty_and_preserve_scope(settings, work_summary, tool):
    q = "" if tool == "list_transcripts" else "needle"
    response = response_page(tool, work_summary, q=q)
    arguments = {} if tool == "list_transcripts" else {"q": q}
    actual, requests = await native_call(settings, tool, arguments, response)
    request = requests[0]
    sent = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
    assert sent["detail"] == "compact" and str(sent["limit"]) == "20"
    assert actual == response
    assert "artifact_library" not in actual
    encoded = json.dumps(actual)
    for omitted in ('"summary"', '"current_context"', '"readiness"', '"sha256"',
                    '"text_sha256"', '"source_path"', '"indexing_started_at"'):
        # The query interpretation legitimately names the summary search field.
        assert omitted + ":" not in encoded
    assert len(requests) == 1


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("failure", ["wrong-detail", "missing-detail", "extra-content", "project"])
async def test_compact_rejects_wrong_shapes_even_when_fields_would_otherwise_parse(
    settings, work_summary, tool, failure,
):
    q = "" if tool == "list_transcripts" else "needle"
    response = response_page(tool, work_summary, q=q)
    if failure == "wrong-detail":
        response["detail"] = "full"
    elif failure == "missing-detail":
        del response["detail"]
    else:
        item = response["items"][0]
        if tool == "search":
            item = item["work_item"]
        elif tool == "search_artifact_contents":
            item = item["artifact"]
        item["private_body" if failure == "extra-content" else "project_id"] = (
            "private-marker" if failure == "extra-content" else OTHER_WORK_ID
        )
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await native_call(settings, tool, {} if not q else {"q": q}, response)
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize("status", ["pending", "active", "dropped", "to-review", "deferred"])
async def test_work_compact_preserves_status_membership_and_canonical_authority(
    settings, work_summary, status,
):
    response = response_page("search_work", work_summary)
    response["applied_filters"]["work_items"]["status"] = status
    pointer = response["items"][0]
    pointer["search_status"] = status
    pointer["status"] = "done" if status in {"to-review", "deferred"} else "pending"
    pointer["display_state"] = "waiting" if status == "pending" else status
    arguments = {"q": "needle", "status": status}
    await native_call(settings, "search_work", arguments, response)
    for patch in [{"search_status": "done"}, {"rank": 2}, {"canonical_work_item_id": OTHER_WORK_ID},
                  {"matched_member": {"id": WORK_ID, "title": "Redundant", "status": "pending"}}]:
        wrong = copy.deepcopy(response)
        wrong["items"][0].update(patch)
        with pytest.raises(ToolError, match="unexpected response"):
            await native_call(settings, "search_work", arguments, wrong)


async def test_alias_evidence_is_only_present_for_different_matching_member(settings, work_summary):
    response = response_page("search_work", work_summary)
    response["items"][0]["matched_member"] = {
        "id": OTHER_WORK_ID, "title": "Matching alias", "status": "done",
    }
    actual, _ = await native_call(settings, "search_work", {"q": "needle"}, response)
    assert actual["items"][0]["matched_member"]["id"] == OTHER_WORK_ID
    response["query_interpretation"]["q"] = ""
    response["query_interpretation"]["work_items"]["match_mode"] = "browse"
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search_work", {}, response)


@pytest.mark.parametrize("tool", ["search_artifact_contents", "search_transcript_contents"])
async def test_compact_never_leaks_content_without_fulltext(settings, work_summary, tool):
    response = response_page(tool, work_summary)
    response["items"][0]["snippet"] = "private-content-marker"
    if tool == "search_artifact_contents":
        response["items"][0]["matched_fields"] = ["content"]
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await native_call(settings, tool, {"q": "needle"}, response)
    assert "private-content-marker" not in str(caught.value)


async def test_compact_work_reduces_twenty_hit_payload_and_full_detail_is_explicit(
    settings, work_summary,
):
    compact = response_page("search_work", work_summary)
    compact["items"] = []
    full = copy.deepcopy(compact)
    full["detail"] = "full"
    for index in range(20):
        summary = copy.deepcopy(work_summary)
        identity = str(UUID(int=index + 1000))
        summary["work_item"].update(id=identity, summary="Retained searchable context. " * 60)
        summary["current_context"]["work_item_id"] = identity
        summary["readiness"]["canonical_work_item_id"] = identity
        compact["items"].append(work_pointer(summary, index + 1))
        full["items"].append({"summary": summary, "matched_member": {
            key: summary["work_item"][key] for key in ("id", "title", "status")
        }})
    full["total"] = compact["total"] = 20
    add_ranking(compact, "work_items", "needle")
    add_ranking(full, "work_items", "needle")
    small, _ = await native_call(settings, "search_work", {"q": "needle"}, compact)
    large, requests = await native_call(settings, "search_work", {"q": "needle", "detail": "full"}, full)
    assert requests[0].url.params["detail"] == "full"
    assert [item["id"] for item in small["items"]] == [
        item["summary"]["work_item"]["id"] for item in large["items"]
    ]
    assert len(json.dumps(small)) <= 0.25 * len(json.dumps(large))
    assert len(json.dumps(small)) <= 20_000


async def test_compact_hierarchy_can_include_nonmatching_ancestor_but_not_forged_self_match(
    settings, work_summary,
):
    response = response_page("search_work", work_summary, q="")
    response["applied_filters"]["work_items"].update(view="roots", status="done")
    pointer = response["items"][0]
    pointer.update(self_matches_filter=False, has_matching_descendants=True, presentation={
        "direct_child_count": 1, "descendant_count": 1, "blocked_descendant_count": 0,
        "active_descendant_count": 0, "completed_descendant_count": 1,
        "discovered_descendant_count": 0, "branch_unresolved_human_gate_count": 0,
        "branch_merged_duplicate_count": 0, "is_discovered_work": False,
        "discovered_from_parent": False, "next_active_descendant_lease_expires_at": None,
    })
    args = {"view": "roots", "status": "done"}
    actual, _ = await native_call(settings, "search_work", args, response)
    assert actual["items"][0]["has_matching_descendants"] is True
    assert "summary" not in actual["items"][0]
    pointer["self_matches_filter"] = True
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search_work", args, response)


async def test_compact_alias_audit_preserves_canonical_filter_without_substituting_identity(
    settings, work_summary,
):
    response = response_page("search_work", work_summary)
    response["applied_filters"]["work_items"].update(
        duplicate_scope="aliases", canonical_work_item_id=OTHER_WORK_ID,
    )
    pointer = response["items"][0]
    pointer.update(canonical_work_item_id=OTHER_WORK_ID, display_state="duplicate")
    args = {"q": "needle", "duplicate_scope": "aliases", "canonical_work_item_id": OTHER_WORK_ID}
    actual, _ = await native_call(settings, "search_work", args, response)
    assert actual["items"][0]["id"] == WORK_ID
    pointer["canonical_work_item_id"] = str(UUID(int=9999))
    with pytest.raises(ToolError, match="unexpected response"):
        await native_call(settings, "search_work", args, response)
