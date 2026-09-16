"""Attach current search metadata to older result fixtures, preserving explicit test envelopes."""

import json
from uuid import UUID

import httpx

from mnemonic_mcp.search_disclosure import (
    ArtifactAppliedFilters,
    TranscriptAppliedFilters,
    WorkAppliedFilters,
    search_disclosure,
)


def disclosed_response(request: httpx.Request, response: httpx.Response) -> httpx.Response:
    path = request.url.path
    route = path.split("/projects/")[-1].split("/", 1)
    if len(route) != 2 or route[1] not in {
        "search", "work-items", "transcripts", "artifacts/search-content", "transcripts/search-content",
    } or response.status_code != 200:
        return response
    value = response.json()
    if not isinstance(value, dict) or not isinstance(value.get("items"), list) or any(
        name in value for name in ("applied_filters", "query_interpretation", "warnings")
    ):
        return response
    params = json.loads(request.content) if request.method == "POST" else dict(request.url.params)
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
    disclosure = search_disclosure(
        UUID(route[0]), q, fulltext=fulltext, semantic=semantic,
        **{source: model.model_validate({key: item for key, item in filters.get(source, {}).items()
                                        if key in model.model_fields})
           for source, model in (("work_items", WorkAppliedFilters),
                                 ("artifacts", ArtifactAppliedFilters),
                                 ("transcripts", TranscriptAppliedFilters)) if source in scopes},
    )
    return httpx.Response(response.status_code, headers=response.headers,
                          json={**value, **disclosure.model_dump(mode="json")})
