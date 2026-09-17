"""Native discovery contracts for temporal scope, partial diagnostics and tag vocabulary."""

import copy
import json
from uuid import UUID

import pytest
from conftest import PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_compact_search import native_call, response_page

from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)
from mnemonic_mcp.search_exploration import DateBounds, TagCountPage, TagCountRequest

TOOLS = ["search", "search_work", "search_artifact_contents", "search_transcript_contents",
         "list_transcripts"]
LOWER = "2000-01-01T00:00:00Z"
UPPER = "2100-01-01T00:00:00Z"
MODELS = {"work_items": WorkAppliedFilters, "artifacts": ArtifactAppliedFilters,
          "transcripts": TranscriptAppliedFilters}


def exploratory_page(tool, work_summary):
    page = response_page(tool, work_summary)
    scopes = [name for name in MODELS if page["applied_filters"][name] is not None]
    dates = {"created_after": LOWER, "updated_before": UPPER}
    page.update(search_disclosure(UUID(PROJECT_ID), "needle", diagnostics="always", **{
        name: MODELS[name](**dates) for name in scopes
    }).model_dump(mode="json"))
    page["term_diagnostics"] = [{"term": "needle", "matches": {
        name: 1 if name in scopes else None for name in MODELS
    }}]
    args = {"diagnostics": "always", "query" if tool == "list_transcripts" else "q": "needle"}
    if tool == "search":
        args["filters"] = {name: dates for name in scopes}
    else:
        args.update(dates)
    return page, args


@pytest.mark.parametrize("tool", TOOLS)
async def test_dates_and_positive_diagnostics_round_trip_at_every_front_door(
    settings, work_summary, tool,
):
    page, args = exploratory_page(tool, work_summary)
    actual, requests = await native_call(settings, tool, args, page)
    assert actual == page
    request = requests[0]
    sent = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
    assert sent["diagnostics"] == "always"
    filters = sent["filters"]["work_items"] if tool == "search" else sent
    assert filters["created_after"] == LOWER and filters["updated_before"] == UPPER


@pytest.mark.parametrize("change", ["mode", "missing_mode", "dates", "naive", "non_utc", "null",
                                    "unsearched_count", "duplicate_term"])
async def test_unified_search_rejects_ambiguous_or_mismatched_exploration_scope(
    settings, work_summary, change,
):
    page, args = exploratory_page("search", work_summary)
    if change == "mode":
        page["diagnostics"] = "off"
    elif change == "missing_mode":
        del page["diagnostics"]
    elif change == "dates":
        del page["applied_filters"]["work_items"]["created_after"]
    elif change in {"naive", "non_utc", "null"}:
        page["applied_filters"]["work_items"]["created_after"] = {
            "naive": "2000-01-01T00:00:00", "non_utc": "2000-01-01T01:00:00+01:00", "null": None,
        }[change]
    elif change == "duplicate_term":
        page["term_diagnostics"] *= 2
    else:
        page["term_diagnostics"][0]["matches"]["work_items"] = None
    with pytest.raises(ToolError):
        await native_call(settings, "search", args, page)


@pytest.mark.parametrize("mode", ["off", "on_empty"])
async def test_positive_counts_require_explicit_always(settings, work_summary, mode):
    page, args = exploratory_page("search_work", work_summary)
    page["diagnostics"] = args["diagnostics"] = mode
    with pytest.raises(ToolError):
        await native_call(settings, "search_work", args, page)
    page["term_diagnostics"] = []
    actual, _ = await native_call(settings, "search_work", args, page)
    assert actual["term_diagnostics"] == []


async def test_unified_tag_vocabulary_pagination_and_declared_population(settings, work_summary):
    page, args = exploratory_page("search", work_summary)
    args["tag_counts"] = {"limit": 1, "offset": 0}
    page["tag_counts"] = {"items": [{"tag": "release", "count": 1}], "total": 2,
                          "limit": 1, "offset": 0, "next_offset": 1,
                          "count_unit": "canonical_work_items", "member_scope": "returned_work_items",
                          "selected_tag_applied": True}
    actual, requests = await native_call(settings, "search", args, page)
    assert actual["tag_counts"] == page["tag_counts"]
    assert json.loads(requests[0].content)["tag_counts"] == args["tag_counts"]
    for patch in ({"next_offset": None}, {"limit": 2}, {"count_unit": "checkpoints"},
                  {"selected_tag_applied": 1}, {"items": [{"tag": "release", "count": 2}]}):
        altered = copy.deepcopy(page)
        altered["tag_counts"].update(patch)
        with pytest.raises(ToolError):
            await native_call(settings, "search", args, altered)


@pytest.mark.parametrize("dates,code", [
    ({"created_after": "2026-09-01T12:00:00"}, "search_datetime_timezone_required"),
    ({"created_after": UPPER, "created_before": LOWER}, "search_created_range_invalid"),
    ({"updated_after": LOWER, "updated_before": LOWER}, "search_updated_range_invalid"),
])
async def test_reviewed_date_rules_are_safe_actionable_errors(settings, work_summary, dates, code):
    with pytest.raises(ToolError, match=code):
        await native_call(settings, "search_work", dates, response_page("search_work", work_summary))


async def test_tag_counts_requires_work_selection(settings, work_summary):
    with pytest.raises(ToolError, match="tag_counts_requires_work_facet"):
        await native_call(settings, "search", {"facets": ["artifacts"], "tag_counts": {}},
                          response_page("search", work_summary))


def test_dates_preserve_microseconds_normalize_utc_and_use_half_open_intervals():
    bounds = DateBounds(created_after="2026-09-01T01:00:00.000001+01:00",
                        created_before="2026-09-01T00:00:00.000002Z")
    assert bounds.model_dump(mode="json")["created_after"] == "2026-09-01T00:00:00.000001Z"
    assert bounds.contains(bounds.created_after, bounds.created_after)
    assert not bounds.contains(bounds.created_before, bounds.created_before)
    with pytest.raises(ValidationError):
        DateBounds(created_after=bounds.created_before, created_before=bounds.created_after)


def test_tag_vocabulary_rejects_duplicate_or_out_of_order_tags_and_boolean_counts():
    page = {"items": [{"tag": "b", "count": 1}, {"tag": "a", "count": 1}], "total": 2,
            "limit": 2, "offset": 0, "next_offset": None, "count_unit": "canonical_work_items",
            "member_scope": "returned_work_items", "selected_tag_applied": True}
    assert not TagCountPage.model_validate(page).matches_request(TagCountRequest(limit=2), 2)
    page["items"][0]["count"] = True
    with pytest.raises(ValidationError):
        TagCountPage.model_validate(page)
