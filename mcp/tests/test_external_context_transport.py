"""Maximum supported reference/context fields fit the coordinated result envelope."""

import json

import httpx
from external_context_fixture import maximum_external_context
from mcp.types import CallToolResult, JSONRPCResponse

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.models import WorkContext
from mnemonic_mcp.server import build_server
from mnemonic_mcp.transport import MCP_RESULT_MAX_BYTES


async def test_maximum_context_fits_both_sdk_representations(
    settings,
    work_context,
    adjacent_relationship,
    progress_event,
    human_gate,
    resolved_human_gate,
):
    context = maximum_external_context(
        work_context, adjacent_relationship, progress_event, human_gate, resolved_human_gate
    )
    raw = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode()
    WorkContext.model_validate_json(raw, strict=True)

    def handler(request):
        return httpx.Response(200, content=raw, headers={"content-type": "application/json"})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    content, structured = await server.call_tool(
        "recall_work",
        {
            "project_id": context["work_item"]["project_id"],
            "work_item_id": context["work_item"]["id"],
            "recent_limit": 20,
            "recent_event_limit": 20,
        },
    )
    result = CallToolResult(content=content, structuredContent=structured, isError=False)
    frame = JSONRPCResponse(
        jsonrpc="2.0", id="x" * 128, result=result.model_dump(by_alias=True, exclude_none=True)
    )
    record = frame.model_dump_json(by_alias=True, exclude_none=True).encode() + b"\n"
    assert json.loads(content[0].text) == structured
    assert len(structured["recent_checkpoints"]) == 20
    assert len(structured["recent_events"]) == 20
    assert all(
        len(structured[name + "_relationships"]) == 100
        for name in ("incoming", "outgoing", "undirected")
    )
    assert 50_000_000 < len(record) <= MCP_RESULT_MAX_BYTES == 67_108_864
