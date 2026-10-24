"""Opt-in projection contracts remain explicit at the strict agent boundary."""

import copy

import pytest
from conftest import PROJECT_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import (
    ASSESSMENT_ID,
    assessment,
    candidate,
    disposition,
    impact,
)
from test_tools import adapter, structured

COMPACT = {"detail": "compact", "include_diff": False, "include_scopes": "summary"}


def compact_candidate():
    item = candidate()
    for name in ("title", "rationale", "related_work_item_ids", "related_artifact_ids", "scopes"):
        item.pop(name)
    item["scope_summary"] = {
        "captured_scope_count": 0, "commit_hashes": [], "evidence_truncated": False,
    }
    return item


def compact_assessment():
    item = assessment(
        projection=COMPACT, kind="code-impact", total=1, candidates=[compact_candidate()],
        dispositions=[{key: value for key, value in disposition().items() if key in {
            "candidate_reference", "version", "disposition", "remediation_work_item_id",
        }}], disposition_history_total=1, version=1, unassessed_count=0,
        change_required_count=1, status="change-required", limit=50, offset=0, next_offset=None,
    )
    item["diff"].pop("unified_diff")
    for name in ("before_author", "after_author"):
        item[name].pop("active_work_items")
        item[name]["active_work_item_ids"] = []
    return item


async def test_compact_assessment_arguments_and_omissions_are_retained(settings):
    expected = compact_assessment()

    def handler(request):
        assert request.url.params["detail"] == "compact"
        assert request.url.params["include_diff"] == "false"
        assert request.url.params["include_scopes"] == "summary"
        return stream_json(expected)

    result = structured(await adapter(settings, handler).call_tool("decision_assessment_get", {
        "project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID, **COMPACT,
    }))
    assert result == expected
    assert "reason" not in result["dispositions"][0]
    assert "scopes" not in result["candidates"][0]


async def test_compact_impact_and_list_keep_projection_contract(settings):
    item = compact_assessment()
    detail_names = {
        "kind", "total", "candidates", "dispositions", "disposition_history_total",
        "limit", "offset", "next_offset",
    }
    listed = {key: value for key, value in item.items() if key not in detail_names}
    page = {"items": [listed], "total": 1, "limit": 50, "offset": 0}
    result = structured(await adapter(settings, lambda request: stream_json(page)).call_tool(
        "decision_assessments", {"project_id": PROJECT_ID, **COMPACT},
    ))
    assert result == page
    expected = impact(projection=COMPACT, items=[compact_candidate()])
    expected["origin_author"].pop("active_work_items")
    expected["origin_author"]["active_work_item_ids"] = []
    result = structured(await adapter(settings, lambda request: stream_json(expected)).call_tool(
        "decision_impact", {"project_id": PROJECT_ID, "decision_id": "ADR-00001-0", **COMPACT},
    ))
    assert result == expected


@pytest.mark.parametrize("mutation", [
    "projection-missing", "diff-present", "title-present", "summary-coverage",
    "summary-count", "summary-duplicate", "disposition-reason", "author-full",
    "candidate-identity", "continuation",
])
async def test_malformed_compact_response_is_rejected(settings, mutation):
    item = compact_assessment()
    changes = {
        "projection-missing": lambda: item.pop("projection"),
        "diff-present": lambda: item["diff"].update(unified_diff=""),
        "title-present": lambda: item["candidates"][0].update(title="Unexpected full field"),
        "summary-coverage": lambda: item["candidates"][0]["scope_summary"].update(
            evidence_truncated=True,
        ),
        "summary-count": lambda: item["candidates"][0]["scope_summary"].update(
            captured_scope_count=1,
        ),
        "summary-duplicate": lambda: item["candidates"][0]["scope_summary"].update(
            captured_scope_count=2, commit_hashes=["a" * 40, "a" * 40],
        ),
        "disposition-reason": lambda: item["dispositions"][0].update(reason="Unexpected detail"),
        "author-full": lambda: item["before_author"].update(active_work_items=[]),
        "candidate-identity": lambda: item["candidates"][0].update(revision=1),
        "continuation": lambda: item.update(next_offset=1),
    }
    changes[mutation]()
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: stream_json(item)).call_tool(
            "decision_assessment_get",
            {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID, **COMPACT},
        )


async def test_full_call_refuses_an_unrequested_compact_projection(settings):
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: stream_json(compact_assessment())).call_tool(
            "decision_assessment_get", {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID},
        )


async def test_compact_call_refuses_full_response_and_default_missing_fields(settings):
    full = assessment(kind="code-impact", total=1, candidates=[candidate()], dispositions=[],
                      disposition_history_total=0, limit=50, offset=0, next_offset=None)
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: stream_json(full)).call_tool(
            "decision_assessment_get",
            {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID, **COMPACT},
        )
    missing = copy.deepcopy(full)
    missing["candidates"][0].pop("title")
    with pytest.raises(ToolError):
        await adapter(settings, lambda request: stream_json(missing)).call_tool(
            "decision_assessment_get", {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID},
        )


async def test_tool_catalog_exposes_optional_projection_defaults(settings):
    catalog = {tool.name: tool for tool in await adapter(settings, lambda request: stream_json({})).list_tools()}
    for name in ("decision_impact", "decision_assessments", "decision_assessment_get"):
        properties = catalog[name].inputSchema["properties"]
        assert properties["detail"]["default"] == "full"
        assert properties["include_diff"]["default"] is True
        assert properties["include_scopes"]["default"] == "full"
