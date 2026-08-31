import os
from pathlib import Path
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from starlette.testclient import TestClient

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.config import Settings
from mnemonic_mcp.security import MAX_REQUEST_BYTES
from mnemonic_mcp.server import create_app

from conftest import API_KEY, HANDOFF_ID, PROJECT_ID

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


def test_http_protocol_initialize_list_and_call(settings, handoff):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/handoffs/{HANDOFF_ID}"
        return httpx.Response(200, json=handoff)

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with TestClient(app, base_url="http://localhost:8001") as client:
        initialized = client.post("/mcp", json=INITIALIZE, headers=JSON_HEADERS)
        assert initialized.status_code == 200
        assert initialized.json()["result"]["serverInfo"]["name"] == "Mnemonic"
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, headers=JSON_HEADERS)
        assert listed.status_code == 200
        assert len(listed.json()["result"]["tools"]) == 7
        called = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "recall_handoff", "arguments": {"project_id": PROJECT_ID, "handoff_id": HANDOFF_ID}},
        }, headers=JSON_HEADERS)
        assert called.status_code == 200
        result = called.json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["prompt"] == handoff["prompt"]
        assert result["structuredContent"]["source_session_id"] == handoff["source_session_id"]
    assert len(seen) == 1


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
        response = client.post("/mcp", content=b"x" * (MAX_REQUEST_BYTES + 1), headers=JSON_HEADERS)
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
    source_dir = str(Path(__file__).parents[1] / "src")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mnemonic_mcp", "--transport", "stdio"],
        env={
            **os.environ, "PYTHONPATH": source_dir, "MNEMONIC_API_KEY": API_KEY,
            "MNEMONIC_API_URL": "http://127.0.0.1:9",
        },
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Mnemonic"
            result = await session.list_tools()
            assert len(result.tools) == 7
            assert all(tool.outputSchema is not None for tool in result.tools)
