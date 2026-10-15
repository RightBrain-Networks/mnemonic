"""Browser duplicate suggestions share admission, deadline, and native-call ownership."""

import asyncio
import json
from threading import Event

import pytest
from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from mnemonic_api.application.suggestion_resources import suggestion_owned_work
from mnemonic_api.config import Settings
from mnemonic_api.inference import INFERENCE_REQUEST_KEY
from mnemonic_api.main import create_app

PATH = "/api/mnemonic/projects/00000000-0000-4000-8000-000000000001/duplicate-suggestions"
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}
PAYLOAD = {
    "title": "Browser comparison", "summary": "Compare the current draft.",
    "initial_prompt": "Check for existing work before creating another item.", "tags": [],
}


def app_with_handler(handler):
    app = create_app(Settings(
        database_url="postgresql://localhost:1/unavailable",
        api_key="browser-suggestion-test-secret-at-least-32-characters",
        artifact_max_bytes=0,
    ))
    # Replace only domain execution. Exercise the real application's browser,
    # auth, broadcast, and shared resource middleware without a live database.
    app.router.routes.insert(0, APIRoute(PATH, handler, methods=["POST"]))
    return app


@pytest.fixture
def browser_suggestions():
    calls = []

    async def domain(request: Request):
        calls.append(await request.json())
        return {"items": []}

    app = app_with_handler(domain)
    with TestClient(app, headers=HEADERS) as client:
        yield client, app.state.duplicate_suggestion_resources, calls


def test_browser_suggestion_capacity_preserves_typed_retry_guidance(browser_suggestions):
    client, resources, calls = browser_suggestions
    resources.request_slots = asyncio.Semaphore(0)
    resources.request_wait_seconds = 0.005
    response = client.post(PATH, json=PAYLOAD)
    assert response.status_code == 429, response.text
    assert response.headers["retry-after"] == "1"
    assert response.headers["cache-control"] == "no-store, max-age=0, no-transform"
    detail = response.json()["detail"]
    assert detail["code"] == "duplicate_suggestion_busy"
    semantic = detail["context"]["semantic"]
    assert semantic["comparison_incomplete"] is True
    assert semantic["inference"]["reason"] == "capacity_exhausted"
    assert semantic["retry"] is not None
    assert calls == []
    assert resources.request_slots.locked()


def test_browser_suggestion_duplicate_keys_never_reach_domain(browser_suggestions):
    client, resources, calls = browser_suggestions
    # The browser JSON check sees a valid last value, while the shared advisory
    # middleware must still reject duplicate keys in the original request bytes.
    body = '{"title":"earlier",' + json.dumps(PAYLOAD)[1:]
    response = client.post(PATH, content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": [{"type": "value_error", "loc": ["body"], "msg": "Value is invalid."}],
    }
    assert calls == []
    assert not resources.request_slots.locked()
    assert resources.inference._active == 0


@pytest.mark.parametrize("timeout_layer", ["suggestion", "browser"])
def test_browser_suggestion_deadlines_retain_slots_until_native_call_returns(
    monkeypatch, timeout_layer,
):
    started = Event()
    finish = Event()
    finished = Event()
    budgets = []

    async def domain(request: Request):
        resources = request.app.state.duplicate_suggestion_resources
        budget = request.scope["state"][INFERENCE_REQUEST_KEY]
        budgets.append(budget)

        def native():
            with resources.inference.reserve(budget):
                started.set()
                try:
                    assert finish.wait(timeout=5), "Native test call was not released"
                finally:
                    finished.set()

        await asyncio.shield(suggestion_owned_work(request.scope).start(native))
        return {"items": []}

    app = app_with_handler(domain)
    resources = app.state.duplicate_suggestion_resources
    resources.request_slots = asyncio.Semaphore(1)
    resources.request_wait_seconds = 0.005
    resources.timeout_seconds = 0.2 if timeout_layer == "suggestion" else 5
    if timeout_layer == "browser":
        monkeypatch.setattr("mnemonic_api.application.browser_boundary.request_timeout",
                            lambda *args: 0.2)

    async def drain():
        if resources.draining_tasks:
            await asyncio.wait_for(asyncio.gather(*resources.draining_tasks), timeout=5)

    with TestClient(app, headers=HEADERS) as client:
        try:
            response = client.post(PATH, json=PAYLOAD)
            assert response.status_code == (503 if timeout_layer == "suggestion" else 502), (
                response.text
            )
            assert started.is_set() and not finished.is_set()
            if timeout_layer == "suggestion":
                detail = response.json()["detail"]
                assert detail["code"] == "duplicate_suggestion_unavailable"
                semantic = detail["context"]["semantic"]
                assert semantic["comparison_incomplete"] is True
                assert semantic["inference"]["reason"] == "deadline_exceeded"
                assert semantic["retry"] is None
            assert "retry-after" not in response.headers
            assert budgets[0].cancelled.is_set()
            assert resources.request_slots.locked()
            assert resources.inference._active == 1
            assert client.post(PATH, json=PAYLOAD).status_code == 429
        finally:
            finish.set()
            assert client.portal is not None
            client.portal.call(drain)
        assert finished.is_set()
        assert not resources.request_slots.locked()
        assert resources.inference._active == 0
        assert not resources.draining_tasks
