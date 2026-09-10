"""Historical receipt parsing is permanent; fresh artifact wire contracts stay strict."""

import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import ARTIFACT_ID, artifact, upload_arguments

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.server import build_server


async def run_response(settings, name, arguments, response):
    def handler(request):
        current = response
        if request.url.path.endswith("/artifacts/status"):
            current = httpx.Response(200, json={
                "enabled": True, "max_bytes": 67108864, "message": "API",
            })
        return httpx.Response(current.status_code, headers=current.headers,
                              stream=httpx.ByteStream(current.content))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = await server.call_tool(name, arguments)
    return result[1]


@pytest.mark.parametrize("replay", [None, "false", "true", "True", "true, true"])
async def test_missing_artifact_fields_parse_only_in_explicit_permanent_receipt_replay(settings, replay):
    payload = artifact()
    payload.pop("sensitive")
    payload.pop("related_artifact_ids")
    headers = {"X-Client-Operation-ID": CLIENT_OPERATION_ID}
    if replay is not None:
        headers["X-Artifact-Operation-Replayed"] = replay
    response = httpx.Response(201, json=payload, headers=headers)
    if replay == "true":
        result = await run_response(settings, "upload_artifact", upload_arguments(), response)
        assert result["sensitive"] is False and result["related_artifact_ids"] == []
    else:
        with pytest.raises(ToolError, match="unknown outcome"):
            await run_response(settings, "upload_artifact", upload_arguments(), response)


@pytest.mark.parametrize("change", ["null-sensitive", "wrong-links", "duplicate-json"])
async def test_historical_replay_never_repairs_supplied_invalid_values_or_duplicate_keys(settings, change):
    payload = artifact()
    if change == "null-sensitive":
        payload["sensitive"] = None
    elif change == "wrong-links":
        payload["related_artifact_ids"] = "not-links"
    content = json.dumps(payload)
    if change == "duplicate-json":
        content = content[:-1] + ',"sensitive":false}'
    response = httpx.Response(201, content=content, headers={
        "Content-Type": "application/json", "X-Client-Operation-ID": CLIENT_OPERATION_ID,
        "X-Artifact-Operation-Replayed": "true",
    })
    with pytest.raises(ToolError, match="unknown outcome"):
        await run_response(settings, "upload_artifact", upload_arguments(), response)


@pytest.mark.parametrize("failure", ["missing-echo", "wrong-echo", "unavailable", "legacy-patch"])
async def test_metadata_update_rejects_unbound_or_incoherent_success(settings, failure):
    headers = {"X-Client-Operation-ID": CLIENT_OPERATION_ID}
    payload = artifact(revision=2, sensitive=True)
    if failure == "missing-echo":
        headers.clear()
    elif failure == "wrong-echo":
        headers["X-Client-Operation-ID"] = ARTIFACT_ID
    elif failure == "unavailable":
        payload["content_available"] = False
    else:
        headers["X-Artifact-Operation-Replayed"] = "true"
        payload.pop("sensitive")
        payload.pop("related_artifact_ids")
    with pytest.raises(ToolError) as raised:
        await run_response(settings, "update_artifact", {
            "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
            "client_operation_id": CLIENT_OPERATION_ID, "expected_revision": 1,
            "agent_session_id": "test-session", "actor_client": "test-agent", "sensitive": True,
        }, httpx.Response(200, json=payload, headers=headers))
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(raised.value)
