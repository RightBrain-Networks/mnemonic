"""Lineage reads retain factual membership, safe readiness, and complete paging."""

import copy

import pytest
from conftest import NOW, PROJECT_ID, WORK_ID, stream_json
from mcp.server.fastmcp.exceptions import ToolError
from test_decision_change_tools import CHANGE_ID, change
from test_tools import adapter, structured

MEMBER = "ab84efc9-8516-478d-8f56-90163a61dbae"


def member(readiness):
    return {
        "work_item_id": MEMBER, "project_id": PROJECT_ID, "title": "Repair known defect",
        "status": "pending", "deleted_at": None, "canonical_work_item_id": MEMBER,
        "readiness": {**readiness, "canonical_work_item_id": MEMBER},
        "is_decision_source": False,
        "discovered_from": {"items": [{"work_item_id": WORK_ID, "project_id": PROJECT_ID}],
                            "total": 1, "omitted_count": 0},
        "code_review": None, "review_follow_up": None,
        "remediation_origin": {"review_id": CHANGE_ID, "source_work_item_id": WORK_ID,
                               "source_project_id": PROJECT_ID, "depth": 1},
    }


async def test_lineage_get_forwards_page_and_keeps_cross_project_context(settings, readiness):
    row = member(readiness)
    row["project_id"] = CHANGE_ID
    row["code_review"] = {"review_id": CHANGE_ID, "state": "completed", "status": None}
    response = change(lineage_work={"items": [row], "total": 4, "limit": 1,
                                   "offset": 2, "next_offset": 3})

    def handler(request):
        assert dict(request.url.params) == {"lineage_limit": "1", "lineage_offset": "2"}
        return stream_json(response)

    result = structured(await adapter(settings, handler).call_tool("decision_change_get", {
        "project_id": PROJECT_ID, "change_id": CHANGE_ID,
        "lineage_limit": 1, "lineage_offset": 2,
    }))
    assert result == response


@pytest.mark.parametrize("mutation", [
    "wrong-offset", "missing-page", "short-page", "driver-member", "duplicate-member",
    "wrong-canonical", "deleted-with-readiness", "bad-origins", "unsafe-lease",
])
async def test_lineage_get_rejects_incoherent_or_unsafe_response(settings, readiness, mutation):
    row = member(readiness)
    response = change(lineage_work={"items": [row], "total": 1, "limit": 2,
                                   "offset": 0, "next_offset": None})
    page = response["lineage_work"]
    if mutation == "wrong-offset":
        page.update(items=[], offset=2)
    elif mutation == "missing-page":
        del response["lineage_work"]
    elif mutation == "short-page":
        page.update(total=2, next_offset=1)
    elif mutation == "driver-member":
        response["review_work_item_id"] = MEMBER
    elif mutation == "duplicate-member":
        page.update(items=[row, copy.deepcopy(row)], total=2)
    elif mutation == "wrong-canonical":
        row["canonical_work_item_id"] = WORK_ID
    elif mutation == "deleted-with-readiness":
        row["deleted_at"] = NOW
    elif mutation == "bad-origins":
        row["discovered_from"]["total"] = 2
    else:
        row["readiness"]["active_lease"] = {
            "holder_client": "codex", "holder_session_id": "thread", "acquired_at": NOW,
            "renewed_at": NOW, "expires_at": NOW, "lease_token": "private-capability",
        }
    server = adapter(settings, lambda request: stream_json(response))
    with pytest.raises(ToolError):
        await server.call_tool("decision_change_get", {
            "project_id": PROJECT_ID, "change_id": CHANGE_ID, "lineage_limit": 2,
        })


@pytest.mark.parametrize("params", [
    {"lineage_limit": 0}, {"lineage_limit": 101}, {"lineage_offset": -1},
    {"lineage_limit": True},
])
async def test_lineage_invalid_paging_does_not_reach_http(settings, params):
    server = adapter(settings, lambda request: pytest.fail("Invalid pagination reached HTTP"))
    with pytest.raises(ToolError):
        await server.call_tool("decision_change_get", {
            "project_id": PROJECT_ID, "change_id": CHANGE_ID, **params,
        })
