"""The project overview is a complete, scoped safe read, including wire validation."""

import copy
import json
from pathlib import Path

import pytest
from conftest import NOW, OTHER_WORK_ID, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, structured

from mnemonic_mcp import project_models
from mnemonic_mcp.command_help import render_help

STATES = (
    "deadlocked", "pending", "active", "to-review", "dropped", "blocked", "waiting",
    "duplicate", "deferred", "done", "wont-do", "promoted",
)


def project_overview():
    counts = dict.fromkeys(STATES, 0)
    counts.update(active=1, done=30)
    tag_counts = dict.fromkeys(STATES, 0)
    tag_counts.update(active=1, done=30)
    return {
        "project_id": PROJECT_ID,
        "settings": {
            "project_id": PROJECT_ID, "revision": "3",
            "lease_default_minutes": 15, "lease_minimum_minutes": 10,
            "lease_maximum_minutes": 120, "code_review_required_min_priority": 100,
            "code_review_optional_min_priority": 100, "allow_remediation_code_reviews": False,
        },
        "work_items_summary": {
            "total": 31, "counts_by_state": counts,
            "active": [{
                "id": WORK_ID, "title": "Review project overview", "created_at": NOW,
                "updated_at": NOW, "session_id": "native-session", "tags": ["code-review"],
            }],
            "counts_by_tag": {"code-review": tag_counts, "unused": dict.fromkeys(STATES, 0)},
        },
        "needs_attention": {"open_count": 2},
        "knowledge_domains": [{
            "id": OTHER_WORK_ID, "name": "Product Decision Records", "prefix": "PDR",
            "decision_document_count": 4,
        }],
    }


async def test_get_project_returns_complete_overview_in_one_safe_read(settings):
    calls = []
    document = project_overview()

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/overview"
        assert not request.url.query and not request.content
        assert request.headers["accept-encoding"] == "identity"
        return stream_json(document)

    result = structured(await adapter(settings, handler).call_tool(
        "get_project", {"project_id": PROJECT_ID},
    ))
    assert result == document
    assert len(calls) == 1


async def test_empty_project_returns_explicit_zero_counts(settings):
    document = project_overview()
    document["work_items_summary"] = {
        "total": 0, "counts_by_state": dict.fromkeys(STATES, 0), "active": [],
        "counts_by_tag": {"code-review": dict.fromkeys(STATES, 0)},
    }
    document["needs_attention"]["open_count"] = 0
    document["knowledge_domains"][0]["decision_document_count"] = 0
    result = structured(await adapter(settings, lambda _: stream_json(document)).call_tool(
        "get_project", {"project_id": PROJECT_ID},
    ))
    assert result == document


@pytest.mark.parametrize("path,value", [
    (("project_id",), OTHER_WORK_ID),
    (("settings", "project_id"), OTHER_WORK_ID),
    (("settings", "job_completion_report_prompt"), "Private authored instructions"),
    (("settings", "lease_default_minutes"), 121),
    (("work_items_summary", "total"), 32),
    (("work_items_summary", "total"), True),
    (("work_items_summary", "counts_by_state", "done"), -1),
    (("work_items_summary", "counts_by_state", "done"), "30"),
    (("work_items_summary", "counts_by_state", "unknown"), 0),
    (("work_items_summary", "active"), []),
    (("work_items_summary", "active", 0, "id"), "invalid"),
    (("work_items_summary", "active", 0, "session_id"), None),
    (("work_items_summary", "active", 0, "updated_at"), "2026-01-01T00:00:00"),
    (("work_items_summary", "active", 0, "tags"), ["unknown"]),
    (("work_items_summary", "active", 0, "tags"), ["code-review", "code-review"]),
    (("work_items_summary", "counts_by_tag", "code-review", "done"), 31),
    (("work_items_summary", "counts_by_tag", "code-review", "active"), 0),
    (("needs_attention", "open_count"), -1),
    (("knowledge_domains", 0, "decision_document_count"), True),
    (("knowledge_domains", 0, "prefix"), "bad prefix"),
])
async def test_get_project_rejects_malformed_or_inconsistent_aggregates(settings, path, value):
    document = project_overview()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    calls = []

    def handler(request):
        calls.append(request)
        return stream_json(document)

    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, handler).call_tool("get_project", {"project_id": PROJECT_ID})
    assert len(calls) == 1


async def test_get_project_rejects_duplicate_active_items_and_domains(settings):
    for key in ("active", "knowledge_domains"):
        document = project_overview()
        if key == "active":
            summary = document["work_items_summary"]
            summary["active"] *= 2
            summary["counts_by_state"]["active"] = 2
            summary["counts_by_tag"]["code-review"]["active"] = 2
            summary["total"] += 1
        else:
            document[key].append(copy.deepcopy(document[key][0]))
        with pytest.raises(ToolError, match="unexpected response"):
            await adapter(settings, lambda _, doc=document: stream_json(doc)).call_tool(
                "get_project", {"project_id": PROJECT_ID},
            )


async def test_get_project_replaces_old_tool_and_exposes_safe_help(settings):
    def forbidden(_):
        pytest.fail("Tool discovery and help must not dispatch a read")

    catalog = {tool.name: tool for tool in await adapter(settings, forbidden).list_tools()}
    assert "get_project_settings" not in catalog
    assert len(catalog) == 79
    tool = catalog["get_project"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.idempotentHint is True
    assert tool.inputSchema["required"] == ["project_id"]
    assert set(tool.inputSchema["properties"]) == {"project_id"}
    assert set(tool.outputSchema["properties"]) == {
        "project_id", "settings", "work_items_summary", "needs_attention", "knowledge_domains",
    }
    assert "Active" in render_help("get_project usage", catalog)


def test_project_overview_matches_published_api_schema():
    components = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/openapi.json").read_text()
    )["components"]["schemas"]
    for name in (
        "ProjectOverviewRead", "ProjectStateCounts", "ProjectActiveWorkItem",
        "ProjectWorkItemsSummary", "ProjectNeedsAttentionSummary", "ProjectKnowledgeDomainSummary",
        "ProjectSettingsValues",
    ):
        actual = getattr(project_models, name).model_json_schema()
        expected = components[name]
        assert set(actual["properties"]) == set(expected["properties"]), name
        assert set(actual.get("required", [])) == set(expected.get("required", [])), name
