"""Unified search uses the same work matching and canonical alias semantics."""

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import semantic_unavailable
from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import WorkIdentityPointer, WorkItemListQuery, WorkSearchHit
from mnemonic_api.search_exploration import date_conditions
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_ranking import search_ranking
from mnemonic_api.search_schemas import SearchHit, SearchRequest, WorkFacetHit
from mnemonic_api.semantic import (
    Embedder,
    EmbeddingCacheUpdate,
    capture_embedding_candidates,
    rank_embedding_candidates,
)
from mnemonic_api.services.compact_work import compact_work_hits
from mnemonic_api.services.duplicates import canonical_projections
from mnemonic_api.services.multi_search_limits import check_work_scope
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource
from mnemonic_api.services.search_tags import work_tag_counts
from mnemonic_api.services.work_evidence import work_match_evidence
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


def _project_work_corpus(
    database: Session, project_id: UUID, filters: WorkItemListQuery, as_of: datetime,
):
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
        *date_conditions(filters, WorkItem.created_at, WorkItem.updated_at),
    ]
    if filters.external_url is not None:
        conditions.append(WorkItem.external_references.contains([{"url": filters.external_url}]))
    filtered = list(database.scalars(select(WorkItem).where(*conditions)))
    scoped = _scope_rows(filtered, filters, projections, root)
    return visible, projections, scoped


def _work_corpus(
    database: Session, selection: ProjectSelection, filters: WorkItemListQuery, as_of: datetime,
):
    projects = selected_project_ids(selection)
    if not isinstance(selection, UUID):
        check_work_scope(database, projects)
    if filters.canonical_work_item_id is not None:
        owner = database.scalar(select(WorkItem.project_id).where(
            WorkItem.id == filters.canonical_work_item_id, WorkItem.deleted_at.is_(None),
        ))
        # Existing validation still rejects a target outside the requested scope.
        projects = (owner,) if owner is not None and owner in projects else projects[:1]
    visible, projections, scoped = [], {}, []
    for project_id in projects:
        project_visible, project_projections, project_scoped = _project_work_corpus(
            database, project_id, filters, as_of,
        )
        visible.extend(project_visible)
        projections.update(project_projections)
        scoped.extend(project_scoped)
    return visible, projections, scoped


def _semantic_selections(
    database: Session, pool, scoped, projections, lexical_rows,
    filters: WorkItemListQuery, query_vector: tuple[float, ...], embedder: Embedder,
) -> tuple[list[SearchSelection], dict[UUID, float], list[EmbeddingCacheUpdate]]:
    captured = capture_embedding_candidates(database, pool, dimensions=len(query_vector))
    try:
        ranked, updates, fused_scores = rank_embedding_candidates(
            captured, [identity for identity, _ in lexical_rows], query_vector, embedder,
        )
    except Exception as exc:
        logger.error("Unified semantic ranking failed (%s)", type(exc).__name__)
        raise semantic_unavailable(
                    "deadline_exceeded" if isinstance(exc, TimeoutError) else "model_failure"
                ) from None
    by_id = {item.id: item for item in scoped}
    selections: list[SearchSelection] = []
    scores: dict[UUID, float] = {}
    for rank, member_id in enumerate(ranked, start=1):
        identity = (projections[member_id].canonical_work_item.id
                    if filters.duplicate_scope == "canonical" else member_id)
        if identity not in by_id or identity in scores:
            continue
        selections.append(SearchSelection(by_id[identity], member_id, fused_scores[member_id]))
        scores[identity] = 1.0 / rank
    return selections, scores, updates


def work_source(
    database: Session, project_id: ProjectSelection, request: SearchRequest, as_of: datetime,
    *, query_vector: tuple[float, ...] | None = None, embedder: Embedder,
) -> tuple[SearchSource, list[EmbeddingCacheUpdate]]:
    filters = WorkItemListQuery(**request.filters.work_items.model_dump(),
                                q=request.q, query_mode=request.query_mode)
    visible, projections, scoped = _work_corpus(database, project_id, filters, as_of)
    pool = visible if filters.duplicate_scope == "canonical" else scoped
    lexical_rows = _lexical_rows(database, request.q, pool, filters)
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
        facet="work_items", id=selection.work_item.id, project_id=selection.work_item.project_id,
        created_at=selection.work_item.created_at,
        updated_at=selection.work_item.updated_at, score=scores[selection.work_item.id],
        priority=selection.work_item.priority,
    ) for selection in selections]

    ranking = search_ranking(request.q, request.query_mode, work=True,
                             semantic=filters.semantic)

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        project_pages = {
            identity: [by_id[item.id].work_item for item in page if item.project_id == identity]
            for identity in {item.project_id for item in page}
        }
        evidence = work_match_evidence(
            database, [by_id[item.id].matched_member_id for item in page], filters)
        if request.detail == "compact":
            compact = [hit for identity, selected_work in project_pages.items()
                       for hit in compact_work_hits(
                           database, identity, selected_work, as_of=as_of,
                           ranks={item.id: item.source_rank for item in page}, evidence=evidence,
                           matched_members={item.id: pointers[by_id[item.id].matched_member_id]
                                            for item in page},
                       )]
            for item in compact:
                item.score, item.score_type = by_id[item.id].score, ranking.score_type
            compact_by_id = {item.id: item for item in compact}
            return {item.id: WorkFacetHit(**item.fields(), work_item=compact_by_id[item.id])
                    for item in page}
        summaries = [summary for identity, selected_work in project_pages.items()
                     for summary in _summaries_with_ancestry(
                         database, identity, selected_work, as_of=as_of,
                     )]
        summary_by_id = {summary.work_item.id: summary for summary in summaries}
        return {item.id: WorkFacetHit(**item.fields(), work_item=WorkSearchHit(
            summary=summary_by_id[item.id], rank=item.source_rank,
            score=by_id[item.id].score, score_type=ranking.score_type,
            matched_member=pointers[by_id[item.id].matched_member_id],
            **evidence[by_id[item.id].matched_member_id].model_dump(),
        )) for item in page}

    def term_counts(terms: list[str]) -> dict[str, int]:
        return {term: len(_lexical_selections(
            scoped, filters, projections, _lexical_rows(database, term, pool,
                filters.model_copy(update={"query_mode": "terms"})), term,
        )) for term in terms}

    members = {identity: projections[identity].canonical_work_item.id for identity in by_id}
    return SearchSource(
        candidates, hydrate, term_counts,
        tag_counts=lambda options: work_tag_counts(database, members, options),
    ), updates
