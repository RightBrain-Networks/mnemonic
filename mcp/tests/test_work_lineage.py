"""MCP requires explicit session lineage while preserving historical replay inputs."""

import json

import httpx
import pytest
from conftest import OTHER_CHECKPOINT_ID, OTHER_WORK_ID, PROJECT_ID, RELATIONSHIP_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, protected_success_responses, protected_tool_arguments, structured


def creation_arguments():
    arguments = protected_tool_arguments()["create_work"]
    arguments["discovered_from_work_item_id"] = OTHER_WORK_ID
    return arguments


@pytest.fixture
def base_creation(work_item, checkpoint, relationship, progress_event, human_gate):
    return protected_success_responses(
        work_item, checkpoint, relationship, progress_event, human_gate,
    )["create_work"]


def creation_response(base_creation):
    response = base_creation
    actor = response["initial_checkpoint"]
    response["initial_relationships"] = [{
        "id": RELATIONSHIP_ID, "project_id": PROJECT_ID,
        "relationship_type": "discovered-from", "source_work_item_id": WORK_ID,
        "target_work_item_id": OTHER_WORK_ID, "context_checkpoint_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
        "created_by_client": actor["source_client"],
        "created_by_session_id": actor["source_session_id"],
        "created_by_model": actor["source_model"], "created_at": actor["created_at"],
    }]
    return response


async def test_lineage_is_required_nullable_in_tool_schema(settings):
    def handler(request):
        raise AssertionError("Schema inspection must not dispatch HTTP")
    server = adapter(settings, handler)
    tool = next(tool for tool in await server.list_tools() if tool.name == "create_work")
    assert "discovered_from_work_item_id" in tool.inputSchema["required"]
    variants = tool.inputSchema["properties"]["discovered_from_work_item_id"]["anyOf"]
    assert {option["type"] for option in variants} == {"string", "null"}


@pytest.mark.parametrize("origin", [None, OTHER_WORK_ID])
async def test_lineage_serializes_and_accepts_generated_edge(
    settings, base_creation, origin,
):
    response = creation_response(base_creation)
    if origin is None:
        response["initial_relationships"] = []
    def handler(request):
        assert json.loads(request.content)["discovered_from_work_item_id"] == origin
        return httpx.Response(201, json=response)
    arguments = {**creation_arguments(), "discovered_from_work_item_id": origin}
    assert structured(await adapter(settings, handler).call_tool("create_work", arguments)) == response


@pytest.mark.parametrize("fault", ["missing", "duplicate", "wrong_origin", "wrong_context", "actor"])
async def test_incoherent_generated_lineage_is_an_unknown_write_outcome(
    settings, base_creation, fault,
):
    response = creation_response(base_creation)
    edge = response["initial_relationships"][0]
    if fault == "missing":
        response["initial_relationships"] = []
    elif fault == "duplicate":
        response["initial_relationships"].append(dict(edge))
    elif fault == "wrong_origin":
        edge["target_work_item_id"] = PROJECT_ID
    elif fault == "wrong_context":
        edge["context_checkpoint_work_item_id"] = WORK_ID
    else:
        edge["created_by_session_id"] = "another-session"
    def handler(request):
        return httpx.Response(201, json=response)
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, handler).call_tool("create_work", creation_arguments())


async def test_explicit_same_origin_edge_is_not_duplicated(settings, base_creation):
    response = creation_response(base_creation)
    arguments = creation_arguments()
    arguments["initial_relationships"] = [{
        "type": "discovered-from", "direction": "outgoing", "other_work_item_id": OTHER_WORK_ID,
        "context_checkpoint_id": OTHER_CHECKPOINT_ID,
    }]
    def handler(request):
        return httpx.Response(201, json=response)
    result = structured(await adapter(settings, handler).call_tool("create_work", arguments))
    assert len(result["initial_relationships"]) == 1


async def test_historical_omission_dispatches_unchanged(settings, base_creation):
    response = base_creation
    def handler(request):
        assert "discovered_from_work_item_id" not in json.loads(request.content)
        return httpx.Response(201, json=response)
    await adapter(settings, handler).call_tool("create_work", protected_tool_arguments()["create_work"])


async def test_fresh_omission_returns_actionable_backend_rejection(settings):
    def handler(request):
        return httpx.Response(422, json={"detail": {
            "code": "discovered_from_work_item_id_required", "message": "PRIVATE_BODY",
        }})
    with pytest.raises(ToolError, match="discovered_from_work_item_id.*explicit null") as captured:
        await adapter(settings, handler).call_tool(
            "create_work", protected_tool_arguments()["create_work"],
        )
    assert "PRIVATE_BODY" not in str(captured.value)
