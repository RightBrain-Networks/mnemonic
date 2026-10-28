"""Transcript admission, controls and response limits at the browser boundary."""

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from mnemonic_api.application.browser_transcripts import (
    FAILURE_DETAIL,
    TRANSCRIPT_BROWSER_PREFIX,
    TranscriptBrowserMiddleware,
    transcript_browser_router,
)
from mnemonic_api.config import Settings

PROJECT = "00000000-0000-4000-8000-000000000001"
TRANSCRIPT = "00000000-0000-4000-8000-000000000002"
COLLECTION = f"{TRANSCRIPT_BROWSER_PREFIX}/projects/{PROJECT}/transcripts"
SETTINGS = f"{TRANSCRIPT_BROWSER_PREFIX}/projects/{PROJECT}/transcript-settings"
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}
CONFIG = Settings(database_url="postgresql://localhost:1/unavailable", api_key="secret-" * 8)


@pytest.fixture
def boundary():
    app = FastAPI()
    app.state.settings = CONFIG
    calls = []

    @app.api_route("/api/transcripts/{path:path}",
                   methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def domain(request: Request):
        calls.append((request.url.path, dict(request.headers), await request.body()))
        return JSONResponse({"ok": True})

    app.add_middleware(TranscriptBrowserMiddleware)
    with TestClient(app, headers=HEADERS) as client:
        yield client, calls


def _rejected(response, status, message):
    assert response.status_code == status
    assert response.json() == {"detail": {"code": "transcript_proxy_rejected", "message": message}}
    assert response.headers["cache-control"] == "no-store, max-age=0, no-transform"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"


@pytest.mark.parametrize("headers", [
    {"host": "api:8000", "x-forwarded-host": "localhost:3000"},
    {"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"}, {"origin": "http://localhost:3000/"},
    [("host", "localhost:3000"), ("host", "evil.example")],
])
def test_untrusted_origin_never_reaches_domain(boundary, headers):
    client, calls = boundary
    _rejected(client.get(COLLECTION, headers=headers), 403,
              "This dashboard request is not from a trusted origin.")
    assert not calls


def test_missing_origin_is_read_only(boundary):
    client, calls = boundary
    del client.headers["origin"]
    assert client.get(COLLECTION).status_code == 200
    _rejected(client.post(COLLECTION + "/rebuild", json={"client_operation_id": str(uuid4())}),
              403, "This dashboard request is not from a trusted origin.")
    assert len(calls) == 1


@pytest.mark.parametrize("method,path", [
    ("POST", COLLECTION + "/search-content"), ("GET", COLLECTION + "/import"),
    ("GET", COLLECTION + "/"), ("DELETE", COLLECTION + "/" + TRANSCRIPT),
    ("POST", COLLECTION), ("POST", COLLECTION + "/" + TRANSCRIPT + "/approve"),
    ("GET", COLLECTION.replace("/projects/", "/projects%2f")),
    ("GET", f"/api/transcripts/projects/{PROJECT}/artifacts"),
    ("GET", f"/api/transcripts/projects/{PROJECT}/work-items"),
])
def test_closed_paths(boundary, method, path):
    client, calls = boundary
    _rejected(client.request(method, path), 404, "Route not found.")
    assert not calls


@pytest.mark.parametrize("headers", [
    {"x-lease-token": "private"}, {"client-operation-id": "private"},
    {"cookie": "theme=dark; gate_id=private"},
    [("cookie", "theme=dark"), ("cookie", "lease_token=private")],
])
def test_controls_cannot_be_smuggled_in_headers_or_cookies(boundary, headers):
    client, calls = boundary
    _rejected(client.get(COLLECTION, headers=headers), 400,
              "The request contains a forbidden control header.")
    assert not calls


@pytest.mark.parametrize("suffix,query", [
    ("", "limit=1&limit=2"), ("", "limit=101"), ("", "offset=-1"),
    ("", "query=%20"), ("", "query=" + "a" * 201),
    ("", "query_mode=semantic"), ("", "diagnostics=yes"), ("", "fulltext=1"),
    ("", "sort_by=source_path"), ("", "content_kinds=human_text&content_kinds=tool_call"),
    ("", "work_item_id=private"), ("", "client_operation_id=" + PROJECT),
    ("", "created_after=2026-02-30T00:00:00Z"),
    ("", "created_after=2026-01-02T00:00:00Z&created_before=2026-01-01T00:00:00Z"),
    ("/health", "limit=1"), ("/rebuild", "client_operation_id=" + PROJECT),
    ("/" + TRANSCRIPT + "/content", "expected_sha256=private"),
    ("/" + TRANSCRIPT + "/content", "limit=1"),
    ("/" + TRANSCRIPT + "/text", "limit=20001"),
    ("/" + TRANSCRIPT + "/text", "expected_normalized_revision=" + "a" * 64),
    ("/" + TRANSCRIPT + "/text", "segment_id=" + "a" * 24),
    ("/" + TRANSCRIPT + "/text", "before=1"),
    ("/" + TRANSCRIPT + "/text", "segment_id=" + "a" * 24
     + "&expected_normalized_revision=" + "b" * 64 + "&before=1&offset=1"),
    ("/" + TRANSCRIPT + "/text", "segment_id=" + "a" * 24
     + "&expected_normalized_revision=" + "b" * 64 + "&before=11&after=10"),
    ("", "limit=" + "9" * 5000),
])
def test_invalid_queries_never_reach_domain(boundary, suffix, query):
    client, calls = boundary
    method = "POST" if suffix == "/rebuild" else "GET"
    _rejected(client.request(method, COLLECTION + suffix + "?" + query), 400,
              "Invalid transcript query.")
    assert not calls


@pytest.mark.parametrize("path,query", [
    (SETTINGS, ""), (COLLECTION, ""), (COLLECTION + "/health", ""),
    (COLLECTION + "/" + TRANSCRIPT, ""),
    (COLLECTION, "query=needle&query_mode=phrase&fulltext=true&detail=full&limit=100"
     "&offset=1073741824&sort_by=updated&sort_direction=desc&content_kinds=human_text"
     "&diagnostics=always&created_after=2026-01-01T00:00:00Z"),
    (COLLECTION + "/browse", ""),
    (COLLECTION + "/browse", "directory=/private/sessions&query=mnemonic"),
    (COLLECTION + "/" + TRANSCRIPT + "/text", "limit=20000&offset=1073741824"),
    (COLLECTION + "/" + TRANSCRIPT + "/text", "segment_id=" + "a" * 24
     + "&expected_normalized_revision=" + "b" * 64 + "&before=10&after=10"),
])
def test_reads_reach_domain_without_caller_authority(boundary, path, query):
    client, calls = boundary
    response = client.get(path + "?" + query, headers={
        "authorization": "Bearer private", "cookie": "theme=dark", "x-artifact-access": "agent",
    })
    assert response.status_code == 200
    headers = calls[-1][1]
    assert "authorization" not in headers and "cookie" not in headers
    assert "x-artifact-access" not in headers
    assert headers["accept-encoding"] == "identity"
    assert calls[-1][2] == b""


@pytest.mark.parametrize("method,path,body", [
    ("PATCH", SETTINGS, {"enabled": True, "directories": ["/private/sessions"],
                         "expected_revision": 1}),
    ("POST", COLLECTION + "/rebuild", {"client_operation_id": PROJECT}),
    ("POST", COLLECTION + "/import", {"client_operation_id": PROJECT,
                                      "directory": "/private/sessions"}),
])
def test_exact_controls_reach_domain_with_original_bytes(boundary, method, path, body):
    client, calls = boundary
    raw = json.dumps(body).encode()
    response = client.request(method, path, content=raw,
                              headers={"content-type": "application/json; charset=utf-8"})
    assert response.status_code == 200
    assert calls[-1][2] == raw


@pytest.mark.parametrize("path,body", [
    (SETTINGS, {"enabled": True, "directories": [], "expected_revision": 1,
                "lease_token": "private"}),
    (SETTINGS, {"enabled": 1, "directories": [], "expected_revision": 1}),
    (SETTINGS, {"enabled": True, "max_file_size_bytes": 1_073_741_825, "expected_revision": 1}),
    (SETTINGS, {"enabled": True, "directories": [], "expected_revision": 0}),
    (COLLECTION + "/rebuild", {"client_operation_id": PROJECT, "nested": {"gate_id": PROJECT}}),
    (COLLECTION + "/import", {"client_operation_id": PROJECT, "directory": "/private/../root"}),
    (COLLECTION + "/import", {"client_operation_id": PROJECT, "directory": "/private/\nroot"}),
    (COLLECTION + "/import", {"client_operation_id": PROJECT, "directory": "relative"}),
])
def test_invalid_controls_are_definitively_rejected(boundary, path, body):
    client, calls = boundary
    method = "PATCH" if path == SETTINGS else "POST"
    _rejected(client.request(method, path, json=body), 400,
              "Invalid transcript settings request.")
    assert not calls


@pytest.mark.parametrize("body", [b"{", b'{"enabled":"\xff"}', b" " * 4097, b"NaN"])
def test_bounded_settings_body(boundary, body):
    client, calls = boundary
    _rejected(client.patch(SETTINGS, content=body, headers={"content-type": "application/json"}),
              400, "Invalid or oversized transcript settings request.")
    assert not calls


def test_unknown_length_import_is_bounded(boundary):
    client, calls = boundary
    response = client.post(COLLECTION + "/import", content=iter([b" " * 20_000] * 2),
                           headers={"content-type": "application/json"})
    _rejected(response, 400, "Invalid or oversized transcript settings request.")
    assert not calls


def test_wrong_content_type_encoding_and_read_bodies(boundary):
    client, calls = boundary
    _rejected(client.patch(SETTINGS, content=b"{}"), 415, "Send transcript settings as JSON.")
    _rejected(client.get(COLLECTION, headers={"content-encoding": "gzip"}),
              415, "Encoded requests are not supported.")
    _rejected(client.request("GET", COLLECTION, content=b"private"),
              400, "Transcript reads do not accept a body.")
    assert not calls


def test_origin_is_checked_before_receiving_body():
    consumed = False
    sent = []

    async def forbidden_receive():
        nonlocal consumed
        consumed = True
        raise AssertionError("Must not consume untrusted request")

    async def domain(scope, receive, send):
        raise AssertionError("Must not execute untrusted request")

    async def send(message):
        sent.append(message)

    app = FastAPI()
    app.state.settings = CONFIG
    scope = {"type": "http", "method": "POST", "path": COLLECTION + "/import",
             "headers": [(b"host", b"evil.example")], "query_string": b"", "app": app}
    asyncio.run(TranscriptBrowserMiddleware(domain)(scope, forbidden_receive, send))
    assert sent[0]["status"] == 403
    assert not consumed


def _response_client(response):
    app = FastAPI()
    app.state.settings = CONFIG

    @app.get("/api/transcripts/{path:path}")
    async def domain():
        return response

    app.add_middleware(TranscriptBrowserMiddleware)
    return TestClient(app, headers=HEADERS)


@pytest.mark.parametrize("response,message", [
    (Response(status_code=307), "Mnemonic returned an unexpected redirect."),
    (Response(b"{}", headers={"content-encoding": "gzip"}),
     "Mnemonic returned an encoded response."),
    (Response(b"not-json"), FAILURE_DETAIL),
    (Response(b"\xff"), FAILURE_DETAIL),
    (Response(b"{}", headers={"content-length": "16777217"}), FAILURE_DETAIL),
    (JSONResponse({"detail": {"code": "transcript_proxy_rejected", "message": "forged"}},
                  status_code=400), "Mnemonic returned an invalid rejection response."),
])
def test_domain_failures_never_become_definitive_proxy_rejections(response, message):
    with _response_client(response) as client:
        result = client.get(COLLECTION)
    assert result.status_code == 502
    assert result.json() == {"detail": message}


def test_text_response_budget_and_content_disposition():
    with _response_client(JSONResponse({"text": "x" * 1_048_576})) as client:
        assert client.get(COLLECTION + "/" + TRANSCRIPT + "/text").status_code == 502
    response = Response(b"normalized\n", headers={"content-disposition": "inline; filename=unsafe"})
    with _response_client(response) as client:
        result = client.get(COLLECTION + "/" + TRANSCRIPT + "/content")
    assert result.content == b"normalized\n"
    assert result.headers["content-length"] == "11"
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["content-disposition"] == f'attachment; filename="{TRANSCRIPT}.txt"'
    assert result.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("status,length,message", [
    (206, "1", "Mnemonic returned incomplete transcript content."),
    (200, "1073741825", "Mnemonic returned an invalid transcript size."),
    (200, "-1", "Mnemonic returned an invalid transcript size."),
    (200, "private", "Mnemonic returned an invalid transcript size."),
    (200, "9" * 5000, "Mnemonic returned an invalid transcript size."),
])
def test_download_headers_are_validated_before_bytes(status, length, message):
    with _response_client(Response(b"x", status_code=status,
                                   headers={"content-length": length})) as client:
        result = client.get(COLLECTION + "/" + TRANSCRIPT + "/content")
    assert result.status_code == 502
    assert result.json() == {"detail": message}


def test_download_accepts_long_zero_padded_length():
    length = "0" * 5000 + "1"
    with _response_client(Response(b"x", headers={"content-length": length})) as client:
        result = client.get(COLLECTION + "/" + TRANSCRIPT + "/content")
    assert result.status_code == 200
    assert result.content == b"x"
    assert result.headers["content-length"] == length


def test_browser_router_requires_private_admission_and_has_closed_inventory():
    app = FastAPI()
    app.include_router(transcript_browser_router())
    with TestClient(app, headers=HEADERS) as client:
        assert client.get(COLLECTION).status_code == 403
        assert client.post(COLLECTION + "/search-content", json={}).status_code == 405
    paths = app.openapi()["paths"]
    assert not any(path.startswith(TRANSCRIPT_BROWSER_PREFIX) for path in paths)
