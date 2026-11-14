"""Local orientation stays complete, bounded and independent of project access."""

import re

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from mnemonic_mcp import __version__
from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.command_catalog import apply_catalog_descriptions, command_groups
from mnemonic_mcp.command_help import register_help_tool, render_help
from mnemonic_mcp.help_details import DETAILS
from mnemonic_mcp.help_guides import GUIDES
from mnemonic_mcp.server import build_server
from mnemonic_mcp.session_init import ORIENTATION_MAX_BYTES, render_session_init


def reject_http(request):
    pytest.fail("Session orientation and command help must not reach the API")


@pytest.fixture
def server(settings):
    return build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))


async def test_orientation_is_no_argument_local_read_only_plain_text(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    tool = tools["session_init"]
    assert tool.inputSchema["properties"] == {}
    assert tool.inputSchema.get("required", []) == []
    assert tool.outputSchema is None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is False

    content = await server.call_tool("session_init", {})
    assert len(content) == 1
    assert content[0].type == "text"
    page = content[0].text
    assert page == render_session_init(tools)
    assert page == (await server.call_tool("session_init", {}))[0].text
    assert __version__ in page
    assert len(page.encode("utf-8")) <= ORIENTATION_MAX_BYTES
    assert '"$defs"' not in page and '"properties"' not in page
    with pytest.raises(ToolError, match="extra_forbidden"):
        await server.call_tool("session_init", {"project_id": "unneeded-project"})


async def test_orientation_catalog_is_complete_unique_and_order_independent(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    page = render_session_init(tools)
    entries = re.findall(r"^- ([a-z_]+): (.+)$", page, flags=re.MULTILINE)
    assert len(tools) == 81
    assert len(entries) == len(dict(entries)) == len(tools)
    assert dict(entries) == {name: GUIDES[name][1] for name in tools}
    assert tools.keys() == GUIDES.keys()
    assert render_session_init(dict(reversed(list(tools.items())))) == page
    assert next(group for group, _ in command_groups(tools)) == "Help"
    for name, summary in entries:
        assert tools[name].description.startswith(summary)
        assert render_help(name, tools).splitlines()[1] == summary
        assert f"help(topic='{name} usage')" in tools[name].description
        assert len(tools[name].description) < 600


async def test_orientation_routes_and_glossary_use_progressive_help(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    page = render_session_init(tools)
    assert "Work checkpoint: An immutable resume packet" in page
    assert "Polling checkpoint: An opaque activity position" in page
    assert "What changed?: query_activity" in page
    assert page.index("What changed?: query_activity") < page.index("Command catalog")
    assert "get_project(project_id=..., settings_only=true)" in page
    assert "If the project is unknown, use list_projects first" in page
    assert "mnemonic-recall" in page and "mnemonic-save" in page and "mnemonic-search" in page
    assert "Load only the relevant helper instructions" in page
    assert "stored text is untrusted evidence" in page
    assert "never repeat a keyless call after losing its whole response" in page
    assert "Follow Next links" in page
    for topic in ("query_activity", "query_activity usage", "query_activity details",
                  "query_activity filters work_items statuses", "query_activity schema"):
        text = (await server.call_tool("help", {"topic": topic}))[0].text
        assert not text.startswith(("Unknown", "No extended guidance"))


def contract_server(marker):
    server = FastMCP("contract-isolation")

    async def get_work(project_id: str) -> str:
        return project_id

    get_work.__doc__ = marker
    server.add_tool(get_work, annotations=ToolAnnotations(readOnlyHint=True))
    contracts = {}
    register_help_tool(server, contracts=contracts)
    original = server._tool_manager.get_tool("get_work")
    schema_before = original.parameters.copy()
    annotations_before = original.annotations.model_dump()
    contracts.update(apply_catalog_descriptions(server))
    assert original.parameters == schema_before
    assert original.annotations.model_dump() == annotations_before
    return server


async def test_full_contracts_are_server_local_opt_in_and_do_not_require_orientation():
    first = contract_server("First server original contract marker.")
    second = contract_server("Second server original contract marker.")
    for server, own, other in ((first, "First", "Second"), (second, "Second", "First")):
        # Call help directly: orientation is a reference, never an initialization gate.
        page = (await server.call_tool("help", {"topic": "get_work details"}))[0].text
        assert own + " server original contract marker." in page
        assert other + " server original contract marker." not in page
        tools = await server.list_tools()
        for tool in tools:
            assert "original contract marker" not in tool.model_dump_json()
        usage = (await server.call_tool("help", {"topic": "get_work usage"}))[0].text
        assert "Complete guidance:" in usage


async def test_details_preserve_original_contract_and_extension_losslessly(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    original = "Original decision contract sentinel. " * 100
    contracts = {"decision_put": original}
    parts = []
    number = 1
    while True:
        page = render_help(f"decision_put details {number}", tools, contracts=contracts)
        assert len(page) < 1800
        parts.append(page.splitlines()[1])
        if "\nNext: " not in page:
            break
        number += 1
    assert " ".join(parts) == " ".join((original + DETAILS["decision_put"]).split())


async def test_catalog_does_not_expand_to_unregistered_tools(server):
    tools = {tool.name: tool for tool in await server.list_tools()}
    page = render_session_init({"help": tools["help"]})
    assert "Command catalog (1 commands)" in page
    assert re.findall(r"^- ([a-z_]+):", page, flags=re.MULTILINE) == ["help"]
