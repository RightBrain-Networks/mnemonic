"""External population contracts, deterministic fusion, and actual worker ownership."""

import asyncio
from threading import Event
from time import monotonic

import pytest
from pydantic import ValidationError

from mnemonic_api.application.suggestion_resources import (
    DuplicateSuggestionControlMiddleware,
    DuplicateSuggestionResources,
    OwnedSuggestionWork,
    suggestion_owned_work,
)
from mnemonic_api.external_duplicate_schemas import (
    ExternalDuplicateCandidate,
    require_external_correspondence,
)
from mnemonic_api.schemas import DuplicateSuggestionPage, DuplicateSuggestionRequest
from mnemonic_api.services import external_duplicate_suggestions as service


def candidate(index=0, **changes):
    return {
        "url": f"https://example.com/{index:03}",
        "title": "Tracker visibility",
        "body": "Text supplied for comparison",
        "state": "open",
        **changes,
    }


def payload(**changes):
    return DuplicateSuggestionRequest.model_validate(
        {
            "title": "Tracker visibility",
            "summary": "Different summary",
            "initial_prompt": "Inspect",
            **changes,
        }
    )


def internal_page():
    return DuplicateSuggestionPage(
        items=[],
        limit=5,
        mode="hybrid_full",
        semantic_available=True,
        semantic_scope="full_project",
        composition_version="duplicate-suggestion-v1",
        exact_title_group_total=0,
        omitted_exact_title_group_count=0,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"external_candidates": None},
        {"external_candidates": [candidate(), candidate()]},
        {"external_candidates": [candidate(i) for i in range(65)]},
        {"external_candidates": [candidate(title=42)]},
        {"external_candidates": [candidate(title="\n")]},
        {"external_candidates": [candidate(title="a\nb")]},
        {"external_candidates": [candidate(body="\ud800")]},
        {"external_candidates": [candidate(body="\x00")]},
        {"external_candidates": [candidate(state="done")]},
        {"external_candidates": [candidate(label="unexpected")]},
    ],
)
def test_invalid_candidate_populations(changes):
    with pytest.raises(ValidationError):
        payload(**changes)


def test_omitted_and_empty_candidates_have_identical_canonical_request():
    assert payload().model_dump() == payload(external_candidates=[]).model_dump()
    assert (
        len(payload(external_candidates=[candidate(i) for i in range(64)]).external_candidates)
        == 64
    )


def test_exact_prefix_url_ties_and_permutation_independence():
    candidates = [ExternalDuplicateCandidate.model_validate(candidate(i)) for i in range(8)]
    urls = tuple(item.url for item in candidates)
    baseline = service.ExternalBaseline(urls[:3], urls)
    first = service.rank_external_candidates(candidates, baseline, tuple(reversed(urls)), 5)
    second = service.rank_external_candidates(
        list(reversed(candidates)), baseline, tuple(reversed(urls)), 5
    )
    assert first == second
    assert [item.reference.url for item in first[:3]] == list(urls[:3])
    assert all(item.signals == ["exact_title", "lexical", "semantic"] for item in first[:3])
    # Exact candidates are excluded before fusion rank numbering; lexical has weight 3.
    assert [item.reference.url for item in first[3:]] == list(urls[3:5])
    assert all("body" not in item.reference.model_dump() for item in first)


def test_lexical_fallback_never_pads_and_external_scope_is_independent():
    request = payload(external_candidates=[candidate(0), candidate(1, title="unrelated")])
    baseline = service.ExternalBaseline((request.external_candidates[0].url,), ())
    items = service.rank_external_candidates(request.external_candidates, baseline, None, 5)
    result = service._extended_page(internal_page(), request, items, "lexical")
    assert len(result.external_items) == 1
    assert result.mode == "hybrid_full" and result.semantic_available
    assert result.external_items[0].signals == ["exact_title"]
    assert result.external_candidate_count == 2


def test_correspondence_rejects_forged_missing_unsolicited_and_exact_overflow():
    request = payload(external_candidates=[candidate(i) for i in range(8)])
    baseline = service.ExternalBaseline(tuple(c.url for c in request.external_candidates), ())
    items = service.rank_external_candidates(request.external_candidates, baseline, None, 5)
    page = service._extended_page(internal_page(), request, items, "lexical")
    require_external_correspondence(page, request.external_candidates, request.title, request.limit)
    with pytest.raises(ValueError, match="Unsolicited"):
        require_external_correspondence(page, [], request.title, 5)
    forged = page.model_copy(deep=True)
    forged.external_items[0].reference.state = "closed"
    with pytest.raises(ValueError, match="submitted candidate"):
        require_external_correspondence(forged, request.external_candidates, request.title, 5)
    forged = page.model_copy(deep=True)
    forged.external_items[0].signals = ["lexical"]
    with pytest.raises(ValueError, match="prefix"):
        require_external_correspondence(forged, request.external_candidates, request.title, 5)
    for extra in (
        {"external_items": []},
        {"external_scope": None},
        {"external_items": [], "external_scope": "lexical", "external_candidate_count": 0},
    ):
        with pytest.raises(ValidationError):
            DuplicateSuggestionPage.model_validate({**internal_page().model_dump(), **extra})


def test_deadline_clips_external_budget_and_reserves_response_time():
    assert service.external_deadline(100, 10) == 15
    assert service.external_deadline(12, 10) == 11
    assert service.external_deadline(10.5, 10) < 10


@pytest.mark.parametrize("bad", [[], [[0, 0]], [[float("nan"), 1]], [[1]], [[1e308, 1e308]]])
def test_invalid_semantic_batch_discards_whole_stage(bad):
    class Embedder:
        def embed_documents(self, _texts):
            return bad

    with pytest.raises(ValueError):
        service.rank_external_semantic(
            [ExternalDuplicateCandidate.model_validate(candidate())],
            Embedder(),
            (1, 0),
            monotonic() + 1,
        )


@pytest.mark.parametrize("count", [1, 16, 64])
def test_semantic_batches_complete_population_with_bounded_text(count):
    class Embedder:
        batches = []

        def embed_documents(self, texts):
            self.batches.append(texts)
            return [[1, 0] for _ in texts]

    model = Embedder()
    candidates = [
        ExternalDuplicateCandidate.model_validate(candidate(i, body="x" * 20000))
        for i in range(count)
    ]
    ranked = service.rank_external_semantic(candidates, model, (1, 0), monotonic() + 1)
    assert ranked == tuple(c.url for c in candidates)
    assert sum(len(batch) for batch in model.batches) == count
    assert max(len(batch) for batch in model.batches) <= 16
    assert all(text.endswith("x" * 1500) and len(text) < 2000 for b in model.batches for text in b)


def test_late_semantic_result_keeps_baseline_and_permits_until_worker_really_finishes(monkeypatch):
    entered, release = Event(), Event()
    responses = []
    request = payload(external_candidates=[candidate()])
    baseline = service.ExternalBaseline((request.external_candidates[0].url,), ())
    monkeypatch.setattr(service, "EXTERNAL_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(service, "capture_external_baseline", lambda *_: baseline)

    class SlowEmbedder:
        def embed_documents(self, _texts):
            entered.set()
            release.wait(3)
            return [[1, 0]]

    async def downstream(scope, _receive, send):
        result = await service.extend_external_suggestions(
            internal_page(),
            request,
            session_factory=None,
            embedder=SlowEmbedder(),
            query_vector=(1, 0),
            inference_permitted=True,
            request_deadline=monotonic() + 5,
            owned_work=suggestion_owned_work(scope),
        )
        responses.append(result)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def exercise():
        resources = DuplicateSuggestionResources(
            asyncio.Semaphore(1),
            asyncio.Semaphore(1),
            0.1,
            0.1,
            2097152,
            5,
        )
        middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(_message):
            pass

        try:
            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "headers": [],
                    "path": "/api/v1/projects/id/duplicate-suggestions",
                },
                receive,
                send,
            )
            assert entered.is_set()
            assert responses[0].external_scope == "lexical"
            assert responses[0].mode == "hybrid_full"
            assert resources.request_slots.locked() and resources.inference_slots.locked()
        finally:
            release.set()
        await asyncio.gather(*resources.draining_tasks)
        assert resources.request_slots._value == resources.inference_slots._value == 1

    asyncio.run(exercise())


def test_late_baseline_returns_unavailable_and_cancelled_awaiter_does_not_cancel_worker():
    entered, release = Event(), Event()

    def slow():
        entered.set()
        release.wait(3)
        return "late"

    async def exercise():
        owner = OwnedSuggestionWork()
        task = owner.start(slow)
        try:
            with pytest.raises(TimeoutError):
                await service._await_owned(task, monotonic() + 0.02)
            assert entered.is_set() and owner.pending
            waiter = asyncio.create_task(service._await_owned(task, monotonic() + 1))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not task.cancelled() and owner.pending
        finally:
            release.set()
        await owner.finish()
        assert task.result() == "late"

    asyncio.run(exercise())
