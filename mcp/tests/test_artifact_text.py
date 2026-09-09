"""Extracted-text reads remain bounded, revision-pinned, and explicit about coverage."""

import httpx
import pytest
from conftest import NOW, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import ARTIFACT_ID, call

from mnemonic_mcp.server import build_server


def text_page(**overrides):
    return {
        "project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID, "revision": 3,
        "sha256": "a" * 64,
        "extraction": {"status": "ready", "truncated": False,
                       "error_code": None, "extracted_at": NOW},
        "text": "é🙂x", "offset": 0, "limit": 3, "total_chars": 5, "next_offset": 3,
        **overrides,
    }


def arguments(**overrides):
    return {"project_id": PROJECT_ID, "artifact_id": ARTIFACT_ID,
            "expected_revision": 3, "limit": 3, **overrides}


async def test_text_pagination_uses_characters_and_never_downloads_original_bytes(settings):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/artifacts/{ARTIFACT_ID}/text"
        assert request.url.params["expected_revision"] == "3"
        assert "x-client-operation-id" not in request.headers
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=text_page())
        return httpx.Response(200, json=text_page(text="yz", offset=3, next_offset=None))

    first = await call(settings, "get_artifact_text", arguments(), handler)
    last = await call(settings, "get_artifact_text", arguments(offset=first["next_offset"]), handler)
    assert first["text"] + last["text"] == "é🙂xyz"
    assert last["next_offset"] is None
    assert len(requests) == 2
    assert "metadata" not in first["extraction"]
    assert "content_base64" not in first


@pytest.mark.parametrize("status", ["pending", "processing", "failed"])
async def test_text_unavailable_is_explicit_and_not_an_empty_success(settings, status):
    payload = text_page(text=None, total_chars=None, next_offset=None)
    payload["extraction"].update(status=status, extracted_at=None,
                                 error_code="extraction_failed" if status == "failed" else None)
    result = await call(settings, "get_artifact_text", arguments(),
                        lambda request: httpx.Response(200, json=payload))
    assert result["text"] is None and result["total_chars"] is None
    assert result["extraction"]["status"] == status


@pytest.mark.parametrize(("offset", "total", "truncated"), [(0, 0, False), (8, 5, True)])
async def test_ready_empty_page_preserves_coverage(settings, offset, total, truncated):
    payload = text_page(text="", offset=offset, total_chars=total, next_offset=None)
    payload["extraction"]["truncated"] = truncated
    result = await call(settings, "get_artifact_text", arguments(offset=offset),
                        lambda request: httpx.Response(200, json=payload))
    assert result["text"] == ""
    assert result["extraction"]["truncated"] is truncated


@pytest.mark.parametrize(("field", "value"), [
    ("project_id", WORK_ID), ("artifact_id", WORK_ID), ("revision", 4), ("offset", 1),
    ("limit", 4), ("next_offset", None), ("next_offset", 2), ("total_chars", 2),
    ("total_chars", True), ("total_chars", 8_000_001), ("text", "é🙂"), ("text", None),
    ("text", "x" * 20_001), ("sha256", "not a checksum"),
])
async def test_text_refuses_malformed_or_mismatched_pages(settings, field, value):
    with pytest.raises(ToolError):
        await call(settings, "get_artifact_text", arguments(),
                   lambda request: httpx.Response(200, json=text_page(**{field: value})))


@pytest.mark.parametrize("status", ["pending", "processing", "failed", "deleted", "superseded"])
async def test_text_refuses_stale_body_with_unavailable_extraction(settings, status):
    payload = text_page()
    payload["extraction"]["status"] = status
    with pytest.raises(ToolError):
        await call(settings, "get_artifact_text", arguments(),
                   lambda request: httpx.Response(200, json=payload))


@pytest.mark.parametrize(("field", "value"), [
    ("expected_revision", None), ("expected_revision", 0), ("expected_revision", True),
    ("offset", -1), ("offset", 8_000_001), ("offset", True),
    ("limit", 0), ("limit", 20_001), ("limit", True),
])
async def test_invalid_text_arguments_send_no_request(settings, field, value):
    def forbidden(request):
        raise AssertionError("invalid text arguments must not reach the API")

    with pytest.raises(ToolError):
        await call(settings, "get_artifact_text", arguments(**{field: value}), forbidden)


async def test_text_tool_requires_revision_and_uses_safe_read_annotations(settings):
    tool = next(tool for tool in await build_server(settings).list_tools()
                if tool.name == "get_artifact_text")
    assert set(tool.inputSchema["required"]) == {"project_id", "artifact_id", "expected_revision"}
    assert tool.inputSchema["properties"]["limit"]["default"] == 20_000
    assert tool.annotations.readOnlyHint and tool.annotations.idempotentHint
    assert not tool.annotations.destructiveHint


async def test_disabled_text_tool_never_accesses_artifact(settings):
    def forbidden(request):
        raise AssertionError("disabled library must not read extracted text")

    with pytest.raises(ToolError, match="disabled"):
        await call(settings, "get_artifact_text", arguments(), forbidden, maximum=0)


@pytest.mark.parametrize("failure", ["timeout", "oversize", "encoded", "status", "missing"])
async def test_text_transport_is_bounded_safe_and_does_not_echo_content(settings, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE TEXT", request=request)
        headers = {"content-length": "262145"} if failure == "oversize" else {}
        if failure == "encoded":
            headers["content-encoding"] = "gzip"
        status = 201 if failure == "status" else 404 if failure == "missing" else 200
        return httpx.Response(status, json=text_page(), headers=headers)

    with pytest.raises(ToolError) as raised:
        await call(settings, "get_artifact_text", arguments(), handler)
    assert len(calls) == 1
    assert "PRIVATE TEXT" not in str(raised.value)
    assert "unknown outcome" not in str(raised.value)
