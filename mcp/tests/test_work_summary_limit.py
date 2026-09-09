"""The API's configured summary policy reaches remote MCP clients unchanged."""

import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server


def arguments(work_item, checkpoint, summary):
    return {
        "project_id": PROJECT_ID, "client_operation_id": CLIENT_OPERATION_ID,
        "title": work_item["title"], "summary": summary, "priority": work_item["priority"],
        "initial_checkpoint": {key: value for key, value in checkpoint.items() if key not in {
            "id", "work_item_id", "kind", "migration_origin", "legacy_record_id", "created_at",
        }},
    }


@pytest.mark.parametrize("maximum", [32, 2048, 4096])
async def test_create_work_reports_server_configured_limit(settings, work_item, checkpoint, maximum):
    summary = "private-summary-" + "x" * maximum
    calls = []

    def handler(request):
        calls.append(request)
        assert json.loads(request.content)["summary"] == summary
        return httpx.Response(422, json={"detail": {
            "code": "work_summary_too_long", "message": "Untrusted upstream diagnostics",
            "context": {"max_chars": maximum},
        }})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as error:
        await server.call_tool("create_work", arguments(work_item, checkpoint, summary))
    assert f"configured maximum of {maximum} characters" in str(error.value)
    assert "MNEMONIC_WORK_SUMMARY_MAX_CHARS" in str(error.value)
    assert "private-summary" not in str(error.value)
    assert "Untrusted upstream" not in str(error.value)
    assert len(calls) == 1


async def test_create_accepts_summary_beyond_default_without_local_cap(settings, work_item, checkpoint):
    summary = "🧠" * 4096
    body = arguments(work_item, checkpoint, summary)

    def handler(request):
        assert json.loads(request.content)["summary"] == summary
        return httpx.Response(201, json={
            "work_item": {**work_item, "summary": summary, "version": 1},
            "initial_checkpoint": checkpoint, "initial_relationships": [],
        })

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert "maxLength" not in tools["create_work"].inputSchema["properties"]["summary"]
    result = await server.call_tool("create_work", body)
    structured = result[1] if isinstance(result, tuple) else result
    assert structured["work_item"]["summary"] == summary


@pytest.mark.parametrize("maximum", [True, 0, -1, "999 private", 2**100, None])
async def test_summary_error_rejects_unsafe_limit_context(settings, work_item, checkpoint, maximum):
    def handler(_request):
        return httpx.Response(422, json={"detail": {
            "code": "work_summary_too_long", "message": "private upstream message",
            "context": {"max_chars": maximum},
        }})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as error:
        await server.call_tool("create_work", arguments(work_item, checkpoint, "summary"))
    assert "API's configured character limit" in str(error.value)
    assert "private" not in str(error.value)
