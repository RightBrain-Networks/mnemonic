"""Implementation-only discovery keeps the review lifecycle and disclosure intact."""

import json

import httpx
import pytest
from conftest import PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import call
from test_compact_search import work_pointer
from test_search_contract import QUERY, search_result
from test_unified_search import page, work_hit


@pytest.mark.parametrize("detail", ["compact", "full"])
@pytest.mark.parametrize("status,accepted", [("done", True), ("to-review", False), ("deferred", False)])
async def test_work_item_scope_uses_parent_status_with_pending_review(
    settings, work_summary, detail, status, accepted,
):
    work_summary["work_item"]["status"] = "done"
    work_summary["readiness"].update(
        lifecycle_status="done", is_terminal=True, is_ready=False,
        display_state="to-review", review_status="to-review",
    )
    hit = work_hit(work_summary, score=0.0)
    if detail == "compact":
        hit["work_item"] = {**work_pointer(work_summary), "search_status": "to-review",
                            "display_state": "to-review"}

    def handler(request):
        assert json.loads(request.content)["filters"]["work_items"]["status_scope"] == "work_item"
        return httpx.Response(200, json=page([hit]))

    arguments = {"project_id": PROJECT_ID, "detail": detail,
                 "filters": {"work_items": {"status": status, "status_scope": "work_item"}}}
    if accepted:
        result = await call(settings, "search", arguments, handler)
        assert result["total"] == 1
        assert result["applied_filters"]["work_items"]["status_scope"] == "work_item"
    else:
        with pytest.raises(ToolError, match="unexpected response"):
            await call(settings, "search", arguments, handler)


@pytest.mark.parametrize("echoed", [None, "effective", "work_item"])
async def test_work_item_scope_requires_matching_disclosure_even_without_hits(settings, echoed):
    response = search_result("search", "done")
    if echoed is not None:
        response["applied_filters"]["work_items"]["status_scope"] = echoed
    arguments = {"project_id": PROJECT_ID, "q": QUERY,
                 "facets": ["work_items", "artifacts", "transcripts"],
                 "filters": {"work_items": {"status": "done", "status_scope": "work_item"}}}
    def handler(request):
        return httpx.Response(200, json=response)

    if echoed == "work_item":
        assert (await call(settings, "search", arguments, handler))["total"] == 0
    else:
        with pytest.raises(ToolError, match="unexpected response"):
            await call(settings, "search", arguments, handler)
