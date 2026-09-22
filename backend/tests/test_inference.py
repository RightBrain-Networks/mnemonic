"""Concurrent native calls, bounded waiting, fairness, and cancellation ownership."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from pydantic import ValidationError

from mnemonic_api.application.suggestion_resources import (
    DuplicateSuggestionControlMiddleware,
    DuplicateSuggestionResources,
)
from mnemonic_api.config import Settings
from mnemonic_api.inference import (
    INFERENCE_REQUEST_KEY,
    InferenceCapacityError,
    InferenceRequest,
    InferenceResources,
    QueuedEmbedder,
    inference_failure_reason,
    with_inference_deadline,
)

from .conftest import TEST_API_KEY


class Model:
    def embed_query(self, text):
        return [1.0, 0.0]

    def embed_documents(self, texts):
        return [self.embed_query(text) for text in texts]


def queued(resources, model=None, *, label="test", seconds=5):
    return QueuedEmbedder(model or Model(), resources,
                          InferenceRequest(monotonic() + seconds, label))


def observe_waiters(monkeypatch, labels):
    entered = {label: Event() for label in labels}
    original = InferenceResources._wait_for_turn

    def observe(self, ticket, request, deadline):
        entered[request.operation].set()
        return original(self, ticket, request, deadline)

    monkeypatch.setattr(InferenceResources, "_wait_for_turn", observe)
    return entered


def test_waiting_queries_precede_the_next_batch(monkeypatch):
    resources = InferenceResources(queue_size=3, wait_seconds=3)
    waiting = observe_waiters(monkeypatch, ["first", "second", "third"])
    entered, release = Event(), Event()
    calls = []

    class ControlledModel(Model):
        def embed_query(self, text):
            calls.append(text)
            if text == "first":
                entered.set()
                assert release.wait(5)
            return super().embed_query(text)

    model = ControlledModel()

    def batches():
        embedder = queued(resources, model, label="first")
        embedder.embed_query("first")
        embedder.embed_documents(["last"])

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(batches)
        try:
            assert entered.wait(5)
            second = pool.submit(queued(resources, model, label="second").embed_query, "second")
            assert waiting["second"].wait(5)
            third = pool.submit(queued(resources, model, label="third").embed_query, "third")
            assert waiting["third"].wait(5)
        finally:
            release.set()
        first.result(5)
        assert second.result(5) == third.result(5) == [1, 0]
    assert calls == ["first", "second", "third", "last"]


def test_full_queue_rejects_more_work_without_loading_the_model(monkeypatch):
    resources = InferenceResources(queue_size=1)
    waiting = observe_waiters(monkeypatch, ["waiting"])
    with ThreadPoolExecutor(max_workers=1) as pool:
        with resources.reserve(InferenceRequest(monotonic() + 5)):
            pending = pool.submit(queued(resources, label="waiting").embed_query, "waiting")
            assert waiting["waiting"].wait(5)
            with pytest.raises(InferenceCapacityError, match="full"):
                queued(resources).embed_query("overflow")
        assert pending.result(5) == [1, 0]


def test_configured_wait_times_out_without_calling_the_model_or_leaking_a_ticket():
    resources = InferenceResources(queue_size=1, wait_seconds=0.01)
    with resources.reserve(InferenceRequest(monotonic() + 5)):
        for _ in range(2):
            with pytest.raises(InferenceCapacityError, match="wait expired") as caught:
                queued(resources).embed_query("waiting")
            assert inference_failure_reason(caught.value) == "capacity_exhausted"
    assert queued(resources).embed_query("after") == [1, 0]


def test_request_deadline_caps_the_configured_queue_wait():
    resources = InferenceResources(wait_seconds=30)
    with resources.reserve(InferenceRequest(monotonic() + 5)):
        started = monotonic()
        with pytest.raises(TimeoutError) as caught:
            queued(resources, seconds=0.02).embed_query("expired")
        assert monotonic() - started < 1
        assert inference_failure_reason(caught.value) == "deadline_exceeded"
    assert queued(resources).embed_query("after") == [1, 0]


def test_external_deadline_caps_waiting_and_shares_request_cancellation():
    resources = InferenceResources(wait_seconds=30)
    original = queued(resources)
    bounded = with_inference_deadline(original, monotonic() + 0.02)
    with resources.reserve(InferenceRequest(monotonic() + 5)):
        with pytest.raises(TimeoutError):
            bounded.embed_query("external")
    assert original.embed_query("internal") == [1, 0]
    resources.cancel(original.request)
    with pytest.raises(TimeoutError):
        bounded.embed_query("cancelled")


def test_cancelling_a_queued_request_removes_it_and_wakes_the_next_waiter(monkeypatch):
    resources = InferenceResources(queue_size=2, wait_seconds=30)
    waiting = observe_waiters(monkeypatch, ["cancelled", "next"])
    cancelled = queued(resources, label="cancelled")
    with ThreadPoolExecutor(max_workers=2) as pool:
        with resources.reserve(InferenceRequest(monotonic() + 5)):
            first = pool.submit(cancelled.embed_query, "cancelled")
            assert waiting["cancelled"].wait(5)
            second = pool.submit(queued(resources, label="next").embed_query, "next")
            assert waiting["next"].wait(5)
            resources.cancel(cancelled.request)
            with pytest.raises(TimeoutError):
                first.result(1)
        assert second.result(5) == [1, 0]


def test_cancelled_native_call_keeps_capacity_until_it_really_returns():
    resources = InferenceResources(wait_seconds=0.01)
    entered, release = Event(), Event()

    class NativeModel(Model):
        def embed_query(self, text):
            entered.set()
            assert release.wait(5)
            return super().embed_query(text)

    embedder = queued(resources, NativeModel())
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(embedder.embed_query, "active")
        try:
            assert entered.wait(5)
            resources.cancel(embedder.request)
            with pytest.raises(InferenceCapacityError):
                queued(resources).embed_query("still busy")
        finally:
            release.set()
        with pytest.raises(TimeoutError):
            active.result(5)
    assert queued(resources).embed_query("recovered") == [1, 0]


def test_model_exception_releases_capacity():
    class BrokenModel(Model):
        def embed_query(self, text):
            raise ValueError("synthetic failure")

    resources = InferenceResources()
    with pytest.raises(ValueError):
        queued(resources, BrokenModel()).embed_query("broken")
    assert queued(resources).embed_query("recovered") == [1, 0]


@pytest.mark.parametrize("wait_ms, queue_size", [(1, 0), (5_000, 8), (30_000, 16)])
@pytest.mark.parametrize("slots, threads", [(1, 1), (2, 1), (4, 8)])
def test_operator_can_configure_bounded_wait_and_queue(wait_ms, queue_size, slots, threads):
    settings = Settings(database_url="postgresql://localhost/mnemonic",
                        api_key=TEST_API_KEY,
                        duplicate_suggestion_inference_wait_ms=wait_ms,
                        duplicate_suggestion_inference_queue_size=queue_size,
                        duplicate_suggestion_inference_slots=slots,
                        duplicate_suggestion_inference_threads=threads)
    assert settings.duplicate_suggestion_inference_wait_ms == wait_ms
    assert settings.duplicate_suggestion_inference_queue_size == queue_size
    assert settings.duplicate_suggestion_inference_slots == slots
    assert settings.duplicate_suggestion_inference_threads == threads


@pytest.mark.parametrize("field,value", [
    ("duplicate_suggestion_inference_slots", 0),
    ("duplicate_suggestion_inference_slots", 5),
    ("duplicate_suggestion_inference_threads", 0),
    ("duplicate_suggestion_inference_threads", 9),
    ("duplicate_suggestion_inference_wait_ms", 0),
    ("duplicate_suggestion_inference_wait_ms", 30_001),
    ("duplicate_suggestion_inference_queue_size", -1),
    ("duplicate_suggestion_inference_queue_size", 17),
])
def test_unbounded_operator_settings_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/mnemonic",
                 api_key=TEST_API_KEY, **{field: value})


@pytest.mark.parametrize("endpoint", ["work-items", "duplicate-suggestions"])
def test_http_cancellation_wakes_queued_work_and_releases_request_capacity(monkeypatch, endpoint):
    waiting = observe_waiters(monkeypatch, ["work_search", "duplicate_suggestions"])
    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1), request_wait_seconds=.1,
        body_max_bytes=2097152, timeout_seconds=30,
        inference=InferenceResources(slots=0, wait_seconds=30),
    )

    async def downstream(scope, _receive, _send):
        model = QueuedEmbedder(Model(), resources.inference, scope["state"][INFERENCE_REQUEST_KEY])
        await asyncio.to_thread(model.embed_query, "queued")

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(_message):
        raise AssertionError("The cancelled request must not send a response")

    async def exercise():
        middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
        scope = {"type": "http", "headers": [], "query_string": b"q=cache&semantic=true",
                 "method": "GET" if endpoint == "work-items" else "POST",
                 "path": f"/api/v1/projects/test/{endpoint}"}
        task = asyncio.create_task(middleware(scope, receive, send))
        label = "work_search" if endpoint == "work-items" else "duplicate_suggestions"
        try:
            assert await asyncio.to_thread(waiting[label].wait, 5)
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(asyncio.gather(*resources.draining_tasks), timeout=1)
        assert resources.request_slots._value == 1
        assert not resources.inference._waiting
        assert resources.inference._active == 0

    asyncio.run(exercise())
