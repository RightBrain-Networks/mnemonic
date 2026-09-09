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
        "extraction": {"status": "pending", "metadata": {}, "truncated": False,
                       "error_code": None, "extracted_at": None},
        **overrides,
    }


def artifact_summary(**overrides):
    return {
        "id": ARTIFACT_ID, "project_id": PROJECT_ID, "filename": "private report.pdf",
        "revision": 1, "size_bytes": len(CONTENT),
        "sha256": hashlib.sha256(CONTENT).hexdigest(), "mime_type": "application/pdf",
        "deleted_at": None, "content_available": True,
        "extraction": {"status": "pending", "truncated": False,
                       "error_code": None, "extracted_at": None},
        **overrides,
    }


def upload_arguments(**overrides):
    return {
        "project_id": PROJECT_ID, "client_operation_id": CLIENT_OPERATION_ID,
        "filename": "private report.pdf", "content_base64": base64.b64encode(CONTENT).decode(),
        "agent_session_id": "test-session", "actor_client": "test-agent",
        "description": "Local report", "work_item_id": WORK_ID, **overrides,
    }


def download_arguments(**overrides):
    return {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
        "agent_session_id": "downloader-session", "actor_client": "downloader-client",
        **overrides,
    }


async def call(settings, name, arguments, handler, *, maximum=67108864, status_response=None):
    def streamed(request):
        if request.url.path == "/api/v1/artifacts/status":
            response = status_response if status_response is not None else httpx.Response(
                200, json={"enabled": maximum > 0, "max_bytes": maximum,
                           "message": "API policy"},
            )
        else:
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
    first = await call(settings, "upload_artifact", args, handler)
    assert first.pop("artifact_library")["max_bytes"] == 67108864
    assert first == artifact()
    second = await call(settings, "upload_artifact", args, handler)
    assert second.pop("artifact_library")["max_bytes"] == 67108864
    assert second == artifact()
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

    result = await call(settings, "download_artifact", download_arguments(), handler)
    assert base64.b64decode(result["content_base64"]) == CONTENT
    assert result["artifact"] == artifact_summary()


async def test_download_rejects_bytes_from_different_revision(settings):
    def handler(request):
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"other bytes")
        return httpx.Response(200, json=artifact())

    with pytest.raises(ToolError, match="requested revision"):
        await call(settings, "download_artifact", download_arguments(), handler)


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


def search_page(**overrides):
    return {"items": [], "total": 0, "limit": 50, "offset": 0, "fulltext": False,
            "indexing": {"ready": 0, "pending": 0, "failed": 0, "truncated": 0}, **overrides}


@pytest.mark.parametrize("fulltext", [False, True])
async def test_content_search_is_opt_in_safe_project_scoped_and_bounded(settings, fulltext):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/artifacts/search-content"
        assert "x-client-operation-id" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "q": "private report", "fulltext": fulltext, "include_deleted": False,
            "artifact_id": ARTIFACT_ID, "work_item_id": WORK_ID, "limit": 10, "offset": 0,
        }
        return httpx.Response(200, json=search_page(
            items=[{"artifact": artifact(), "score": 1.2,
                    "snippet": "<script>untrusted content</script>" if fulltext else None,
                    "matched_fields": ["content"] if fulltext else ["metadata"]}],
            total=1, limit=10, fulltext=fulltext,
        ))

    result = await call(settings, "search_artifact_contents", {
        "project_id": PROJECT_ID, "query": "private report", "fulltext": fulltext,
        "artifact_id": ARTIFACT_ID, "work_item_id": WORK_ID, "limit": 10,
    }, handler)
    assert result["fulltext"] is fulltext
    assert result["items"][0]["artifact"] == artifact_summary()
    assert result["items"][0]["matched_fields"] == (["content"] if fulltext else ["metadata"])


async def test_artifact_catalog_annotations_and_required_receipt_provenance(settings):
    tools = {item.name: item for item in await build_server(settings).list_tools()}
    for name in ("upload_artifact", "replace_artifact", "delete_artifact"):
        required = tools[name].inputSchema["required"]
        assert {"client_operation_id", "agent_session_id", "actor_client"} <= set(required)
        assert tools[name].annotations.idempotentHint
        assert not tools[name].annotations.readOnlyHint
    assert tools["replace_artifact"].annotations.destructiveHint
    assert tools["delete_artifact"].annotations.destructiveHint


def artifact_calls():
    identity = {"project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID}
    delete = {**identity, "client_operation_id": CLIENT_OPERATION_ID, "expected_revision": 1,
              "agent_session_id": "test-session", "actor_client": "test-agent"}
    return [
        ("list_artifacts", {"project_id": PROJECT_ID}),
        ("get_artifact", identity), ("list_artifact_history", identity),
        ("download_artifact", download_arguments()),
        ("search_artifact_contents", {"project_id": PROJECT_ID, "query": "report"}),
        ("upload_artifact", upload_arguments()),
        ("replace_artifact", upload_arguments(artifact_id=ARTIFACT_ID, expected_revision=1)),
        ("delete_artifact", delete),
    ]


@pytest.mark.parametrize(("name", "arguments"), artifact_calls())
async def test_disabled_status_stops_every_artifact_tool_before_data_access(settings, name, arguments):
    with pytest.raises(ToolError) as raised:
        await call(settings, name, arguments,
                   lambda request: pytest.fail("Disabled tools must not access artifacts"), maximum=0)
    message = str(raised.value)
    assert "disabled" in message and "MNEMONIC_ARTIFACT_MAX_BYTES=0" in message
    assert "max_bytes=0" in message and "retained" in message
    assert "original operation UUID" in message and "unknown write" in message


@pytest.mark.parametrize(("name", "arguments"), artifact_calls())
async def test_every_tool_reports_actual_configured_limit(settings, name, arguments):
    page = {"items": [], "total": 0, "limit": 50, "offset": 0}

    def handler(request):
        if request.url.path.endswith("/search-content"):
            return httpx.Response(200, json=search_page())
        if request.method == "POST":
            return httpx.Response(201, json=artifact())
        if request.method == "PUT":
            return httpx.Response(200, json=artifact(revision=2))
        if request.method == "DELETE":
            return httpx.Response(200, json=artifact(deleted_at=NOW, content_available=False))
        if request.url.path.endswith("/history"):
            return httpx.Response(200, json={"revisions": page, "audit": page})
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=CONTENT)
        if request.url.path.endswith("/artifacts"):
            return httpx.Response(200, json=page)
        return httpx.Response(200, json=artifact())

    result = await call(settings, name, arguments, handler, maximum=1048576)
    status = result["artifact_library"]
    assert status["enabled"] is True
    assert status["max_bytes"] == status["effective_upload_max_bytes"] == 1048576
    assert status["mcp_transfer_max_bytes"] == 67108864
    assert "1048576 bytes" in status["message"]


@pytest.mark.parametrize("status", [
    {"enabled": True, "max_bytes": 0, "message": "secret upstream data"},
    {"enabled": False, "max_bytes": 2, "message": "secret upstream data"},
    {"enabled": True, "max_bytes": True, "message": "secret upstream data"},
    {"enabled": True, "max_bytes": 1073741825, "message": "secret upstream data"},
    {"enabled": True, "max_bytes": "12", "message": "secret upstream data"},
])
async def test_invalid_policy_fails_closed_without_echo_or_artifact_dispatch(settings, status):
    with pytest.raises(ToolError) as raised:
        await call(settings, "upload_artifact", upload_arguments(),
                   lambda request: pytest.fail("Must validate policy before dispatch"),
                   status_response=httpx.Response(200, json=status))
    assert "Could not determine artifact-library status" in str(raised.value)
    assert "no artifact operation was sent" in str(raised.value)
    assert "secret upstream data" not in str(raised.value)


async def test_limit_error_reports_authoritative_raced_limit_without_echo(settings):
    def handler(request):
        return httpx.Response(413, json={"detail": {
            "code": "artifact_too_large", "message": "secret upstream data",
            "context": {"max_bytes": 12},
        }})

    with pytest.raises(ToolError) as raised:
        await call(settings, "upload_artifact", upload_arguments(), handler, maximum=1024)
    assert "configured upload limit of 12 bytes" in str(raised.value)
    assert "Last observed configuration" in str(raised.value)
    assert "secret upstream data" not in str(raised.value)


async def test_disabling_after_preflight_is_explicit_and_preserves_uncertain_intent(settings):
    def handler(request):
        return httpx.Response(503, json={"detail": {
            "code": "artifact_library_disabled", "message": "secret upstream data",
            "context": {"max_bytes": 0},
        }})

    with pytest.raises(ToolError) as raised:
        await call(settings, "upload_artifact", upload_arguments(), handler)
    assert "Artifact library is disabled" in str(raised.value)
    assert "original operation UUID" in str(raised.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(raised.value)
    assert "secret upstream data" not in str(raised.value)


async def test_lowered_positive_limit_does_not_prevent_exact_receipt_replay(settings):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, json=artifact())

    result = await call(settings, "upload_artifact", upload_arguments(), handler, maximum=1)
    assert result["artifact_library"]["max_bytes"] == 1
    assert len(requests) == 1 and requests[0].content == CONTENT


async def test_large_config_explicitly_reports_distinct_mcp_cap(settings):
    result = await call(settings, "search_artifact_contents", {
        "project_id": PROJECT_ID, "query": "report",
    }, lambda request: httpx.Response(200, json=search_page()), maximum=1073741824)
    assert result["artifact_library"]["max_bytes"] == 1073741824
    assert result["artifact_library"]["effective_upload_max_bytes"] == 67108864


async def test_replacement_timeout_keeps_exact_receipt_and_limit_guidance(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "PUT"
        raise httpx.ReadTimeout("private upstream diagnostic", request=request)

    with pytest.raises(ToolError) as raised:
        await call(settings, "replace_artifact", upload_arguments(
            artifact_id=ARTIFACT_ID, expected_revision=1,
        ), handler, maximum=1024)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in str(raised.value)
    assert "Configured upload limit: 1024 bytes" in str(raised.value)
    assert "private upstream diagnostic" not in str(raised.value)
    assert len(calls) == 1
