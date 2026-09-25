"""Explicit force arguments and discoverable recovery guidance at the MCP boundary."""

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter
from test_variable_leases import _arguments

from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.server import build_server


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
@pytest.mark.parametrize("force", [False, True])
async def test_force_forwarding(settings, claim_receipt, active_work_context, tool_name, force):
    requests = []
    response = claim_receipt if tool_name == "claim_work" else {
        "lease": claim_receipt, "context": active_work_context,
    }

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    await adapter(settings, handler).call_tool(tool_name, {**_arguments(tool_name), "force": force})
    assert len(requests) == 1
    assert requests[0].get("force", False) is force


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall"])
@pytest.mark.parametrize("force", [None, 0, 1, "true", "false"])
async def test_force_requires_a_boolean(settings, tool_name, force):
    def handler(request):
        pytest.fail("Invalid force reached the backend")

    with pytest.raises(ToolError, match="force"):
        await adapter(settings, handler).call_tool(
            tool_name, {**_arguments(tool_name), "force": force},
        )


async def test_force_help_is_discoverable_and_preserves_compact_schema(settings):
    catalog = {tool.name: tool for tool in await build_server(settings).list_tools()}
    for name in ("claim_work", "claim_and_recall"):
        schema = catalog[name].inputSchema
        assert schema["properties"]["force"]["type"] == "boolean"
        assert schema["properties"]["force"]["default"] is False
        assert "force" not in schema["required"]
        assert "force" in render_help(name, catalog)
        usage = render_help(name + " usage", catalog)
        assert "confirm no other active session" in usage
        assert "force=true" in usage
        page = render_help(name + " force", catalog)
        assert "invalidates the previous token" in page
        assert "Released/replaced force requests cannot take the lease back" in page
