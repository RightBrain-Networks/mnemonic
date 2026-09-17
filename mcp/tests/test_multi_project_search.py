"""The existing search tool binds explicit project selection to every result and count."""

import copy
import json
from uuid import UUID

import pytest
from conftest import NOW, OTHER_WORK_ID, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from search_ranking_fixtures import add_ranking
from test_compact_search import native_call, response_page, work_pointer
from test_unified_search import page

from mnemonic_mcp.search_disclosure import WorkAppliedFilters, search_disclosure

OTHER_PROJECT = "77777777-7777-4777-8777-777777777777"


def combined(summary):
    first = work_pointer(summary)
    second = copy.deepcopy(first)
    second.update(id=OTHER_WORK_ID, project_id=OTHER_PROJECT,
                  canonical_work_item_id=OTHER_WORK_ID, rank=2)
    items = [{"facet": "work_items", "id": item["id"], "project_id": item["project_id"],
              "created_at": NOW, "updated_at": NOW, "score": 1 / (60 + rank), "work_item": item}
             for rank, item in enumerate((first, second), start=1)]
    result = page(items, detail="compact", limit=20, work_rank_scope="work_items", tag_counts=None)
    result["search_scope"].update(searched_facets=["work_items"], transcripts="not_selected")
    result["project_coverage"] = [{
        "project_id": identity, "project_name": "Selected project", "project_slug": "selected",
        "facet_totals": {"work_items": 1, "artifacts": 0, "transcripts": 0},
        "coverage": copy.deepcopy(result["coverage"]), "indexing_incomplete": False,
    } for identity in (PROJECT_ID, OTHER_PROJECT)]
    result.update(search_disclosure(
        tuple(sorted([UUID(PROJECT_ID), UUID(OTHER_PROJECT)], key=str)), "needle",
        work_items=WorkAppliedFilters(),
    ).model_dump(mode="json"))
    return add_ranking(result, "search", "needle")


async def test_selected_projects_use_one_safe_read_and_preserve_global_order(settings, work_summary):
    response = combined(work_summary)
    actual, requests = await native_call(settings, "search", {
        "project_id": None, "project_ids": [OTHER_PROJECT, PROJECT_ID],
        "q": "needle", "facets": ["work_items"],
    }, response)
    assert actual == response
    assert len(requests) == 1 and requests[0].method == "POST"
    assert requests[0].url.path == "/api/v1/search"
    body = json.loads(requests[0].content)
    assert body["project_ids"] == sorted([PROJECT_ID, OTHER_PROJECT])
    assert "project_id" not in body and "client_operation_id" not in body


@pytest.mark.parametrize("failure", ["hit-project", "missing-project", "duplicate-project",
                                     "facet-count", "coverage-count", "incomplete"])
async def test_project_scopes_and_coverage_cannot_be_silently_changed(
    settings, work_summary, failure,
):
    response = combined(work_summary)
    if failure == "hit-project":
        response["items"][1]["project_id"] = PROJECT_ID
    elif failure == "missing-project":
        response["project_coverage"].pop()
    elif failure == "duplicate-project":
        response["project_coverage"][1]["project_id"] = PROJECT_ID
    elif failure == "facet-count":
        response["project_coverage"][0]["facet_totals"]["work_items"] = 2
    elif failure == "coverage-count":
        response["project_coverage"][0]["coverage"]["artifacts"]["indexing"]["pending"] = 1
    else:
        response["project_coverage"][0]["indexing_incomplete"] = True
    with pytest.raises(ToolError):
        await native_call(settings, "search", {
            "project_id": None, "project_ids": [PROJECT_ID, OTHER_PROJECT],
            "q": "needle", "facets": ["work_items"],
        }, response)


@pytest.mark.parametrize("selection", [
    {"project_id": None},
    {"project_id": PROJECT_ID, "project_ids": [OTHER_PROJECT]},
    {"project_id": None, "project_ids": []},
    {"project_id": None, "project_ids": [PROJECT_ID, PROJECT_ID]},
    {"project_id": None, "project_ids": [PROJECT_ID] * 11},
])
async def test_project_selectors_are_exclusive_unique_and_bounded(settings, selection):
    with pytest.raises(ToolError):
        await native_call(settings, "search", selection, {})


@pytest.mark.parametrize("facet", ["artifacts", "transcripts"])
async def test_returned_incomplete_hits_are_bound_to_their_own_project(
    settings, work_summary, facet,
):
    response = response_page("search", work_summary, q="needle")
    item = next(hit for hit in response["items"] if hit["facet"] == facet)
    response["items"] = [item]
    response["total"] = 1
    response["facet_totals"] = {"work_items": 0, "artifacts": 0, "transcripts": 0, facet: 1}
    response["search_scope"].update(searched_facets=[facet],
                                    transcripts="searched" if facet == "transcripts" else "not_selected")
    if facet == "transcripts":
        item["transcript"].update(status="pending", index_status="pending")
        response["coverage"]["artifacts"]["indexing"].update(pending=0, ready=0)
        response["coverage"]["transcripts"]["indexing_incomplete"] = True
    response["indexing_incomplete"] = True
    owner = {"project_id": PROJECT_ID, "project_name": "Owner", "project_slug": "owner",
             "facet_totals": copy.deepcopy(response["facet_totals"]),
             "coverage": copy.deepcopy(response["coverage"]), "indexing_incomplete": True}
    empty = {"project_id": OTHER_PROJECT, "project_name": "Empty", "project_slug": "empty",
             "facet_totals": {"work_items": 0, "artifacts": 0, "transcripts": 0},
             "coverage": {"artifacts": {"enabled": True, "indexing": {
                 "pending": 0, "ready": 0, "failed": 0, "truncated": 0,
             }, "sensitive_content_withheld": 0}, "transcripts": {"indexing_incomplete": False}},
             "indexing_incomplete": False}
    response["project_coverage"] = [owner, empty]
    from mnemonic_mcp.search_disclosure import ArtifactAppliedFilters, TranscriptAppliedFilters
    scopes = {"artifacts": ArtifactAppliedFilters()} if facet == "artifacts" else {
        "transcripts": TranscriptAppliedFilters(),
    }
    response.update(search_disclosure(
        tuple(sorted([UUID(PROJECT_ID), UUID(OTHER_PROJECT)], key=str)), "needle", **scopes,
    ).model_dump(mode="json"))
    item["rank"] = 1
    for key in ("facet_total_kinds", "facet_score_types"):
        response[key] = {source: value if source == facet else None
                         for source, value in response[key].items()}
    add_ranking(response, "search", "needle")
    args = {"project_id": None, "project_ids": [PROJECT_ID, OTHER_PROJECT],
            "q": "needle", "facets": [facet]}
    await native_call(settings, "search", args, response)
    owner["coverage"], empty["coverage"] = empty["coverage"], owner["coverage"]
    owner["indexing_incomplete"], empty["indexing_incomplete"] = False, True
    with pytest.raises(ToolError):
        await native_call(settings, "search", args, response)
