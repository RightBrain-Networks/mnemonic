"""Search spelling and capacity failures remain explicit and secret-safe."""

import json

import httpx
import pytest
from conftest import PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import call
from test_unified_search import page

from mnemonic_mcp.api import MnemonicAPI, TransportEffect, _raise_for_response_error
from mnemonic_mcp.server import build_server


@pytest.mark.parametrize("tool", ["search", "search_work"])
@pytest.mark.parametrize("spelling", ["q", "query"])
@pytest.mark.parametrize("query", ["", "café search", "x" * 1000])
async def test_discovery_alias_preserves_query_and_defaults(settings, tool, spelling, query):
    def handler(request):
        if tool == "search":
            payload = json.loads(request.content)
            assert payload["q"] == query and "query" not in payload
            assert payload["filters"]["work_items"]["status"] == "all"
            return httpx.Response(200, json=page())
        assert request.url.params["q"] == query
        assert request.url.params["status"] == "all"
        return httpx.Response(200, json={"items": [], "total": 0, "limit": 30, "offset": 0})

    result = await call(settings, tool, {"project_id": PROJECT_ID, spelling: query}, handler)
    assert result["total"] == 0


@pytest.mark.parametrize("tool", ["search", "search_work"])
@pytest.mark.parametrize("arguments", [
    {"q": "private-marker", "query": "private-marker"},
    {"q": "", "query": "private-marker"},
    {"q": "x" * 1001},
    {"query": "x" * 1001},
])
async def test_discovery_alias_refusals_do_not_send_http_or_echo_values(settings, tool, arguments):
    def handler(request):
        pytest.fail("Alias/length errors must be refused before HTTP")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool(tool, {"project_id": PROJECT_ID, **arguments})
    assert "private-marker" not in str(caught.value)
    if "q" in arguments and "query" in arguments:
        assert "exactly one of query or q" in str(caught.value)
    else:
        assert "string_too_long" in str(caught.value)


@pytest.mark.parametrize("code,status,remedy", [
    ("transcript_search_capacity", 503, "created_after, work_item_id, or content_kinds"),
    ("transcript_search_busy", 503, "sequentially"),
    ("search_result_too_large", 413, "detail=compact"),
])
@pytest.mark.parametrize("path", ["projects/id/search", "projects/id/transcripts/search-content"])
def test_search_failures_name_actionable_cause_without_upstream_text(code, status, remedy, path):
    response = httpx.Response(status, json={"detail": {
        "code": code, "message": "private-marker", "context": {"secret": "private-marker"},
    }})
    with pytest.raises(ToolError) as caught:
        _raise_for_response_error(response, "POST", path, semantic_read=False,
                                  effect=TransportEffect.SAFE_READ)
    message = str(caught.value)
    assert code in message and remedy in message
    assert "API is unavailable" not in message and "private-marker" not in message


def test_unknown_server_failure_retains_generic_safe_read_message():
    response = httpx.Response(503, json={"detail": {
        "code": "private-marker", "message": "private-marker", "context": {},
    }})
    with pytest.raises(ToolError, match="could not complete this safe read") as caught:
        _raise_for_response_error(response, "POST", "projects/id/search", semantic_read=False,
                                  effect=TransportEffect.SAFE_READ)
    assert "private-marker" not in str(caught.value)


@pytest.mark.parametrize("code", ["semantic_unavailable", "duplicate_suggestion_unavailable"])
@pytest.mark.parametrize("reason", [
    "capacity_exhausted", "deadline_exceeded", "model_failure", "vectors_pending",
])
def test_typed_semantic_errors_only_invite_capacity_retry(code, reason):
    disposition = {
        "inference": {"status": "unavailable", "reason": reason},
        "candidate_scope": "none", "partial_vectors": False, "comparison_incomplete": True,
        "retry": {"max_attempts": 1, "after_seconds": 1} if reason == "capacity_exhausted" else None,
        "cache_refresh": {"status": "not_needed", "reason": None},
    }
    response = httpx.Response(503, json={"detail": {
        "code": code, "message": "private-marker",
        "context": {"semantic": disposition},
    }})
    with pytest.raises(ToolError) as caught:
        _raise_for_response_error(response, "POST", "projects/id/search", semantic_read=False,
                                  effect=TransportEffect.SAFE_READ)
    message = str(caught.value)
    assert ("Retry once after one second" in message) is (reason == "capacity_exhausted")
    assert "private-marker" not in message
