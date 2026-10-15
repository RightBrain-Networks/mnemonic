"""Artifact browser admission and wire shape independent of the domain database."""

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient

from mnemonic_api.application.browser_artifacts import (
    ArtifactBrowserMiddleware,
    artifact_browser_router,
)
from mnemonic_api.application.browser_artifacts_policy import safe_artifact_disposition
from mnemonic_api.config import Settings

PROJECT = "00000000-0000-4000-8000-000000000001"
ARTIFACT = "00000000-0000-4000-8000-000000000002"
COLLECTION = f"/api/artifacts/projects/{PROJECT}/artifacts"
ITEM = COLLECTION + "/" + ARTIFACT
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}


@pytest.fixture
def boundary():
    app = FastAPI()
    app.state.settings = Settings(database_url="postgresql://localhost:1/unavailable",
                                  api_key="secret-" * 8)
    calls = []

    @app.api_route("/api/artifacts/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def domain(request: Request):
        calls.append((request.url.path, dict(request.headers), await request.body()))
        if request.url.path.endswith("/content") and request.method == "GET":
            return Response(b"file bytes", headers={
                "content-disposition": "attachment; filename*=UTF-8''safe%20name.txt",
                "etag": "secret", "x-artifact-revision": "2",
            })
        return JSONResponse({"ok": True}, headers={
            "x-client-operation-id": request.headers.get("x-client-operation-id", ""),
            "x-artifact-operation-replayed": "true", "set-cookie": "secret=hidden",
        })

    app.add_middleware(ArtifactBrowserMiddleware)
    with TestClient(app, headers=HEADERS) as client:
        yield client, calls


@pytest.mark.parametrize("method,path,headers,expected", [
    ("GET", COLLECTION, {"origin": "https://evil.example"}, 403),
    ("GET", ITEM + "/download-grants", {}, 404),
    ("GET", COLLECTION + "?limit=1&limit=2", {}, 400),
    ("GET", ITEM + "/text", {}, 400),
    ("GET", ITEM, {"x-client-operation-id": str(uuid4())}, 400),
    ("GET", ITEM, {"cookie": "lease_token=forbidden"}, 400),
    ("DELETE", ITEM, {}, 400),
    ("PUT", ITEM + "/content", {"x-client-operation-id": str(uuid4())}, 400),
    ("PATCH", ITEM, {"x-artifact-metadata": "{}"}, 400),
    ("GET", ITEM, {"content-encoding": "gzip"}, 415),
])
def test_prebody_rejections_do_not_execute_domain(boundary, method, path, headers, expected):
    client, calls = boundary
    result = client.request(method, path, headers=headers)
    assert result.status_code == expected, result.text
    assert result.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert not calls


def test_upload_preserves_raw_bytes_receipt_and_closed_headers(boundary):
    client, calls = boundary
    operation = str(uuid4())
    response = client.post(COLLECTION, content=b"raw\0\xffbytes", headers={
        "content-type": "application/octet-stream", "x-client-operation-id": operation,
        "x-artifact-metadata": json.dumps({"filename": "bytes.bin", "sensitive": True}),
        "authorization": "Bearer untrusted", "x-artifact-access": "human-dashboard",
        "x-artifact-download-grant": "private", "cookie": "session=private",
    })
    assert response.status_code == 200
    assert calls[0][2] == b"raw\0\xffbytes"
    assert set(calls[0][1]) == {"host", "accept-encoding", "content-type",
                               "x-artifact-metadata", "x-client-operation-id"}
    assert response.headers["x-client-operation-id"] == operation
    assert response.headers["x-artifact-operation-replayed"] == "true"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("length,status", [("0" * 5000 + "1", 200), ("9" * 5000, 413)])
def test_upload_length_validates_value_without_unbounded_integer_parse(boundary, length, status):
    client, calls = boundary
    response = client.post(COLLECTION, content=b"x", headers={
        "content-length": length, "content-type": "application/octet-stream",
        "x-client-operation-id": str(uuid4()),
        "x-artifact-metadata": '{"filename":"one-byte.bin"}',
    })
    assert response.status_code == status, response.text
    assert len(calls) == (1 if status == 200 else 0)


def test_download_sets_server_owned_human_context_and_safe_attachment(boundary):
    client, calls = boundary
    response = client.get(ITEM + "/content", headers={
        "x-artifact-access": "agent", "x-artifact-metadata": '{"approval_token":"forged"}',
        "x-artifact-download-grant": "forged", "authorization": "Bearer secret",
    })
    assert response.content == b"file bytes"
    assert calls[0][1]["x-artifact-access"] == "human-dashboard"
    assert "x-artifact-metadata" not in calls[0][1]
    assert "authorization" not in calls[0][1]
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-length"] == "10"
    assert response.headers["content-disposition"] == (
        'attachment; filename="artifact.bin"; filename*=UTF-8\'\'safe%20name.txt'
    )
    assert "etag" not in response.headers and "x-artifact-revision" not in response.headers


def test_json_update_requires_matching_operation_and_dashboard_actor(boundary):
    client, calls = boundary
    operation = str(uuid4())
    payload = {"client_operation_id": operation, "expected_revision": 1,
               "description": "updated", "actor_client": "dashboard"}
    headers = {"x-client-operation-id": operation}
    assert client.patch(ITEM, json={**payload, "actor_client": "agent"},
                        headers=headers).status_code == 400
    assert client.patch(ITEM, json={**payload, "client_operation_id": str(uuid4())},
                        headers=headers).status_code == 400
    assert client.patch(ITEM, json=payload, headers=headers).status_code == 200
    assert len(calls) == 1
    assert "x-client-operation-id" not in calls[0][1]


def test_search_rejects_controls_and_forwards_human_context(boundary):
    client, calls = boundary
    path = COLLECTION + "/search-content"
    assert client.post(path, json={"q": "topic", "approval_token": "forged"}).status_code == 400
    assert client.post(path, json={"q": "topic", "semantic": True}).status_code == 400
    assert client.post(path, json={"q": "topic", "fulltext": True}).status_code == 200
    assert calls[0][1]["x-artifact-access"] == "human-dashboard"


def test_router_cannot_execute_without_private_admission():
    app = FastAPI()
    app.include_router(artifact_browser_router())
    with TestClient(app, headers=HEADERS) as client:
        assert client.get("/api/artifacts/status").status_code == 403
    assert not any(path.startswith("/api/artifacts") for path in app.openapi()["paths"])


@pytest.mark.parametrize("value", [
    "attachment; filename*=UTF-8''..%2Fsecret", "attachment; filename*=UTF-8''bad%ZZ",
    "attachment; filename*=UTF-8''bad%C0", 'attachment; filename="bad\\name"',
])
def test_unsafe_disposition_uses_fixed_basename(value):
    assert safe_artifact_disposition(value).endswith("UTF-8''artifact.bin")
