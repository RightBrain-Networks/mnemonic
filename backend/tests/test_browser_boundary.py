"""Behavior at the real pre-body browser boundary, independent of a database."""

import asyncio
import threading
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from mnemonic_api.application.browser_boundary import BrowserBoundaryMiddleware
from mnemonic_api.application.browser_responses import BrowserResponse, response_limit
from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

PROJECT = "00000000-0000-4000-8000-000000000001"
WORK = "00000000-0000-4000-8000-000000000002"
ORIGIN = "http://localhost:3000"
HEADERS = {"host": "localhost:3000", "origin": ORIGIN}
CONFIG = Settings(database_url="postgresql://localhost:1/unavailable", api_key="secret-" * 8)


@pytest.fixture
def boundary():
    app = FastAPI()
    app.state.settings = CONFIG
    calls = []

    @app.api_route("/api/mnemonic/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def domain(request: Request):
        calls.append((request.url.path, dict(request.headers), await request.body()))
        return JSONResponse({"ok": True})

    app.add_middleware(BrowserBoundaryMiddleware)
    with TestClient(app, headers=HEADERS) as client:
        yield client, calls


@pytest.mark.parametrize("headers", [
    {"host": "evil.example"},
    {"origin": "https://evil.example"},
    {"origin": ORIGIN + "/"},
    {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"},
    {"host": "api:8000", "x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http"},
])
def test_origin_rejections_never_execute_domain(boundary, headers):
    client, calls = boundary
    response = client.post("/api/mnemonic/projects", headers=headers, json={"name": "Test"})
    assert response.status_code == 403
    assert response.json() == {"detail": "This dashboard request is not from a trusted origin."}
    assert calls == []


def test_missing_origin_allowed_only_for_reads(boundary):
    client, calls = boundary
    del client.headers["origin"]
    assert client.get("/api/mnemonic/projects").status_code == 200
    assert client.post("/api/mnemonic/projects", json={"name": "Test"}).status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize("suffix,method", [
    ("ready-work", "GET"), (f"work-items/{WORK}/claim", "POST"),
    (f"work-items/{WORK}/gates", "POST"), ("artifacts", "GET"),
    ("transcripts", "GET"), ("work-items", "DELETE"), ("settings/", "GET"),
])
def test_machine_only_routes_are_closed(boundary, suffix, method):
    client, calls = boundary
    response = client.request(method, f"/api/mnemonic/projects/{PROJECT}/{suffix}", json={})
    assert response.status_code == 404
    assert calls == []


@pytest.mark.parametrize("query", ["limit=1&limit=2", "lease_token=private", "unknown=x"])
def test_query_rejected_before_domain(boundary, query):
    client, calls = boundary
    response = client.get("/api/mnemonic/projects?" + query)
    assert response.status_code == 400
    assert "private" not in response.text
    assert not calls


@pytest.mark.parametrize("headers", [
    {"x-client-operation-id": "private"}, {"lease-token": "private"},
    {"cookie": "client_operation_id=private"}, {"cookie": "gate_id=private"},
])
def test_control_transports_rejected(boundary, headers):
    client, calls = boundary
    response = client.get("/api/mnemonic/projects", headers=headers)
    assert response.status_code == 400
    assert "private" not in response.text
    assert not calls


@pytest.mark.parametrize("body,content_type,status", [
    (b"", "application/json", 400),
    (b"[]", "application/json", 400),
    (b"{", "application/json", 400),
    (b'{"name":"bad\xff"}', "application/json", 400),
    (b'{"name":"Test"}', "text/plain", 415),
    (b'{"nested":{"lease_token":"private"}}', "application/json", 400),
    (b" " * 1_048_577, "application/json", 413),
])
def test_bounded_body_rejections_never_execute(boundary, body, content_type, status):
    client, calls = boundary
    response = client.post("/api/mnemonic/projects", content=body,
                           headers={"content-type": content_type})
    assert response.status_code == status
    assert "private" not in response.text
    assert not calls


def test_streamed_limit_does_not_depend_on_content_length(boundary):
    client, calls = boundary
    response = client.post("/api/mnemonic/projects", content=iter([b" " * 600_000] * 2),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert not calls


def test_utf8_bom_is_validated_without_changing_the_original_body(boundary):
    client, calls = boundary
    body = b'\xef\xbb\xbf{"name":"Test"}'
    response = client.post("/api/mnemonic/projects", content=body,
                           headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert calls[-1][2] == body


def test_encoded_slash_cannot_turn_one_next_path_segment_into_two(boundary):
    client, calls = boundary
    response = client.get(f"/api/mnemonic/projects%2f{PROJECT}")
    assert response.status_code == 404
    assert not calls


def test_caller_privileged_headers_are_removed_and_human_search_context_is_server_owned(boundary):
    client, calls = boundary
    caller = {"authorization": "Bearer private", "x-artifact-access": "agent", "cookie": "a=b"}
    assert client.get("/api/mnemonic/projects", headers=caller).status_code == 200
    assert "authorization" not in calls[-1][1]
    assert "x-artifact-access" not in calls[-1][1]
    assert "cookie" not in calls[-1][1]
    response = client.post(f"/api/mnemonic/projects/{PROJECT}/search", json={"q": "test"},
                           headers=caller)
    assert response.status_code == 200
    assert calls[-1][1]["x-artifact-access"] == "human-dashboard"


def test_duplicate_host_or_origin_is_never_first_value_trusted(boundary):
    client, calls = boundary
    response = client.get("/api/mnemonic/projects", headers=[
        ("host", "localhost:3000"), ("host", "evil.example"), ("origin", ORIGIN),
    ])
    assert response.status_code == 403
    assert not calls


def test_repeated_cookies_do_not_hide_a_forbidden_control(boundary):
    client, calls = boundary
    response = client.get("/api/mnemonic/projects", headers=[
        ("cookie", "theme=dark"), ("cookie", "lease_token=private"),
    ])
    assert response.status_code == 400
    assert "private" not in response.text
    assert not calls


def test_origin_rejected_without_receiving_privileged_body():
    consumed = False
    executed = False
    sent = []

    async def domain(scope, receive, send):
        nonlocal executed
        executed = True

    async def receive():
        nonlocal consumed
        consumed = True
        raise AssertionError("Body must not be consumed")

    async def send(message):
        sent.append(message)

    app = FastAPI()
    app.state.settings = CONFIG
    scope = {"type": "http", "method": "POST", "path": "/api/mnemonic/projects",
             "headers": [(b"host", b"evil.example")], "query_string": b"", "app": app}
    asyncio.run(BrowserBoundaryMiddleware(domain)(scope, receive, send))
    assert sent[0]["status"] == 403
    assert not consumed and not executed


def test_timeout_is_uncertain_and_does_not_cancel_an_executing_write(monkeypatch):
    app = FastAPI()
    app.state.settings = CONFIG
    effects = []

    @app.post("/api/mnemonic/projects")
    async def write():
        await asyncio.sleep(0.04)
        effects.append("committed")
        return {"ok": True}

    app.add_middleware(BrowserBoundaryMiddleware)
    monkeypatch.setattr("mnemonic_api.application.browser_boundary.request_timeout",
                        lambda *args: 0.01)
    with TestClient(app, headers=HEADERS) as client:
        response = client.post("/api/mnemonic/projects", json={"name": "Test"})
        assert response.status_code == 502
        assert effects == []
        time.sleep(0.08)
        assert effects == ["committed"]


def test_synchronous_read_cleanup_does_not_extend_the_browser_timeout(monkeypatch):
    app = FastAPI()
    app.state.settings = CONFIG
    finish = threading.Event()

    @app.get("/api/mnemonic/projects")
    def read():
        finish.wait(2)
        return {"ok": True}

    app.add_middleware(BrowserBoundaryMiddleware)
    monkeypatch.setattr("mnemonic_api.application.browser_boundary.request_timeout",
                        lambda *args: 0.02)
    with TestClient(app, headers=HEADERS) as client:
        started = time.monotonic()
        try:
            response = client.get("/api/mnemonic/projects")
            assert response.status_code == 502
            assert time.monotonic() - started < 1
        finally:
            finish.set()


def test_shutdown_drains_detached_browser_tasks_before_shared_resources_close():
    app = create_app(CONFIG.model_copy(update={"artifact_max_bytes": 0}))
    finished = []

    async def pending():
        await asyncio.sleep(0.01)
        finished.append(True)

    async def exercise():
        async with app.router.lifespan_context(app):
            app.state.browser_pending_tasks.add(asyncio.create_task(pending()))
        assert finished == [True]

    asyncio.run(exercise())


def test_runtime_route_and_machine_authentication_are_separate():
    with TestClient(create_app(CONFIG)) as client:
        assert client.get("/api/v1/projects", headers=HEADERS).status_code == 401
        response = client.get("/api/mnemonic/runtime-config", headers=HEADERS)
        assert response.status_code == 200
        assert set(response.json()) == {
            "timezone", "work_summary_max_chars", "artifact_max_bytes", "backup_max_bytes",
        }
        assert client.get("/api/mnemonic/runtime-config").status_code == 403
        assert not any(path.startswith("/api/mnemonic") for path in client.get(
            "/openapi.json").json()["paths"])


@pytest.mark.parametrize("status,body,headers,expected", [
    (302, b"", {"location": "https://evil.example"}, 502),
    (200, b"html", {"content-type": "text/html"}, 502),
    (200, b"{", {"content-type": "application/json"}, 502),
    (200, b"NaN", {"content-type": "application/json"}, 502),
    (200, b'{"id":9223372036854775806}', {"content-type": "application/json"}, 200),
    (204, b"", {}, 204),
])
def test_response_contract_preserves_valid_bytes(status, body, headers, expected):
    async def run():
        capture = BrowserResponse("projects", "GET")
        await capture.capture({"type": "http.response.start", "status": status,
                               "headers": [(k.encode(), v.encode()) for k, v in headers.items()]})
        await capture.capture({"type": "http.response.body", "body": body})
        return capture.response()

    response = asyncio.run(run())
    assert response.status_code == expected
    if expected != 502:
        assert response.body == body
    assert response.headers["cache-control"] == "no-store, max-age=0, no-transform"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_evidence_identity_and_bounded_response():
    async def run():
        path = f"projects/{PROJECT}/work-items/{WORK}/completion-evidence"
        capture = BrowserResponse(path, "GET")
        await capture.capture({"type": "http.response.start", "status": 200,
                               "headers": [(b"content-type", b"application/json"),
                                           (b"content-encoding", b"gzip")]})
        await capture.capture({"type": "http.response.body", "body": b"{}"})
        assert capture.response().status_code == 502
        assert capture.response().headers["content-encoding"] == "identity"
        capture = BrowserResponse(path, "GET")
        await capture.capture({"type": "http.response.body", "body": b" " * 3_145_729})
        assert not capture.body
        assert capture.response().status_code == 502
        assert response_limit(f"projects/{PROJECT}/search", "POST") == 16_777_216

    asyncio.run(run())
