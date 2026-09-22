"""Real routes share model capacity without reserving it during SQL/cache work."""

from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from threading import Event, Lock

import pytest

from mnemonic_api.inference import InferenceResources

from .test_duplicate_suggestions_postgres import DeterministicEmbedder, save, suggest

pytestmark = pytest.mark.postgres


def semantic_search(api, project):
    return api.get(f"/api/v1/projects/{project['id']}/work-items",
                   params={"q": "cache", "semantic": True})


@pytest.mark.parametrize("endpoint", ["work", "unified", "duplicates"])
def test_cache_publication_does_not_reserve_model_capacity(
    api, project, work_payload, monkeypatch, endpoint,
):
    save(api, project, work_payload, title="Cache capacity candidate",
         prompt="cache [dense-target]")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    gate = api.app.state.duplicate_suggestion_resources.inference
    gate.slots, gate.wait_seconds = 1, 0.05
    cache_entered, release_cache = Event(), Event()
    once = Lock()
    module_path, name = {
        "work": ("application.routes.work_search", "persist_embedding_updates"),
        "unified": ("services.search", "persist_embedding_updates"),
        "duplicates": ("services.duplicate_suggestions", "_persist_cache_updates"),
    }[endpoint]
    module = import_module("mnemonic_api." + module_path)
    original = getattr(module, name)

    def delayed_cache(*args, **kwargs):
        if once.acquire(blocking=False):
            cache_entered.set()
            assert release_cache.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, delayed_cache)

    def first_request():
        if endpoint == "work":
            return semantic_search(api, project)
        if endpoint == "duplicates":
            return suggest(api, project)
        return api.post(f"/api/v1/projects/{project['id']}/search", json={
            "q": "cache", "facets": ["work_items"],
            "filters": {"work_items": {"semantic": True}}})

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(first_request)
        try:
            assert cache_entered.wait(5)
            second = semantic_search(api, project)
            assert second.status_code == 200, second.text
            assert second.json()["semantic"]["inference"]["status"] == "completed"
        finally:
            release_cache.set()
        response = first.result(5)
        assert response.status_code == 200, response.text


def test_competing_duplicate_request_waits_for_search_inference_and_completes(
    api, project, work_payload, monkeypatch,
):
    save(api, project, work_payload, title="Cache capacity candidate",
         prompt="cache [dense-target]")
    entered, release, queued = Event(), Event(), Event()
    first_call = Lock()

    class ControlledModel(DeterministicEmbedder):
        def embed_query(self, text):
            if first_call.acquire(blocking=False):
                entered.set()
                assert release.wait(5)
            return super().embed_query(text)

    original = InferenceResources._wait_for_turn

    def observe(self, ticket, request, deadline):
        queued.set()
        return original(self, ticket, request, deadline)

    monkeypatch.setattr(InferenceResources, "_wait_for_turn", observe)
    api.app.state.semantic_embedder = ControlledModel()
    gate = api.app.state.duplicate_suggestion_resources.inference
    gate.slots, gate.wait_seconds = 1, 3
    with ThreadPoolExecutor(max_workers=2) as pool:
        search = pool.submit(semantic_search, api, project)
        try:
            assert entered.wait(5)
            comparison = pool.submit(suggest, api, project)
            assert queued.wait(5)
        finally:
            release.set()
        for future in (search, comparison):
            result = future.result(5)
            assert result.status_code == 200, result.text
            assert result.json()["semantic"]["inference"]["status"] == "completed"


@pytest.mark.parametrize("endpoint", ["work", "unified", "duplicates"])
def test_document_batch_capacity_failure_preserves_its_reason(
    api, project, work_payload, endpoint,
):
    from mnemonic_api.inference import InferenceCapacityError

    class BusyDocuments(DeterministicEmbedder):
        def embed_documents(self, _texts):
            raise InferenceCapacityError("synthetic document contention")

    save(api, project, work_payload, title="Cache missing vectors", prompt="cache [dense-target]")
    api.app.state.semantic_embedder = BusyDocuments()
    if endpoint == "duplicates":
        response = suggest(api, project)
        assert response.status_code == 200, response.text
        semantic = response.json()["semantic"]
    else:
        response = semantic_search(api, project) if endpoint == "work" else api.post(
            f"/api/v1/projects/{project['id']}/search", json={
                "q": "cache", "facets": ["work_items"],
                "filters": {"work_items": {"semantic": True}}})
        assert response.status_code == 503, response.text
        semantic = response.json()["detail"]["context"]["semantic"]
    assert semantic["inference"] == {"status": "unavailable", "reason": "capacity_exhausted"}
    assert semantic["comparison_incomplete"] is True
