"""A slow native call must not hold the lexical response past its deadline."""

import asyncio
from threading import Event
from time import monotonic
from types import SimpleNamespace

import pytest

from mnemonic_api import semantic
from mnemonic_api.application.routes import duplicates as routes
from mnemonic_api.services import duplicate_suggestions as service

from .test_duplicate_suggestions_postgres import (
    DeterministicEmbedder,
    cache_project_vectors,
    save,
    suggest,
)

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("stage", ["model_load", "embed_query", "refresh_enrollment"])
def test_slow_inference_retains_response_and_permits_within_budget(
    api, project, work_payload, monkeypatch, postgres_engine, stage
):
    work = save(api, project, work_payload, title="cache repair candidate")
    if stage != "refresh_enrollment":
        cache_project_vectors(postgres_engine, project["id"], None)
    resources = api.app.state.duplicate_suggestion_resources
    resources.timeout_seconds = 30.0
    resources.request_slots = asyncio.Semaphore(1)
    started = Event()
    release = Event()
    finished = Event()

    _expire_after_stage_starts(monkeypatch, started)
    _install_slow_stage(api, monkeypatch, stage, started, release, finished)
    before = monotonic()
    try:
        response = suggest(api, project)
        elapsed = monotonic() - before
        assert started.is_set(), response.text
        assert elapsed < resources.timeout_seconds
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"][0]["canonical_work"]["work_item_id"] == work["id"]
        assert body["mode"] == "lexical"
        assert body["semantic_available"] is False
        assert body["semantic"]["comparison_incomplete"] is True
        assert body["semantic"]["inference"] == {
            "status": "unavailable", "reason": "deadline_exceeded",
        }
        assert body["semantic"]["retry"] is None
        assert resources.inference._active == int(stage != "refresh_enrollment")
        assert not finished.is_set()
        assert resources.request_slots.locked()
        assert suggest(api, project).status_code == 429
        # The retained inference/request permit never becomes a creation prerequisite.
        save(api, project, work_payload, title="independent new work")
    finally:
        release.set()
        if api.portal is not None:
            api.portal.call(_drain, resources)
    assert finished.is_set()
    assert not resources.request_slots.locked()
    assert resources.inference._active == 0


def _expire_after_stage_starts(monkeypatch, started):
    # Real PostgreSQL setup must not race a one-second work deadline on a busy
    # runner. Keep the outer safety budget generous, then exercise the real
    # route's short await deadline only once the injected stall is active.
    original = routes._await_internal
    monkeypatch.setattr(service, "SUGGESTION_INTERACTIVE_SECONDS", 30.0)

    async def await_started(task, deadline, responses, ready):
        assert await asyncio.to_thread(started.wait, 10), "Injected stage did not start"
        return await original(task, min(deadline, monotonic() + 0.05), responses, ready)

    monkeypatch.setattr(routes, "_await_internal", await_started)


def _install_slow_stage(api, monkeypatch, stage, started, release, finished):
    embedder = DeterministicEmbedder()
    target = service if stage == "refresh_enrollment" else embedder
    method = "_request_background_refresh" if stage == "refresh_enrollment" else stage
    if stage == "model_load":
        embedder = semantic.FastembedEmbedder(workers=1, threads=1)
        target, method = semantic._EmbeddingWorker, "_load"
        monkeypatch.setattr(target, method, lambda _self: SimpleNamespace(
            query_embed=lambda _texts: [[1.0, 0.0]]))
    api.app.state.semantic_embedder = embedder
    original = getattr(target, method)

    def slow(*args, **kwargs):
        started.set()
        try:
            # Only the test releases native work, after checking fallback,
            # retained permits, saturation and independent work creation.
            assert release.wait(30), "Test did not release the injected stage"
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(target, method, slow)


async def _drain(resources):
    if resources.draining_tasks:
        await asyncio.wait_for(asyncio.gather(*resources.draining_tasks), timeout=10)


def test_stalled_snapshot_returns_typed_error_without_starting_late_inference(
    api, project, monkeypatch
):
    resources = api.app.state.duplicate_suggestion_resources
    resources.timeout_seconds = 30.0
    resources.request_slots = asyncio.Semaphore(1)
    started = Event()
    release = Event()
    finished = Event()
    queries = []
    embedder = DeterministicEmbedder()
    monkeypatch.setattr(embedder, "embed_query", lambda value: queries.append(value))
    api.app.state.semantic_embedder = embedder
    original = service._capture_snapshot
    _expire_after_stage_starts(monkeypatch, started)

    def slow(*args, **kwargs):
        started.set()
        try:
            assert release.wait(30), "Test did not release the snapshot"
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(service, "_capture_snapshot", slow)
    before = monotonic()
    try:
        response = suggest(api, project)
        assert monotonic() - before < resources.timeout_seconds
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "duplicate_suggestion_unavailable"
        assert detail["context"]["semantic"]["inference"]["reason"] == "deadline_exceeded"
        assert resources.request_slots.locked()
    finally:
        release.set()
        if api.portal is not None:
            api.portal.call(_drain, resources)
    assert finished.is_set()
    assert queries == []
