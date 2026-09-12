"""The MCP uses the current project prompt and exact report work scope."""

import hashlib
import json

import httpx
import pytest
from conftest import PROJECT_ID, WORK_ID, stream_json
from test_phase12 import call, envelope
from test_tools import adapter, structured


async def test_resume_uses_current_rendered_project_prompt_without_claiming(settings, work_context):
    calls = []
    content = "Review the incident for this project."

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=work_context)
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/prompts/resume-work/render"
        assert request.method == "POST"
        assert json.loads(request.content) == {"work_item_id": WORK_ID}
        assert request.headers["accept-encoding"] == "identity"
        return stream_json({"content": content})

    server = adapter(settings, handler)
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    first = await server.get_prompt("resume_work", arguments)
    text = first.messages[0].content.text
    assert text.startswith(content + "\n\n")
    assert json.loads(text.split("\n\n", 1)[1])["work_item"]["id"] == WORK_ID
    content = "The external prompt editor changed the project instructions."
    second = await server.get_prompt("resume_work", arguments)
    assert second.messages[0].content.text.startswith(content + "\n\n")
    assert calls == [
        ("GET", f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/context"),
        ("POST", f"/api/v1/projects/{PROJECT_ID}/prompts/resume-work/render"),
    ] * 2


@pytest.mark.parametrize("document", [
    {"content": ""}, {"content": " \n"}, {"content": "unsafe\x00content"},
    {"content": 2}, {"content": "\ud800"}, {"content": "x" * 100001}, {"content": "guidance", "extra": True},
])
async def test_resume_rejects_invalid_rendered_content(settings, work_context, document):
    def handler(request):
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=work_context)
        return stream_json(document)

    with pytest.raises(ValueError, match="unexpected response"):
        await adapter(settings, handler).get_prompt(
            "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )


@pytest.mark.parametrize("status", [404, 503])
async def test_resume_missing_project_prompt_does_not_use_embedded_fallback(
    settings, work_context, status,
):
    def handler(request):
        if request.url.path.endswith("/context"):
            return httpx.Response(200, json=work_context)
        return stream_json({"error": {"code": "prompt_storage_unavailable"}}, status)

    with pytest.raises(ValueError):
        await adapter(settings, handler).get_prompt(
            "resume_work", {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )


@pytest.mark.parametrize("work_item_id", [None, WORK_ID])
async def test_report_settings_forward_exact_optional_work_scope(settings, work_item_id):
    response = {
        "lease_default_minutes": 15, "lease_minimum_minutes": 10, "lease_maximum_minutes": 120,
        "code_review_required_min_priority": 100, "code_review_optional_min_priority": 100,
        "allow_remediation_code_reviews": False,
        "project_id": PROJECT_ID, "revision": "3", "recall_pointer_template": None,
        "job_completion_report_prompt": "Summarize this expanded work context: " + "x" * 9000,
    }

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/settings"
        expected = {"work_item_id": work_item_id} if work_item_id else {}
        assert dict(request.url.params) == expected
        return stream_json(response)

    arguments = {"project_id": PROJECT_ID}
    if work_item_id is not None:
        arguments["work_item_id"] = work_item_id
    result = structured(await adapter(settings, handler).call_tool("get_project_settings", arguments))
    assert result == response


async def test_expanded_report_snapshot_preserves_large_macro_values_and_hash(settings):
    document = envelope(detail=True)
    content = "Expanded work context: " + "📄" * 9000
    document["report"]["authoring_prompt"] = content
    document["report"]["prompt_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    result = await call(settings, "get_job_completion_report", document,
                        {"report_id": document["report"]["id"]})
    assert result["report"]["authoring_prompt"] == content
    assert result["report"]["prompt_sha256"] == document["report"]["prompt_sha256"]
