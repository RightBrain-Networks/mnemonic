import base64
import hashlib
import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.artifact_transport import decode_content
from mnemonic_mcp.server import build_server

ARTIFACT_ID = "7953c722-de76-44b0-a6ab-e27d9a156e8d"
CONTENT = b"%PDF-1.7\nprivate document\x00\xff"


def artifact(**overrides):
    return {
        "id": ARTIFACT_ID, "project_id": PROJECT_ID, "filename": "private report.pdf",
        "description": "Local report", "revision": 1, "size_bytes": len(CONTENT),
        "sha256": hashlib.sha256(CONTENT).hexdigest(), "mime_type": "application/pdf",
        "created_by_agent_session_id": "test-session", "created_by_client": "test-agent",
        "originating_work_item_id": WORK_ID, "related_work_item_ids": [],
        "created_at": NOW, "modified_at": NOW, "deleted_at": None, "content_available": True,
        **overrides,
    }


def upload_arguments(**overrides):
    return {
        "project_id": PROJECT_ID, "client_operation_id": CLIENT_OPERATION_ID,
        "filename": "private report.pdf", "content_base64": base64.b64encode(CONTENT).decode(),
        "agent_session_id": "test-session", "actor_client": "test-agent",
        "description": "Local report", "work_item_id": WORK_ID, **overrides,
    }


async def call(settings, name, arguments, handler):
    def streamed(request):
        response = handler(request)
        if "X-Client-Operation-ID" in request.headers:
            response.headers["X-Client-Operation-ID"] = request.headers["X-Client-Operation-ID"]
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=httpx.ByteStream(response.content))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(streamed)))
    result = await server.call_tool(name, arguments)
    return result[1] if isinstance(result, tuple) else result


async def test_upload_raw_bytes_private_metadata_header_and_exact_receipt(settings):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=artifact())

    args = upload_arguments()
    assert await call(settings, "upload_artifact", args, handler) == artifact()
    assert await call(settings, "upload_artifact", args, handler) == artifact()
    assert calls[0].content == calls[1].content == CONTENT
    assert calls[0].headers == calls[1].headers
    assert calls[0].url.query == b""
    assert calls[0].headers["X-Client-Operation-ID"] == CLIENT_OPERATION_ID
    metadata = json.loads(calls[0].headers["X-Artifact-Metadata"])
    assert metadata["work_item_id"] == WORK_ID
    assert metadata["agent_session_id"] == "test-session"
    assert "content_base64" not in metadata


@pytest.mark.parametrize("value", ["%%%", "YQ", "YQ===", "YR==", "Y Q==", "☃"])
def test_base64_rejects_noncanonical_without_echo(value):
    with pytest.raises(ToolError, match="canonical base64"):
        decode_content(value)


async def test_replacement_pins_revision_and_keeps_omitted_metadata_out(settings):
    args = upload_arguments(artifact_id=ARTIFACT_ID, expected_revision=1)
    args.pop("description")
    args.pop("work_item_id")

    def handler(request):
        assert request.method == "PUT"
        assert request.url.path.endswith(f"/{ARTIFACT_ID}/content")
        assert request.headers["X-Artifact-Expected-Revision"] == "1"
        metadata = json.loads(request.headers["X-Artifact-Metadata"])
        assert "description" not in metadata and "related_work_item_ids" not in metadata
        return httpx.Response(200, json=artifact(revision=2))

    result = await call(settings, "replace_artifact", args, handler)
    assert result["revision"] == 2


@pytest.mark.parametrize("failure", ["timeout", "malformed", "wrong-project", "wrong-hash",
                                      "wrong-session", "wrong-description"])
async def test_upload_uncertainty_has_no_implicit_retry_or_content_echo(settings, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private diagnostic", request=request)
        if failure == "malformed":
            return httpx.Response(201, content=b"private diagnostic")
        changes = {"wrong-project": {"project_id": WORK_ID}, "wrong-hash": {"sha256": "0" * 64},
                   "wrong-session": {"created_by_agent_session_id": "someone-else"},
                   "wrong-description": {"description": "a different request"}}
        return httpx.Response(201, json=artifact(**changes[failure]))

    with pytest.raises(ToolError) as raised:
        await call(settings, "upload_artifact", upload_arguments(), handler)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(raised.value)
    assert len(calls) == 1
    assert "private diagnostic" not in str(raised.value)


async def test_download_pins_metadata_revision_and_verifies_binary_hash(settings):
    def handler(request):
        if request.url.path.endswith("/content"):
            assert request.url.params["expected_revision"] == "1"
            return httpx.Response(200, content=CONTENT)
        return httpx.Response(200, json=artifact())

    result = await call(settings, "download_artifact", {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
    }, handler)
    assert base64.b64decode(result["content_base64"]) == CONTENT
    assert result["artifact"] == artifact()


async def test_download_rejects_bytes_from_different_revision(settings):
    def handler(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"other bytes")
        return httpx.Response(200, json=artifact())

    with pytest.raises(ToolError, match="requested revision"):
        await call(settings, "download_artifact", {
            "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
        }, handler)


async def test_delete_returns_metadata_tombstone(settings):
    def handler(request):
        assert request.method == "DELETE"
        assert request.headers["X-Artifact-Expected-Revision"] == "1"
        return httpx.Response(200, json=artifact(deleted_at=NOW, content_available=False))

    result = await call(settings, "delete_artifact", {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
        "client_operation_id": CLIENT_OPERATION_ID, "expected_revision": 1,
        "agent_session_id": "test-session", "actor_client": "test-agent",
    }, handler)
    assert result["deleted_at"] == NOW


async def test_metadata_search_scopes_project_and_work(settings):
    def handler(request):
        assert request.url.params["q"] == "report"
        assert request.url.params["work_item_id"] == WORK_ID
        return httpx.Response(200, json={"items": [artifact()], "total": 1,
                                         "limit": 50, "offset": 0})

    result = await call(settings, "list_artifacts", {
        "project_id": PROJECT_ID, "q": "report", "work_item_id": WORK_ID,
    }, handler)
    assert result["items"] == [artifact()]


async def test_content_search_is_explicit_unimplemented_and_never_dispatches(settings):
    def handler(request):
        pytest.fail("Unimplemented content search must never read files")

    result = await call(settings, "search_artifact_contents", {
        "project_id": PROJECT_ID, "query": "private report",
    }, handler)
    assert result["status"] == "unimplemented"


async def test_artifact_catalog_annotations_and_required_receipt_provenance(settings):
    tools = {item.name: item for item in await build_server(settings).list_tools()}
    for name in ("upload_artifact", "replace_artifact", "delete_artifact"):
        required = tools[name].inputSchema["required"]
        assert {"client_operation_id", "agent_session_id", "actor_client"} <= set(required)
        assert tools[name].annotations.idempotentHint
        assert not tools[name].annotations.readOnlyHint
    assert tools["replace_artifact"].annotations.destructiveHint
    assert tools["delete_artifact"].annotations.destructiveHint
