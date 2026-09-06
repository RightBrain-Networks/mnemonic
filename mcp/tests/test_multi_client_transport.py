"""MCP initialization metadata must not become durable agent provenance."""

import asyncio
import json
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from conftest import API_KEY, PROJECT_ID
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation, TextContent
from test_transport import assert_serialized_tool_contract

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import create_app

CLIENTS = (
    ("Claude Code", "claude-code"),
    ("codex-cli", "codex"),
    ("OpenCode", "opencode"),
    ("Unrecognized AI IDE", "example-ide"),
)


def operation_case(tool_name, client_name, session_id, index, template):
    work_item_id = str(UUID(int=index + 1))
    operation_id = str(UUID(int=index + 100))
    arguments = {"project_id": PROJECT_ID, "work_item_id": work_item_id}
    response = {**template, "work_item_id": work_item_id}
    if tool_name == "claim_work":
        payload = {
            "holder_client": client_name,
            "holder_session_id": session_id,
            "claim_request_id": f"claim:{client_name}/{index}",
        }
        if client_name == "opencode":
            payload.update(
                purpose="code_review", code_review_id=str(UUID(int=500)), mode="cold",
            )
            response.update(
                lease_generation_id=str(UUID(int=index + 600)),
                code_review_version=1,
                scope_sha256="a" * 64,
            )
        arguments.update(payload)
        response.update(payload)
        return arguments, payload, response
    if tool_name == "add_checkpoint":
        checkpoint_input = {
            "prompt": f"Continue the task from {client_name}.",
            "source_client": client_name,
            "source_session_id": session_id,
            "source_model": f"model/{client_name}",
            "source_session_url": None,
            "repository_branch": None,
            "verified_against": None,
            "tags": [],
            "source_metadata": {},
        }
        arguments.update(checkpoint=checkpoint_input, client_operation_id=operation_id)
        payload = {"kind": "context", **checkpoint_input, "client_operation_id": operation_id}
        response.update(checkpoint_input)
        return arguments, payload, response
    actor = {
        "actor_client": client_name,
        "actor_session_id": session_id,
        "actor_model": f"model/{client_name}",
    }
    body = f"Verified the task assigned to {client_name}."
    arguments.update(**actor, body=body, metadata={}, client_operation_id=operation_id)
    payload = {
        "event_type": "progress", "actor": actor, "body": body,
        "metadata": {}, "client_operation_id": operation_id,
    }
    response.update(**actor, body=body, metadata={})
    return arguments, payload, response


@pytest.mark.parametrize(
    ("tool_name", "endpoint", "fixture_name", "status_code"),
    [
        ("claim_work", "claim", "claim_receipt", 200),
        ("add_checkpoint", "checkpoints", "checkpoint", 201),
        ("append_event", "events", "progress_event", 201),
    ],
)
async def test_concurrent_client_sessions_keep_explicit_provenance(
    settings, request, tool_name, endpoint, fixture_name, status_code,
):
    template = request.getfixturevalue(fixture_name)
    cases = {}
    for client_index, (_, client_name) in enumerate(CLIENTS):
        for round_index in range(2):
            # Native session IDs are opaque and may collide across different clients.
            session_id = "native:shared/session.1" if round_index == 0 else f"{client_name}:次/2"
            cases[client_index, round_index] = operation_case(
                tool_name, client_name, session_id, client_index * 2 + round_index, template,
            )
    expected = {arguments["work_item_id"]: (payload, response)
                for arguments, payload, response in cases.values()}
    seen = []
    upstream_barrier = asyncio.Barrier(len(CLIENTS))

    async def handler(upstream_request):
        work_item_id = upstream_request.url.path.split("/")[-2]
        seen.append(work_item_id)
        payload, response = expected[work_item_id]
        assert upstream_request.method == "POST"
        assert upstream_request.url.path == (
            f"/api/v1/projects/{PROJECT_ID}/work-items/{work_item_id}/{endpoint}"
        )
        assert json.loads(upstream_request.content) == payload
        # All four operations must reach the same server before any can finish.
        await upstream_barrier.wait()
        return httpx.Response(status_code, json=response)

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    initialized_barrier = asyncio.Barrier(len(CLIENTS))

    async def run_client(client_index, client_info_name):
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {API_KEY}"},
            ) as http_client,
            streamable_http_client(
                "http://localhost:8001/mcp", http_client=http_client,
            ) as (read, write, get_session_id),
            ClientSession(
                read, write,
                read_timeout_seconds=timedelta(seconds=15),
                client_info=Implementation(name=client_info_name, version="1.0"),
            ) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Mnemonic"
            assert get_session_id() is None
            await initialized_barrier.wait()
            catalog = await session.list_tools()
            assert_serialized_tool_contract([
                tool.model_dump(by_alias=True, exclude_none=True) for tool in catalog.tools
            ])
            for round_index in range(2):
                arguments, _, response = cases[client_index, round_index]
                result = await session.call_tool(tool_name, arguments)
                assert result.isError is False
                assert result.structuredContent == response
                assert len(result.content) == 1
                assert isinstance(result.content[0], TextContent)
                assert json.loads(result.content[0].text) == response
                assert get_session_id() is None
            # Neither clientInfo nor a prior successful call supplies missing provenance.
            missing_session = dict(arguments)
            if tool_name == "add_checkpoint":
                missing_session["checkpoint"] = dict(arguments["checkpoint"])
                del missing_session["checkpoint"]["source_session_id"]
                field = "source_session_id"
            else:
                field = "holder_session_id" if tool_name == "claim_work" else "actor_session_id"
                del missing_session[field]
            rejected = await session.call_tool(tool_name, missing_session)
            assert rejected.isError is True
            assert isinstance(rejected.content[0], TextContent)
            assert f"{field} (missing)" in rejected.content[0].text

    async with asyncio.timeout(30), app.router.lifespan_context(app):
        await asyncio.gather(*(
            run_client(index, client_info_name)
            for index, (client_info_name, _) in enumerate(CLIENTS)
        ))
    assert sorted(seen) == sorted(expected)
