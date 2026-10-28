"""Backup browser parity: pre-contact admission, worker isolation and raw responses."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mnemonic_api.application.browser_backups import BackupBrowserMiddleware
from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

PROJECT = "00000000-0000-4000-8000-000000000001"
JOB = "00000000-0000-4000-8000-000000000002"
ROOT = f"/api/backups/projects/{PROJECT}"
COLLECTION = ROOT + "/backups"
DOWNLOAD = COLLECTION + "/archive.bz2"
RESTORE = ROOT + "/restore"
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}
UPLOAD = {"content-type": "application/octet-stream", "x-confirm-project": PROJECT}
TOKEN = "private-backup-" * 4


class RawStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def upstream(body=b'{"ok":true}', *, status=200, headers=None):
    return httpx.Response(status, headers={"content-type": "application/json", **(headers or {})},
                          stream=RawStream([body]))


def make_app(handler, **overrides):
    app = FastAPI()
    app.state.settings = Settings(
        database_url="postgresql://localhost:1/unavailable", api_key="private-" * 8,
        **{"backup_token": TOKEN, "backup_max_bytes": 16, **overrides})
    app.state.backup_client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                               trust_env=False, timeout=None)
    app.add_middleware(BackupBrowserMiddleware)
    return app


@pytest.fixture
def boundary():
    calls = []

    async def handler(request):
        calls.append((request, await request.aread()))
        return upstream()

    app = make_app(handler)
    with TestClient(app, headers=HEADERS) as client:
        yield client, calls, app


@pytest.mark.parametrize("method,path,headers,body,status", [
    ("POST", COLLECTION, {"origin": "https://evil.test"}, b"", 403),
    ("GET", COLLECTION, {"sec-fetch-site": "same-site"}, b"", 403),
    ("GET", COLLECTION, {"host": "evil.test", "x-forwarded-host": "localhost:3000"}, b"", 403),
    ("GET", COLLECTION, [("host", "localhost:3000"), ("host", "evil.test")], b"", 403),
    ("DELETE", COLLECTION, {}, b"", 404),
    ("GET", ROOT + "/backup-jobs/not-a-uuid", {}, b"", 404),
    ("GET", COLLECTION + "/../private.bz2", {}, b"", 404),
    ("GET", COLLECTION + "/archive.zip", {}, b"", 404),
    ("GET", COLLECTION + "/archive.bz2/extra", {}, b"", 404),
    ("GET", COLLECTION.replace("/projects/", "/projects%2f"), {}, b"", 404),
    ("GET", COLLECTION + "?token=private", {}, b"", 400),
    ("GET", COLLECTION, {"x-lease-token": "private"}, b"", 400),
    ("GET", COLLECTION, {"cookie": "theme=dark; gate_id=private"}, b"", 400),
    ("POST", RESTORE, {**UPLOAD, "x-confirm-project": JOB}, b"BZh", 400),
    ("POST", RESTORE, {**UPLOAD, "content-length": "17"}, b"BZh", 413),
    ("POST", RESTORE, {**UPLOAD, "content-length": "-1"}, b"BZh", 400),
    ("POST", RESTORE, {**UPLOAD, "content-length": "9007199254740992"}, b"BZh", 400),
    ("POST", RESTORE, {**UPLOAD, "content-length": "0"}, b"", 400),
    ("POST", RESTORE, {**UPLOAD, "content-type": "text/plain"}, b"BZh", 415),
    ("POST", RESTORE, {**UPLOAD, "content-encoding": "gzip"}, b"BZh", 415),
    ("POST", COLLECTION, {}, b"unexpected", 400),
    ("GET", COLLECTION, {}, b"unexpected", 400),
])
def test_admission_never_contacts_worker(boundary, method, path, headers, body, status):
    client, calls, _ = boundary
    response = client.request(method, path, headers=headers, content=body)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store, max-age=0, no-transform"
    assert response.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert not calls


@pytest.mark.parametrize("method,path", [
    ("GET", COLLECTION), ("POST", COLLECTION), ("GET", ROOT + "/backup-jobs/" + JOB),
])
def test_closed_actions_reach_worker_with_only_service_authority(boundary, method, path):
    client, calls, _ = boundary
    response = client.request(method, path, headers={"authorization": "Bearer browser-secret",
                                                     "cookie": "session=private"})
    assert response.status_code == 200
    request, body = calls[-1]
    assert str(request.url) == "http://worker:8002" + path.removeprefix("/api/backups")
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert request.headers["accept-encoding"] == "identity"
    assert "cookie" not in request.headers
    assert "origin" not in request.headers
    assert body == b""


def test_missing_origin_only_allows_get(boundary):
    client, calls, _ = boundary
    del client.headers["origin"]
    assert client.get(COLLECTION).status_code == 200
    assert client.post(COLLECTION).status_code == 403
    assert len(calls) == 1


@pytest.mark.parametrize("settings", [
    {"backup_token": None}, {"backup_token": "short"},
    {"backup_url": "file:///private"}, {"backup_url": "http://user:password@worker:8002"},
    {"backup_url": "http://worker:8002/path"}, {"backup_url": "http://worker:8002?private=1"},
])
def test_invalid_worker_configuration_is_closed_503(settings):
    def handler(request):
        raise AssertionError("worker must not be contacted")

    with TestClient(make_app(handler, **settings), headers=HEADERS) as client:
        assert client.get(COLLECTION).status_code == 503


def test_restore_stream_is_byte_exact_and_drops_caller_length(boundary):
    client, calls, _ = boundary
    body = b"BZh\x00\xff\x00"
    assert client.post(RESTORE, headers=UPLOAD, content=body).status_code == 200
    request, received = calls[-1]
    assert received == body
    assert request.headers["x-confirm-project"] == PROJECT
    assert request.headers["content-type"] == "application/octet-stream"
    assert "content-length" not in request.headers


def test_unknown_length_restore_enforces_inclusive_compressed_limit(boundary):
    client, calls, _ = boundary
    assert client.post(RESTORE, headers=UPLOAD, content=iter([b"x" * 8] * 2)).status_code == 200
    assert calls[-1][1] == b"x" * 16
    over = client.post(RESTORE, headers=UPLOAD, content=iter([b"x" * 8, b"x" * 9]))
    assert over.status_code == 413
    assert len(calls) == 1


def test_download_keeps_raw_bytes_safe_filename_and_security_headers():
    response = upstream(b"BZh\x00\xff", headers={"content-length": "5", "content-type": "text/html",
        "content-disposition": "inline; filename=unsafe.html", "set-cookie": "worker=private"})
    with TestClient(make_app(lambda _: response), headers=HEADERS) as client:
        result = client.get(DOWNLOAD)
    assert result.content == b"BZh\x00\xff"
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["content-disposition"] == 'attachment; filename="archive.bz2"'
    assert result.headers["content-length"] == "5"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert result.headers["cross-origin-resource-policy"] == "same-origin"
    assert "set-cookie" not in result.headers
    assert response.is_closed


@pytest.mark.parametrize("status,headers,body", [
    (302, {"location": "https://evil.test"}, b"redirect poison"),
    (200, {"content-encoding": "gzip"}, b"encoded poison"),
    (500, {"content-encoding": "gzip"}, b"encoded poison"),
    (200, {"content-type": "text/html"}, b"html poison"),
    (200, {}, b"{"), (200, {}, b'{"private":"\xff"}'),
    (200, {}, b"NaN"), (200, {"content-length": "4194305"}, b"{}"),
    (200, {}, b" " * 4_194_305),
    (204, {}, b"{}"), (205, {}, b"{}"),
])
def test_bad_worker_response_is_closed_and_never_retried(status, headers, body):
    calls = []
    response = upstream(body, status=status, headers=headers)

    def handler(request):
        calls.append(request)
        return response

    with TestClient(make_app(handler), headers=HEADERS) as client:
        result = client.post(COLLECTION)
    assert result.status_code == 502
    assert "poison" not in result.text
    assert len(calls) == 1
    assert response.is_closed


def test_json_limit_is_inclusive_and_definitive_service_error_bytes_are_preserved():
    body = b'{"error":{"code":"backup_busy","message":"Busy"}}'
    body += b" " * (4_194_304 - len(body))
    response = httpx.Response(409, headers={"content-type": "application/json"},
                              stream=RawStream([body[:2_000_000], body[2_000_000:]]))
    with TestClient(make_app(lambda _: response), headers=HEADERS) as client:
        result = client.post(COLLECTION)
    assert result.status_code == 409
    assert result.content == body


def test_worker_cookie_is_not_reused_on_another_request():
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        return upstream(headers={"set-cookie": "worker=private; Path=/"})

    with TestClient(make_app(handler), headers=HEADERS) as client:
        assert client.get(COLLECTION).status_code == 200
        assert client.get(COLLECTION).status_code == 200
    assert seen == [None, None]


@pytest.mark.parametrize("status,length", [
    (206, "3"), (200, None), (200, "0"), (200, "-1"), (200, "bad"),
    (200, "9007199254740992"),
])
def test_invalid_archive_headers_fail_before_start(status, length):
    headers = {"content-length": length} if length is not None else {}
    with TestClient(make_app(lambda _: upstream(b"BZh", status=status, headers=headers)),
                    headers=HEADERS) as client:
        response = client.get(DOWNLOAD)
    assert response.status_code == 502
    assert response.json() == {"detail": "The backup service returned an invalid archive response."}


def test_download_size_is_independent_of_restore_limit():
    with TestClient(make_app(lambda _: upstream(b"x" * 32, headers={"content-length": "32"})),
                    headers=HEADERS) as client:
        response = client.get(DOWNLOAD)
    assert response.status_code == 200
    assert response.content == b"x" * 32


def test_actual_application_owns_gateway_and_keeps_machine_auth_separate(tmp_path):
    app = create_app(Settings(
        database_url="postgresql://localhost:1/unavailable", api_key="private-" * 8,
        artifact_max_bytes=0, transcript_index_dir=tmp_path / "index", backup_token=TOKEN))
    asyncio.run(app.state.backup_client.aclose())
    app.state.backup_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: upstream()))
    with TestClient(app, headers=HEADERS) as client:
        assert client.get(COLLECTION).json() == {"ok": True}
        assert client.get("/api/v1/projects").status_code == 401
        assert client.get(f"/api/mnemonic/projects/{PROJECT}/backups").status_code == 404
        assert not any(path.startswith("/api/backups") for path in app.openapi()["paths"])
    assert app.state.backup_client.is_closed


@pytest.mark.parametrize("suffix,method,payload", [
    ("/backup-settings", "PUT",
     b'{"enabled":false,"time":"02:00","timezone":"UTC","retention_days":7}'),
])
def test_json_backup_actions_forward_bounded_body(boundary, suffix, method, payload):
    client, calls, _ = boundary
    response = client.request(method, ROOT + suffix, content=payload,
                              headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert calls[-1][1] == payload
    assert calls[-1][0].headers["content-type"] == "application/json"


def test_backup_delete_is_admitted_without_body(boundary):
    client, calls, _ = boundary
    assert client.delete(DOWNLOAD).status_code == 200
    assert calls[-1][0].method == "DELETE"
    assert calls[-1][1] == b""


def test_stored_restore_requires_confirmation(boundary):
    client, calls, _ = boundary
    assert client.post(DOWNLOAD + "/restore").status_code == 400
    assert not calls
    response = client.post(DOWNLOAD + "/restore", headers={"x-confirm-project": PROJECT})
    assert response.status_code == 200
    assert calls[-1][0].headers["x-confirm-project"] == PROJECT
    assert calls[-1][1] == b""


def test_selection_rejects_large_body_before_worker_contact(boundary):
    client, calls, _ = boundary
    response = client.post(ROOT + "/exports", content=b" " * (128 * 1024 + 1),
                           headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert not calls


def test_export_stream_has_fixed_download_name():
    async def handler(request):
        assert await request.aread() == b'{"domain_ids":["' + PROJECT.encode() + b'"]}'
        return upstream(b"BZh-data", headers={"content-length": "8"})
    with TestClient(make_app(handler), headers=HEADERS) as client:
        result = client.post(ROOT + "/exports", json={"domain_ids": [PROJECT]})
        assert result.status_code == 200
        assert result.content == b"BZh-data"
        assert result.headers["content-disposition"] == (
            'attachment; filename="selected-assets.tar.bz2"')
