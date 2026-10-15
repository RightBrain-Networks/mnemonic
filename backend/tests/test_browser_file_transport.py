"""ASGI streaming backpressure, limits, cancellation and receipt completion."""

import asyncio
import io

import pytest
from starlette.background import BackgroundTask
from starlette.datastructures import Headers

from mnemonic_api.application.browser_file_transport import (
    ClosingStreamingResponse,
    FileResponsePolicy,
    execute_file_request,
)


def policy(**overrides):
    return FileResponsePolicy(json_maximum=64, failure_detail="failed",
                              redirect_detail="redirect", encoding_detail="encoded", **overrides)


def content_headers(status, headers):
    assert status == 200
    return {"content-type": "application/octet-stream", **dict(headers)}


async def waiting_receive():
    await asyncio.Event().wait()


def test_streaming_send_backpressure_prevents_reading_next_chunk():
    async def scenario():
        sent = []
        first = asyncio.Event()
        resume = asyncio.Event()
        consumed = []

        async def send(message):
            sent.append(message)
            if message["type"] == "http.response.body" and message.get("more_body"):
                first.set()
                await resume.wait()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers({"content-length": "4"}).raw})
            consumed.append(1)
            await send({"type": "http.response.body", "body": b"ab", "more_body": True})
            consumed.append(2)
            await send({"type": "http.response.body", "body": b"cd"})

        task = asyncio.create_task(execute_file_request(
            app, {"type": "http"}, waiting_receive, send, policy=policy(stream=content_headers),
        ))
        await asyncio.wait_for(first.wait(), 1)
        assert consumed == [1]
        resume.set()
        await task
        assert b"".join(row.get("body", b"") for row in sent) == b"abcd"

    asyncio.run(scenario())


@pytest.mark.parametrize("headers,chunks,expected", [
    ({"content-length": "2"}, [b"abc"], "failed"),
    ({"content-length": "4"}, [b"abc"], "failed"),
    ({"content-length": "1073741825"}, [b""], "failed"),
    ({"content-encoding": "gzip"}, [b"x"], "encoded"),
    ({"content-length": "oops"}, [b"x"], "failed"),
    ({"content-length": "9" * 5000}, [b"x"], "failed"),
])
def test_bad_streams_before_first_bytes_become_closed_502(headers, chunks, expected):
    async def scenario():
        sent = []

        async def send(message):
            sent.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers(headers).raw})
            for chunk in chunks:
                await send({"type": "http.response.body", "body": chunk})

        await execute_file_request(app, {"type": "http"}, waiting_receive, send,
                                   policy=policy(stream=content_headers))
        assert sent[0]["status"] == 502
        assert expected.encode() in sent[1]["body"]

    asyncio.run(scenario())


def test_long_zero_padded_stream_length_preserves_original_header():
    async def scenario():
        sent = []
        length = "0" * 5000 + "1"

        async def send(message):
            sent.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers({"content-length": length}).raw})
            await send({"type": "http.response.body", "body": b"x"})

        await execute_file_request(app, {"type": "http"}, waiting_receive, send,
                                   policy=policy(stream=content_headers))
        assert sent[0]["status"] == 200
        assert Headers(raw=sent[0]["headers"])["content-length"] == length
        assert sent[1]["body"] == b"x"

    asyncio.run(scenario())


def test_short_stream_after_headers_aborts_without_second_response():
    async def scenario():
        sent = []

        async def send(message):
            sent.append(message)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers({"content-length": "4"}).raw})
            await send({"type": "http.response.body", "body": b"ab", "more_body": True})
            await send({"type": "http.response.body", "body": b""})

        with pytest.raises(ValueError, match="Incomplete"):
            await execute_file_request(app, {"type": "http"}, waiting_receive, send,
                                       policy=policy(stream=content_headers))
        assert len(sent) == 2
        assert sent[1]["body"] == b"ab"
        assert sent[1]["more_body"] is True

    asyncio.run(scenario())


@pytest.mark.parametrize("complete", [False, True])
def test_upload_disconnect_cancels_incomplete_input_but_preserves_receipt_completion(complete):
    async def scenario():
        pending = set()
        consumed = asyncio.Event()
        resume = asyncio.Event()
        cleaned = asyncio.Event()
        committed = []
        request_sent = False

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"data", "more_body": not complete}
            await consumed.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            pytest.fail("Disconnected request must not emit a response")

        async def app(scope, receive, send):
            try:
                assert (await receive())["body"] == b"data"
                consumed.set()
                await resume.wait()
                committed.append(True)
                await send({"type": "http.response.start", "status": 200,
                            "headers": Headers({"content-type": "application/json"}).raw})
                await send({"type": "http.response.body", "body": b"{}"})
            finally:
                cleaned.set()

        await execute_file_request(app, {"type": "http"}, receive, send, policy=policy(), body=None,
                                   mutation=True, upload_maximum=64, pending_tasks=pending)
        resume.set()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.sleep(0)
        assert cleaned.is_set()
        assert committed == ([True] if complete else [])
        assert not pending

    asyncio.run(scenario())


def test_read_timeout_cancels_and_cleans_up():
    async def scenario():
        pending = set()
        cleaned = asyncio.Event()
        sent = []

        async def app(scope, receive, send):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        async def send(message):
            sent.append(message)

        await execute_file_request(app, {"type": "http"}, waiting_receive, send, policy=policy(),
                                   timeout=0.01, pending_tasks=pending)
        await asyncio.gather(*pending, return_exceptions=True)
        assert cleaned.is_set()
        assert sent[0]["status"] == 502

    asyncio.run(scenario())


def test_final_byte_disconnect_waits_for_route_dependency_cleanup():
    async def scenario():
        final_sent = asyncio.Event()
        release = asyncio.Event()
        cleaned = []

        async def receive():
            await final_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                final_sent.set()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers({"content-length": "1"}).raw})
            await send({"type": "http.response.body", "body": b"x"})
            await release.wait()
            cleaned.append(True)

        task = asyncio.create_task(execute_file_request(
            app, {"type": "http"}, receive, send, policy=policy(stream=content_headers),
        ))
        await final_sent.wait()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        await task
        assert cleaned == [True]

    asyncio.run(scenario())


def test_completed_stream_cleanup_outlives_wire_deadline_without_cancellation():
    async def scenario():
        pending = set()
        final_sent = asyncio.Event()
        release = asyncio.Event()
        cleaned = []

        async def receive():
            await final_sent.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                final_sent.set()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": Headers({"content-length": "1"}).raw})
            await send({"type": "http.response.body", "body": b"x"})
            await release.wait()
            cleaned.append(True)

        await execute_file_request(
            app, {"type": "http"}, receive, send, policy=policy(stream=content_headers),
            timeout=0.01, pending_tasks=pending,
        )
        assert pending
        assert not cleaned
        release.set()
        await asyncio.gather(*pending)
        await asyncio.sleep(0)
        assert cleaned == [True]
        assert not pending

    asyncio.run(scenario())


@pytest.mark.parametrize("reject_start", [False, True])
def test_stream_response_closes_handle_on_header_rejection_or_cancel(reject_start):
    async def scenario():
        handle = io.BytesIO(b"native bytes")
        blocked = asyncio.Event()

        async def send(message):
            if reject_start:
                raise ValueError("Rejected response headers")
            if message["type"] == "http.response.body":
                blocked.set()
                await asyncio.Event().wait()

        def chunks():
            yield handle.read()

        response = ClosingStreamingResponse(chunks(), background=BackgroundTask(handle.close))
        task = asyncio.create_task(response(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, waiting_receive, send,
        ))
        if not reject_start:
            await blocked.wait()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert handle.closed

    asyncio.run(scenario())
