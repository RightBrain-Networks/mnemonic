"""Request-bound ranking and evidence checks in addition to identity checks."""

from .models import CompactWorkHit, HierarchySummary, WorkPage
from .search_models import (
    ArtifactFacetHit,
    SearchPage,
    SearchRequest,
    TranscriptFacetHit,
    WorkFacetHit,
)
from .search_query import QueryMode, WorkField, evidence_matches
from .search_ranking import ranking_matches, semantic_matches, source_ranking


def work_ranking_matches(page: WorkPage, query: str | None, mode: QueryMode,
                         fields: list[WorkField], semantic: bool) -> bool:
    if not ranking_matches(page, query, mode, work=True, semantic=semantic):
        return False
    for position, item in enumerate(page.items, page.offset + 1):
        if not {"rank", "score", "score_type"} <= item.model_fields_set:
            return False
        if item.rank != position or item.score_type != page.score_type or (
            item.score_type == "none" and item.score != 0
        ):
            return False
        if isinstance(item, HierarchySummary):
            if (query or "").strip() or item.score != 0:
                return False
            continue
        member = item.matched_member
        identity = member.id if member else item.id if isinstance(item, CompactWorkHit) else None
        if identity is None or not evidence_matches(item, identity, query, mode, fields, semantic):
            return False
    return True


def _native_matches(hit: WorkFacetHit | ArtifactFacetHit | TranscriptFacetHit,
                    page: SearchPage, request: SearchRequest) -> bool:
    native = (hit.work_item if isinstance(hit, WorkFacetHit) else hit.artifact
              if isinstance(hit, ArtifactFacetHit) else hit.transcript)
    if native.rank is None or native.rank > getattr(page.facet_totals, hit.facet) or (
        native.score_type != getattr(page.facet_score_types, hit.facet)
    ):
        return False
    if native.score_type == "none" and native.score not in (None, 0):
        return False
    if not isinstance(hit, WorkFacetHit):
        return True
    item = hit.work_item
    member = item.matched_member
    identity = member.id if member else item.id if isinstance(item, CompactWorkHit) else None
    filters = request.filters.work_items
    return identity is not None and evidence_matches(
        item, identity, request.q, request.query_mode, filters.work_fields, filters.semantic,
    )


def unified_ranking_matches(page: SearchPage, request: SearchRequest) -> bool:
    selected = page.search_scope.searched_facets
    semantic = request.filters.work_items.semantic and "work_items" in selected
    if not semantic_matches(page.semantic, semantic):
        return False
    kinds = set()
    for source in ("work_items", "artifacts", "transcripts"):
        expected = source_ranking(request.q, request.query_mode, work=source == "work_items",
                                  semantic=semantic and source == "work_items")
        actual = (getattr(page.facet_score_types, source), getattr(page.facet_total_kinds, source))
        if actual != (expected if source in selected else (None, None)):
            return False
        if source in selected:
            kinds.add(expected[1])
    expected_kind = ("mixed" if len(kinds) > 1 else next(iter(kinds)) if kinds else
                     "lexical_matches" if request.q.strip() else "browsed_records")
    expected_score = "unified_reciprocal_rank" if request.q.strip() else "none"
    if page.total_kind != expected_kind or page.score_type != expected_score:
        return False
    for source in selected:
        ranks = [hit.work_item.rank if isinstance(hit, WorkFacetHit) else hit.artifact.rank
                 if isinstance(hit, ArtifactFacetHit) else hit.transcript.rank
                 for hit in page.items if hit.facet == source]
        if len(set(ranks)) != len(ranks):
            return False
    return all(hit.rank == position and hit.score_type == expected_score
               and (expected_score != "none" or hit.score == 0)
               and _native_matches(hit, page, request)
               for position, hit in enumerate(page.items, page.offset + 1))
