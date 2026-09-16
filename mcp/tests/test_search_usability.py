"""Public search aliases and actionable errors preserve the secret-safe boundary."""

import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import ARTIFACT_ID, call, search_page
from test_transcripts import page as transcript_page

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server

CONTENT_TOOLS = ("search_artifact_contents", "search_transcript_contents")
PRIVATE = "private-input-marker"


def no_http(request):
    pytest.fail("Invalid input must be rejected before any HTTP request")


@pytest.mark.parametrize("tool", CONTENT_TOOLS)
@pytest.mark.parametrize("field", ["query", "q"])
@pytest.mark.parametrize("fulltext", [False, True])
async def test_content_query_alias_preserves_wire_query_and_scope(settings, tool, field, fulltext):
    artifact = tool == "search_artifact_contents"
    arguments = {"project_id": PROJECT_ID, field: "café FastAPI", "fulltext": fulltext,
                 "work_item_id": WORK_ID, "limit": 2, "offset": 3}
    expected = {"q" if artifact else "query": "café FastAPI", "fulltext": fulltext,
                "detail": "full",
                "work_item_id": WORK_ID, "limit": 2, "offset": 3}
    if artifact:
        arguments["artifact_id"] = ARTIFACT_ID
        expected.update(artifact_id=ARTIFACT_ID, include_deleted=False)
    response = (search_page(fulltext=fulltext, limit=2, offset=3) if artifact else
                transcript_page(items=[], total=0, limit=2, offset=3))
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content) == expected
        route = "artifacts" if artifact else "transcripts"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/{route}/search-content"
        return httpx.Response(200, json=response)

    actual = await call(settings, tool, arguments, handler)
    assert actual["items"] == [] and actual["total"] == 0
    assert len(seen) == 1


@pytest.mark.parametrize("tool", CONTENT_TOOLS)
@pytest.mark.parametrize("arguments, expected", [
    ({}, "query (missing)"),
    ({"q": PRIVATE, "query": PRIVATE}, "exactly one of query or q"),
    ({"q": PRIVATE, "query": PRIVATE + "-different"}, "exactly one of query or q"),
    ({"query": None}, "query (string_type)"),
    ({"q": None}, "q (string_type)"),
    ({"query": ""}, "query (string_too_short)"),
    ({"q": ""}, "q (string_too_short)"),
    ({"query": PRIVATE * 30}, "query (string_too_long)"),
    ({"q": PRIVATE * 30}, "q (string_too_long)"),
    ({"q": {PRIVATE: PRIVATE}}, "q (string_type)"),
])
async def test_content_query_refusals_are_local_and_value_free(settings, tool, arguments, expected,
                                                              caplog):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(no_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool(tool, {"project_id": PROJECT_ID, **arguments})
    assert expected in str(caught.value)
    assert PRIVATE not in str(caught.value) + caplog.text


async def test_both_query_spellings_are_discoverable_and_equally_bounded(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    for name in CONTENT_TOOLS:
        schema = tools[name].inputSchema
        assert schema["additionalProperties"] is False
        for field in ("query", "q"):
            assert schema["properties"][field]["type"] == "string"
            assert schema["properties"][field]["minLength"] == 1
            assert schema["properties"][field]["maxLength"] == 200
        assert "exactly one of query" in tools[name].description
    for name in (*CONTENT_TOOLS, "search"):
        description = tools[name].description
        assert "all query terms" in description.lower()
        assert "zero-hit multi-term query does not prove the subject is absent" in description
        assert "individual distinctive terms" in description


@pytest.mark.parametrize("arguments, expected, hint", [
    ({"sources": [PRIVATE]}, "extra_forbidden", "Use facets"),
    ({"filters": {"artifacts": {"artifact_id": PRIVATE}}},
     "filters.artifacts.artifact_id (uuid_parsing)", None),
    ({"filters": {"artifacts": {PRIVATE: PRIVATE}}},
     "filters.artifacts (extra_forbidden)", None),
    ({"filters": {"artifacts": {"sources": PRIVATE}}},
     "filters.artifacts (extra_forbidden)", None),
    ({PRIVATE: PRIVATE}, "extra_forbidden", None),
    ({"sources" + PRIVATE: PRIVATE}, "extra_forbidden", None),
])
async def test_search_hints_only_name_reviewed_mistakes(settings, arguments, expected, hint, caplog):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(no_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("search", {"project_id": PROJECT_ID, "q": "term", **arguments})
    message = str(caught.value)
    assert expected in message
    assert ("Use facets" in message) is (hint is not None)
    assert PRIVATE not in message + caplog.text


@pytest.mark.parametrize("tool", ["claim_work", "claim_and_recall"])
@pytest.mark.parametrize("retry_key_present", [False, True])
async def test_claim_operation_id_refusal_teaches_correct_retry_key(settings, tool, retry_key_present,
                                                                  caplog):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(no_http)))
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                 "holder_client": "claude_code", "holder_session_id": PRIVATE,
                 "session_transcript": None, "client_operation_id": CLIENT_OPERATION_ID}
    if retry_key_present:
        arguments["claim_request_id"] = PRIVATE
    with pytest.raises(ToolError) as caught:
        await server.call_tool(tool, arguments)
    message = str(caught.value)
    assert "client_operation_id (extra_forbidden)" in message
    assert "Claim tools use claim_request_id as the retry key" in message
    assert "client_operation_id is not accepted" in message
    assert PRIVATE not in message + caplog.text
    assert CLIENT_OPERATION_ID not in message + caplog.text


@pytest.mark.parametrize("explicit", [False, True])
async def test_unified_empty_conjunction_discloses_counts_and_transcript_opt_in(settings, explicit):
    from test_unified_search import FACETS, page

    selected = FACETS if explicit else ["work_items", "artifacts"]
    result = page()
    result["search_scope"].update(
        searched_facets=selected, transcripts="searched" if explicit else "omitted_by_default",
    )
    result["term_diagnostics"] = [
        {"term": "fastapi", "matches": {"work_items": 2, "artifacts": 1,
                                         "transcripts": 3 if explicit else None}},
        {"term": "absent", "matches": {"work_items": 0, "artifacts": 0,
                                        "transcripts": 0 if explicit else None}},
    ]
    arguments = {"project_id": PROJECT_ID, "q": "FastAPI absent", "fulltext": True}
    if explicit:
        arguments["facets"] = selected

    def handler(request):
        body = json.loads(request.content)
        assert ("facets" in body) is explicit
        if explicit:
            assert body["facets"] == selected
        return httpx.Response(200, json=result)

    actual = await call(settings, "search", arguments, handler)
    assert actual["term_diagnostics"] == result["term_diagnostics"]
    assert actual["search_scope"] == result["search_scope"]
    assert 'explicitly including "transcripts" in facets' in (
        actual["search_scope"]["transcript_search_hint"]
    )


@pytest.mark.parametrize("tool", CONTENT_TOOLS)
async def test_dedicated_search_preserves_per_term_counts(settings, tool):
    artifact = tool == "search_artifact_contents"
    facet = "artifacts" if artifact else "transcripts"
    diagnostics = [{"term": "present", "matches": {
        "work_items": None, "artifacts": None, "transcripts": None, facet: 2,
    }}, {"term": "absent", "matches": {
        "work_items": None, "artifacts": None, "transcripts": None, facet: 0,
    }}]
    result = (search_page(term_diagnostics=diagnostics) if artifact else
              transcript_page(items=[], total=0, term_diagnostics=diagnostics))
    actual = await call(settings, tool, {"project_id": PROJECT_ID, "q": "present absent"},
                        lambda request: httpx.Response(200, json=result))
    assert actual["term_diagnostics"] == diagnostics


@pytest.mark.parametrize("failure", ["negative", "bool", "unsearched-count", "missing-count",
                                     "duplicate-term", "scope", "hint", "false-opt-out"])
async def test_unified_diagnostics_reject_incoherent_or_untrusted_envelopes(settings, failure):
    from test_unified_search import page

    result = page()
    result["search_scope"].update(searched_facets=["work_items", "artifacts"],
                                  transcripts="omitted_by_default")
    counts = {"work_items": 0, "artifacts": 1, "transcripts": None}
    result["term_diagnostics"] = [{"term": "present", "matches": counts}]
    arguments = {"project_id": PROJECT_ID, "q": "present absent"}
    if failure in {"negative", "bool", "missing-count"}:
        counts["artifacts"] = {"negative": -1, "bool": True, "missing-count": None}[failure]
    if failure == "unsearched-count":
        counts["transcripts"] = 0
    if failure == "duplicate-term":
        result["term_diagnostics"] *= 2
    if failure == "scope":
        result["search_scope"]["searched_facets"] = ["work_items", "artifacts", "artifacts"]
    if failure == "hint":
        result["search_scope"]["transcript_search_hint"] = PRIVATE
    if failure == "false-opt-out":
        arguments["facets"] = ["work_items", "artifacts", "transcripts"]
    with pytest.raises(ToolError, match="unexpected response") as caught:
        await call(settings, "search", arguments, lambda request: httpx.Response(200, json=result))
    assert PRIVATE not in str(caught.value)
