"""Effective scope and reviewed query rules stay explicit through the MCP boundary."""

import copy
from uuid import UUID

import httpx
import pytest
from conftest import PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import call, search_page
from test_transcripts import page as transcript_page
from test_unified_search import page as unified_page

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)
from mnemonic_mcp.server import build_server
from mnemonic_mcp.validation_rules import VALIDATION_RULES

QUERY = '"admission cookie"'
TOOLS = ["search_work", "search", "search_artifact_contents", "search_transcript_contents"]


def search_result(tool, status="all"):
    if tool == "search_work":
        result = {"items": [], "total": 0, "limit": 30, "offset": 0}
        scopes = {"work_items": WorkAppliedFilters(status=status)}
    elif tool == "search":
        result = unified_page()
        scopes = {"work_items": WorkAppliedFilters(status=status),
                  "artifacts": ArtifactAppliedFilters(), "transcripts": TranscriptAppliedFilters()}
    elif tool == "search_artifact_contents":
        result = search_page()
        scopes = {"artifacts": ArtifactAppliedFilters()}
    else:
        result = transcript_page(items=[], total=0)
        scopes = {"transcripts": TranscriptAppliedFilters()}
    return {**result, **search_disclosure(UUID(PROJECT_ID), QUERY, **scopes).model_dump(mode="json")}


@pytest.mark.parametrize("tool", TOOLS)
async def test_empty_quoted_search_preserves_effective_scope_and_warning(settings, tool):
    response = search_result(tool)
    arguments = {"project_id": PROJECT_ID, "q": QUERY}
    if tool == "search":
        arguments["facets"] = ["work_items", "artifacts", "transcripts"]
    actual = await call(settings, tool, arguments, lambda request: httpx.Response(200, json=response))
    for key in ("applied_filters", "query_interpretation", "warnings"):
        assert actual[key] == response[key]
    if tool in {"search", "search_work"}:
        assert actual["applied_filters"]["work_items"]["status"] == "all"
    assert actual["warnings"][0]["code"] == "phrase_operators_ignored"
    assert actual["query_interpretation"]["q"] == QUERY


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("failure", ["project", "query", "missing", "missing-filter", "warning"])
async def test_empty_search_cannot_hide_changed_scope_or_query(settings, tool, failure):
    response = search_result(tool)
    if failure == "project":
        response["applied_filters"]["project_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    elif failure == "query":
        response["query_interpretation"]["q"] = "different query"
    elif failure == "missing":
        del response["applied_filters"]
    elif failure == "missing-filter":
        source = next(key for key in ("work_items", "artifacts", "transcripts")
                      if response["applied_filters"][key] is not None)
        del response["applied_filters"][source][next(iter(response["applied_filters"][source]))]
    else:
        response["warnings"][0]["message"] = "private-upstream-marker"
    arguments = {"project_id": PROJECT_ID, "q": QUERY}
    if tool == "search":
        arguments["facets"] = ["work_items", "artifacts", "transcripts"]
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await call(settings, tool, arguments, lambda request: httpx.Response(200, json=response))
    assert "private-upstream-marker" not in str(caught.value)


@pytest.mark.parametrize("status", ["all", "pending", "done"])
async def test_search_work_echo_must_match_explicit_status_even_with_no_hits(settings, status):
    result = search_result("search_work", status)
    arguments = {"project_id": PROJECT_ID, "q": QUERY, "status": status}
    await call(settings, "search_work", arguments, lambda request: httpx.Response(200, json=result))
    wrong = copy.deepcopy(result)
    wrong["applied_filters"]["work_items"]["status"] = "deferred"
    with pytest.raises(ToolError, match="unexpected response"):
        await call(settings, "search_work", arguments,
                   lambda request: httpx.Response(200, json=wrong))


@pytest.mark.parametrize("code", VALIDATION_RULES)
async def test_reviewed_backend_validation_types_use_static_fields_and_prose(settings, code, caplog):
    field, message = VALIDATION_RULES[code]
    loc = ["query", "external_url"] if field is None else ["query"]
    response = {"detail": [{"type": code, "loc": loc, "msg": "private-upstream-marker",
                             "input": "private-input-marker"}]}
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(
        lambda request: httpx.Response(422, json=response),
    )))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("search_work", {"project_id": PROJECT_ID})
    assert message in str(caught.value)
    assert f"{field or 'query.external_url'} ({code})" in str(caught.value)
    assert "private-" not in str(caught.value) + caplog.text


async def test_missing_url_scheme_explains_local_rule_without_request(settings, caplog):
    def handler(request):
        pytest.fail("A missing URL scheme must be rejected locally")
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("search_work", {
            "project_id": PROJECT_ID, "external_url": "github.com/private-marker/issues/2378",
        })
    assert "external_url (absolute_http_url_required)" in str(caught.value)
    assert "Include an absolute http:// or https:// URL." in str(caught.value)
    assert "private-marker" not in str(caught.value) + caplog.text
