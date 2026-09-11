"""Unified discovery stays scoped, bounded, compact, and a safe read."""

import json

import httpx
import pytest
from conftest import NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import ARTIFACT_ID, artifact, call
from test_transcripts import TRANSCRIPT_ID, transcript

from mnemonic_mcp.server import build_server

FACETS = ["work_items", "artifacts", "transcripts"]


def artifact_hit(**changes):
    source = artifact(extraction={"status": "ready", "metadata": {"title": ["Long property"]},
                                  "truncated": False, "error_code": None, "extracted_at": NOW})
    return {"facet": "artifacts", "id": ARTIFACT_ID, "created_at": NOW, "updated_at": NOW,
            "score": 1 / 61, "artifact": {"artifact": source, "score": 1.0, "snippet": None,
                                         "matched_fields": ["metadata"]}, **changes}


def transcript_hit(**changes):
    return {"facet": "transcripts", "id": TRANSCRIPT_ID, "created_at": NOW, "updated_at": NOW,
            "score": 1 / 61, "transcript": transcript(), **changes}


def work_hit(work_summary):
    return {"facet": "work_items", "id": WORK_ID, "created_at": NOW, "updated_at": NOW,
            "score": 1 / 61, "work_item": {"summary": work_summary, "matched_member": {
                key: work_summary["work_item"][key] for key in ("id", "title", "status")
            }}}


def page(items=(), **changes):
    return {"items": list(items), "total": len(items), "limit": 50, "offset": 0,
            "facet_totals": {facet: sum(item["facet"] == facet for item in items) for facet in FACETS},
            "coverage": {"artifacts": {"enabled": True, "indexing": {
                "pending": 0, "ready": sum(item["facet"] == "artifacts" for item in items),
                "failed": 0, "truncated": 0,
            }, "sensitive_content_withheld": 0}, "transcripts": {"indexing_incomplete": False}},
            "indexing_incomplete": False, **changes}


async def test_defaults_search_every_facet_and_status_in_one_safe_post(settings, work_summary):
    requests = []
    hits = [work_hit(work_summary), artifact_hit(), transcript_hit()]

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/search"
        assert body["q"] == "report" and body["facets"] == FACETS
        assert body["fulltext"] is False
        assert body["filters"]["work_items"]["status"] == "all"
        assert body["filters"]["work_items"]["duplicate_scope"] == "canonical"
        assert body["sort"] == {"by": "relevance", "direction": "desc"}
        assert body["facet_order"] == []
        assert (body["limit"], body["offset"]) == (50, 0)
        assert "client_operation_id" not in body
        return httpx.Response(200, json=page(hits))

    result = await call(settings, "search", {"project_id": PROJECT_ID, "q": "report"}, handler)
    assert len(requests) == 1
    assert [item["facet"] for item in result["items"]] == FACETS
    compact = result["items"][1]["artifact"]["artifact"]
    assert (compact["id"], compact["filename"], compact["revision"]) == (
        ARTIFACT_ID, "private report.pdf", 1,
    )
    assert compact["sha256"] == hits[1]["artifact"]["artifact"]["sha256"]
    assert "metadata" not in compact["extraction"] and "description" not in compact
    assert "transcript" not in result["items"][1]
    assert result["facet_totals"] == dict.fromkeys(FACETS, 1)


async def test_filters_group_sorts_and_global_offset_forward(settings, work_summary):
    filters = {
        "work_items": {"status": "all", "tag": "search", "source_client": "claude-code",
                       "source_session_id": "work-session", "duplicate_scope": "all"},
        "artifacts": {"artifact_id": ARTIFACT_ID, "work_item_id": WORK_ID, "sensitive": False,
                      "mime_type": "application/pdf", "created_by_agent_session_id": "test-session"},
        "transcripts": {"work_item_id": WORK_ID, "agent_session_id": "independent-session",
                        "client": "claude_code", "kind": "primary", "status": "ready"},
    }
    ordered = [{"facet": "artifacts", "sort": {"by": "relevance", "direction": "desc"}},
               {"facet": "work_items", "sort": {"by": "created_at", "direction": "asc"}}]
    hits = [artifact_hit(), work_hit(work_summary), transcript_hit()]
    response = page(hits, total=5, limit=3, offset=2,
                    facet_totals={"artifacts": 3, "work_items": 1, "transcripts": 1})

    def handler(request):
        body = json.loads(request.content)
        for facet, expected in filters.items():
            assert expected.items() <= body["filters"][facet].items()
        assert body["facet_order"] == ordered
        assert body["sort"] == {"by": "updated_at", "direction": "desc"}
        assert body["fulltext"] is True
        assert (body["limit"], body["offset"]) == (3, 2)
        return httpx.Response(200, json=response)

    result = await call(settings, "search", {
        "project_id": PROJECT_ID, "q": "report", "fulltext": True, "filters": filters,
        "sort": {"by": "updated_at"}, "facet_order": ordered, "limit": 3, "offset": 2,
    }, handler)
    assert [item["facet"] for item in result["items"]] == ["artifacts", "work_items", "transcripts"]
    assert result["total"] == 5 and result["offset"] == 2


async def test_unavailable_and_sensitive_coverage_is_preserved(settings):
    hit = artifact_hit()
    hit["artifact"]["artifact"]["sensitive"] = True
    hit["artifact"]["artifact"]["extraction"]["metadata"] = {}
    response = page([hit, transcript_hit()])
    response["coverage"]["artifacts"]["sensitive_content_withheld"] = 4
    response["coverage"]["artifacts"]["indexing"]["failed"] = 2
    response["coverage"]["transcripts"]["indexing_incomplete"] = True
    response["indexing_incomplete"] = True
    result = await call(settings, "search", {"project_id": PROJECT_ID, "q": "report", "fulltext": True},
                        lambda request: httpx.Response(200, json=response))
    assert result["coverage"] == response["coverage"] and result["indexing_incomplete"] is True


async def test_disabled_artifacts_do_not_block_other_source_results(settings):
    response = page([transcript_hit()])
    response["coverage"]["artifacts"]["enabled"] = False
    response["indexing_incomplete"] = True
    result = await call(settings, "search", {"project_id": PROJECT_ID},
                        lambda request: httpx.Response(200, json=response))
    assert result["items"][0]["id"] == TRANSCRIPT_ID
    assert result["coverage"]["artifacts"]["enabled"] is False


@pytest.mark.parametrize("failure", [
    "project", "id", "created", "updated", "deleted", "sensitive-content", "sensitive-properties",
    "content-opt-in", "snippet-opt-in", "duplicate", "mixed-variant", "total", "facet-total",
    "offset", "limit", "page-length", "indexing", "score", "unselected-facet", "artifact-scope",
    "work-scope", "sensitive-filter", "mime-filter", "artifact-session", "disabled-results",
])
async def test_artifact_responses_are_validated_before_disclosure(settings, failure):
    hit = artifact_hit()
    response = page([hit])
    arguments = {"project_id": PROJECT_ID, "q": "report"}
    source = hit["artifact"]["artifact"]
    modifications = {
        "project": (source, "project_id", OTHER_WORK_ID), "id": (hit, "id", OTHER_WORK_ID),
        "created": (hit, "created_at", "2020-01-01T00:00:00Z"),
        "updated": (hit, "updated_at", "2020-01-01T00:00:00Z"),
        "deleted": (source, "deleted_at", NOW),
        "content-opt-in": (hit["artifact"], "matched_fields", ["content"]),
        "snippet-opt-in": (hit["artifact"], "snippet", "private body"),
        "total": (response, "total", 2), "facet-total": (response["facet_totals"], "artifacts", 0),
        "offset": (response, "offset", 1), "limit": (response, "limit", 1),
        "page-length": (response, "items", []), "score": (hit, "score", -1),
        "indexing": (response["coverage"]["artifacts"]["indexing"], "failed", 1),
        "mixed-variant": (hit, "transcript", transcript()),
        "disabled-results": (response["coverage"]["artifacts"], "enabled", False),
    }
    if failure in modifications:
        target, field, value = modifications[failure]
        target[field] = value
    if failure in {"sensitive-content", "sensitive-properties"}:
        source["sensitive"] = True
        arguments["fulltext"] = True
        if failure == "sensitive-content":
            source["extraction"]["metadata"] = {}
            hit["artifact"]["snippet"] = "sensitive body"
            hit["artifact"]["matched_fields"] = ["content"]
            arguments["filters"] = {"artifacts": {"artifact_id": ARTIFACT_ID}}
    if failure == "duplicate":
        response["items"] *= 2
        response["total"] = response["facet_totals"]["artifacts"] = 2
    if failure == "unselected-facet":
        arguments["facets"] = ["transcripts"]
    filter_errors = {"artifact-scope": {"artifact_id": OTHER_WORK_ID},
                     "work-scope": {"work_item_id": OTHER_WORK_ID},
                     "sensitive-filter": {"sensitive": True},
                     "mime-filter": {"mime_type": "text/plain"},
                     "artifact-session": {"created_by_agent_session_id": "other-session"}}
    if failure in filter_errors:
        arguments["filters"] = {"artifacts": filter_errors[failure]}
    with pytest.raises(ToolError):
        await call(settings, "search", arguments, lambda request: httpx.Response(200, json=response))


@pytest.mark.parametrize("field,value", [
    ("work_item_id", OTHER_WORK_ID), ("agent_session_id", "other-session"),
    ("client", "other-client"), ("kind", "subagent"), ("status", "failed"),
])
async def test_transcript_filters_are_enforced(settings, field, value):
    with pytest.raises(ToolError):
        await call(settings, "search", {"project_id": PROJECT_ID,
                                       "filters": {"transcripts": {field: value}}},
                   lambda request: httpx.Response(200, json=page([transcript_hit()])))


@pytest.mark.parametrize("changes", [
    {"facets": []}, {"facets": ["unknown"]}, {"facets": ["artifacts", "artifacts"]},
    {"facets": ["work_items"], "facet_order": [{"facet": "artifacts"}]},
    {"facet_order": [{"facet": "artifacts"}, {"facet": "artifacts"}]},
    {"sort": {"by": "priority"}}, {"sort": {"direction": "sideways"}},
    {"facet_order": [{"facet": "artifacts", "sort": {"by": "priority"}}]},
    {"filters": {"work_items": {"semantic": True}}},
    {"q": "term", "facets": ["transcripts"], "filters": {"work_items": {"semantic": True}}},
    {"filters": {"work_items": {"status": "unknown"}}},
    {"filters": {"work_items": {"canonical_work_item_id": WORK_ID}}},
    {"filters": {"transcripts": {"agent_session_id": ""}}},
    {"filters": {"artifacts": {"sensitive": "true"}}},
    {"q": "x" * 1001}, {"q": "bad\x00query"}, {"q": "bad\ud800query"},
    {"fulltext": "true"}, {"offset": -1}, {"offset": 1_000_001}, {"limit": True},
    {"limit": 0}, {"limit": 101}, {"filters": {"private_marker": {}}},
])
async def test_invalid_requests_fail_locally_without_echoing_user_values(settings, changes):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=page())

    with pytest.raises(ToolError) as raised:
        await call(settings, "search", {"project_id": PROJECT_ID, **changes}, handler)
    assert requests == []
    assert "private_marker" not in str(raised.value)


@pytest.mark.parametrize("failure", ["timeout", "status", "encoded", "oversize"])
async def test_failure_stays_a_bounded_safe_read(settings, failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private diagnostics", request=request)
        headers = {"content-encoding": "identity, gzip"} if failure == "encoded" else {}
        if failure == "oversize":
            headers["content-length"] = "16777217"
        return httpx.Response(201 if failure == "status" else 200, json=page(), headers=headers)

    with pytest.raises(ToolError) as raised:
        await call(settings, "search", {"project_id": PROJECT_ID}, handler)
    assert len(requests) == 1
    assert "private diagnostics" not in str(raised.value)
    assert "unknown outcome" not in str(raised.value)


async def test_search_tool_schema_and_cold_review_guidance(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    tool = tools["search"]
    properties = tool.inputSchema["properties"]
    assert tool.inputSchema["required"] == ["project_id"]
    assert properties["facets"]["default"] == FACETS
    assert properties["q"]["default"] == "" and properties["fulltext"]["default"] is False
    assert properties["offset"]["default"] == 0 and properties["limit"]["default"] == 50
    assert tool.annotations.readOnlyHint is True and tool.annotations.idempotentHint is True
    assert "cold review before findings freeze" in tool.description
    assert "sensitive artifact bodies and properties" in tool.description
    assert "fully recall the exact checkpoint" in tool.description.lower()
    assert '"prompt"' not in json.dumps(tool.outputSchema)


@pytest.mark.parametrize("selected,actual,accepted", [
    ("pending", "pending", True), ("pending", "blocked", True), ("pending", "waiting", True),
    ("pending", "active", False), ("pending", "dropped", False),
    ("active", "active", True), ("active", "pending", False),
    ("dropped", "dropped", True), ("dropped", "pending", False),
    ("done", "done", True), ("done", "to-review", False), ("done", "deferred-review", False),
    ("to-review", "to-review", True), ("to-review", "done", False),
    ("deferred", "deferred-review", True), ("wont-do", "wont-do-review", True),
    ("all", "active", True), ("all", "to-review", True),
])
async def test_work_status_uses_lifecycle_leases_and_review_disposition(
    settings, work_summary, active_work_context, selected, actual, accepted,
):
    summary = work_summary
    readiness = summary["readiness"]
    if actual == "active":
        summary["readiness"] = active_work_context["readiness"]
    elif actual == "dropped":
        readiness.update(has_dropped_lease=True, display_state="dropped")
    elif actual in {"blocked", "waiting"}:
        readiness.update(is_ready=False, display_state=actual)
        if actual == "blocked":
            readiness.update(is_blocked=True, unresolved_blocker_count=1)
        else:
            readiness.update(is_gated=True, unresolved_gate_count=1)
    elif actual in {"done", "to-review", "deferred-review", "wont-do-review"}:
        summary["work_item"]["status"] = "done"
        effective = actual.removesuffix("-review") if actual.endswith("-review") else actual
        if actual == "to-review":
            effective = actual
        readiness.update(lifecycle_status="done", is_terminal=True, is_ready=False,
                         display_state=effective)
        if actual != "done":
            readiness["review_status"] = effective
    response = page([work_hit(summary)])
    arguments = {"project_id": PROJECT_ID, "filters": {"work_items": {"status": selected}}}
    if accepted:
        result = await call(settings, "search", arguments,
                            lambda request: httpx.Response(200, json=response))
        assert result["total"] == 1
    else:
        with pytest.raises(ToolError):
            await call(settings, "search", arguments,
                       lambda request: httpx.Response(200, json=response))


async def test_blank_query_browses_all_facets_without_artifact_match_fields(settings, work_summary):
    artifact = artifact_hit(score=0.0)
    artifact["artifact"].update(score=0.0, matched_fields=[])
    payload = page([work_hit(work_summary), artifact, transcript_hit()])
    result = await call(settings, "search", {"project_id": PROJECT_ID},
                        lambda request: httpx.Response(200, json=payload))
    assert [item["facet"] for item in result["items"]] == FACETS
    assert result["items"][1]["artifact"]["matched_fields"] == []
    with pytest.raises(ToolError):
        await call(settings, "search", {"project_id": PROJECT_ID, "q": "report"},
                   lambda request: httpx.Response(200, json=payload))
