"""A slow native call must not hold the lexical response past its deadline."""

import asyncio
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from mnemonic_api import semantic
from mnemonic_api.services import duplicate_suggestions as service

from .test_duplicate_suggestions_postgres import DeterministicEmbedder, save, suggest

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("stage", ["model_load", "embed_query", "embed_documents", "cache_refresh"])
def test_slow_inference_retains_response_and_permits_within_budget(
    api, project, work_payload, monkeypatch, stage
):
    work = save(api, project, work_payload, title="cache repair candidate")
    resources = api.app.state.duplicate_suggestion_resources
    resources.timeout_seconds = 2.0
    resources.request_slots = asyncio.Semaphore(1)
    started = Event()
    finished = Event()

    _install_slow_stage(api, monkeypatch, stage, started, finished)
    before = monotonic()
    try:
        response = suggest(api, project)
        elapsed = monotonic() - before
        assert started.is_set()
        assert elapsed < resources.timeout_seconds
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["items"][0]["canonical_work"]["work_item_id"] == work["id"]
        if stage == "cache_refresh":
            assert body["semantic_available"] is True
            assert body["semantic"]["inference"]["status"] == "completed"
            assert body["semantic"]["cache_refresh"] == {
                "status": "failed", "reason": "cache_refresh_failed",
            }
        else:
            assert body["mode"] == "lexical"
            assert body["semantic_available"] is False
            assert body["semantic"]["comparison_incomplete"] is True
            assert body["semantic"]["inference"] == {
                "status": "unavailable", "reason": "deadline_exceeded",
            }
            assert body["semantic"]["retry"] == {"max_attempts": 1, "after_seconds": 1}
        assert not finished.is_set()
        assert resources.request_slots.locked()
        assert suggest(api, project).status_code == 429
        # The retained inference/request permit never becomes a creation prerequisite.
        save(api, project, work_payload, title="independent new work")
    finally:
        assert finished.wait(timeout=5)
        if api.portal is not None:
            api.portal.call(_drain, resources)
    assert not resources.request_slots.locked()
    assert resources.inference._active == 0


def _install_slow_stage(api, monkeypatch, stage, started, finished):
    embedder = DeterministicEmbedder()
    target = service if stage == "cache_refresh" else embedder
    method = "_persist_cache_updates" if stage == "cache_refresh" else stage
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
            sleep(3.0)  # Outlast even the outer response deadline, not just a stage check.
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(target, method, slow)


async def _drain(resources):
    if resources.draining_tasks:
        await asyncio.wait_for(asyncio.gather(*resources.draining_tasks), timeout=5)


def test_stalled_snapshot_returns_typed_error_without_starting_late_inference(
    api, project, monkeypatch
):
    resources = api.app.state.duplicate_suggestion_resources
    resources.timeout_seconds = 2.0
    resources.request_slots = asyncio.Semaphore(1)
    finished = Event()
    queries = []
    embedder = DeterministicEmbedder()
    monkeypatch.setattr(embedder, "embed_query", lambda value: queries.append(value))
    api.app.state.semantic_embedder = embedder
    original = service._capture_snapshot

    def slow(*args, **kwargs):
        try:
            sleep(3.0)
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
        assert finished.wait(timeout=5)
        if api.portal is not None:
            api.portal.call(_drain, resources)
    assert queries == []
