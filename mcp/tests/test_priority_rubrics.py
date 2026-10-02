"""The long project rubric is an explicit, current, bounded safe read."""

import json
from pathlib import Path

import pytest
from conftest import OTHER_WORK_ID, PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, structured

from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.priority_rubrics import PriorityRubricRead


async def test_dedicated_tool_reads_current_full_markdown_without_other_settings(settings):
    content = "# Project rubric\n" + "📄" * 20000
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/priority-rubric"
        assert not request.url.query
        assert not request.content
        assert request.headers["accept-encoding"] == "identity"
        return stream_json({"project_id": PROJECT_ID, "content": content, "revision": "2"})

    server = adapter(settings, handler)
    for text in (content, "# Changed in the dashboard\r\n\nUse **impact**.\n"):
        content = text
        result = structured(await server.call_tool("get_priority_rubric", {"project_id": PROJECT_ID}))
        assert result == {"project_id": PROJECT_ID, "content": text, "revision": "2"}
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"project_id": OTHER_WORK_ID}, {"content": ""}, {"content": " \n"},
    {"content": "a\x00b"}, {"content": "\ud800"}, {"content": "x" * 100001},
    {"revision": 1}, {"revision": "0"}, {"revision": "9223372036854775808"},
    {"extra": "unrelated settings"},
])
async def test_rubric_rejects_wrong_scope_or_invalid_response(settings, changes):
    server = adapter(settings, lambda _: stream_json({
        "project_id": PROJECT_ID, "content": "# Guidance", "revision": "1", **changes,
    }))
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool("get_priority_rubric", {"project_id": PROJECT_ID})


@pytest.mark.parametrize("status", [404, 503])
async def test_unavailable_rubric_does_not_substitute_bundled_guidance(settings, status):
    server = adapter(settings, lambda _: stream_json({"detail": "Unavailable"}, status))
    with pytest.raises(ToolError):
        await server.call_tool("get_priority_rubric", {"project_id": PROJECT_ID})


async def test_help_points_to_dedicated_read_without_fetching_or_embedding_rubric(settings):
    def forbidden(_):
        pytest.fail("Help must not load rubric content")

    catalog = {tool.name: tool for tool in await adapter(settings, forbidden).list_tools()}
    tool = catalog["get_priority_rubric"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert set(tool.inputSchema["properties"]) == {"project_id"}
    for topic in ("get_project_settings usage", "create_work usage", "create_work priority",
                  "update_work usage"):
        assert "get_priority_rubric" in render_help(topic, catalog)
    assert "Markdown" in render_help("get_priority_rubric usage", catalog)
    assert "Consequence of delay" not in render_help("", catalog)


def test_rubric_response_shape_matches_openapi():
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "docs/openapi.json").read_text())["components"]["schemas"]
    actual = PriorityRubricRead.model_json_schema()
    for field in ("properties", "required"):
        assert set(actual[field]) == set(schema["PriorityRubricRead"][field])
