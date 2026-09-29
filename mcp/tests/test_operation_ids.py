"""Fresh automatic IDs remain recoverable without weakening explicit receipt replay."""

import asyncio
import copy
import json
from uuid import UUID

import httpx
import pytest
from conftest import PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult
from starlette.testclient import TestClient
from test_artifacts import ARTIFACT_ID, artifact_calls
from test_code_reviews import answer_arguments, complete_arguments
from test_tools import protected_tool_arguments
from test_transport import INITIALIZE, JSON_HEADERS

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server, create_app


def retry_id(result, field="client_operation_id"):
    assert isinstance(result, CallToolResult)
    value = result.meta["mnemonic_generated_ids"][field]
    assert str(UUID(value)) == value and UUID(value).version == 4
    assert any(f"{field}={value}" in part.text for part in result.content)
    return value


async def test_uuid_helper_is_local_fresh_and_strict(settings):
    def handler(request):
        pytest.fail("UUID generation must not access the API")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    tools = {tool.name: tool for tool in await server.list_tools()}
    tool = tools["generate_uuid"]
    assert tool.inputSchema["properties"] == {}
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is False
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.openWorldHint is False
    results = await asyncio.gather(*(server.call_tool("generate_uuid", {}) for _ in range(64)))
    values = [result[1]["uuid"] for result in results]
    assert len(set(values)) == len(values)
    assert all(UUID(value).version == 4 for value in values)
    with pytest.raises(ToolError):
        await server.call_tool("generate_uuid", {"project_id": PROJECT_ID})


@pytest.mark.parametrize("tool_name", protected_tool_arguments())
async def test_generated_write_id_survives_unknown_response_and_exact_retry(settings, tool_name):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        raise httpx.ReadTimeout("private transport diagnostic")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = protected_tool_arguments()[tool_name]
    del arguments["client_operation_id"]
    original = copy.deepcopy(arguments)
    result = await server.call_tool(tool_name, arguments)
    assert result.isError is True
    generated = retry_id(result)
    assert len(calls) == 1
    assert calls[0]["client_operation_id"] == generated
    assert arguments == original
    assert "private transport diagnostic" not in str(result)
    with pytest.raises(ToolError, match="unknown|committed"):
        await server.call_tool(tool_name, {**arguments, "client_operation_id": generated})
    assert len(calls) == 2 and calls[0] == calls[1]


async def test_concurrent_identical_fresh_calls_are_distinct_and_leave_content_unchanged(
    settings, checkpoint,
):
    calls = []

    async def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        await asyncio.sleep(0)
        return httpx.Response(201, json={**checkpoint, **{key: value for key, value in payload.items()
                                                       if key != "client_operation_id"}})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = protected_tool_arguments()["add_checkpoint"]
    del arguments["client_operation_id"]
    results = await asyncio.gather(*(server.call_tool("add_checkpoint", arguments) for _ in range(8)))
    ids = [retry_id(result) for result in results]
    assert len(set(ids)) == 8
    assert set(ids) == {call["client_operation_id"] for call in calls}
    for result in results:
        assert not result.isError
        assert "client_operation_id" not in result.structuredContent
        assert "mnemonic_generated_ids" not in result.structuredContent
        assert result.structuredContent["prompt"] == arguments["checkpoint"]["prompt"]


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
async def test_generated_claim_id_replays_with_frozen_transcript_and_duration(
    settings, claim_receipt, active_work_context, tool_name,
):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        receipt = {**claim_receipt, "claim_request_id": payload["claim_request_id"]}
        response = receipt if tool_name == "claim_work" else {"lease": receipt, "context": active_work_context}
        return httpx.Response(200, json=response)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                 "holder_client": claim_receipt["holder_client"],
                 "holder_session_id": claim_receipt["holder_session_id"],
                 "session_transcript": None, "lease_minutes": 15}
    result = await server.call_tool(tool_name, arguments)
    assert not result.isError
    generated = retry_id(result, "claim_request_id")
    replay = await server.call_tool(tool_name, {**arguments, "claim_request_id": generated})
    assert result.structuredContent == replay[1]
    assert calls[0] == calls[1]
    assert calls[0]["session_transcript"] is None and calls[0]["lease_minutes"] == 15


@pytest.mark.parametrize("upstream", ["timeout", "malformed", "rejected"])
def test_generated_id_is_visible_over_real_mcp_protocol(settings, upstream):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if upstream == "timeout":
            raise httpx.ReadTimeout("private diagnostic")
        if upstream == "malformed":
            return httpx.Response(201, json={"unexpected": True})
        return httpx.Response(409, json={"detail": {"code": "work_gated"}})

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = protected_tool_arguments()["create_work"]
    del arguments["client_operation_id"]
    with TestClient(app, base_url="http://localhost:8001") as client:
        assert client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS).status_code == 200
        response = client.post("/mcp", headers=JSON_HEADERS, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "create_work", "arguments": arguments},
        }).json()["result"]
    result = CallToolResult.model_validate(response)
    assert result.isError is True
    assert retry_id(result) == calls[0]["client_operation_id"]
    assert len(calls) == 1
    assert "private diagnostic" not in str(result)


async def test_explicit_invalid_ids_are_never_replaced(settings):
    def handler(request):
        pytest.fail("Invalid explicit IDs must fail before dispatch")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    for invalid in (None, "", "not-a-uuid", 123):
        arguments = protected_tool_arguments()["create_work"]
        arguments["client_operation_id"] = invalid
        with pytest.raises(ToolError):
            await server.call_tool("create_work", arguments)


def other_write_arguments():
    artifacts = {name: arguments for name, arguments in artifact_calls()
                 if name in {"upload_artifact", "replace_artifact", "delete_artifact"}}
    return {**artifacts,
            "update_artifact": {"project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
                                "expected_revision": 1, "description": "New description",
                                "actor_client": "codex", "agent_session_id": "native-session"},
            "respond_to_work_follow_up": answer_arguments(),
            "complete_code_review": complete_arguments()}


@pytest.mark.parametrize("tool_name", other_write_arguments())
async def test_artifact_and_review_writes_preserve_generated_ids_on_failure(settings, tool_name):
    calls = []

    def handler(request):
        if request.url.path.endswith("/artifacts/status"):
            return httpx.Response(200, stream=httpx.ByteStream(json.dumps({
                "enabled": True, "max_bytes": 67108864, "message": "API policy",
            }).encode()))
        calls.append((dict(request.headers), request.content))
        raise httpx.ReadTimeout("private transport diagnostic")

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = other_write_arguments()[tool_name]
    arguments.pop("client_operation_id", None)
    result = await server.call_tool(tool_name, arguments)
    assert result.isError
    generated = retry_id(result)
    assert len(calls) == 1
    headers, body = calls[0]
    assert generated == (headers.get("x-client-operation-id")
                         or json.loads(body)["client_operation_id"])
    with pytest.raises(ToolError):
        await server.call_tool(tool_name, {**arguments, "client_operation_id": generated})
    assert len(calls) == 2 and calls[0] == calls[1]


def test_successful_generated_id_envelope_over_real_mcp_protocol(settings, checkpoint):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(201, json={**checkpoint, **{
            key: value for key, value in payload.items() if key != "client_operation_id"
        }})

    arguments = protected_tool_arguments()["add_checkpoint"]
    del arguments["client_operation_id"]
    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with TestClient(app, base_url="http://localhost:8001") as client:
        client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        response = client.post("/mcp", headers=JSON_HEADERS, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "add_checkpoint", "arguments": arguments},
        }).json()["result"]
    result = CallToolResult.model_validate(response)
    assert not result.isError
    assert retry_id(result) == calls[0]["client_operation_id"]
    assert result.structuredContent["id"] == checkpoint["id"]
    assert "client_operation_id" not in result.structuredContent
