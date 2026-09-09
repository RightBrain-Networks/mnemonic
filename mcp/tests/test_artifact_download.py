"""Downloads require asserted caller context without gaining write semantics."""

import base64
import json

import httpx
import pytest
from conftest import NOW
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import CONTENT, artifact, artifact_summary, call, download_arguments

from mnemonic_mcp import artifact_transport
from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server


async def test_download_schema_requires_bounded_actor_context_and_remains_safe_read(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    tool = tools["download_artifact"]
    assert set(tool.inputSchema["required"]) == {
        "project_id", "artifact_id", "agent_session_id", "actor_client",
    }
    for field, maximum in (("agent_session_id", 200), ("actor_client", 80)):
        assert tool.inputSchema["properties"][field] == (
            tools["upload_artifact"].inputSchema["properties"][field]
        )
        assert tool.inputSchema["properties"][field]["minLength"] == 1
        assert tool.inputSchema["properties"][field]["maxLength"] == maximum
    assert "client_operation_id" not in tool.inputSchema["properties"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.openWorldHint is False


@pytest.mark.parametrize("omitted", [
    ("agent_session_id",), ("actor_client",), ("agent_session_id", "actor_client"),
])
async def test_download_missing_actor_fails_before_any_http_request(settings, omitted):
    requests = []

    def handler(request):
        requests.append(request)
        pytest.fail("Missing download actor must fail before status or artifact HTTP requests")

    arguments = download_arguments()
    for field in omitted:
        arguments.pop(field)
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as raised:
        await server.call_tool("download_artifact", arguments)
    assert "(missing)" in str(raised.value)
    if "actor_client" in omitted:
        assert "actor_client (missing)" in str(raised.value)
    assert requests == []


@pytest.mark.parametrize(("field", "maximum"), [("agent_session_id", 200), ("actor_client", 80)])
@pytest.mark.parametrize(("invalid", "kind"), [
    ("", "string_too_short"), ("oversize", "string_too_long"),
    (None, "string_type"), (123, "string_type"), (True, "string_type"),
    (["private actor"], "string_type"), ({"private actor": "value"}, "string_type"),
])
async def test_download_invalid_actor_fails_locally_without_value_echo(
    settings, field, maximum, invalid, kind,
):
    def handler(request):
        pytest.fail("Invalid download actor must fail before any HTTP request")

    if invalid == "oversize":
        invalid = "private actor" + "x" * maximum
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as raised:
        await server.call_tool("download_artifact", download_arguments(**{field: invalid}))
    assert f"({kind})" in str(raised.value)
    if field == "actor_client":
        assert f"actor_client ({kind})" in str(raised.value)
    assert "private actor" not in str(raised.value)


@pytest.mark.parametrize(("session", "client"), [
    ("s", "c"), ("session-雪-🗎-\"\\\r\n", "client-é-\"\\\r\n"), ("🗎" * 200, "雪" * 80),
], ids=["minimum", "escaped-unicode", "maximum-unicode"])
@pytest.mark.parametrize("anonymous_creator", [False, True])
async def test_download_sends_exact_ascii_actor_only_to_binary_get(
    settings, session, client, anonymous_creator,
):
    requests = []
    metadata = artifact(**({"created_by_agent_session_id": None, "created_by_client": None}
                           if anonymous_creator else {}))

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["accept-encoding"] == "identity"
        assert "x-client-operation-id" not in request.headers
        assert "x-artifact-expected-revision" not in request.headers
        if request.url.path.endswith("/content"):
            assert dict(request.url.params) == {"expected_revision": "1"}
            encoded = request.headers["x-artifact-metadata"]
            assert encoded.isascii()
            assert "\r" not in encoded and "\n" not in encoded
            assert json.loads(encoded) == {"agent_session_id": session, "actor_client": client}
            assert request.content == b""
            return httpx.Response(200, stream=httpx.ByteStream(CONTENT))
        assert "x-artifact-metadata" not in request.headers
        assert request.url.query == b""
        payload = metadata
        if request.url.path == "/api/v1/artifacts/status":
            payload = {"enabled": True, "max_bytes": 1, "message": "API"}
        return httpx.Response(200, headers={"content-type": "application/json"},
                              stream=httpx.ByteStream(json.dumps(payload).encode()))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = await server.call_tool("download_artifact", download_arguments(
        agent_session_id=session, actor_client=client,
    ))
    assert isinstance(result, tuple)
    assert base64.b64decode(result[1]["content_base64"]) == CONTENT
    assert result[1]["artifact"] == artifact_summary()
    assert result[1]["artifact_library"]["max_bytes"] == 1
    assert len(requests) == 3


@pytest.mark.parametrize(("changes", "error"), [
    ({"content_available": False}, "content is unavailable"),
    ({"deleted_at": NOW, "content_available": False}, "content is unavailable"),
    ({"size_bytes": 67108865}, "limited to 64 MiB"),
])
async def test_download_unavailable_or_oversize_metadata_prevents_binary_request(
    settings, changes, error,
):
    requests = []

    def handler(request):
        requests.append(request)
        assert not request.url.path.endswith("/content")
        return httpx.Response(200, json=artifact(**changes))

    with pytest.raises(ToolError, match=error) as raised:
        await call(settings, "download_artifact", download_arguments(), handler)
    assert len(requests) == 1
    assert "Last observed configuration" in str(raised.value)


@pytest.mark.parametrize("headers", [
    {"content-encoding": "identity, gzip"},
    [("content-encoding", "identity"), ("content-encoding", "gzip")],
    [("content-encoding", "identity"), ("content-encoding", "identity")],
    {"content-length": "67108865"},
])
async def test_download_preserves_strict_binary_response_headers(settings, headers):
    binary_requests = []

    def handler(request):
        if request.url.path.endswith("/content"):
            binary_requests.append(request)
            return httpx.Response(200, content=CONTENT, headers=headers)
        return httpx.Response(200, json=artifact())

    with pytest.raises(ToolError, match="safe read") as raised:
        await call(settings, "download_artifact", download_arguments(), handler)
    assert len(binary_requests) == 1
    assert "Last observed configuration" in str(raised.value)


@pytest.mark.parametrize("headers", [
    {"content-length": "invalid"}, [("content-length", "1"), ("content-length", "2")],
])
async def test_download_bounds_actual_bytes_with_unusable_content_length(
    settings, monkeypatch, headers,
):
    monkeypatch.setattr(artifact_transport, "MCP_ARTIFACT_MAX_BYTES", len(CONTENT))
    binary_requests = []

    def handler(request):
        if request.url.path.endswith("/content"):
            binary_requests.append(request)
            return httpx.Response(200, content=CONTENT + b"extra", headers=headers)
        return httpx.Response(200, json=artifact())

    with pytest.raises(ToolError, match="safe read"):
        await call(settings, "download_artifact", download_arguments(), handler)
    assert len(binary_requests) == 1


@pytest.mark.parametrize("failure", ["timeout", "redirect", "wrong-status", "wrong-size"])
async def test_download_failures_keep_safe_read_semantics_without_implicit_retry(settings, failure):
    binary_requests = []

    def handler(request):
        if not request.url.path.endswith("/content"):
            return httpx.Response(200, json=artifact())
        binary_requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream diagnostic", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.invalid/private"})
        return httpx.Response(201 if failure == "wrong-status" else 200,
                              content=CONTENT + (b"extra" if failure == "wrong-size" else b""))

    with pytest.raises(ToolError) as raised:
        await call(settings, "download_artifact", download_arguments(), handler)
    assert len(binary_requests) == 1
    message = str(raised.value)
    assert "Last observed configuration" in message
    for forbidden in ("private upstream", "untrusted.invalid", "mutation outcome", "operation UUID"):
        assert forbidden not in message


@pytest.mark.parametrize("field", ["agent_session_id", "actor_client"])
async def test_download_secret_echo_is_a_sanitized_definite_read_rejection(settings, field):
    requests = []
    private_marker = "private download actor diagnostic"

    def handler(request):
        requests.append(request)
        if not request.url.path.endswith("/content"):
            return httpx.Response(200, json=artifact())
        assert request.method == "GET"
        assert json.loads(request.headers["x-artifact-metadata"])[field] == settings.api_key
        assert "x-client-operation-id" not in request.headers
        return httpx.Response(422, json={"detail": {
            "code": "client_operation_secret_echo",
            "message": f"{private_marker}: {settings.api_key}",
            "context": {"private": private_marker, "credential": settings.api_key},
        }})

    with pytest.raises(ToolError) as raised:
        await call(settings, "download_artifact", download_arguments(**{field: settings.api_key}),
                   handler)
    message = str(raised.value)
    assert "rejected the safe read" in message
    assert "caller context" in message
    assert "Last observed configuration" in message
    for forbidden in (
        settings.api_key, private_marker, "mutation", "UUID", "client_operation_id",
        "new intent", "unknown outcome",
    ):
        assert forbidden not in message
    assert len(requests) == 2


async def test_download_omits_document_properties_and_description(settings):
    source = artifact(description="Private description", extraction={
        "status": "ready", "metadata": {"pdf:docinfo:subject": ["Private property"]},
        "truncated": True, "error_code": None, "extracted_at": NOW,
    })

    def handler(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=CONTENT)
        return httpx.Response(200, json=source)

    result = await call(settings, "download_artifact", download_arguments(), handler)
    assert base64.b64decode(result["content_base64"]) == CONTENT
    assert "description" not in result["artifact"]
    assert "metadata" not in result["artifact"]["extraction"]
    assert result["artifact"]["extraction"]["truncated"] is True
    assert result["artifact"]["sha256"] == source["sha256"]
