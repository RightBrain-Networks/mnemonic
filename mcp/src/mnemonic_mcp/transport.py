"""Bounded pre-SDK JSON-RPC ingress and identity-only response helpers."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from io import TextIOWrapper
from typing import Any, NoReturn

import anyio
import anyio.lowlevel
import mcp.types as mcp_types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MCP_REQUEST_MAX_BYTES = 1_048_576
MCP_ARTIFACT_REQUEST_MAX_BYTES = 90 * 1024 * 1024
MCP_ARTIFACT_CONTENT_MAX_CHARS = 4 * ((64 * 1024 * 1024 + 2) // 3)
MCP_HTTP_REQUEST_SLOTS = 2
MCP_HTTP_REQUEST_TIMEOUT_SECONDS = 180
COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES = 3_145_728
# Full context includes up to300 reference-bearing counterparts,22 full
# checkpoints and20 events. The SDK emits both JSON text and structuredContent;
# measured maximal fixtures exceed48MiB. See external-records performance evidence.
# Artifact downloads contain up to64MiB base64 twice: text and structuredContent.
MCP_RESULT_MAX_BYTES = 192 * 1024 * 1024
MCP_STREAM_CHUNK_BYTES = 65_536
MCP_SIZE_LIMIT_MESSAGE = (
    "MCP request exceeds its transport limits: ordinary requests and artifact metadata "
    "allow 1048576 bytes; artifact upload/replace content allows 67108864 bytes "
    "(64 MiB) encoded as literal base64 within a 94371840-byte request. "
    "The configured artifact library limit may be lower or the library may be disabled; "
    "call list_artifacts without content to learn its current status. "
    "Larger files require the binary REST API when the configured library limit permits."
)

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_BASE64_LITERAL_PATTERN = re.compile(rb'"content_base64"\s*:\s*"([A-Za-z0-9+/=]*)"')


class MCPTransportViolation(ValueError):
    """A pre-SDK frame violation whose caller-controlled content must not escape."""


class MCPRequestTooLarge(MCPTransportViolation):
    """The parsed method does not qualify for its request envelope size."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MCPTransportViolation("duplicate JSON key")
        result[key] = value
    return result


def _invalid_json_constant(value: str) -> NoReturn:
    raise MCPTransportViolation(f"invalid JSON constant: {value}")


def validated_jsonrpc_document(raw: bytes) -> dict[str, Any]:
    """Decode one bounded JSON-RPC object and validate its reflection-sensitive ID."""
    if len(raw) > MCP_ARTIFACT_REQUEST_MAX_BYTES:
        raise MCPRequestTooLarge("oversized MCP request")
    _prevalidate_large_upload(raw)
    try:
        text = raw.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_invalid_json_constant,
        )
    except MCPTransportViolation:
        raise
    except (RecursionError, UnicodeDecodeError, ValueError) as error:
        raise MCPTransportViolation("invalid UTF-8 or JSON") from error
    if not isinstance(document, dict):
        raise MCPTransportViolation("JSON-RPC top level must be one object")
    if "id" in document and not valid_jsonrpc_request_id(document["id"]):
        raise MCPTransportViolation("invalid JSON-RPC request ID")
    _validate_method_envelope(raw, document)
    return document


def _prevalidate_large_upload(raw: bytes) -> None:
    """Exclude JSON object/array memory amplification before decoding large bodies."""
    if len(raw) <= MCP_REQUEST_MAX_BYTES:
        return
    minimum_content = len(raw) - MCP_REQUEST_MAX_BYTES
    for match in _BASE64_LITERAL_PATTERN.finditer(raw):
        size = match.end(1) - match.start(1)
        if minimum_content <= size <= MCP_ARTIFACT_CONTENT_MAX_CHARS:
            return
    raise MCPRequestTooLarge("large MCP bodies require bounded literal base64 content")


def _artifact_transfer(document: dict[str, Any]) -> bool:
    params = document.get("params")
    if document.get("method") != "tools/call" or not isinstance(params, dict):
        return False
    name = params.get("name")
    return isinstance(name, str) and name in {
        "upload_artifact", "replace_artifact", "download_artifact",
    }


def _validate_method_envelope(raw: bytes, document: dict[str, Any]) -> None:
    if len(raw) <= MCP_REQUEST_MAX_BYTES:
        return
    params = document.get("params")
    if document.get("method") != "tools/call" or not isinstance(params, dict):
        raise MCPRequestTooLarge("oversized ordinary MCP request")
    name = params.get("name")
    if not isinstance(name, str) or name not in {"upload_artifact", "replace_artifact"}:
        raise MCPRequestTooLarge("oversized ordinary MCP request")
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        raise MCPRequestTooLarge("invalid artifact arguments")
    content = arguments.get("content_base64")
    if not isinstance(content, str) or len(content) > MCP_ARTIFACT_CONTENT_MAX_CHARS:
        raise MCPRequestTooLarge("oversized artifact content")
    # Count the original bytes, including padding and escaped encodings, so
    # only actual base64 content receives the additional transfer allowance.
    if len(raw) - len(content) > MCP_REQUEST_MAX_BYTES:
        raise MCPRequestTooLarge("oversized artifact metadata")


def _correlated_size_rejection(raw: bytes) -> dict[str, Any] | None:
    """Validate a bounded JSON skeleton before correlating a rejected tool call.

    A literal base64 string cannot contain JSON structure. Removing only its
    contents lets us validate the complete frame without decoding a large string
    or an attacker-controlled large object graph. Frames above the transport cap,
    or with more than the ordinary allowance outside that string, remain static
    transport rejections because their request identity cannot be safely parsed.
    """
    if len(raw) > MCP_ARTIFACT_REQUEST_MAX_BYTES:
        return None
    for match in _BASE64_LITERAL_PATTERN.finditer(raw):
        if len(raw) - (match.end(1) - match.start(1)) > MCP_REQUEST_MAX_BYTES:
            continue
        skeleton = raw[:match.start(1)] + raw[match.end(1):]
        try:
            document = validated_jsonrpc_document(skeleton)
            request = mcp_types.JSONRPCRequest.model_validate(document)
            mcp_types.CallToolRequest.model_validate(document)
        except (MCPTransportViolation, ValueError):
            return None
        return {
            "jsonrpc": "2.0", "id": request.id,
            "result": {
                "content": [{"type": "text", "text": MCP_SIZE_LIMIT_MESSAGE}],
                "isError": True,
            },
        }
    return None


def valid_jsonrpc_request_id(value: object) -> bool:
    """Accept the bounded Phase 11 request-ID domain without coercion."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return -(2**63) <= value <= 2**63 - 1
    return isinstance(value, str) and _REQUEST_ID_PATTERN.fullmatch(value) is not None


def identity_content_encoding_values(values: Sequence[str]) -> bool:
    """Validate the exact identity-coding contract for any header container."""
    return not values or (len(values) == 1 and values[0].strip().lower() == "identity")


def identity_content_encoding(headers: Headers) -> bool:
    """Accept only absent coding or one case-insensitive identity token."""
    return identity_content_encoding_values(headers.getlist("content-encoding"))


def declared_oversize_values(values: Sequence[str], maximum_bytes: int) -> bool:
    """Use one syntactically valid nonnegative length only for early rejection."""
    if len(values) != 1 or re.fullmatch(r"[0-9]+", values[0], re.ASCII) is None:
        return False
    try:
        declared = int(values[0])
    except ValueError:
        return False
    return declared >= 0 and declared > maximum_bytes


def declared_oversize(headers: Headers, maximum_bytes: int) -> bool:
    """Use a single valid length only for early rejection, never acceptance."""
    return declared_oversize_values(headers.getlist("content-length"), maximum_bytes)


async def _bounded_http_entity(receive: Receive) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise anyio.EndOfStream
        chunk = message.get("body", b"")
        if len(chunk) > MCP_ARTIFACT_REQUEST_MAX_BYTES - len(body):
            raise MCPTransportViolation("oversized MCP request")
        body.extend(chunk)
        if not message.get("more_body", False):
            return bytes(body)


async def _static_rejection(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
) -> None:
    await JSONResponse(
        {"detail": MCP_SIZE_LIMIT_MESSAGE if status_code == 413 else "Invalid MCP request."},
        status_code=status_code,
    )(scope, receive, send)


def _ingress_header_rejection(headers: Headers) -> int | None:
    if not identity_content_encoding(headers):
        return 415
    if declared_oversize(headers, MCP_ARTIFACT_REQUEST_MAX_BYTES):
        return 413
    return None


class BoundedMCPIngressMiddleware:
    """Validate Streamable HTTP entities before FastMCP parses or dispatches them."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._slots = anyio.Semaphore(MCP_HTTP_REQUEST_SLOTS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] != "/mcp"
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return

        rejection = _ingress_header_rejection(Headers(scope=scope))
        if rejection is not None:
            await _static_rejection(scope, receive, send, status_code=rejection)
            return
        try:
            self._slots.acquire_nowait()
        except anyio.WouldBlock:
            await _static_rejection(scope, receive, send, status_code=429)
            return
        response_started = False
        slot_held = True

        def release_slot() -> None:
            nonlocal slot_held
            if slot_held:
                self._slots.release()
                slot_held = False

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            with anyio.fail_after(MCP_HTTP_REQUEST_TIMEOUT_SECONDS):
                await self._validated_request(scope, receive, tracked_send, release_slot)
        except TimeoutError:
            if not response_started:
                await _static_rejection(scope, receive, send, status_code=408)
        finally:
            release_slot()

    async def _validated_request(
        self, scope: Scope, receive: Receive, send: Send, release_slot: Callable[[], None],
    ) -> None:
        try:
            body = await _bounded_http_entity(receive)
        except anyio.EndOfStream:
            return
        except MCPTransportViolation:
            await _static_rejection(scope, receive, send, status_code=413)
            return
        try:
            document = validated_jsonrpc_document(body)
        except MCPRequestTooLarge:
            rejection = _correlated_size_rejection(body)
            if rejection is not None:
                await JSONResponse(rejection)(scope, receive, send)
            else:
                await _static_rejection(scope, receive, send, status_code=413)
            return
        except MCPTransportViolation:
            status = 413 if len(body) > MCP_REQUEST_MAX_BYTES else 400
            await _static_rejection(scope, receive, send, status_code=status)
            return

        retain_slot = _artifact_transfer(document)
        del document
        if not retain_slot:
            release_slot()

        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered, body
            if not delivered:
                delivered = True
                content, body = body, b""
                return {"type": "http.request", "body": content, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


async def _send_stdio_record(
    record: bytes,
    target: MemoryObjectSendStream[SessionMessage | Exception],
    reject_oversize: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> bool:
    """Send one accepted object to the SDK, or stop on a transport violation."""
    try:
        document = validated_jsonrpc_document(record)
    except MCPRequestTooLarge:
        if reject_oversize is None:
            raise
        rejection = _correlated_size_rejection(record)
        await reject_oversize(rejection)
        return rejection is not None
    except MCPTransportViolation:
        return False
    try:
        message = mcp_types.JSONRPCMessage.model_validate(document)
    # Preserve the installed SDK transport's semantic-validation handoff while
    # ensuring its stream-exception logger cannot render caller-controlled data.
    except Exception as error:  # noqa: BLE001
        await target.send(error)
    else:
        await target.send(SessionMessage(message))
    return True


async def _stdin_records(stdin: anyio.AsyncFile[bytes]) -> AsyncIterator[bytes]:
    buffer = bytearray()
    # BufferedReader.read(n) can wait for all n bytes on an interactive
    # pipe. read1(n) returns the currently available bounded chunk.
    while chunk := await stdin.read1(MCP_STREAM_CHUNK_BYTES):
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            piece = chunk[start:end]
            if len(piece) > MCP_ARTIFACT_REQUEST_MAX_BYTES - len(buffer):
                raise MCPRequestTooLarge("oversized MCP request")
            buffer.extend(piece)
            if newline < 0:
                break
            yield bytes(buffer)
            buffer.clear()
            start = newline + 1
    if buffer:
        yield bytes(buffer)


async def _bounded_stdin_reader(
    stdin: anyio.AsyncFile[bytes],
    target: MemoryObjectSendStream[SessionMessage | Exception],
    reject_oversize: Callable[[dict[str, Any] | None], Awaitable[None]] | None = None,
) -> None:
    async with target:
        try:
            async for record in _stdin_records(stdin):
                if not await _send_stdio_record(record, target, reject_oversize):
                    return
        except MCPRequestTooLarge:
            # Flush before closing the SDK's input stream, which ends its session.
            if reject_oversize is not None:
                await reject_oversize(None)
        except anyio.ClosedResourceError:  # pragma: no cover - SDK closed normally
            await anyio.lowlevel.checkpoint()


async def _write_stdout_record(
    stdout: anyio.AsyncFile[str], record: str, write_lock: anyio.Lock,
) -> None:
    async with write_lock:
        await stdout.write(record)
        await stdout.flush()


async def _bounded_stdout_writer(
    stdout: anyio.AsyncFile[str],
    source: MemoryObjectReceiveStream[SessionMessage],
    write_lock: anyio.Lock | None = None,
) -> None:
    write_lock = write_lock or anyio.Lock()
    try:
        async with source:
            async for session_message in source:
                rendered = session_message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                record = rendered + "\n"
                if len(record.encode("utf-8")) > MCP_RESULT_MAX_BYTES:
                    raise RuntimeError("MCP result exceeds the bounded transport envelope.")
                await _write_stdout_record(stdout, record, write_lock)
    except anyio.ClosedResourceError:  # pragma: no cover - SDK closed normally
        await anyio.lowlevel.checkpoint()


@asynccontextmanager
async def bounded_stdio_server(
    stdin: anyio.AsyncFile[bytes] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
):
    """Provide FastMCP streams without its unbounded decoded-line iterator."""
    if stdin is None:
        stdin = anyio.wrap_file(sys.stdin.buffer)
    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_sender: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_receiver: MemoryObjectReceiveStream[SessionMessage]
    read_sender, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_receiver = anyio.create_memory_object_stream(0)
    write_lock = anyio.Lock()

    async def reject_oversize(rejection: dict[str, Any] | None) -> None:
        # Complete, bounded tool calls receive a correlated tool error. For an
        # unparseable frame only a static terminal transport error is possible;
        # SDK clients may expose connection closure instead of its explanation.
        record = json.dumps(rejection if rejection is not None else {
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32600, "message": MCP_SIZE_LIMIT_MESSAGE},
        }) + "\n"
        await _write_stdout_record(stdout, record, write_lock)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_bounded_stdin_reader, stdin, read_sender, reject_oversize)
        task_group.start_soon(_bounded_stdout_writer, stdout, write_receiver, write_lock)
        yield read_stream, write_stream
