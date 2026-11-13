"""Activity queries remain safe, bounded and explicit about polling scope."""

import base64
import copy
import hashlib
import json

import httpx
import pytest
from conftest import NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError

from mnemonic_mcp.activity_query_models import QueryActivityPage, QueryActivityRequest
from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.server import build_server

STREAM_ID = "17780b88-6968-4717-983a-9b235962be13"


class ActivityStream(httpx.AsyncByteStream):
    def __init__(self, document):
        self.body = json.dumps(document).encode()

    async def __aiter__(self):
        yield self.body


def position(request=None, **fields):
    query = QueryActivityRequest.model_validate(request or {})
    omitted = {"cursor", "start", "limit"}
    if fields.get("kind", "checkpoint") == "checkpoint":
        omitted |= {"since", "until", "after"}
    scope = hashlib.sha256(json.dumps(
        query.model_dump(mode="json", exclude=omitted), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    document = {
        "v": 1, "kind": "checkpoint", "project_id": PROJECT_ID, "stream_id": STREAM_ID,
        "scope": scope, **fields,
    }
    return base64.urlsafe_b64encode(json.dumps(
        document, sort_keys=True, separators=(",", ":"),
    ).encode()).decode().rstrip("=")


def activity_page(request=None):
    return {
        "project_id": PROJECT_ID, "stream_id": STREAM_ID, "view": "resources",
        "items": [{
            "resource_type": "work_items", "resource_id": WORK_ID, "title": "A work item",
            "revision": "3", "first_sequence": "2", "last_sequence": "8",
            "first_occurred_at": NOW, "last_occurred_at": NOW, "recorded_at": NOW,
            "change_count": 2, "operations": ["created", "updated"],
            "changed_fields": ["title"], "before_state": {},
            "after_state": {"title": "A work item"}, "summary": "Work item changed twice.",
            "origins": ["live"], "current": {
                "exists": True, "deleted": False, "title": "A work item", "revision": "3",
                "status": "pending", "tags": ["bug"], "system_tags": [], "domain_id": None,
                "domain_prefix": None, "decision_id": None, "state": None,
            },
        }],
        "through_sequence": "10", "next_cursor": None,
        "checkpoint": position(request, after="10"),
        "has_more": False, "counts": {
            "changes": 2, "resources": 1, "by_facet": {"work_items": {
                "changes": 2, "resources": 1,
            }},
        },
        "coverage": {
            "capture_started_at": NOW, "historical_coverage": "partial", "imported_changes": 0,
            "known_gaps": ["Earlier scalar overwrites are unavailable."],
            "timestamps": "occurred_at_source_time_not_commit_time",
        },
        "filter_semantics": "current_state_rechecked_each_page",
        "page_limited_by_bytes": False, "byte_limit": 32768,
    }


async def invoke(settings, document, arguments=None):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/activity/query"
        return httpx.Response(200, stream=ActivityStream(document))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = await server.call_tool("query_activity", {
        "project_id": PROJECT_ID, **(arguments or {}),
    })
    return result[1], calls


async def test_safe_query_sends_compact_typed_scope_and_accepts_sequence_gaps(settings):
    arguments = {
        "since": NOW, "facets": ["work_items"],
        "filters": {"work_items": {"statuses": ["pending"], "tags": ["bug"]}},
    }
    expected = activity_page(arguments)
    result, calls = await invoke(settings, expected, arguments)
    assert result == expected
    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    assert payload["since"] == NOW
    assert payload["view"] == "resources"
    assert payload["limit"] == 20
    assert payload["filters"]["work_items"]["tag_mode"] == "or"
    assert "client_operation_id" not in payload


@pytest.mark.parametrize("arguments", [
    {"since": "2026-11-02"}, {"since": 123}, {"since": NOW, "until": NOW},
    {"since": NOW, "after": "checkpoint"}, {"start": "now", "after": "checkpoint"},
    {"start": "now", "cursor": "continuation"}, {"facets": []},
    {"facets": ["work_items", "work_items"]}, {"limit": 101}, {"limit": True},
    {"facets": ["artifacts"], "filters": {"work_items": {"statuses": ["pending"]}}},
    {"filters": {"decisions": {"states": ["done"]}}},
    {"filters": {"work_items": {"statuses": []}}},
    {"filters": {"artifacts": {"tags": []}}},
])
async def test_bad_query_is_rejected_before_transport(settings, arguments):
    def handler(request):
        pytest.fail("Invalid query must not reach API")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError):
        await server.call_tool("query_activity", {"project_id": PROJECT_ID, **arguments})


def test_continuation_keeps_original_after_and_scope_but_can_change_limit():
    request = QueryActivityRequest.model_validate({
        "after": "checkpoint", "cursor": "continuation", "limit": 4,
        "facets": ["decisions"], "filters": {"decisions": {"domain_prefixes": ["ADR"]}},
    })
    assert request.after == "checkpoint" and request.cursor == "continuation"
    assert request.filters.decisions.domain_prefixes == ["ADR"]


@pytest.mark.parametrize("mutation", [
    "early_checkpoint", "missing_cursor", "above_head", "reverse",
    "repeat_resource", "count_mismatch", "event_group", "unknown_body", "bool_count",
])
def test_malformed_activity_page_is_rejected(mutation):
    page = activity_page()
    if mutation == "early_checkpoint":
        page.update(has_more=True, next_cursor="next")
    elif mutation == "missing_cursor":
        page.update(has_more=True, checkpoint=None)
    elif mutation == "above_head":
        page["through_sequence"] = "7"
    elif mutation == "reverse":
        page["items"][0]["first_sequence"] = "9"
    elif mutation == "repeat_resource":
        duplicate = copy.deepcopy(page["items"][0])
        duplicate["last_sequence"] = "9"
        page["items"].append(duplicate)
    elif mutation == "count_mismatch":
        page["counts"]["changes"] = 3
    elif mutation == "event_group":
        page["view"] = "events"
    elif mutation == "unknown_body":
        page["items"][0]["body"] = "private"
    elif mutation == "bool_count":
        page["items"][0]["change_count"] = True
    with pytest.raises(ValidationError):
        QueryActivityPage.model_validate_json(json.dumps(page), strict=True)


@pytest.mark.parametrize("mutation", ["wrong_project", "wrong_facet", "wrong_status"])
async def test_wire_scope_mismatch_rejected(settings, mutation):
    arguments = {
        "facets": ["work_items"], "filters": {"work_items": {"statuses": ["pending"]}},
    }
    page = activity_page(arguments)
    if mutation == "wrong_project":
        page["project_id"] = OTHER_WORK_ID
    elif mutation == "wrong_facet":
        page["items"][0]["resource_type"] = "artifacts"
    else:
        page["items"][0]["current"]["status"] = "done"
    with pytest.raises(ToolError):
        await invoke(settings, page, arguments)


@pytest.mark.parametrize("system_tag", ["remediation", "dir"])
async def test_work_filters_accept_intrinsic_tag_membership(settings, system_tag):
    arguments = {
        "facets": ["work_items"], "filters": {"work_items": {"tags": [system_tag]}},
    }
    page = activity_page(arguments)
    page["items"][0]["current"].update(tags=None, system_tags=[system_tag])
    result, _ = await invoke(settings, page, arguments)
    assert result["items"][0]["current"]["system_tags"] == [system_tag]


async def test_work_all_tags_filter_combines_assigned_and_intrinsic_membership(settings):
    arguments = {
        "facets": ["work_items"],
        "filters": {"work_items": {"tags": ["bug", "remediation"], "tag_mode": "and"}},
    }
    page = activity_page(arguments)
    page["items"][0]["current"]["system_tags"] = ["remediation"]
    await invoke(settings, page, arguments)
    page["items"][0]["current"]["system_tags"] = []
    with pytest.raises(ToolError):
        await invoke(settings, page, arguments)


async def test_decision_delta_preserves_canonical_lookup_identifier(settings):
    arguments = {"facets": ["decisions"]}
    page = activity_page(arguments)
    page["items"][0]["resource_type"] = "decisions"
    page["items"][0]["current"].update(
        tags=None, status=None, domain_id=OTHER_WORK_ID, domain_prefix="ADR",
        decision_id="ADR-00001", state="accepted",
    )
    page["counts"]["by_facet"] = {"decisions": {"changes": 2, "resources": 1}}
    result, _ = await invoke(settings, page, arguments)
    assert result["items"][0]["resource_id"] == WORK_ID
    assert result["items"][0]["current"]["decision_id"] == "ADR-00001"


@pytest.mark.parametrize("identifier", [WORK_ID, "ADR-00001-3", "adr-00001", "ADR-1"])
def test_activity_rejects_noncanonical_decision_lookup_identifier(identifier):
    page = activity_page()
    page["items"][0]["current"]["decision_id"] = identifier
    with pytest.raises(ValidationError):
        QueryActivityPage.model_validate_json(json.dumps(page), strict=True)


async def test_empty_final_page_advances_checkpoint(settings):
    page = activity_page()
    page.update(items=[], counts={"changes": 0, "resources": 0, "by_facet": {}})
    result, _ = await invoke(settings, page, {"start": "now"})
    assert result["checkpoint"] == position(after="10")


async def test_closed_interval_never_advances_polling_checkpoint(settings):
    page = activity_page()
    page["checkpoint"] = None
    result, _ = await invoke(settings, page, {"until": "2026-09-01T00:00:00Z"})
    assert result["checkpoint"] is None
    with pytest.raises(ToolError):
        await invoke(settings, page)
    with pytest.raises(ToolError):
        await invoke(settings, activity_page(), {"until": "2026-09-01T00:00:00Z"})


async def test_continuation_preserves_upper_bound_and_final_checkpoint(settings):
    page = activity_page()
    continuation = position(kind="page", lower="0", upper="10", last="8")
    page.update(has_more=True, checkpoint=None, next_cursor=continuation)
    result, _ = await invoke(settings, page, {"limit": 1})
    assert result["next_cursor"] == continuation
    final = activity_page()
    final["items"][0].update(first_sequence="9", last_sequence="10", resource_id=OTHER_WORK_ID)
    result, _ = await invoke(settings, final, {"cursor": continuation, "limit": 2})
    assert result["checkpoint"] == position(after="10")


@pytest.mark.parametrize("bad_token", [
    "not-a-position", position(after="9"), position(after="11"),
    position(after="10", project_id=OTHER_WORK_ID),
    position({"facets": ["artifacts"]}, after="10"),
])
async def test_malformed_success_cannot_advance_wrong_checkpoint(settings, bad_token):
    page = activity_page()
    page["checkpoint"] = bad_token
    with pytest.raises(ToolError):
        await invoke(settings, page)


async def test_continuation_rejects_repeated_groups_and_changed_high_water(settings):
    continuation = position(kind="page", lower="0", upper="10", last="8")
    with pytest.raises(ToolError):
        await invoke(settings, activity_page(), {"cursor": continuation})
    page = activity_page()
    page.update(through_sequence="11", checkpoint=position(after="11"))
    page["items"][0].update(first_sequence="9", last_sequence="10")
    with pytest.raises(ToolError):
        await invoke(settings, page, {"cursor": continuation})


async def test_query_help_contains_examples_and_complete_contract_without_api(settings):
    def handler(request):
        pytest.fail("Help must not reach API")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    catalog = {tool.name: tool for tool in await server.list_tools()}
    tool = catalog["query_activity"]
    assert tool.annotations.readOnlyHint and tool.annotations.idempotentHint
    assert not tool.annotations.destructiveHint
    assert "Complete guidance:" in render_help("query_activity usage", catalog)
    pages = [render_help(f"query_activity details {number}", catalog) for number in range(1, 8)]
    combined = " ".join(pages)
    for required in (
        '"pending"', '"ADR"', "occurred_at", "checkpoint", "32768", "current resource state",
        "stream-change", "Sensitive artifact", "not an LLM", "Cold reviewers",
    ):
        assert required in combined
    assert all(len(page) < 1900 for page in pages)
    assert "Current search_work statuses" in render_help(
        "query_activity filters work_items statuses", catalog,
    )
