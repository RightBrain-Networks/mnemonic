import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from conftest import (
    API_KEY,
    CLIENT_OPERATION_ID,
    LEASE_TOKEN,
    LOCAL_VALIDATION_CASES,
    PROJECT_ID,
    WORK_ID,
    expected_validation_message,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.testclient import TestClient

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.config import Settings
from mnemonic_mcp.server import create_app
from mnemonic_mcp.transport import MCP_REQUEST_MAX_BYTES

JSON_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {API_KEY}",
}
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "mnemonic-test", "version": "1.0"},
    },
}

CANONICAL_TOOL_NAMES = {
    "get_activity",
    "get_project_settings",
    "list_job_completion_reports",
    "get_job_completion_report",
    "list_projects",
    "create_project",
    "create_work",
    "search_work",
    "list_ready_work",
    "get_work",
    "add_checkpoint",
    "list_checkpoints",
    "list_completion_evidence",
    "recall_work",
    "append_event",
    "list_work_events",
    "claim_work",
    "claim_and_recall",
    "renew_claim",
    "release_claim",
    "add_relationship",
    "get_relationship",
    "list_relationships",
    "remove_relationship",
    "update_work",
    "complete_work",
    "delete_work",
    "request_human_input",
    "list_human_attention",
    "list_work_gates",
    "merge_work",
    "suggest_duplicate_work",
}
PROTECTED_TOOL_NAMES = {
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "merge_work",
}
UNPROTECTED_MUTATION_TOOL_NAMES = {
    "create_project",
    "claim_work",
    "claim_and_recall",
    "renew_claim",
}
READ_ONLY_TOOL_NAMES = (
    CANONICAL_TOOL_NAMES - PROTECTED_TOOL_NAMES - UNPROTECTED_MUTATION_TOOL_NAMES
)
DESTRUCTIVE_TOOL_NAMES = {
    "update_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "merge_work",
}


def assert_serialized_tool_contract(tools: list[dict[str, object]]) -> None:
    """Assert the exact schema and annotation contract after transport serialization."""
    by_name = {tool["name"]: tool for tool in tools}
    assert len(PROTECTED_TOOL_NAMES) == 11
    assert set(by_name) == CANONICAL_TOOL_NAMES

    annotation_fields = {
        "readOnlyHint",
        "destructiveHint",
        "idempotentHint",
        "openWorldHint",
    }
    for name, tool in by_name.items():
        schema = tool["inputSchema"]
        assert isinstance(schema, dict)
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        annotations = tool["annotations"]
        assert isinstance(annotations, dict)
        assert set(annotations) == annotation_fields
        assert annotations == {
            "readOnlyHint": name in READ_ONLY_TOOL_NAMES,
            "destructiveHint": name in DESTRUCTIVE_TOOL_NAMES,
            "idempotentHint": name in READ_ONLY_TOOL_NAMES | PROTECTED_TOOL_NAMES,
            "openWorldHint": False,
        }
        if name in PROTECTED_TOOL_NAMES:
            assert "client_operation_id" in required
            assert properties["client_operation_id"]["format"] == "uuid"
        else:
            assert "client_operation_id" not in required
            assert "client_operation_id" not in properties


def test_http_protocol_initialize_list_and_call(settings, work_context):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"
        assert dict(request.url.params) == {"recent_limit": "5", "recent_event_limit": "10"}
        return httpx.Response(200, json=work_context)

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with TestClient(app, base_url="http://localhost:8001") as client:
        initialized = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "Mnemonic"
        assert initialized.json()["result"]["serverInfo"]["version"] == "0.10.0"
        instructions = initialized.json()["result"]["instructions"]
        # Clients truncate this block, so it must stay short and lead with the
        # trigger condition. Per-tool doctrine lives in the tool descriptions.
        assert len(instructions) <= 1200
        first_sentence = instructions.split(". ")[0]
        assert "outlives one session" in first_sentence
        assert "list_projects" in instructions
        assert "search_work" in instructions
        assert "recall_work" in instructions
        assert "list_ready_work" in instructions
        assert "append_event" in instructions
        # The claim trigger fires in a later turn than the recall_work description
        # that states it, so the always-present block must name it too.
        assert "claim_and_recall" in instructions
        assert "add_checkpoint" in instructions
        assert "merge_work" in instructions
        assert "Duplicate suggestions are advisory evidence" in instructions
        assert "historical evidence" in instructions
        assert "grants no authority" in instructions
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=JSON_HEADERS)
        assert listed.status_code == 200
        listed_tools = listed.json()["result"]["tools"]
        assert len(listed_tools) == 32
        assert_serialized_tool_contract(listed_tools)
        assert all(
            tool["inputSchema"].get("additionalProperties") is False
            for tool in listed_tools
        )
        assert {
            "claim_work",
            "claim_and_recall",
            "renew_claim",
            "release_claim",
            "add_relationship",
            "get_relationship",
            "list_relationships",
            "remove_relationship",
            "list_ready_work",
            "append_event",
            "list_work_events",
            "suggest_duplicate_work",
        } <= {tool["name"] for tool in listed_tools}
        called = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "recall_work",
                "arguments": {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
            },
        }, headers=JSON_HEADERS)
        assert called.status_code == 200
        result = called.json()["result"]
        assert result["isError"] is False
        # A single-checkpoint item carries its body once, on initial_checkpoint.
        assert result["structuredContent"]["current_context"] is None
        assert result["structuredContent"]["current_context_is_initial"] is True
        assert result["structuredContent"]["initial_checkpoint"]["prompt"] == (
            work_context["initial_checkpoint"]["prompt"]
        )
        assert result["structuredContent"]["initial_checkpoint"]["source_session_id"] == (
            work_context["initial_checkpoint"]["source_session_id"]
        )
    assert len(seen) == 1


def test_http_protected_call_makes_one_rest_attempt(settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"work_item_id": WORK_ID, "released": True})

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with TestClient(app, base_url="http://localhost:8001") as client:
        assert client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS).status_code == 200
        called = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "release_claim",
                    "arguments": {
                        "project_id": PROJECT_ID,
                        "work_item_id": WORK_ID,
                        "lease_token": LEASE_TOKEN,
                        "actor_client": "transport-test",
                        "actor_session_id": "raw-http-session",
                        "client_operation_id": CLIENT_OPERATION_ID,
                    },
                },
            },
            headers=JSON_HEADERS,
        )

    assert called.status_code == 200
    assert called.json()["result"]["structuredContent"] == {
        "work_item_id": WORK_ID,
        "released": True,
    }
    assert len(seen) == 1
    assert seen[0].method == "POST"
    assert seen[0].url.path == (
        f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/release-claim"
    )
    assert json.loads(seen[0].content) == {
        "client_operation_id": CLIENT_OPERATION_ID,
        "lease_token": LEASE_TOKEN,
        "actor": {
            "actor_client": "transport-test",
            "actor_session_id": "raw-http-session",
        },
    }


def test_http_tool_validation_is_strict_and_value_free(settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500)

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with TestClient(app, base_url="http://localhost:8001") as client:
        initialized = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert initialized.status_code == 200

        for request_id, (tool_name, arguments, fields, secrets, kinds) in enumerate(
            LOCAL_VALIDATION_CASES,
            start=10,
        ):
            response = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                headers=JSON_HEADERS,
            )
            assert response.status_code == 200
            payload = response.json()["result"]
            assert payload["isError"] is True
            assert payload["content"][0]["text"] == expected_validation_message(fields, kinds)
            for secret in secrets:
                assert secret not in response.text
            assert "input_value" not in response.text
            assert "input_type" not in response.text
            assert "errors.pydantic.dev" not in response.text

    assert seen == []


def test_malformed_http_envelope_is_value_free_in_response_and_logs(settings, caplog):
    secret = "malformed-envelope-secret-lease-token"
    app = create_app(settings)
    caplog.set_level("DEBUG")

    with TestClient(app, base_url="http://localhost:8001") as client:
        initialized = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert initialized.status_code == 200
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {
                    "name": "release_claim",
                    "arguments": secret,
                },
            },
            headers=JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["error"]["message"] == "Invalid request parameters"
    assert secret not in response.text
    assert secret not in caplog.text
    assert "input_value" not in caplog.text
    assert "Invalid MCP request details were suppressed." in caplog.text
    assert "MCP request parameters were invalid." in caplog.text


@pytest.mark.parametrize("headers,expected", [
    ({"Accept": "application/json, text/event-stream"}, 401),
    ({**JSON_HEADERS, "Authorization": "Bearer bad-key"}, 401),
    ({**JSON_HEADERS, "Host": "evil.example:8001"}, 421),
    ({**JSON_HEADERS, "Host": "localhost.evil.example:8001"}, 421),
    ({**JSON_HEADERS, "Origin": "https://evil.example"}, 403),
    ({**JSON_HEADERS, "Origin": "null"}, 403),
    ({**JSON_HEADERS, "Origin": "http://localhost:9999"}, 403),
])
def test_http_security_rejects_untrusted_requests(settings, headers, expected):
    with TestClient(create_app(settings), base_url="http://localhost:8001") as client:
        response = client.post("/mcp", json=INITIALIZE, headers=headers)
    assert response.status_code == expected
    assert API_KEY not in response.text


def test_health_is_public_without_exposing_api_configuration(settings):
    with TestClient(create_app(settings), base_url="http://localhost:8001") as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_same_origin_and_explicit_additional_origin_work(settings):
    configured = Settings(
        api_key=API_KEY,
        allowed_hosts=("localhost:8001", "mnemonic.internal:443"),
        allowed_origins=("http://localhost:8001", "https://mnemonic.internal"),
    )
    with TestClient(create_app(configured), base_url="http://localhost:8001") as client:
        response = client.post("/mcp", json=INITIALIZE, headers={**JSON_HEADERS, "Origin": "http://localhost:8001"})
        assert response.status_code == 200
        response = client.post("/mcp", json=INITIALIZE, headers={
            **JSON_HEADERS, "Host": "mnemonic.internal:443", "Origin": "https://mnemonic.internal",
        })
        assert response.status_code == 200


def test_body_size_is_bounded(settings):
    with TestClient(create_app(settings), base_url="http://localhost:8001") as client:
        response = client.post(
            "/mcp",
            content=b"x" * (MCP_REQUEST_MAX_BYTES + 1),
            headers=JSON_HEADERS,
        )
    assert response.status_code == 413


def test_settings_reject_wildcards_and_hide_keys():
    with pytest.raises(ValueError, match="wildcards"):
        Settings(api_key=API_KEY, allowed_hosts=("*",))
    assert API_KEY not in repr(Settings(api_key=API_KEY))
    with pytest.raises(ValueError, match="MNEMONIC_API_KEY"):
        Settings(api_key="too-short")
    with pytest.raises(ValueError, match="HTTP") as caught:
        Settings(api_key=API_KEY, api_url=f"http://user:{API_KEY}@api:8000")
    assert API_KEY not in str(caught.value)


async def test_stdio_transport_handshake_and_catalog():
    requests: list[dict[str, object]] = []

    class ReleaseHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            response = json.dumps(
                {"work_item_id": WORK_ID, "released": True}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            pass

    rest_server = ThreadingHTTPServer(("127.0.0.1", 0), ReleaseHandler)
    rest_thread = threading.Thread(target=rest_server.serve_forever, daemon=True)
    rest_thread.start()
    source_dir = str(Path(__file__).parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemonic_mcp", "--transport", "stdio"],
        env={
            **os.environ, "PYTHONPATH": source_dir, "MNEMONIC_API_KEY": API_KEY,
            "MNEMONIC_API_URL": f"http://127.0.0.1:{rest_server.server_port}",
        },
    )
    try:
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Mnemonic"
            assert initialized.serverInfo.version == "0.10.0"
            result = await session.list_tools()
            assert len(result.tools) == 32
            assert all(tool.outputSchema is not None for tool in result.tools)
            assert all(
                tool.inputSchema.get("additionalProperties") is False
                for tool in result.tools
            )
            assert_serialized_tool_contract(
                [
                    tool.model_dump(by_alias=True, exclude_none=True)
                    for tool in result.tools
                ]
            )

            for tool_name, arguments, fields, secrets, kinds in LOCAL_VALIDATION_CASES:
                invalid = await session.call_tool(tool_name, arguments)
                assert invalid.isError is True
                assert len(invalid.content) == 1
                text = invalid.content[0].text
                assert text == expected_validation_message(fields, kinds)
                rendered = repr(invalid)
                for secret in secrets:
                    assert secret not in rendered
                assert "input_value" not in rendered
                assert "input_type" not in rendered
                assert "errors.pydantic.dev" not in rendered

            released = await session.call_tool(
                "release_claim",
                {
                    "project_id": PROJECT_ID,
                    "work_item_id": WORK_ID,
                    "lease_token": LEASE_TOKEN,
                    "actor_client": "transport-test",
                    "actor_session_id": "stdio-session",
                    "client_operation_id": CLIENT_OPERATION_ID,
                },
            )
            assert released.isError is False
            assert released.structuredContent == {
                "work_item_id": WORK_ID,
                "released": True,
            }
    finally:
        rest_server.shutdown()
        rest_server.server_close()
        rest_thread.join()

    assert requests == [
        {
            "path": f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/release-claim",
            "authorization": f"Bearer {API_KEY}",
            "body": {
                "client_operation_id": CLIENT_OPERATION_ID,
                "lease_token": LEASE_TOKEN,
                "actor": {
                    "actor_client": "transport-test",
                    "actor_session_id": "stdio-session",
                },
            },
        }
    ]
