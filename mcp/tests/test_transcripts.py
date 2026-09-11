"""MCP transcript assertions, scoped reads, stable text paging and bounded transfer."""

import asyncio
import base64
import copy
import hashlib
import json

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError
from test_artifacts import call
from test_tools import protected_tool_arguments

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.server import build_server
from mnemonic_mcp.transcript_models import SubagentTranscripts, TranscriptLocation

TRANSCRIPT_ID = "fdc27e36-ce8d-4249-984a-638b59b7f874"
LOCATION = {"client": "claude_code", "path": "/transcripts/primary.jsonl"}
SUBAGENT = {"client": "claude_code", "path": "/transcripts/session/subagents/agent-one.jsonl"}
TEXT = "user: Find the durable objective.\nassistant: It is indexed. 🌲"
HASH = hashlib.sha256(TEXT.encode()).hexdigest()


def transcript(**changes):
    return {
        "id": TRANSCRIPT_ID, "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "lease_generation_id": CLIENT_OPERATION_ID, "client": "claude_code",
        "session_id": "independent-session", "source_path": LOCATION["path"],
        "filename": "primary.jsonl", "kind": "primary", "status": "ready",
        "indexing_started_at": NOW, "indexing_completed_at": NOW, "error_code": None,
        "size_bytes": 180, "mime_type": "application/x-ndjson", "format": "claude_code_jsonl",
        "sha256": "a" * 64, "text_sha256": HASH, "metadata": {}, "truncated": False,
        "created_at": NOW, "snippet": None, "score": None, **changes,
    }


def page(**changes):
    return {"items": [transcript()], "total": 1, "limit": 50, "offset": 0,
            "indexing_incomplete": False, **changes}


@pytest.mark.parametrize("tool", ["claim_work", "claim_and_recall"])
async def test_claim_requires_explicit_location_or_null_and_forwards_exactly(settings, tool):
    calls = []

    class CapturingAPI:
        async def request(self, method, path, **kwargs):
            calls.append(kwargs["payload"])
            raise ToolError("captured assertion")

    server = build_server(settings, CapturingAPI())
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                 "holder_client": "claude-code", "holder_session_id": "current-session",
                 "claim_request_id": "claim-with-transcript"}
    with pytest.raises(ToolError, match=r"session_transcript \(missing\)"):
        await server.call_tool(tool, arguments)
    assert calls == []
    for assertion in (None, LOCATION):
        with pytest.raises(ToolError, match="captured assertion"):
            await server.call_tool(tool, {**arguments, "session_transcript": assertion})
        assert calls[-1]["session_transcript"] == assertion


@pytest.mark.parametrize("location", [
    {"client": "claude_code", "path": "relative.jsonl"},
    {"client": "claude_code", "path": "/allowed/../private.jsonl"},
    {"client": "claude_code", "path": "/path\x00.jsonl"},
    {"client": "claude_code", "path": "/path\ud800.jsonl"},
    {"client": "", "path": "/file.jsonl"},
    {"client": "claude_code", "path": "/file.jsonl", "sensitive": True},
])
def test_locations_reject_unsafe_or_unsupported_options(location):
    with pytest.raises(ValidationError):
        TranscriptLocation.model_validate(location)


def test_unknown_clients_reach_backend_disposition_and_subagents_are_distinct():
    assert TranscriptLocation.model_validate({**LOCATION, "client": "future_client"}).client == (
        "future_client"
    )
    adapter = TypeAdapter(SubagentTranscripts)
    assert adapter.validate_python(None) is None
    for invalid in ([], [LOCATION, LOCATION], [LOCATION] * 101):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


@pytest.mark.parametrize("tool", ["complete_work", "merge_work", "delete_work"])
async def test_closeout_preserves_omitted_assertion_and_retries_preserve_order(settings, tool):
    calls = []

    class CapturingAPI:
        async def request(self, method, path, **kwargs):
            calls.append(kwargs["payload"])
            raise ToolError("captured assertion")

    server = build_server(settings, CapturingAPI())
    args = protected_tool_arguments()[tool]
    args.pop("subagent_transcripts")
    with pytest.raises(ToolError, match="captured assertion"):
        await server.call_tool(tool, args)
    assert "subagent_transcripts" not in calls.pop()
    args["subagent_transcripts"] = [SUBAGENT, {**SUBAGENT, "path": "/transcripts/second.jsonl"}]
    for _ in range(2):
        with pytest.raises(ToolError, match="captured assertion"):
            await server.call_tool(tool, copy.deepcopy(args))
    assert calls[0] == calls[1]
    assert calls[0]["subagent_transcripts"] == args["subagent_transcripts"]


async def test_terminal_update_preserves_omission_and_explicit_null(settings):
    calls = []

    class CapturingAPI:
        async def request(self, method, path, **kwargs):
            calls.append(kwargs["payload"])
            raise ToolError("captured assertion")

    server = build_server(settings, CapturingAPI())
    args = protected_tool_arguments()["update_work"]
    with pytest.raises(ToolError, match="captured assertion"):
        await server.call_tool("update_work", args)
    assert "subagent_transcripts" not in calls[0]
    with pytest.raises(ToolError, match="require a closeout"):
        await server.call_tool("update_work", {**args, "subagent_transcripts": [SUBAGENT]})
    with pytest.raises(ToolError, match="Mnemonic rejected the input"):
        await server.call_tool("update_work", {**args, "changes": {"status": []}})
    assert len(calls) == 1
    with pytest.raises(ToolError, match="captured assertion"):
        await server.call_tool("update_work", {**args, "subagent_transcripts": None})
    assert calls[-1]["subagent_transcripts"] is None
    args["changes"] = {"status": "wont-do"}
    with pytest.raises(ToolError, match="captured assertion"):
        await server.call_tool("update_work", args)
    assert "subagent_transcripts" not in calls[-1]
    args["subagent_transcripts"] = None
    with pytest.raises(ToolError, match="captured assertion"):
        await server.call_tool("update_work", args)
    assert calls[-1]["subagent_transcripts"] is None


async def test_search_is_metadata_by_default_and_fulltext_is_explicit(settings):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=page())

    args = {"project_id": PROJECT_ID, "query": "objective", "work_item_id": WORK_ID}
    await call(settings, "search_transcript_contents", args, handler)
    await call(settings, "search_transcript_contents", {**args, "fulltext": True}, handler)
    assert requests == [{"query": "objective", "fulltext": fulltext, "limit": 50,
                         "offset": 0, "work_item_id": WORK_ID} for fulltext in (False, True)]


@pytest.mark.parametrize("failure", ["project", "work", "duplicate", "page", "snippet", "oversize", "coverage"])
async def test_metadata_search_rejects_scope_leaks_and_malformed_pages(settings, failure):
    result = page()
    if failure in ("project", "work"):
        result["items"][0][f"{failure}_id" if failure == "project" else "work_item_id"] = (
            OTHER_WORK_ID
        )
    if failure == "duplicate":
        result["items"] *= 2
        result["total"] = 2
    if failure == "page":
        result["offset"] = 1
    if failure == "snippet":
        result["items"][0]["snippet"] = "Content leaked without opt in"
    if failure == "coverage":
        result["items"][0]["status"] = "pending"
    headers = {"content-length": "16777217"} if failure == "oversize" else {}
    with pytest.raises(ToolError):
        await call(settings, "search_transcript_contents", {
            "project_id": PROJECT_ID, "query": "objective", "work_item_id": WORK_ID,
        }, lambda request: httpx.Response(200, json=result, headers=headers))


async def test_listing_exposes_failed_disposition_and_incomplete_coverage(settings):
    result = page(items=[transcript(status="failed", error_code="source_io_error",
                                   text_sha256=None)], indexing_incomplete=True)
    actual = await call(settings, "list_transcripts", {"project_id": PROJECT_ID},
                        lambda request: httpx.Response(200, json=result))
    assert actual["indexing_incomplete"]
    assert actual["items"][0]["error_code"] == "source_io_error"


@pytest.mark.parametrize("failure", [None, "hash", "identity", "length", "next"])
async def test_text_pages_are_hash_pinned_and_coherent(settings, failure):
    result = {"project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID, "text_sha256": HASH,
              "text": TEXT[:12], "total_chars": len(TEXT), "offset": 0, "limit": 12,
              "next_offset": 12, "status": "ready", "truncated": False}
    if failure == "hash":
        result["text_sha256"] = "b" * 64
    if failure == "identity":
        result["transcript_id"] = WORK_ID
    if failure == "length":
        result["text"] = TEXT[:10]
    if failure == "next":
        result["next_offset"] = None

    def handler(request):
        assert dict(request.url.params) == {"expected_sha256": HASH, "offset": "0", "limit": "12"}
        return httpx.Response(200, json=result)

    args = {"project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID,
            "expected_sha256": HASH, "limit": 12}
    if failure:
        with pytest.raises(ToolError):
            await call(settings, "get_transcript_text", args, handler)
    else:
        actual = await call(settings, "get_transcript_text", args, handler)
        assert actual["next_offset"] == 12


@pytest.mark.parametrize("failure", [None, "checksum", "header", "oversize", "redirect"])
async def test_download_validates_normalized_bytes_and_bounded_response(settings, failure):
    def handler(request):
        if not request.url.path.endswith("/content"):
            return httpx.Response(200, json=transcript())
        assert request.url.params["expected_sha256"] == HASH
        headers = {"X-Content-SHA256": HASH}
        if failure == "header":
            headers["X-Content-SHA256"] = "b" * 64
        if failure == "oversize":
            headers["Content-Length"] = str(32 * 1024 * 1024 + 1)
        return httpx.Response(302 if failure == "redirect" else 200, headers=headers,
                              content=b"altered" if failure == "checksum" else TEXT.encode())

    args = {"project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID, "expected_sha256": HASH}
    if failure:
        with pytest.raises(ToolError):
            await call(settings, "download_transcript", args, handler)
    else:
        result = await call(settings, "download_transcript", args, handler)
        assert base64.b64decode(result["content_base64"]).decode() == TEXT


async def test_new_tools_are_safe_reads_with_no_sensitive_option(settings):
    tools = {item.name: item for item in await build_server(settings).list_tools()}
    for name in ("list_transcripts", "get_transcript", "search_transcript_contents",
                 "get_transcript_text", "download_transcript"):
        assert tools[name].annotations.readOnlyHint
        assert "sensitive" not in tools[name].inputSchema["properties"]
        assert "client_operation_id" not in tools[name].inputSchema["properties"]
    assert len(tools) == 53


def _historical_closeout_case(kind, work_item, checkpoint, relationship, progress_event, human_gate):
    from test_code_reviews import complete_arguments, completion_response
    from test_phase12 import closeout_fixture
    from test_tools import protected_success_responses

    if kind in {"done", "wont-do", "promoted"}:
        tool, args, response = closeout_fixture(kind, work_item, checkpoint, include_report=False)
    elif kind == "complete_code_review":
        tool, args, response = kind, complete_arguments(), completion_response()
    else:
        tool, args = kind, protected_tool_arguments()[kind]
        response = protected_success_responses(
            work_item, checkpoint, relationship, progress_event, human_gate,
        )[kind]
    args.pop("subagent_transcripts", None)
    return tool, args, response


@pytest.mark.parametrize("kind", [
    "done", "wont-do", "promoted", "merge_work", "delete_work", "complete_code_review",
])
async def test_historical_sparse_closeouts_replay_without_inventing_transcript_assertions(
    settings, work_item, checkpoint, relationship, progress_event, human_gate, kind,
):
    tool, args, response = _historical_closeout_case(
        kind, work_item, checkpoint, relationship, progress_event, human_gate,
    )
    requests = []

    def handler(request):
        requests.append(request.content)
        assert "subagent_transcripts" not in json.loads(request.content)
        assert json.loads(request.content)["client_operation_id"] == args["client_operation_id"]
        return httpx.Response(201 if kind == "merge_work" else 200, json=response)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    for _ in range(2):
        actual = await server.call_tool(tool, copy.deepcopy(args))
        assert (actual[1] if isinstance(actual, tuple) else actual) == response
    assert len(requests) == 2 and requests[0] == requests[1]


@pytest.mark.parametrize("kind", [
    "done", "wont-do", "promoted", "merge_work", "delete_work", "complete_code_review",
])
async def test_fresh_sparse_closeouts_receive_backend_assertion_guard_once(
    settings, work_item, checkpoint, relationship, progress_event, human_gate, kind,
):
    tool, args, _ = _historical_closeout_case(
        kind, work_item, checkpoint, relationship, progress_event, human_gate,
    )
    requests = []

    def handler(request):
        requests.append(request)
        assert "subagent_transcripts" not in json.loads(request.content)
        return httpx.Response(422, json={"detail": {
            "code": "subagent_transcripts_required", "message": "private-backend-detail",
        }})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="Closeout requires subagent transcript locations") as raised:
        await server.call_tool(tool, args)
    assert len(requests) == 1
    assert "private-backend-detail" not in str(raised.value)
    assert "unknown outcome" not in str(raised.value)


async def test_closeout_schema_permits_sparse_receipt_replay_without_default_null(settings):
    tools = {item.name: item for item in await build_server(settings).list_tools()}
    for name in ("complete_work", "update_work", "merge_work", "delete_work", "complete_code_review"):
        schema = tools[name].inputSchema
        assert "subagent_transcripts" not in schema["required"]
        assert "default" not in schema["properties"]["subagent_transcripts"]
        assert "historical receipt replay" in tools[name].description


async def test_invalid_filesystem_filename_keeps_failure_record_readable(settings):
    filename = "🌲" * 4095
    item = transcript(filename=filename, source_path="/" + filename, status="failed",
                      text_sha256=None, error_code="transcript_io_error")
    actual = await call(settings, "get_transcript", {
        "project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID,
    }, lambda request: httpx.Response(200, content=json.dumps(item).encode(),
                                    headers={"Content-Type": "application/json"}))
    assert actual["filename"] == filename
    assert actual["status"] == "failed"


async def test_failed_long_filename_page_fits_bounded_metadata_budget(settings):
    filename = "🌲" * 4095
    items = [transcript(id=f"{index:08x}-0000-4000-8000-000000000000", filename=filename,
                        source_path="/" + filename, status="failed", text_sha256=None,
                        error_code="transcript_io_error") for index in range(100)]
    result = page(items=items, total=100, limit=100, indexing_incomplete=True)
    response = httpx.Response(200, content=json.dumps(result).encode(),
                              headers={"Content-Type": "application/json"})
    assert 6 * 1024 * 1024 < len(response.content) < 16 * 1024 * 1024
    actual = await call(settings, "list_transcripts", {"project_id": PROJECT_ID, "limit": 100},
                        lambda request: response)
    assert len(actual["items"]) == 100 and actual["indexing_incomplete"]


class SlowTranscriptStream(httpx.AsyncByteStream):
    def __init__(self):
        self.chunks = 0
        self.closed = False

    async def __aiter__(self):
        while self.chunks < 100:
            self.chunks += 1
            yield b" "
            await asyncio.sleep(0.005)

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize("name,extra", [
    ("list_transcripts", {}),
    ("search_transcript_contents", {"query": "objective"}),
    ("get_transcript", {"transcript_id": TRANSCRIPT_ID}),
    ("get_transcript_text", {"transcript_id": TRANSCRIPT_ID, "expected_sha256": HASH}),
    ("download_transcript", {"transcript_id": TRANSCRIPT_ID, "expected_sha256": HASH}),
])
@pytest.mark.parametrize("stall_at", ["headers", "body"])
async def test_transcript_json_deadlines_cover_headers_and_slow_drip_body(
    settings, monkeypatch, name, extra, stall_at,
):
    from mnemonic_mcp import api as api_module

    monkeypatch.setattr(api_module, "_EXTENDED_READ_TIMEOUT_SECONDS", 0.03)
    stream = SlowTranscriptStream()
    requests = []
    header_cancelled = False

    async def handler(request):
        nonlocal header_cancelled
        requests.append(request)
        if stall_at == "headers":
            try:
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                header_cancelled = True
                raise
        return httpx.Response(200, stream=stream)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="safe read") as raised:
        await asyncio.wait_for(server.call_tool(name, {"project_id": PROJECT_ID, **extra}), 0.3)
    assert len(requests) == 1
    if stall_at == "headers":
        assert header_cancelled
    else:
        assert stream.closed and 0 < stream.chunks < 100
    assert "unknown outcome" not in str(raised.value)


async def test_transcript_binary_download_has_a_total_body_deadline(settings, monkeypatch):
    from mnemonic_mcp import artifact_transport

    timeouts = []
    original_timeout = asyncio.timeout

    def shortened_timeout(seconds):
        timeouts.append(seconds)
        return original_timeout(0.03)

    monkeypatch.setattr(artifact_transport.asyncio, "timeout", shortened_timeout)
    stream = SlowTranscriptStream()
    requests = []

    async def handler(request):
        requests.append(request)
        if request.url.path.endswith("/content"):
            return httpx.Response(200, stream=stream, headers={"X-Content-SHA256": HASH})
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(transcript()).encode()))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="safe read"):
        await asyncio.wait_for(server.call_tool("download_transcript", {
            "project_id": PROJECT_ID, "transcript_id": TRANSCRIPT_ID, "expected_sha256": HASH,
        }), 0.3)
    assert len(requests) == 2
    assert timeouts == [60.0, 120]
    assert stream.closed and 0 < stream.chunks < 100
