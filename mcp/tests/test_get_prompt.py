"""Current project prompts share one bounded, scoped safe-read contract."""

import hashlib
import json
from pathlib import Path
from typing import get_args

import pytest
from conftest import OTHER_WORK_ID, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, structured

from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.prompt_models import PromptId, RenderedPrompt


def rendered(prompt_id="resume-work", content="Current project instructions", **context):
    return {
        "project_id": PROJECT_ID, "prompt_id": prompt_id,
        "work_item_id": None, "code_review_id": None,
        "content": content, "revision": hashlib.sha256(content.encode()).hexdigest(),
        "prompt_revision": "3" if prompt_id == "job-completion-report" else None,
        **context,
    }


@pytest.mark.parametrize("prompt_id", get_args(PromptId))
@pytest.mark.parametrize("context", [
    {}, {"work_item_id": WORK_ID}, {"code_review_id": OTHER_WORK_ID},
    {"work_item_id": WORK_ID, "code_review_id": OTHER_WORK_ID},
])
async def test_get_prompt_reads_current_content_with_exact_context(settings, prompt_id, context):
    calls = []
    document = rendered(prompt_id, **context)

    def handler(request):
        calls.append(request)
        assert request.method == "POST"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/prompts/{prompt_id}/render"
        assert not request.url.query
        assert json.loads(request.content) == context
        assert request.headers["accept-encoding"] == "identity"
        return stream_json(document)

    server = adapter(settings, handler)
    arguments = {"project_id": PROJECT_ID, "prompt_id": prompt_id, **context}
    assert structured(await server.call_tool("get_prompt", arguments)) == document
    document = rendered(prompt_id, "Changed by the project owner. $UNKNOWN", **context)
    assert structured(await server.call_tool("get_prompt", arguments)) == document
    assert len(calls) == 2


@pytest.mark.parametrize("changes", [
    {"project_id": OTHER_WORK_ID}, {"prompt_id": "resume-work"},
    {"work_item_id": OTHER_WORK_ID}, {"code_review_id": OTHER_WORK_ID},
    {"content": ""}, {"content": " \n"}, {"content": "a\x00b"}, {"content": "\ud800"},
    {"content": 2}, {"content": "x" * 100001}, {"revision": "3"},
    {"prompt_revision": None}, {"prompt_revision": 1}, {"prompt_revision": "0"},
    {"prompt_revision": "9223372036854775808"}, {"extra": "unexpected"},
])
async def test_get_prompt_rejects_wrong_scope_or_invalid_response(settings, changes):
    document = {**rendered("job-completion-report", work_item_id=WORK_ID), **changes}
    server = adapter(settings, lambda _: stream_json(document))
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool("get_prompt", {
            "project_id": PROJECT_ID, "prompt_id": "job-completion-report", "work_item_id": WORK_ID,
        })


async def test_non_report_prompt_cannot_claim_a_closeout_revision(settings):
    server = adapter(settings, lambda _: stream_json(rendered(prompt_revision="1")))
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool("get_prompt", {"project_id": PROJECT_ID, "prompt_id": "resume-work"})


@pytest.mark.parametrize("status", [404, 409, 422, 503])
async def test_missing_prompt_does_not_fall_back_to_bundled_instructions(settings, status):
    calls = []

    def handler(request):
        calls.append(request)
        return stream_json({"detail": "Unavailable"}, status)

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("get_prompt", {
            "project_id": PROJECT_ID, "prompt_id": "request-human-review", "work_item_id": WORK_ID,
        })
    assert len(calls) == 1


@pytest.mark.parametrize("arguments", [
    {"prompt_id": "../../other-project"}, {"prompt_id": "unknown"},
    {"prompt_id": "resume-work", "work_item_id": "not-a-uuid"},
    {"prompt_id": "resume-work", "macros": {"$PROJECT_ID": "invented"}},
])
async def test_invalid_prompt_context_never_dispatches(settings, arguments):
    def forbidden(_):
        pytest.fail("Invalid context must not reach the API")

    with pytest.raises(ToolError):
        await adapter(settings, forbidden).call_tool(
            "get_prompt", {"project_id": PROJECT_ID, **arguments},
        )


async def test_prompt_help_is_metadata_only_and_old_rubric_tool_is_removed(settings):
    def forbidden(_):
        pytest.fail("Help must not load project prompt content")

    catalog = {tool.name: tool for tool in await adapter(settings, forbidden).list_tools()}
    assert "get_priority_rubric" not in catalog
    tool = catalog["get_prompt"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert set(tool.inputSchema["properties"]) == {
        "project_id", "prompt_id", "work_item_id", "code_review_id",
    }
    assert set(tool.inputSchema["required"]) == {"project_id", "prompt_id"}
    assert "request-human-review" in render_help("get_prompt usage", catalog)
    assert "Consequence of delay" not in render_help("", catalog)
    assert "job_completion_report_prompt" not in catalog["get_project"].outputSchema[
        "$defs"
    ]["ProjectSettingsValues"]["properties"]


def test_rendered_prompt_matches_rest_schema():
    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "docs/openapi.json").read_text())["components"]["schemas"]
    actual = RenderedPrompt.model_json_schema()
    for field in ("properties", "required"):
        assert set(actual[field]) == set(schema["PromptRenderRead"][field])
