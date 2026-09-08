"""Oversize tool errors reach real SDK callers on both supported transports."""

import os
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import httpx
from conftest import API_KEY, CLIENT_OPERATION_ID, PROJECT_ID
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from mnemonic_mcp import transport
from mnemonic_mcp.server import create_app


def oversize_upload():
    return {
        "project_id": PROJECT_ID, "client_operation_id": CLIENT_OPERATION_ID,
        "filename": "PRIVATE_FILENAME", "content_base64": "A" * 2048,
        "agent_session_id": "test-session", "actor_client": "test-client",
    }


async def assert_oversize_tool_error(session):
    await session.initialize()
    result = await session.call_tool("upload_artifact", oversize_upload())
    assert result.isError is True
    assert len(result.content) == 1
    assert result.content[0].text == transport.MCP_SIZE_LIMIT_MESSAGE
    assert "PRIVATE_FILENAME" not in result.model_dump_json()
    # The agent can issue a subsequent status/read call on the same session.
    await session.send_ping()


async def test_stdio_sdk_receives_limit_guidance_and_keeps_session_open():
    code = (
        "from mnemonic_mcp import transport; "
        "transport.MCP_REQUEST_MAX_BYTES=1024; "
        "transport.MCP_ARTIFACT_REQUEST_MAX_BYTES=4096; "
        "transport.MCP_ARTIFACT_CONTENT_MAX_CHARS=1024; "
        "from mnemonic_mcp.server import main; main()"
    )
    params = StdioServerParameters(
        command=sys.executable, args=["-c", code, "--transport", "stdio"],
        env={
            **os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "MNEMONIC_API_KEY": API_KEY, "MNEMONIC_API_URL": "http://127.0.0.1:1",
        },
    )
    with anyio.fail_after(10):
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=5)) as session,
        ):
            await assert_oversize_tool_error(session)


async def test_http_sdk_receives_limit_guidance_and_keeps_session_open(settings, monkeypatch):
    monkeypatch.setattr(transport, "MCP_REQUEST_MAX_BYTES", 1024)
    monkeypatch.setattr(transport, "MCP_ARTIFACT_REQUEST_MAX_BYTES", 4096)
    monkeypatch.setattr(transport, "MCP_ARTIFACT_CONTENT_MAX_CHARS", 1024)
    app = create_app(settings)
    with anyio.fail_after(10):
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {API_KEY}"},
            ) as client,
            streamable_http_client("http://localhost:8001/mcp", http_client=client)
            as (read, write, _),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=5)) as session,
        ):
            await assert_oversize_tool_error(session)
