"""Unified search uses the same work matching and canonical alias semantics."""

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import semantic_unavailable
from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import WorkIdentityPointer, WorkItemListQuery, WorkSearchHit
from mnemonic_api.search_schemas import SearchHit, SearchRequest, WorkFacetHit
from mnemonic_api.semantic import (
    Embedder,
    EmbeddingCacheUpdate,
    capture_embedding_candidates,
    rank_embedding_candidates,
)
from mnemonic_api.services.duplicates import canonical_projections
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource
from mnemonic_api.services.work_search import (
    SearchSelection,
    _lexical_rows,
    _lexical_selections,
    _scope_rows,
    _summaries_with_ancestry,
    _validate_root_filter,
    provenance_conditions,
    status_conditions,
)

logger = logging.getLogger(__name__)


def _work_corpus(database: Session, project_id: UUID, filters: WorkItemListQuery, as_of: datetime):
    visible = list(database.scalars(select(WorkItem).where(
        WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None),
    )))
    projections = canonical_projections(database, project_id, visible)
    root = _validate_root_filter(
        database, project_id, filters.canonical_work_item_id, visible, projections,
    )
    conditions = [
        WorkItem.project_id == project_id, WorkItem.deleted_at.is_(None),
        *status_conditions(filters.status, as_of), *provenance_conditions(filters),
    ]
    if filters.external_url is not None:
        conditions.append(WorkItem.external_references.contains([{"url": filters.external_url}]))
    filtered = list(database.scalars(select(WorkItem).where(*conditions)))
    scoped = _scope_rows(filtered, filters, projections, root)
    return visible, projections, scoped


def _semantic_selections(
    database: Session, pool, scoped, projections, lexical_rows,
    filters: WorkItemListQuery, query_vector: tuple[float, ...], embedder: Embedder,
) -> tuple[list[SearchSelection], dict[UUID, float], list[EmbeddingCacheUpdate]]:
    captured = capture_embedding_candidates(database, pool, dimensions=len(query_vector))
    try:
        ranked, updates = rank_embedding_candidates(
            captured, [identity for identity, _ in lexical_rows], query_vector, embedder,
        )
    except Exception as exc:
        logger.error("Unified semantic ranking failed (%s)", type(exc).__name__)
        raise semantic_unavailable() from None
    by_id = {item.id: item for item in scoped}
    selections: list[SearchSelection] = []
    scores: dict[UUID, float] = {}
    for rank, member_id in enumerate(ranked, start=1):
        identity = (projections[member_id].canonical_work_item.id
                    if filters.duplicate_scope == "canonical" else member_id)
        if identity not in by_id or identity in scores:
            continue
        selections.append(SearchSelection(by_id[identity], member_id))
        scores[identity] = 1.0 / rank
    return selections, scores, updates


def work_source(
    database: Session, project_id: UUID, request: SearchRequest, as_of: datetime,
    *, query_vector: tuple[float, ...] | None = None, embedder: Embedder,
) -> tuple[SearchSource, list[EmbeddingCacheUpdate]]:
    filters = WorkItemListQuery(**request.filters.work_items.model_dump(exclude={"semantic"}))
    visible, projections, scoped = _work_corpus(database, project_id, filters, as_of)
    pool = visible if filters.duplicate_scope == "canonical" else scoped
    lexical_rows = _lexical_rows(database, request.q, pool)
    updates: list[EmbeddingCacheUpdate] = []
    if query_vector is not None:
        selections, scores, updates = _semantic_selections(
            database, pool, scoped, projections, lexical_rows, filters, query_vector, embedder,
        )
    else:
        selections = _lexical_selections(scoped, filters, projections, lexical_rows, request.q)
        lexical_scores = dict(lexical_rows)
        scores = {selection.work_item.id: lexical_scores.get(selection.matched_member_id, 0.0)
                  for selection in selections}
    by_id = {selection.work_item.id: selection for selection in selections}
    pointers = {item.id: WorkIdentityPointer.model_validate(item) for item in visible}
    candidates = [SearchCandidate(
        facet="work_items", id=selection.work_item.id, created_at=selection.work_item.created_at,
        updated_at=selection.work_item.updated_at, score=scores[selection.work_item.id],
        priority=selection.work_item.priority,
    ) for selection in selections]

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        summaries = _summaries_with_ancestry(
            database, project_id, [by_id[item.id].work_item for item in page], as_of=as_of,
        )
        summary_by_id = {summary.work_item.id: summary for summary in summaries}
        return {item.id: WorkFacetHit(**item.fields(), work_item=WorkSearchHit(
            summary=summary_by_id[item.id],
            matched_member=pointers[by_id[item.id].matched_member_id],
        )) for item in page}

    return SearchSource(candidates, hydrate), updates
