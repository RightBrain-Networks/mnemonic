"""Canonical-aware work search with explicit alias audit scopes."""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.application.state import embedder_of
from mnemonic_api.application.suggestion_resources import (
    semantic_search_inference_acquired,
)
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError, semantic_unavailable
from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import (
    HierarchySummary,
    Page,
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkSearchHit,
)
from mnemonic_api.semantic import (
    Embedder,
    EmbeddingCandidate,
    capture_embedding_candidates,
    persist_embedding_updates,
    rank_embedding_candidates,
    semantic_query_vector,
)
from mnemonic_api.services.duplicates import canonical_projections
from mnemonic_api.services.hierarchy import hierarchy_page
from mnemonic_api.services.work_items import require_project
from mnemonic_api.services.work_search import (
    SearchSelection,
    _lexical_rows,
    _lexical_selections,
    _page,
    _scope_rows,
    _summaries_with_ancestry,
    _validate_root_filter,
    provenance_conditions,
    status_conditions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/projects/{project_id}/work-items",
    response_model=Page[WorkSearchHit | HierarchySummary],
)
def search_work(
    project_id: UUID,
    filters: Annotated[WorkItemListQuery, Query()],
    request: Request,
    database: Database,
) -> Page[WorkSearchHit | HierarchySummary]:
    if filters.view == "roots":
        roots, total = hierarchy_page(database, project_id, filters)
        return Page(items=roots, total=total, limit=filters.limit, offset=filters.offset)

    query = (filters.q or "").strip()
    embedder = embedder_of(request)
    query_vector: tuple[float, ...] | None = None
    if filters.semantic:
        if not semantic_search_inference_acquired(request.scope):
            raise semantic_unavailable()
        try:
            query_vector = semantic_query_vector(embedder, query)
        except Exception as exc:
            raise _semantic_unavailable(exc) from None

    begin_coherent_read(database)
    as_of = database.scalar(select(func.transaction_timestamp()))
    if as_of is None:
        raise RuntimeError("Database did not provide a transaction timestamp")
    require_project(database, project_id)
    all_visible = list(
        database.scalars(
            select(WorkItem).where(
                WorkItem.project_id == project_id,
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    projections = canonical_projections(database, project_id, all_visible)
    root_filter = _validate_root_filter(
        database,
        project_id,
        filters.canonical_work_item_id,
        all_visible,
        projections,
    )
    filtered = list(
        database.scalars(
            select(WorkItem).where(
                WorkItem.project_id == project_id,
                WorkItem.deleted_at.is_(None),
                *status_conditions(filters.status, as_of),
                *provenance_conditions(filters),
                *([WorkItem.external_references.contains([{ "url": filters.external_url }])]
                  if filters.external_url is not None else []),
            )
        )
    )
    scoped = _scope_rows(filtered, filters, projections, root_filter)
    lexical_rows = _lexical_rows(
        database,
        query,
        all_visible if filters.duplicate_scope == "canonical" else scoped,
    )

    if query_vector is not None:
        return _semantic_response(
            database=database,
            project_id=project_id,
            filters=filters,
            all_visible=all_visible,
            scoped=scoped,
            projections=projections,
            lexical_rows=lexical_rows,
            query_vector=query_vector,
            embedder=embedder,
            as_of=as_of,
        )

    selections = _lexical_selections(scoped, filters, projections, lexical_rows, query)
    total = len(selections)
    page = selections[filters.offset : filters.offset + filters.limit]
    summaries = _summaries_with_ancestry(
        database,
        project_id,
        [selection.work_item for selection in page],
        as_of=as_of,
    )
    pointers = {
        item.id: WorkIdentityPointer.model_validate(item)
        for item in all_visible
    }
    return _page(filters, page, summaries, pointers, total)


def _semantic_response(
    *,
    database: Session,
    project_id: UUID,
    filters: WorkItemListQuery,
    all_visible: Sequence[WorkItem],
    scoped: Sequence[WorkItem],
    projections: dict[UUID, Any],
    lexical_rows: Sequence[tuple[UUID, float]],
    query_vector: Sequence[float],
    embedder: Embedder,
    as_of: datetime,
) -> Page[WorkSearchHit | HierarchySummary]:
    semantic_pool = all_visible if filters.duplicate_scope == "canonical" else scoped
    captured: list[EmbeddingCandidate] = capture_embedding_candidates(
        database,
        semantic_pool,
        dimensions=len(query_vector),
    )
    summaries = _summaries_with_ancestry(
        database,
        project_id,
        scoped,
        as_of=as_of,
    )
    database.commit()
    try:
        ranked_ids, updates = rank_embedding_candidates(
            captured,
            [work_item_id for work_item_id, _score in lexical_rows],
            query_vector,
            embedder,
        )
        persist_embedding_updates(database, updates)
    except Exception as exc:
        database.rollback()
        raise _semantic_unavailable(exc) from None

    by_id = {item.id: item for item in scoped}
    selections: list[SearchSelection] = []
    seen_roots: set[UUID] = set()
    for member_id in ranked_ids:
        if filters.duplicate_scope == "canonical":
            root_id = projections[member_id].canonical_work_item.id
            if root_id in seen_roots or root_id not in by_id:
                continue
            seen_roots.add(root_id)
            selections.append(SearchSelection(by_id[root_id], member_id))
        elif member_id in by_id:
            selections.append(SearchSelection(by_id[member_id], member_id))
    total = len(selections)
    page = selections[filters.offset : filters.offset + filters.limit]
    summaries_by_id = {summary.work_item.id: summary for summary in summaries}
    page_summaries = [summaries_by_id[selection.work_item.id] for selection in page]
    pointers = {
        item.id: WorkIdentityPointer.model_validate(item)
        for item in all_visible
    }
    return _page(filters, page, page_summaries, pointers, total)


def _semantic_unavailable(exc: Exception) -> ApplicationError:
    logger.error("Semantic search failed (%s)", type(exc).__name__)
    return semantic_unavailable()
