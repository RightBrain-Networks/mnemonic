"""Artifact-sized MCP transfers retain strict ordinary-call and resource bounds."""

import io
import json

import anyio
import pytest
from mcp.shared.message import SessionMessage
from starlette.responses import JSONResponse

from mnemonic_mcp import transport
from mnemonic_mcp.security import LocalAccessMiddleware


def scope(headers=()):
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/mcp", "raw_path": b"/mcp",
        "query_string": b"", "headers": list(headers), "client": ("127.0.0.1", 1234),
        "server": ("localhost", 8001), "root_path": "",
    }


def artifact_call(name="upload_artifact", size=2 * 1024 * 1024, **extra):
    return json.dumps({
        "jsonrpc": "2.0", "id": "bounded-artifact", "method": "tools/call",
        "params": {"name": name, "arguments": {"content_base64": "A" * size, **extra}},
    }).encode()


async def run_guard(raw, *, middleware=None, headers=()):
    calls, sent = [], []
    parts = [raw[index:index + 65536] for index in range(0, len(raw), 65536)]

    async def receive():
        return {"type": "http.request", "body": parts.pop(0), "more_body": len(parts) > 0}

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        calls.append((await receive())["body"])
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = middleware or transport.BoundedMCPIngressMiddleware(downstream)
    await middleware(scope(headers), receive, send)
    return calls, sent


@pytest.mark.parametrize("name", ["upload_artifact", "replace_artifact"])
async def test_http_artifact_uploads_admit_base64_larger_than_ordinary_envelope(name):
    raw = artifact_call(name)
    calls, sent = await run_guard(raw)
    assert calls == [raw]
    assert sent[0]["status"] == 200


@pytest.mark.parametrize("name", ["list_artifacts", "download_artifact", "create_work"])
async def test_large_content_does_not_expand_other_tools(name):
    calls, sent = await run_guard(artifact_call(name))
    assert calls == []
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("name", [None, [], {}])
async def test_invalid_large_call_name_is_rejected_without_server_error(name):
    calls, sent = await run_guard(artifact_call(name))
    assert calls == []
    assert sent[0]["status"] == 413


async def test_upload_metadata_does_not_receive_binary_allowance():
    calls, sent = await run_guard(artifact_call(description="x" * transport.MCP_REQUEST_MAX_BYTES))
    assert calls == []
    assert sent[0]["status"] == 413


async def test_upload_whitespace_cannot_consume_binary_allowance():
    raw = artifact_call(size=0) + b" " * transport.MCP_REQUEST_MAX_BYTES
    calls, sent = await run_guard(raw)
    assert calls == []
    assert sent[0]["status"] == 413


async def test_base64_character_limit_is_checked_before_sdk(monkeypatch):
    monkeypatch.setattr(transport, "MCP_ARTIFACT_CONTENT_MAX_CHARS", 1024 * 1024)
    calls, sent = await run_guard(artifact_call())
    assert calls == []
    assert sent[0]["status"] == 413


async def test_actual_received_bytes_enforce_global_cap(monkeypatch):
    monkeypatch.setattr(transport, "MCP_ARTIFACT_REQUEST_MAX_BYTES", 1024 * 1024)
    calls, sent = await run_guard(artifact_call(), headers=[(b"content-length", b"1")])
    assert calls == []
    assert sent[0]["status"] == 413


async def test_stdin_accepts_artifact_record_and_preserves_following_message():
    sender, receiver = anyio.create_memory_object_stream(3)
    ping = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    stream = anyio.wrap_file(io.BytesIO(artifact_call() + b"\n" + ping))
    await transport._bounded_stdin_reader(stream, sender)
    async with receiver:
        values = [value async for value in receiver]
    assert len(values) == 2
    assert all(isinstance(value, SessionMessage) for value in values)


async def test_artifact_slots_cover_dispatch_and_reject_extra_without_reading():
    active, entered, release = 0, anyio.Event(), anyio.Event()
    sent = []

    async def downstream(scope, receive, send):
        nonlocal active
        await receive()
        active += 1
        if active == transport.MCP_HTTP_REQUEST_SLOTS:
            entered.set()
        await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    async def forbidden_receive():
        raise AssertionError("capacity rejection must not buffer more content")

    async def send(message):
        sent.append(message)

    async def accepted():
        await run_guard(artifact_call("download_artifact", size=0), middleware=middleware)

    middleware = transport.BoundedMCPIngressMiddleware(downstream)
    with anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            for _ in range(transport.MCP_HTTP_REQUEST_SLOTS):
                group.start_soon(accepted)
            await entered.wait()
            await middleware(scope(), forbidden_receive, send)
            release.set()
    assert sent[0]["status"] == 429


@pytest.mark.parametrize("stall_at", ["receive", "dispatch"])
async def test_total_deadline_cancels_slow_upload_and_releases_slot(monkeypatch, stall_at):
    monkeypatch.setattr(transport, "MCP_HTTP_REQUEST_TIMEOUT_SECONDS", 0.01)
    sent = []

    async def receive():
        if stall_at == "receive":
            await anyio.sleep_forever()
        return {"type": "http.request", "body": b'{"method":"ping"}', "more_body": False}

    async def downstream(scope, receive, send):
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    middleware = transport.BoundedMCPIngressMiddleware(downstream)
    await middleware(scope(), receive, send)
    assert sent[0]["status"] == 408
    assert middleware._slots.value == transport.MCP_HTTP_REQUEST_SLOTS


async def test_authentication_rejects_before_artifact_buffering(settings):
    sent = []

    async def receive():
        raise AssertionError("unauthorized artifact bytes must not be buffered")

    async def downstream(scope, receive, send):
        raise AssertionError("unauthorized request must not dispatch")

    async def send(message):
        sent.append(message)

    middleware = LocalAccessMiddleware(transport.BoundedMCPIngressMiddleware(downstream), settings)
    await middleware(scope([(b"host", b"localhost:8001")]), receive, send)
    assert sent[0]["status"] == 401


def test_result_envelope_fits_both_sdk_copies_of_maximum_base64():
    assert transport.MCP_RESULT_MAX_BYTES > 2 * transport.MCP_ARTIFACT_CONTENT_MAX_CHARS + 65536


async def test_ordinary_dispatch_does_not_hold_binary_transfer_slots():
    observed = []

    async def downstream(scope, receive, send):
        observed.append(middleware._slots.value)
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = transport.BoundedMCPIngressMiddleware(downstream)
    await run_guard(b'{"jsonrpc":"2.0","id":1,"method":"ping"}', middleware=middleware)
    assert observed == [transport.MCP_HTTP_REQUEST_SLOTS]


def test_large_json_structure_cannot_reach_amplifying_decoder(monkeypatch):
    raw = b'{"method":"tools/call","params":{"name":"upload_artifact","arguments":{"x":['
    raw += b"{}," * (transport.MCP_REQUEST_MAX_BYTES // 3) + b'{}]}}}'

    def forbidden_decode(*args, **kwargs):
        raise AssertionError("large non-binary JSON cannot reach the decoder")

    monkeypatch.setattr(transport.json, "loads", forbidden_decode)
    with pytest.raises(transport.MCPRequestTooLarge):
        transport.validated_jsonrpc_document(raw)
