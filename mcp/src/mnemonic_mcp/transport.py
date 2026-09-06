"""Bounded pre-SDK JSON-RPC ingress and identity-only response helpers."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
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
COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES = 3_145_728
# Full context includes up to300 reference-bearing counterparts,22 full
# checkpoints and20 events. The SDK emits both JSON text and structuredContent;
# measured maximal fixtures exceed48MiB. See external-records performance evidence.
MCP_RESULT_MAX_BYTES = 67_108_864
MCP_STREAM_CHUNK_BYTES = 65_536

_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)


class MCPTransportViolation(ValueError):
    """A pre-SDK frame violation whose caller-controlled content must not escape."""


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
    return document


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
        if len(chunk) > MCP_REQUEST_MAX_BYTES - len(body):
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
        {"detail": "Invalid MCP request."},
        status_code=status_code,
    )(scope, receive, send)


class BoundedMCPIngressMiddleware:
    """Validate Streamable HTTP entities before FastMCP parses or dispatches them."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["path"] != "/mcp"
            or scope["method"] != "POST"
        ):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not identity_content_encoding(headers):
            await _static_rejection(scope, receive, send, status_code=415)
            return
        if declared_oversize(headers, MCP_REQUEST_MAX_BYTES):
            await _static_rejection(scope, receive, send, status_code=413)
            return

        try:
            body = await _bounded_http_entity(receive)
        except anyio.EndOfStream:
            return
        except MCPTransportViolation:
            await _static_rejection(scope, receive, send, status_code=413)
            return
        try:
            validated_jsonrpc_document(body)
        except MCPTransportViolation:
            await _static_rejection(scope, receive, send, status_code=400)
            return

        delivered = False

        async def buffered_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


async def _send_stdio_record(
    record: bytes,
    target: MemoryObjectSendStream[SessionMessage | Exception],
) -> bool:
    """Send one accepted object to the SDK, or stop on a transport violation."""
    try:
        document = validated_jsonrpc_document(record)
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


async def _bounded_stdin_reader(
    stdin: anyio.AsyncFile[bytes],
    target: MemoryObjectSendStream[SessionMessage | Exception],
) -> None:
    buffer = bytearray()
    try:
        async with target:
            # BufferedReader.read(n) can wait for all n bytes on an interactive
            # pipe. read1(n) returns the currently available bounded chunk.
            while chunk := await stdin.read1(MCP_STREAM_CHUNK_BYTES):
                start = 0
                while start < len(chunk):
                    newline = chunk.find(b"\n", start)
                    end = len(chunk) if newline < 0 else newline
                    piece = chunk[start:end]
                    if len(piece) > MCP_REQUEST_MAX_BYTES - len(buffer):
                        return
                    buffer.extend(piece)
                    if newline < 0:
                        break
                    if not await _send_stdio_record(bytes(buffer), target):
                        return
                    buffer.clear()
                    start = newline + 1
            if buffer:
                await _send_stdio_record(bytes(buffer), target)
    except anyio.ClosedResourceError:  # pragma: no cover - SDK closed normally
        await anyio.lowlevel.checkpoint()


async def _bounded_stdout_writer(
    stdout: anyio.AsyncFile[str],
    source: MemoryObjectReceiveStream[SessionMessage],
) -> None:
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
                await stdout.write(record)
                await stdout.flush()
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

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(_bounded_stdin_reader, stdin, read_sender)
        task_group.start_soon(_bounded_stdout_writer, stdout, write_receiver)
        yield read_stream, write_stream
