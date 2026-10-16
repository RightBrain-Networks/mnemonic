"""Bounded JSON and backpressured raw streams for the two file browser transports.

Only admitted, sanitized scopes enter this adapter. File chunks are never assembled
into a response buffer. A lost response after a complete mutation input leaves its
receipt publication running; interrupted uploads and reads are cancelled.
"""

import asyncio
import json
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import ContentStream, JSONResponse, Response, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0, no-transform",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "sandbox; default-src 'none'",
}
FILE_MAXIMUM = 1024 * 1024 * 1024


class ClosingStreamingResponse(StreamingResponse):
    """Release native handles even when response admission or sending is cancelled."""

    def __init__(self, content: ContentStream, **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._source = content

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        background, self.background = self.background, None
        try:
            await super().__call__(scope, receive, send)
        finally:
            # StreamingResponse otherwise skips background tasks when send fails,
            # and iterate_in_threadpool does not close its underlying iterator.
            with anyio.CancelScope(shield=True):
                try:
                    await self._close_source()
                finally:
                    if background is not None:
                        await background()

    async def _close_source(self) -> None:
        async_close = getattr(self._source, "aclose", None)
        close = getattr(self._source, "close", None)
        if async_close is not None:
            await async_close()
        elif close is not None:
            await run_in_threadpool(close)


def file_failure(status: int, detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=status, headers=SECURITY_HEADERS)


class FileTransportError(Exception):
    """A closed, nonsensitive response error safe for the browser."""


def combined_headers(headers: Headers) -> Headers:
    return Headers({name: ("; " if name == "cookie" else ", ").join(headers.getlist(name))
                    for name in headers})


def _reject_constant(_: str) -> None:
    raise ValueError("Non-JSON constant")


def decode_file_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8-sig"), parse_constant=_reject_constant)


def declared_length(headers: Headers, maximum: int) -> int | None:
    value = headers.get("content-length")
    if value is None:
        return None
    digits = value.lstrip("0") or "0"
    if (not re.fullmatch(r"[0-9]+", value) or len(digits) > len(str(maximum))
            or int(digits) > maximum):
        raise ValueError("Invalid bounded content length")
    return int(digits)


async def read_file_json(request: Request, maximum: int) -> bytes:
    declared_length(combined_headers(request.headers), maximum)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise ValueError("Oversized JSON request")
        body.extend(chunk)
    return bytes(body)


@dataclass
class FileResponsePolicy:
    json_maximum: int
    failure_detail: str
    redirect_detail: str
    encoding_detail: str
    stream: Callable[[int, Headers], dict[str, str] | None] | None = None
    transform_json: Callable[[Any], Any] | None = None
    response_headers: Callable[[Headers], dict[str, str]] | None = None
    stream_maximum: int = FILE_MAXIMUM
    require_json_content_type: bool = True


@dataclass
class _ResponseGate:
    policy: FileResponsePolicy
    send: Send
    status: int = 502
    headers: Headers = field(default_factory=Headers)
    body: bytearray = field(default_factory=bytearray)
    stream_headers: dict[str, str] | None = None
    length: int | None = None
    received: int = 0
    started: bool = False
    complete: bool = False
    detached: bool = False

    def _start(self, message: Message) -> None:
        self.status = message["status"]
        self.headers = combined_headers(Headers(raw=message.get("headers", [])))
        if 300 <= self.status < 400:
            raise FileTransportError(self.policy.redirect_detail)
        encoding = self.headers.get("content-encoding")
        if encoding and encoding.lower() != "identity":
            raise FileTransportError(self.policy.encoding_detail)
        if self.policy.stream is not None:
            self.stream_headers = self.policy.stream(self.status, self.headers)
        if self.stream_headers is None:
            declared_length(self.headers, self.policy.json_maximum)
            if (self.policy.require_json_content_type
                    and "application/json" not in self.headers.get("content-type", "")):
                raise FileTransportError("Mnemonic's API returned an unexpected response.")
        else:
            self.length = declared_length(self.headers, self.policy.stream_maximum)

    async def capture(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self._start(message)
        elif message["type"] == "http.response.body":
            await self._body(message)

    async def _body(self, message: Message) -> None:
        chunk = message.get("body", b"")
        self.received += len(chunk)
        final = not message.get("more_body", False)
        if self.stream_headers is None:
            if self.received > self.policy.json_maximum:
                raise ValueError("Oversized JSON response")
            self.body.extend(chunk)
            self.complete = final
            return
        maximum = self.length if self.length is not None else self.policy.stream_maximum
        if self.received > maximum or final and self.length not in (None, self.received):
            raise ValueError("Incomplete or oversized file response")
        if self.detached:
            return
        if not self.started:
            self.started = True
            headers = Headers({**SECURITY_HEADERS, **self.stream_headers})
            await self.send({"type": "http.response.start", "status": 200,
                             "headers": headers.raw})
        await self.send(message)
        self.complete = final

    def response(self) -> Response | None:
        if not self.complete:
            raise ValueError("Incomplete response")
        if self.stream_headers is not None:
            return None
        value = decode_file_json(bytes(self.body))
        headers = dict(SECURITY_HEADERS)
        if self.policy.response_headers is not None:
            headers.update(self.policy.response_headers(self.headers))
        if self.policy.transform_json is not None:
            value = self.policy.transform_json(value)
            return JSONResponse(value, status_code=self.status, headers=headers)
        return Response(bytes(self.body), status_code=self.status,
                        media_type="application/json", headers=headers)


class _Input:
    def __init__(self, receive: Receive, body: bytes | None, maximum: int | None) -> None:
        self.receive = receive
        self.body = body
        self.maximum = maximum
        self.complete = body is not None
        self.replayed = False
        self.disconnected = asyncio.Event()
        self.queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)
        self.received = 0

    async def pump(self) -> None:
        while True:
            message = await self.receive()
            if message["type"] == "http.disconnect":
                self.disconnected.set()
                return
            if self.body is None and not self.complete:
                await self.queue.put(message)

    async def read(self) -> Message:
        if self.body is not None and not self.replayed:
            self.replayed = True
            return {"type": "http.request", "body": self.body, "more_body": False}
        if self.complete:
            await self.disconnected.wait()
            return {"type": "http.disconnect"}
        message = await self.queue.get()
        self.received += len(message.get("body", b""))
        if self.maximum is not None and self.received > self.maximum:
            raise ValueError("Oversized file upload")
        self.complete = not message.get("more_body", False)
        return message


def _track(task: asyncio.Task[None], pending: set[asyncio.Task[None]]) -> None:
    pending.add(task)

    def finished(done: asyncio.Task[None]) -> None:
        pending.discard(done)
        with suppress(asyncio.CancelledError):
            done.exception()

    task.add_done_callback(finished)


async def _run_file(
    app: ASGIApp, scope: Scope, source: _Input, gate: _ResponseGate,
    pending: set[asyncio.Task[None]], mutation: bool, timeout: float,
) -> None:
    execution = anyio.CancelScope()

    async def execute() -> None:
        with execution:
            await app(scope, source.read, gate.capture)

    task = asyncio.create_task(execute())
    _track(task, pending)
    watch = asyncio.create_task(source.pump())
    try:
        async with asyncio.timeout(timeout):
            done, _ = await asyncio.wait((task, watch), return_when=asyncio.FIRST_COMPLETED)
            if task not in done:
                await watch
                # Final bytes can make the client disconnect before the route's
                # background tasks and yield dependencies finish releasing handles.
                if not gate.complete:
                    raise ClientDisconnect()
            await asyncio.shield(task)
    finally:
        gate.detached = True
        source.disconnected.set()
        watch.cancel()
        if not task.done() and not gate.complete and (not mutation or not source.complete):
            # Preserve native worker shields before releasing sessions or handles.
            execution.cancel()
        with suppress(asyncio.CancelledError):
            await watch


async def execute_file_request(
    app: ASGIApp, scope: Scope, receive: Receive, send: Send, *, policy: FileResponsePolicy,
    body: bytes | None = b"", upload_maximum: int | None = None, timeout: float = 60,
    mutation: bool = False, pending_tasks: set[asyncio.Task[None]] | None = None,
) -> None:
    source = _Input(receive, body, upload_maximum)
    gate = _ResponseGate(policy, send)
    pending = pending_tasks if pending_tasks is not None else set()
    try:
        await _run_file(app, scope, source, gate, pending, mutation, timeout)
        response = gate.response()
    except ClientDisconnect:
        return
    except Exception as exc:
        if isinstance(exc, TimeoutError) and gate.complete and gate.stream_headers is not None:
            # The transfer finished inside its deadline. Drain slow resource and
            # dependency cleanup through the application-owned pending task set.
            return
        if gate.started:
            # Headers already left the process: terminate the stream instead of
            # pretending a short transfer or appending a JSON error is success.
            raise
        detail = str(exc) if isinstance(exc, FileTransportError) else policy.failure_detail
        response = file_failure(502, detail)
    if response is not None:
        await response(scope, receive, send)
