"""Adversarial capability tests; no API credential or file payload in the agent process."""

import copy
import hashlib
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid5

import httpx
import pytest
from conftest import API_KEY, CLIENT_OPERATION_ID, NOW, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from starlette.requests import Request

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server, create_app
from mnemonic_mcp.upload_grants import (
    UploadIntent,
    canonical,
    issue_token,
    signature,
    upload_url,
)

CONTENT = b"private-file-sentinel\x00\xff" * 4000


def intent(**changes):
    return UploadIntent.model_validate(
        {
            "project_id": PROJECT_ID,
            "client_operation_id": CLIENT_OPERATION_ID,
            "artifact_id": None,
            "expected_revision": None,
            "size_bytes": len(CONTENT),
            "sha256": hashlib.sha256(CONTENT).hexdigest(),
            "metadata": {
                "filename": "report.bin",
                "actor_client": "pytest",
                "agent_session_id": "upload-session",
                "description": "Résumé",
            },
            **changes,
        }
    )


def receipt(request, uploaded):
    data = uploaded.metadata.model_dump(mode="json", exclude_unset=True)
    target = uploaded.artifact_id or uuid5(
        uploaded.project_id, f"artifact:{uploaded.client_operation_id}"
    )
    return httpx.Response(
        201 if uploaded.artifact_id is None else 200,
        json={
            "id": str(target),
            "project_id": str(uploaded.project_id),
            "filename": data["filename"],
            "description": data.get("description", "preserved"),
            "revision": (uploaded.expected_revision or 0) + 1,
            "size_bytes": uploaded.size_bytes,
            "sha256": uploaded.sha256,
            "mime_type": "application/octet-stream",
            "created_by_agent_session_id": "upload-session",
            "created_by_client": "pytest",
            "originating_work_item_id": None,
            "related_work_item_ids": [],
            "related_artifact_ids": [],
            "sensitive": False,
            "content_available": True,
            "created_at": NOW,
            "modified_at": NOW,
            "deleted_at": None,
            "extraction": {
                "status": "pending",
                "metadata": {},
                "truncated": False,
                "error_code": None,
                "extracted_at": None,
            },
        },
        headers={
            "X-Client-Operation-ID": str(uploaded.client_operation_id),
            "X-Artifact-Operation-Replayed": "false",
        },
    )


def headers(uploaded, token=None):
    token = token or issue_token(uploaded, API_KEY)[0]
    return {
        "Authorization": f"MnemonicUpload {token}",
        "X-Artifact-Upload-Intent": canonical(uploaded),
        "Content-Type": "application/octet-stream",
        "Content-Length": str(uploaded.size_bytes),
    }


async def send(settings, uploaded, *, body=CONTENT, supplied=None, path="/mcp", handler=None):
    calls = []

    async def backend(request):
        await request.aread()
        calls.append(request)
        result = handler(request) if handler else receipt(request, uploaded)
        return httpx.Response(
            result.status_code, headers=result.headers, stream=httpx.ByteStream(result.content)
        )

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(backend)))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8001"
    ) as client:
        wire_headers = [
            (key.encode(), value.encode("latin1"))
            for key, value in (supplied or headers(uploaded)).items()
        ]
        response = await client.post(path, headers=wire_headers, content=body)
    return response, calls


async def test_raw_transfer_uses_only_frozen_scope_and_validates_receipt(settings):
    uploaded = intent()
    response, calls = await send(settings, uploaded)
    assert response.status_code == 201, response.text
    assert len(calls) == 1
    request = calls[0]
    assert request.url == f"http://api:8000/api/v1/projects/{PROJECT_ID}/artifacts"
    assert request.content == CONTENT
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-client-operation-id"] == CLIENT_OPERATION_ID
    assert json.loads(request.headers["x-artifact-metadata"]) == json.loads(
        canonical(uploaded.metadata)
    )
    assert "x-artifact-upload-intent" not in request.headers
    assert API_KEY not in response.text and "private-file-sentinel" not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_replacement_preserves_omission_and_expected_revision(settings):
    uploaded = intent(
        artifact_id="d22a17c3-2870-478c-a1ea-f437f6da521d",
        expected_revision=7,
        metadata={
            "filename": "report.bin",
            "actor_client": "pytest",
            "agent_session_id": "upload-session",
        },
    )
    response, calls = await send(settings, uploaded)
    assert response.status_code == 200, response.text
    assert calls[0].method == "PUT"
    assert calls[0].url.path.endswith(f"/{uploaded.artifact_id}/content")
    assert calls[0].headers["x-artifact-expected-revision"] == "7"
    assert "description" not in json.loads(calls[0].headers["x-artifact-metadata"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", "dda23738-3e3f-4d98-998d-613335408c36"),
        ("client_operation_id", "dda23738-3e3f-4d98-998d-613335408c36"),
        ("size_bytes", 1),
        ("sha256", "f" * 64),
        (
            "metadata",
            {"filename": "other.bin", "actor_client": "pytest", "agent_session_id": "other"},
        ),
    ],
)
async def test_grants_reject_scope_tampering_before_reading_body(settings, field, value):
    original = intent()
    altered = original.model_dump(mode="json", exclude_unset=True)
    altered[field] = value
    granted = headers(original)
    granted["X-Artifact-Upload-Intent"] = json.dumps(altered)
    read = False

    async def content():
        nonlocal read
        read = True
        yield CONTENT

    response, calls = await send(settings, original, body=content(), supplied=granted)
    assert response.status_code == 401
    assert not read and not calls


@pytest.mark.parametrize(
    "token",
    [
        "v1.0." + "a" * 64,
        "v1.9999999999999." + "a" * 64,
        "v1.9999999999." + "é" * 64,
        "Bearer " + API_KEY,
        "bad",
    ],
)
async def test_malformed_or_expired_credentials_never_reach_api(settings, token):
    response, calls = await send(settings, intent(), supplied=headers(intent(), token))
    assert response.status_code == 401
    assert not calls and token not in response.text


async def test_correctly_signed_expired_ticket_is_rejected(settings):
    uploaded = intent()
    expired = int(time.time()) - 1
    token = f"v1.{expired}.{signature(uploaded, expired, API_KEY)}"
    response, calls = await send(settings, uploaded, supplied=headers(uploaded, token))
    assert response.status_code == 401 and not calls


@pytest.mark.parametrize(
    "body,status", [(b"x" * len(CONTENT), 422), (CONTENT[:-1], 422), (CONTENT + b"x", 413)]
)
async def test_byte_mismatch_never_starts_a_mutation(settings, body, status):
    response, calls = await send(settings, intent(), body=body)
    assert response.status_code == status and not calls


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/projects", "/mcp/tools/call"])
async def test_grant_does_not_authenticate_other_routes(settings, path):
    response, calls = await send(settings, intent(), path=path)
    assert response.status_code == 401 and not calls


@pytest.mark.parametrize(
    "override",
    [
        {"Content-Type": "application/json"},
        {"X-Client-Operation-ID": CLIENT_OPERATION_ID},
        {"X-Artifact-Metadata": "{}"},
        {"Content-Encoding": "gzip"},
        {"Content-Length": "0"},
        {"Transfer-Encoding": "chunked"},
        {"Origin": "https://evil.example"},
        {"Host": "evil.example"},
    ],
)
async def test_grant_cannot_override_transport_or_bypass_host_origin(settings, override):
    supplied = {**headers(intent()), **override}
    response, calls = await send(settings, intent(), supplied=supplied)
    assert response.status_code in {400, 403, 415, 421} and not calls


async def test_lost_or_unverifiable_upstream_receipt_is_unknown(settings):
    def fail(request):
        raise httpx.ReadError("private upstream diagnostic", request=request)

    response, calls = await send(settings, intent(), handler=fail)
    assert response.status_code == 502 and len(calls) == 1
    assert response.json()["detail"]["code"] == "artifact_upload_outcome_unknown"
    assert "private upstream" not in response.text

    def corrupted(request):
        result = receipt(request, intent())
        document = result.json()
        document["sha256"] = "0" * 64
        return httpx.Response(201, json=document, headers=result.headers)

    response, calls = await send(settings, intent(), handler=corrupted)
    assert response.status_code == 502 and len(calls) == 1


async def test_authorize_is_stateless_and_retains_exact_omissions(settings):
    seen = []

    def backend(request):
        seen.append(request)
        body = json.dumps({"enabled": True, "max_bytes": 1, "message": "configured"}).encode()
        return httpx.Response(
            200, headers={"Content-Type": "application/json"}, stream=httpx.ByteStream(body)
        )

    configured = replace(settings, public_url="https://mnemonic.example/service/mcp")
    server = build_server(configured, MnemonicAPI(configured, httpx.MockTransport(backend)))
    uploaded = intent()
    result = await server.call_tool(
        "authorize_artifact_upload", {"intent": json.loads(canonical(uploaded))}
    )
    grant = result[1]
    assert grant["intent"] == json.loads(canonical(uploaded))
    assert grant["upload_url"] == configured.public_url
    assert grant["upload_token"].startswith("v1.")
    assert API_KEY not in json.dumps(grant)
    assert [request.method for request in seen] == ["GET"]
    assert seen[0].url.path == "/api/v1/artifacts/status"
    # A lowered positive maximum must not prevent authorizing exact old receipt replay.
    assert grant["artifact_library"]["max_bytes"] == 1


def test_observed_endpoint_and_proxy_configuration(settings):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "scheme": "http",
            "query_string": b"",
            "headers": [(b"host", b"localhost:8001")],
        }
    )
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))
    assert upload_url(MnemonicAPI(settings), ctx) == "http://localhost:8001/mcp"
    request.scope["headers"].append((b"x-forwarded-proto", b"https"))
    request = Request(copy.deepcopy(request.scope))
    ctx.request_context.request = request
    with pytest.raises(ToolError, match="reverse-proxied"):
        upload_url(MnemonicAPI(settings), ctx)
    public = replace(settings, public_url="https://mnemonic.example/prefix/mcp")
    assert upload_url(MnemonicAPI(public), ctx) == public.public_url
