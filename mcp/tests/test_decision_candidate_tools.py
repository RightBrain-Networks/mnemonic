"""Grouped candidate reads bind scope, filters and both reachable pagination layers."""

import copy

import pytest
from conftest import NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import ASSESSMENT_ID, DISPOSITION_ID
from test_tools import adapter, structured

CHANGE = "516c597f-90a6-487c-afce-2e7375aeccf0"


def occurrence(revision):
    return {"assessment_id": ASSESSMENT_ID if revision == 0 else WORK_ID,
        "assessment_version": 0 if revision == 0 else 5,
        "before_reference": f"ADR-00001-{revision}", "after_reference": f"ADR-00002-{revision}",
        "trigger": "supersession", "candidate_reference": f"ADR-00001-{revision}",
        "relationship": "origin", "related_direction": None,
        "latest_disposition": None if revision == 0 else {"id": DISPOSITION_ID, "version": 5,
            "disposition": "change-required", "remediation_work_item_id": None}}


def response():
    return {"project_id": PROJECT_ID, "change_id": CHANGE, "scope_version": 2,
        "acknowledged_scope_version": 0, "assessment_count": 2, "observed_at": NOW,
        "decision_id": None, "decision_count": 1, "revision_count": 2, "occurrence_count": 2,
        "unassessed_count": 1, "change_required_count": 1, "total": 1,
        "limit": 20, "offset": 0, "next_offset": None, "occurrence_limit": 10,
        "occurrence_offset": 0, "max_occurrences_per_page": 200, "items": [{
            "decision_id": "ADR-00001", "occurrence_count": 2, "revision_count": 2,
            "unassessed_count": 1, "change_required_count": 1,
            "captured_references": ["ADR-00001-0", "ADR-00001-1"], "references_complete": True,
            "occurrences": [occurrence(0), occurrence(1)], "next_occurrence_offset": None,
        }]}


async def test_grouped_candidate_read_is_request_bound_and_uses_compact_dispositions(settings):
    expected = response()

    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith(f"/changes/{CHANGE}/candidates")
        assert dict(request.url.params) == {"limit": "20", "offset": "0", "occurrence_limit": "10",
                                           "occurrence_offset": "0"}
        return stream_json(expected)

    actual = structured(await adapter(settings, handler).call_tool("decision_change_candidates", {
        "project_id": PROJECT_ID, "change_id": CHANGE,
    }))
    assert actual == expected
    assert "reason" not in actual["items"][0]["occurrences"][1]["latest_disposition"]


@pytest.mark.parametrize("offset", [0, 1, 2, 10])
async def test_occurrence_pages_disclose_partial_references_and_exact_continuation(settings, offset):
    body = response()
    body.update(decision_id="ADR-00001", limit=1, occurrence_limit=1, occurrence_offset=offset)
    group = body["items"][0]
    group["occurrences"] = group["occurrences"][offset:offset + 1]
    group["captured_references"] = [item["candidate_reference"] for item in group["occurrences"]]
    group["references_complete"] = False
    group["next_occurrence_offset"] = 1 if offset == 0 else None

    def handler(request):
        assert request.url.params["decision_id"] == "ADR-00001"
        assert request.url.params["occurrence_offset"] == str(offset)
        return stream_json(body)

    actual = structured(await adapter(settings, handler).call_tool("decision_change_candidates", {
        "project_id": PROJECT_ID, "change_id": CHANGE, "decision_id": "ADR-00001", "limit": 1,
        "occurrence_limit": 1, "occurrence_offset": offset,
    }))
    assert actual == body


@pytest.mark.parametrize("changes", [
    {"project_id": WORK_ID}, {"change_id": WORK_ID}, {"scope_version": 3},
    {"acknowledged_scope_version": 3}, {"decision_count": 2}, {"revision_count": 3},
    {"occurrence_count": 3}, {"unassessed_count": 0}, {"change_required_count": 0},
    {"total": 0}, {"limit": 10}, {"offset": 1}, {"next_offset": 1},
    {"occurrence_limit": 5}, {"occurrence_offset": 1}, {"max_occurrences_per_page": 1000},
    {"decision_id": "ADR-00001"},
])
async def test_unbound_scope_totals_and_query_arguments_are_rejected(settings, changes):
    body = response() | changes
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(body)).call_tool(
            "decision_change_candidates", {"project_id": PROJECT_ID, "change_id": CHANGE},
        )


@pytest.mark.parametrize("change", ["unassessed", "revisions", "false-complete", "hidden-reference",
                                    "duplicate", "wrong-group", "context", "outgoing", "version",
                                    "reason", "continuation"])
async def test_grouped_view_cannot_hide_or_reclassify_episode_obligations(settings, change):
    body = response()
    group = body["items"][0]
    if change == "duplicate":
        group["occurrences"][1] = copy.deepcopy(group["occurrences"][0])
    elif change == "hidden-reference":
        group["captured_references"].pop()
    elif change == "context":
        group["occurrences"][0]["relationship"] = "depends-on-prerequisite"
    elif change == "outgoing":
        group["occurrences"][0].update(relationship="related-only", related_direction="outgoing")
    elif change == "version":
        group["occurrences"][1]["assessment_version"] = 4
    elif change == "reason":
        group["occurrences"][1]["latest_disposition"]["reason"] = "Unbounded evidence prose"
    else:
        key, value = {"unassessed": ("unassessed_count", 0), "revisions": ("revision_count", 1),
            "false-complete": ("references_complete", False), "wrong-group": ("decision_id", "ADR-00003"),
            "continuation": ("next_occurrence_offset", 1)}[change]
        group[key] = value
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: stream_json(body)).call_tool(
            "decision_change_candidates", {"project_id": PROJECT_ID, "change_id": CHANGE},
        )


@pytest.mark.parametrize("changes", [{"limit": 0}, {"limit": 101}, {"occurrence_limit": 0},
                                     {"limit": 21}, {"occurrence_offset": -1}])
async def test_invalid_group_bounds_fail_before_transport(settings, changes):
    calls = []

    def handler(request):
        calls.append(request)
        return stream_json(response())

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("decision_change_candidates", {
            "project_id": PROJECT_ID, "change_id": CHANGE, **changes,
        })
    assert calls == []
