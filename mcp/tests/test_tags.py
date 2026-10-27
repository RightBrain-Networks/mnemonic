"""Catalog discovery and current tag assignments across native MCP calls."""

import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_compact_search import native_call, response_page
from test_tools import ACTOR_ARGUMENTS, adapter, structured

from mnemonic_mcp.search_disclosure import WorkAppliedFilters

TAG_ID = "f7ec9f48-027e-4793-a2ec-338cc8a41dc8"


def catalog():
    return {"project_id": PROJECT_ID, "items": [{
        "id": TAG_ID, "project_id": PROJECT_ID, "name": "code-review",
        "description": "Review completed implementation work.", "color": "#abcdee",
        "menu_order": 0, "system": True,
    }], "total": 1}


async def test_list_tags_returns_complete_ordered_project_catalog(settings):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/tags"
        assert not request.content
        return httpx.Response(200, json=catalog())

    server = adapter(settings, handler)
    result = structured(await server.call_tool("list_tags", {"project_id": PROJECT_ID}))
    assert result == catalog()
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert tools["list_tags"].annotations.readOnlyHint
    assert not {"create_tag", "update_tag", "delete_tag", "reorder_tags"} & tools.keys()


@pytest.mark.parametrize("defect", ["wrong_project", "duplicate", "incomplete", "bad_name"])
async def test_list_tags_rejects_incoherent_catalog(settings, defect):
    page = catalog()
    if defect == "wrong_project":
        page["items"][0]["project_id"] = WORK_ID
    elif defect == "duplicate":
        page["items"] *= 2
        page["total"] = 2
    elif defect == "incomplete":
        page["total"] = 2
    else:
        page["items"][0]["name"] = "Code Review"
    with pytest.raises(ToolError):
        await adapter(settings, lambda _: httpx.Response(200, json=page)).call_tool(
            "list_tags", {"project_id": PROJECT_ID},
        )


@pytest.mark.parametrize("tags", [None, ["bug", "accessibility"]])
async def test_create_work_preserves_explicit_current_tags(settings, work_item, checkpoint, tags):
    context = {"prompt": checkpoint["prompt"], "source_client": checkpoint["source_client"],
               "source_session_id": checkpoint["source_session_id"]}

    def handler(request):
        body = json.loads(request.content)
        assert "tags" in body and body["tags"] == tags
        assert body["initial_checkpoint"]["tags"] == []
        return httpx.Response(201, json={
            "work_item": {**work_item, "version": 1, "tags": tags, "system_tags": []},
            "initial_checkpoint": {**checkpoint, **body["initial_checkpoint"]},
            "initial_relationships": [],
        })

    result = structured(await adapter(settings, handler).call_tool("create_work", {
        "project_id": PROJECT_ID, "title": work_item["title"], "summary": work_item["summary"],
        "priority": work_item["priority"], "tags": tags, "discovered_from_work_item_id": None,
        "initial_checkpoint": context, "client_operation_id": CLIENT_OPERATION_ID,
    }))
    assert result["work_item"]["tags"] == tags


@pytest.mark.parametrize("tags", [[], ["Bug"], ["bug", "bug"], ["not a tag"]])
async def test_current_tag_inputs_reject_empty_or_noncanonical_values(settings, tags):
    with pytest.raises(ToolError):
        await adapter(settings, lambda _: pytest.fail("Invalid tags must not dispatch")).call_tool(
            "create_work", {
                "project_id": PROJECT_ID, "title": "Tags", "summary": "Tags",
                "initial_checkpoint": {"prompt": "Tags", "source_client": "codex",
                                       "source_session_id": "test"},
                "tags": tags, "client_operation_id": CLIENT_OPERATION_ID,
            },
        )


async def test_update_work_can_clear_current_tags_without_changing_catalog(settings, work_item):
    def handler(request):
        body = json.loads(request.content)
        assert body["tags"] is None
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}"
        return httpx.Response(200, json={**work_item, "version": work_item["version"] + 1,
                                        "tags": None, "system_tags": ["code-review"]})

    result = structured(await adapter(settings, handler).call_tool("update_work", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "expected_version": work_item["version"], "changes": {"tags": None},
        "client_operation_id": CLIENT_OPERATION_ID, **ACTOR_ARGUMENTS,
    }))
    assert result["tags"] is None and result["system_tags"] == ["code-review"]


@pytest.mark.parametrize("tool", ["search", "search_work"])
@pytest.mark.parametrize("mode", ["or", "and"])
async def test_search_round_trips_multi_tag_boolean_filters(settings, work_summary, tool, mode):
    page = response_page(tool, work_summary)
    filters = {"tags": ["bug", "accessibility"], "tag_mode": mode}
    page["applied_filters"]["work_items"] = WorkAppliedFilters(**filters).model_dump(mode="json")
    arguments = {"q": "needle"}
    if tool == "search":
        arguments["filters"] = {"work_items": filters}
    else:
        arguments.update(filters)
    result, requests = await native_call(settings, tool, arguments, page)
    assert result["applied_filters"]["work_items"]["tags"] == filters["tags"]
    if tool == "search":
        sent = json.loads(requests[0].content)["filters"]["work_items"]
        assert sent["tags"] == filters["tags"]
        assert sent.get("tag_mode", "or") == mode
    else:
        assert requests[0].url.params.get_list("tags") == filters["tags"]
        assert requests[0].url.params.get("tag_mode", "or") == mode
