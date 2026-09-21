"""Attach current search metadata to older result fixtures, preserving explicit test envelopes."""

import json
from types import SimpleNamespace
from uuid import UUID

import httpx
from search_ranking_fixtures import add_ranking

from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
    withholding_warnings,
)


def disclosed_response(request: httpx.Request, response: httpx.Response) -> httpx.Response:
    path = request.url.path
    route = path.split("/projects/")[-1].split("/", 1)
    if len(route) != 2 or route[1] not in {
        "search", "work-items", "transcripts", "artifacts/search-content", "transcripts/search-content",
    } or response.status_code != 200:
        return response
    value = response.json()
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return response
    params = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
    if request.method == "GET" and "work_fields" in request.url.params:
        params["work_fields"] = request.url.params.get_list("work_fields")
    value.setdefault("diagnostics", params.get("diagnostics", "on_empty"))
    if route[1] == "search":
        value.setdefault("tag_counts", None)
    if route[1] == "work-items":
        value.setdefault("term_diagnostics", [])
    value.setdefault("detail", params.get("detail", "compact"))
    if route[1] in {"search", "work-items"}:
        value.setdefault("work_rank_scope", "work_items")
    _project_defaults(value, route)
    q = params.get("q", params.get("query", ""))
    fulltext = params.get("fulltext", False)
    semantic = params.get("semantic") in (True, "true")
    filters = params.get("filters", {})
    scopes = value.get("search_scope", {}).get("searched_facets", [])
    if route[1] != "search":
        scopes = [{"work-items": "work_items", "transcripts": "transcripts",
                   "artifacts/search-content": "artifacts",
                   "transcripts/search-content": "transcripts"}[route[1]]]
        filters = {scopes[0]: params}
    work = filters.get("work_items", {})
    semantic = semantic or work.get("semantic", False)
    source = "search" if route[1] == "search" else scopes[0]
    add_ranking(value, source, q or "", params.get("query_mode", "terms"), semantic)
    if any(name in value for name in ("applied_filters", "query_interpretation", "warnings")):
        return httpx.Response(response.status_code, headers=response.headers, json=value)
    disclosure = search_disclosure(
        UUID(route[0]), q, fulltext=fulltext, semantic=semantic,
        query_mode=params.get("query_mode", "terms"),
        diagnostics=params.get("diagnostics", "on_empty"),
        **{source: model.model_validate({key: item for key, item in filters.get(source, {}).items()
                                        if key in model.model_fields})
           for source, model in (("work_items", WorkAppliedFilters),
                                 ("artifacts", ArtifactAppliedFilters),
                                 ("transcripts", TranscriptAppliedFilters)) if source in scopes},
    )
    withheld = value.get("sensitive_content_withheld", value.get("coverage", {}).get(
        "artifacts", {}).get("sensitive_content_withheld", 0))
    disclosure.warnings += withholding_warnings(SimpleNamespace(sensitive_content_withheld=withheld))
    return httpx.Response(response.status_code, headers=response.headers,
                          json={**value, **disclosure.model_dump(mode="json")})


def _project_defaults(value, route):
    if route[1] == "search":
        for hit in value["items"]:
            hit.setdefault("project_id", route[0])
        value.setdefault("project_coverage", [{
            "project_id": route[0], "project_name": "Test project", "project_slug": "test-project",
            "facet_totals": value["facet_totals"], "coverage": value["coverage"],
            "indexing_incomplete": value["indexing_incomplete"],
        }])
