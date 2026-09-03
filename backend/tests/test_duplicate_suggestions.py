"""Strict Advisory wire contracts and pre-routing resource bounds."""

import asyncio
import json
from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mnemonic_api.application.suggestion_resources import (
    DuplicateSuggestionControlMiddleware,
    DuplicateSuggestionResources,
    _is_semantic_search_request,
    suggestion_inference_acquired,
)
from mnemonic_api.config import Settings
from mnemonic_api.main import create_app
from mnemonic_api.schemas import (
    DuplicateCandidateSummary,
    DuplicateSuggestion,
    DuplicateSuggestionPage,
    DuplicateSuggestionRequest,
    WorkIdentityPointer,
)
from mnemonic_api.services import duplicate_suggestions as suggestion_service

API_KEY = "duplicate-suggestion-unit-key-32-characters"


class NeverEmbedder:
    def embed_query(self, _text):
        raise AssertionError("body and queue failures must happen before inference")

    def embed_documents(self, _texts):
        raise AssertionError("body and queue failures must happen before inference")


def request_payload(**changes):
    return {
        "title": "Investigate cache invalidation",
        "summary": "Cached state survives a branch switch.",
        "initial_prompt": "Reproduce and repair the invalidation path.",
        "tags": [" Cache ", "cache", "Correctness"],
        **changes,
    }


def suggestion(rank=1, signals=None):
    root_id = uuid4()
    return DuplicateSuggestion(
        canonical_work=DuplicateCandidateSummary(
            work_item_id=root_id,
            title="Existing cache work",
            summary="The retained canonical work item.",
            status="done",
            updated_at=datetime.now(UTC),
            duplicate_member_count=2,
        ),
        matched_member=WorkIdentityPointer(
            id=root_id,
            title="Existing cache work",
            status="done",
        ),
        rank=rank,
        signals=signals or ["exact_title", "lexical"],
    )


def test_request_uses_create_draft_normalization_and_strict_fields():
    parsed = DuplicateSuggestionRequest.model_validate(request_payload())
    assert parsed.tags == ["cache", "correctness"]
    assert parsed.limit == 5
    assert parsed.exclude_work_item_id is None
    with pytest.raises(ValidationError):
        DuplicateSuggestionRequest.model_validate(request_payload(limit=True))
    with pytest.raises(ValidationError):
        DuplicateSuggestionRequest.model_validate(request_payload(client_operation_id=str(uuid4())))
    with pytest.raises(ValidationError):
        DuplicateSuggestionRequest.model_validate(request_payload(tags=["x"] * 21))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"signals": ["semantic", "lexical"]}, "canonical order"),
        ({"signals": ["lexical", "lexical"]}, "unique"),
        ({"rank": True}, "valid integer"),
    ],
)
def test_suggestion_rejects_invalid_rank_and_signals(changes, message):
    payload = suggestion().model_dump()
    payload.update(changes)
    with pytest.raises(ValidationError, match=message):
        DuplicateSuggestion.model_validate(payload)


def test_page_enforces_mode_rank_group_and_exact_lane_coherence():
    item = suggestion()
    page = DuplicateSuggestionPage(
        items=[item],
        limit=5,
        mode="lexical",
        semantic_available=False,
        semantic_scope="unavailable",
        composition_version="duplicate-suggestion-v1",
        exact_title_group_total=1,
        omitted_exact_title_group_count=0,
    )
    assert page.items[0].rank == 1
    invalid = page.model_dump()
    invalid["semantic_available"] = True
    with pytest.raises(ValidationError, match="mode and semantic scope"):
        DuplicateSuggestionPage.model_validate(invalid)
    invalid = page.model_dump()
    invalid["items"][0]["rank"] = 2
    with pytest.raises(ValidationError, match="contiguous"):
        DuplicateSuggestionPage.model_validate(invalid)
    invalid = page.model_dump()
    invalid["exact_title_group_total"] = 2
    with pytest.raises(ValidationError, match="fill available response slots"):
        DuplicateSuggestionPage.model_validate(invalid)


def test_valid_worst_case_json_remains_below_frozen_streaming_cap():
    request = DuplicateSuggestionRequest.model_validate(
        request_payload(
            title="t" * 200,
            summary="s" * 1_000,
            initial_prompt="\U0010ffff" * 100_000,
            tags=[f"tag-{index:02d}-" + "x" * 43 for index in range(20)],
            exclude_work_item_id=uuid4(),
            limit=10,
        )
    )
    encoded = json.dumps(request.model_dump(mode="json"), ensure_ascii=True).encode()
    assert len(encoded) < 2_097_152


@pytest.mark.parametrize(
    ("vector", "dimensions", "valid"),
    [
        ([1.0, 0.0], 2, True),
        ([], None, False),
        ([1.0], 2, False),
        ([0.0, 0.0], 2, False),
        ([float("nan"), 0.0], 2, False),
        ([float("inf"), 0.0], 2, False),
        ([1e308, 1e308], 2, False),
    ],
)
def test_advisory_vector_validation_requires_finite_nonzero_bounded_norm(
    vector, dimensions, valid
):
    assert suggestion_service._valid_vector(vector, dimensions) is valid


def test_expired_request_deadline_rejects_before_inference():
    settings = Settings(
        database_url="postgresql://localhost/mnemonic",
        api_key=API_KEY,
    )
    with pytest.raises(TimeoutError):
        suggestion_service.suggest_duplicate_work(
            None,
            uuid4(),
            DuplicateSuggestionRequest.model_validate(request_payload()),
            settings=settings,
            embedder=NeverEmbedder(),
            inference_permitted=True,
            deadline=monotonic() - 1,
        )


def test_authentication_precedes_declared_body_cap():
    settings = Settings(
        database_url="postgresql://localhost:1/unavailable",
        api_key=API_KEY,
    )
    with TestClient(create_app(settings, semantic_embedder=NeverEmbedder())) as client:
        path = f"/api/v1/projects/{uuid4()}/duplicate-suggestions"
        headers = {"Content-Length": str(2_097_153)}
        unauthenticated = client.post(path, content=b"{}", headers=headers)
        assert unauthenticated.status_code == 401
        oversized = client.post(
            path,
            content=b"{}",
            headers={**headers, "Authorization": f"Bearer {API_KEY}"},
        )
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "request_body_too_large"
        assert oversized.headers["cache-control"] == "no-store"


def test_chunked_body_cap_is_enforced_without_content_length():
    downstream_called = False
    sent = []

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    messages = iter(
        (
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        )
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=5,
        timeout_seconds=1.0,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }
    asyncio.run(middleware(scope, receive, send))
    assert downstream_called is False
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
    assert (b"cache-control", b"no-store") in start["headers"]


def test_disconnect_during_body_stops_before_inference_and_releases_request_slot():
    downstream_called = False
    body = json.dumps(request_payload()).encode()
    messages = iter(
        (
            {"type": "http.request", "body": body, "more_body": True},
            {"type": "http.disconnect"},
        )
    )

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return next(messages)

    async def send(_message):
        raise AssertionError("A disconnected client must not receive a response")

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=2_097_152,
        timeout_seconds=1.0,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        await middleware(scope, receive, send)
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)

    asyncio.run(exercise())
    assert downstream_called is False
    assert "state" not in scope


def test_duplicate_json_keys_are_rejected_before_inference():
    settings = Settings(
        database_url="postgresql://localhost:1/unavailable",
        api_key=API_KEY,
    )
    body = b'{"title":"first","title":"second"}'
    with TestClient(create_app(settings, semantic_embedder=NeverEmbedder())) as client:
        response = client.post(
            f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
            content=body,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422
    assert response.json() == {
        "detail": [{"type": "value_error", "loc": ["body"], "msg": "Value is invalid."}]
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "body",
    [
        b"[" * 500_000 + b"{}" + b"]" * 500_000,
        b'{"title":' + b"9" * 100_000 + b"}",
    ],
    ids=["excessive-nesting", "integer-digit-limit"],
)
def test_adversarial_json_parser_failures_are_bounded_422_errors(body):
    downstream_called = False
    sent = []

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent.append(message)

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=2_097_152,
        timeout_seconds=1.0,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        await middleware(scope, receive, send)
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)

    asyncio.run(exercise())
    assert downstream_called is False
    assert "state" not in scope
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 422
    assert (b"cache-control", b"no-store") in start["headers"]


def test_timeout_budget_is_typed_and_releases_both_resource_slots():
    sent = []
    downstream_finished = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_finished
        await asyncio.sleep(0.02)
        downstream_finished = True

    messages = iter(
        ({"type": "http.request", "body": b"{}", "more_body": False},)
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=2_097_152,
        timeout_seconds=0.001,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        started_at = asyncio.get_running_loop().time()
        await middleware(scope, receive, send)
        assert asyncio.get_running_loop().time() - started_at < 0.02
        assert resources.request_slots.locked()
        assert resources.inference_slots.locked()
        await asyncio.sleep(0.03)
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)
        assert not resources.draining_tasks

    asyncio.run(exercise())
    assert downstream_finished is True
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 503
    assert (b"cache-control", b"no-store") in start["headers"]
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert json.loads(body)["detail"]["code"] == "duplicate_suggestion_unavailable"


def test_timeout_response_cancellation_does_not_over_release_resource_slots():
    downstream_finished = asyncio.Event()

    async def downstream(_scope, _receive, _send):
        await asyncio.sleep(0.02)
        downstream_finished.set()

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def cancelled_send(_message):
        raise asyncio.CancelledError

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=2_097_152,
        timeout_seconds=0.001,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await middleware(scope, receive, cancelled_send)
        drains = tuple(resources.draining_tasks)
        assert len(drains) == 1
        await asyncio.gather(*drains)
        assert downstream_finished.is_set()

        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.01)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.01)

    asyncio.run(exercise())


def test_body_and_inference_wait_share_one_route_deadline():
    sent = []
    downstream_called = False

    async def downstream(_scope, _receive, _send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        await asyncio.sleep(0.04)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message):
        sent.append(message)

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(0),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.2,
        body_max_bytes=2_097_152,
        timeout_seconds=0.05,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        started_at = asyncio.get_running_loop().time()
        await middleware(scope, receive, send)
        elapsed = asyncio.get_running_loop().time() - started_at
        assert elapsed < 0.12
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)

    asyncio.run(exercise())
    assert downstream_called is False
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 503
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    assert json.loads(body)["detail"]["code"] == "duplicate_suggestion_unavailable"


def test_semantic_search_and_suggestion_share_one_inference_gate():
    suggestion_states = []

    async def exercise():
        search_started = asyncio.Event()
        finish_search = asyncio.Event()

        async def downstream(scope, _receive, _send):
            if str(scope["path"]).endswith("/work-items"):
                search_started.set()
                await finish_search.wait()
                return
            suggestion_states.append(suggestion_inference_acquired(scope))

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(_message):
            return

        resources = DuplicateSuggestionResources(
            request_slots=asyncio.Semaphore(1),
            inference_slots=asyncio.Semaphore(1),
            request_wait_seconds=0.1,
            inference_wait_seconds=0.001,
            body_max_bytes=2_097_152,
            timeout_seconds=1.0,
        )
        middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
        project_id = uuid4()
        search_scope = {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/projects/{project_id}/work-items",
            "query_string": b"q=cache&sem%61ntic=true",
            "headers": [],
        }
        suggestion_scope = {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/projects/{project_id}/duplicate-suggestions",
            "headers": [],
        }
        search_task = asyncio.create_task(
            middleware(search_scope, receive, send)
        )
        await search_started.wait()
        assert resources.inference_slots.locked()

        await middleware(suggestion_scope, receive, send)
        assert suggestion_states == [False]
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)

        finish_search.set()
        await search_task
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)

    asyncio.run(exercise())


@pytest.mark.parametrize("value", ["1", "on", "t", "true", "y", "yes"])
def test_all_pydantic_truthy_semantic_values_use_inference_gate(value):
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/v1/projects/{uuid4()}/work-items",
        "query_string": f"semantic={value}".encode(),
    }
    assert _is_semantic_search_request(scope) is True


def test_client_cancellation_retains_slots_until_downstream_exits():
    downstream_started = asyncio.Event()
    finish_downstream = asyncio.Event()

    async def downstream(_scope, _receive, _send):
        downstream_started.set()
        await finish_downstream.wait()

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(_message):
        raise AssertionError("A cancelled client must not receive a response")

    resources = DuplicateSuggestionResources(
        request_slots=asyncio.Semaphore(1),
        inference_slots=asyncio.Semaphore(1),
        request_wait_seconds=0.1,
        inference_wait_seconds=0.1,
        body_max_bytes=2_097_152,
        timeout_seconds=1.0,
    )
    middleware = DuplicateSuggestionControlMiddleware(downstream, resources=resources)
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/api/v1/projects/{uuid4()}/duplicate-suggestions",
        "headers": [],
    }

    async def exercise():
        request_task = asyncio.create_task(middleware(scope, receive, send))
        await downstream_started.wait()
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task
        assert resources.request_slots.locked()
        assert resources.inference_slots.locked()
        drains = tuple(resources.draining_tasks)
        assert len(drains) == 1
        finish_downstream.set()
        await asyncio.gather(*drains)
        assert await asyncio.wait_for(resources.request_slots.acquire(), timeout=0.1)
        assert await asyncio.wait_for(resources.inference_slots.acquire(), timeout=0.1)

    asyncio.run(exercise())


def test_settings_freeze_advisory_defaults_and_timeout_ceiling():
    settings = Settings(
        database_url="postgresql://localhost/mnemonic",
        api_key=API_KEY,
    )
    assert settings.duplicate_suggestion_body_max_bytes == 2_097_152
    assert settings.duplicate_suggestion_request_slots == 4
    assert settings.duplicate_suggestion_request_wait_ms == 250
    assert settings.duplicate_suggestion_inference_slots == 1
    assert settings.duplicate_suggestion_inference_wait_ms == 50
    assert settings.duplicate_suggestion_lexical_shortlist == 200
    assert settings.duplicate_suggestion_missing_vector_limit == 128
    assert settings.duplicate_suggestion_full_population_ceiling == 10_000
    assert settings.duplicate_suggestion_timeout_seconds == 60
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://localhost/mnemonic",
            api_key=API_KEY,
            duplicate_suggestion_timeout_seconds=61,
        )


def test_candidate_contract_rejects_endpoint_and_timestamp_incoherence():
    root_mismatch = suggestion().model_dump()
    root_mismatch["matched_member"]["title"] = "Different root title"
    with pytest.raises(ValidationError, match="root match"):
        DuplicateSuggestion.model_validate(root_mismatch)

    alias_without_group = suggestion().model_dump()
    alias_without_group["matched_member"]["id"] = uuid4()
    alias_without_group["canonical_work"]["duplicate_member_count"] = 0
    with pytest.raises(ValidationError, match="alias match"):
        DuplicateSuggestion.model_validate(alias_without_group)

    non_utc = suggestion().model_dump()
    non_utc["canonical_work"]["updated_at"] = "2026-09-02T12:00:00+01:00"
    with pytest.raises(ValidationError, match="UTC"):
        DuplicateSuggestion.model_validate(non_utc)

    out_of_page_rank = suggestion().model_dump()
    out_of_page_rank["rank"] = 11
    with pytest.raises(ValidationError):
        DuplicateSuggestion.model_validate(out_of_page_rank)
