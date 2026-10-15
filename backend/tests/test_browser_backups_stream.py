"""ASGI backup upload/download backpressure, cleanup, cancellation and deadlines."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.datastructures import Headers

from mnemonic_api.application import browser_backups

from .test_browser_backups import (
    COLLECTION,
    DOWNLOAD,
    HEADERS,
    RESTORE,
    UPLOAD,
    RawStream,
    make_app,
    upstream,
)


class DirectTransport(httpx.AsyncBaseTransport):
    """Unlike MockTransport, let the handler consume upload chunks itself."""

    def __init__(self, handler):
        self.handler = handler

    async def handle_async_request(self, request):
        return await self.handler(request)


def scope(path=DOWNLOAD, method="GET", headers=None):
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "scheme": "http", "server": ("localhost", 3000), "client": ("127.0.0.1", 1234),
            "root_path": "", "path": path, "raw_path": path.encode(), "query_string": b"",
            "method": method, "headers": Headers({**HEADERS, **(headers or {})}).raw}


def test_origin_admission_precedes_receiving_any_body():
    async def scenario():
        sent = []

        async def receive():
            raise AssertionError("Untrusted request body must not be read")

        async def send(message):
            sent.append(message)

        app = make_app(lambda _: upstream())
        await app(scope(RESTORE, "POST", {"origin": "https://evil.test"}), receive, send)
        assert sent[0]["status"] == 403
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


def test_download_backpressure_and_disconnect_close_worker_stream():
    async def scenario():
        consumed = []
        first = asyncio.Event()
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        sent = []

        class Chunks(RawStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                for chunk in self.chunks:
                    consumed.append(chunk)
                    yield chunk

        stream = Chunks([b"ab", b"cd"])
        response = httpx.Response(200, headers={"content-length": "4"}, stream=stream)
        app = make_app(lambda _: response)

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body":
                first.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(app(scope(), incoming.get, send))
        await asyncio.wait_for(first.wait(), 1)
        assert consumed == [b"ab"]
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
        assert stream.closed
        assert len(sent) == 2
        assert sent[-1]["more_body"] is True
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("complete", [False, True])
def test_upload_or_lost_mutation_response_disconnect_cancels_without_retry(complete):
    async def scenario():
        first = asyncio.Event()
        cancelled = asyncio.Event()
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b"BZh", "more_body": not complete})
        attempts = []
        sent = []

        async def handler(request):
            attempts.append(request)
            try:
                async for chunk in request.stream:
                    assert chunk == b"BZh"
                    first.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        app = make_app(handler)
        app.state.backup_client = httpx.AsyncClient(transport=DirectTransport(handler))

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(app(scope(RESTORE, "POST", UPLOAD), incoming.get, send))
        await asyncio.wait_for(first.wait(), 1)
        incoming.put_nowait({"type": "http.disconnect"})
        await asyncio.wait_for(task, 1)
        assert cancelled.is_set()
        assert len(attempts) == 1
        assert not sent
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


def test_upload_backpressure_stops_pump_after_bounded_queue():
    async def scenario():
        awaiting_worker = asyncio.Event()
        reads = []
        cancelled = asyncio.Event()

        async def receive():
            reads.append(1)
            return {"type": "http.request", "body": b"x", "more_body": True}

        async def handler(request):
            awaiting_worker.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def send(message):
            raise AssertionError("No response before worker runs")

        app = make_app(handler)
        app.state.backup_client = httpx.AsyncClient(transport=DirectTransport(handler))
        task = asyncio.create_task(app(scope(RESTORE, "POST", UPLOAD), receive, send))
        await asyncio.wait_for(awaiting_worker.wait(), 1)
        await asyncio.sleep(0)
        assert len(reads) == 2  # One queued chunk and one blocked at queue.put.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


def test_total_deadline_cancels_single_worker_attempt(monkeypatch):
    monkeypatch.setattr(browser_backups, "BACKUP_TIMEOUT_SECONDS", 0.01)

    async def scenario():
        attempts = []
        cancelled = asyncio.Event()
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        sent = []

        async def handler(request):
            attempts.append(request)
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def send(message):
            sent.append(message)

        app = make_app(handler)
        await app(scope(COLLECTION, "POST"), incoming.get, send)
        assert cancelled.is_set() and len(attempts) == 1
        assert sent[0]["status"] == 502
        assert b"outcome may be uncertain" in sent[1]["body"]
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("chunks,length,started", [
    ([b"a", b"b"], "3", True), ([b"a", b"bcd"], "3", True),
    ([b"abcd"], "3", False), ([], "3", False),
])
def test_short_or_long_download_fails_without_fabricating_success(chunks, length, started):
    async def scenario():
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        sent = []
        stream = RawStream(chunks)
        response = httpx.Response(200, headers={"content-length": length}, stream=stream)
        app = make_app(lambda _: response)

        async def send(message):
            sent.append(message)

        if started:
            with pytest.raises(ValueError, match="backup download"):
                await app(scope(), incoming.get, send)
            assert [row["type"] for row in sent].count("http.response.start") == 1
            assert all(row.get("more_body") for row in sent if row["type"] == "http.response.body")
        else:
            await app(scope(), incoming.get, send)
            assert sent[0]["status"] == 502
        assert stream.closed
        await app.state.backup_client.aclose()

    asyncio.run(scenario())


def test_lost_worker_mutation_response_is_uncertain_and_not_retried():
    async def scenario():
        attempts = []
        incoming = asyncio.Queue()
        incoming.put_nowait({"type": "http.request", "body": b""})
        sent = []

        async def handler(request):
            attempts.append(request)
            raise httpx.ReadError("private worker detail")

        async def send(message):
            sent.append(message)

        app = make_app(handler)
        await app(scope(COLLECTION, "POST"), incoming.get, send)
        assert len(attempts) == 1
        assert sent[0]["status"] == 502
        assert b"outcome may be uncertain" in sent[1]["body"]
        assert b"private worker detail" not in sent[1]["body"]
        await app.state.backup_client.aclose()

    asyncio.run(scenario())
