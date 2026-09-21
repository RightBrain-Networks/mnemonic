"""Download capabilities never dispatch tools or disclose unverified upstream bytes."""

import hashlib
import json

import httpx
import pytest
from conftest import API_KEY, PROJECT_ID
from test_artifacts import ARTIFACT_ID

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import create_app

CONTENT = b"private-binary-body\x00\xff" * 5000
TOKEN = "g" * 43
INTENT = {"project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID, "revision": 1,
          "size_bytes": len(CONTENT), "sha256": hashlib.sha256(CONTENT).hexdigest()}


def headers():
    return {"Authorization": f"MnemonicDownload {TOKEN}",
            "X-Artifact-Download-Intent": json.dumps(INTENT)}


async def transfer(settings, *, supplied=None, path="/mcp", method="GET", corrupt=False):
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(200, headers={
            "Content-Type": "application/octet-stream", "Content-Length": str(len(CONTENT)),
            "ETag": f'"{INTENT["sha256"]}"', "X-Artifact-Revision": "1",
        }, stream=httpx.ByteStream(b"corrupt" if corrupt else CONTENT))

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(backend)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                base_url="http://localhost:8001") as client:
        result = await client.request(method, path, headers=supplied or headers())
    return result, calls


async def test_download_gateway_uses_fixed_api_route_and_verified_bytes(settings):
    result, calls = await transfer(settings)
    assert result.status_code == 200 and result.content == CONTENT
    assert result.headers["cache-control"] == "no-store"
    assert len(calls) == 1
    request = calls[0]
    assert request.url == (f"http://api:8000/api/v1/projects/{PROJECT_ID}"
                           f"/artifacts/{ARTIFACT_ID}/content?expected_revision=1")
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-artifact-download-grant"] == TOKEN
    assert "x-artifact-access" not in request.headers


@pytest.mark.parametrize("path,method", [("/mcp", "POST"), ("/other", "GET"),
                                         ("/api/v1/artifacts", "GET")])
async def test_download_token_cannot_dispatch_rpc_or_other_routes(settings, path, method):
    result, calls = await transfer(settings, path=path, method=method)
    assert result.status_code == 401 and not calls


@pytest.mark.parametrize("change", ["token", "intent", "host", "encoding", "duplicate"])
async def test_invalid_download_envelopes_do_not_reach_api(settings, change):
    supplied = headers()
    if change == "token":
        supplied["Authorization"] = "MnemonicDownload invalid"
    elif change == "intent":
        supplied["X-Artifact-Download-Intent"] = json.dumps({**INTENT, "url": "http://other"})
    elif change == "host":
        supplied["Host"] = "other"
    elif change == "encoding":
        supplied["Content-Encoding"] = "gzip"
    else:
        supplied["X-Artifact-Download-Intent"] = '{"project_id":"one","project_id":"two"}'
    result, calls = await transfer(settings, supplied=supplied)
    assert result.status_code in {400, 401, 421} and not calls


async def test_corrupt_upstream_never_publishes_bytes(settings):
    result, _ = await transfer(settings, corrupt=True)
    assert result.status_code == 502
    assert result.json()["detail"]["code"] == "artifact_download_integrity_error"
    assert "private-binary-body" not in result.text and API_KEY not in result.text
