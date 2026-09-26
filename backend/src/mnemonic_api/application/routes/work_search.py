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
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError, semantic_unavailable
from mnemonic_api.inference import inference_failure_reason
from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import (
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkSearchPage,
)
from mnemonic_api.search_exploration import date_conditions
from mnemonic_api.search_pagination import bound_search_page
from mnemonic_api.search_ranking import search_ranking
from mnemonic_api.search_timing import refresh_cache
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
    _validate_root_filter,
    provenance_conditions,
    status_conditions,
    work_search_disclosure,
    work_term_diagnostics,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/projects/{project_id}/work-items",
    response_model=WorkSearchPage,
)
def search_work(
    project_id: UUID,
    filters: Annotated[WorkItemListQuery, Query()],
    request: Request,
    database: Database,
) -> WorkSearchPage:
    if filters.view == "roots":
        roots, total = hierarchy_page(database, project_id, filters)
        for rank, item in enumerate(roots, filters.offset + 1):
            item.rank = rank
        return bound_search_page(WorkSearchPage(
            **search_ranking(None, "terms", work=True).model_dump(),
            work_rank_scope="work_items",
            **work_search_disclosure(project_id, filters).model_dump(),
            detail=filters.detail, items=roots, total=total,
            limit=filters.limit, offset=filters.offset,
        ))

    query = (filters.q or "").strip()
    embedder = embedder_of(request)
    query_vector: tuple[float, ...] | None = None
    if filters.semantic:
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
                *status_conditions(filters.status, as_of, filters.status_scope),
                *provenance_conditions(filters),
                *date_conditions(filters, WorkItem.created_at, WorkItem.updated_at),
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
        filters,
    )

    if query_vector is not None:
        diagnostics = work_term_diagnostics(
            database, filters, scoped, projections,
            all_visible if filters.duplicate_scope == "canonical" else scoped, len(scoped),
        )
        response = _semantic_response(
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
        response.term_diagnostics = diagnostics
        return bound_search_page(response)

    selections = _lexical_selections(scoped, filters, projections, lexical_rows, query)
    total = len(selections)
    page = selections[filters.offset : filters.offset + filters.limit]
    pointers = {
        item.id: WorkIdentityPointer.model_validate(item)
        for item in all_visible
    }
    response = _page(database, project_id, filters, page, pointers, total, as_of=as_of)
    response.term_diagnostics = work_term_diagnostics(
        database, filters, scoped, projections,
        all_visible if filters.duplicate_scope == "canonical" else scoped, total,
    )
    return bound_search_page(response)


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
) -> WorkSearchPage:
    semantic_pool = all_visible if filters.duplicate_scope == "canonical" else scoped
    captured: list[EmbeddingCandidate] = capture_embedding_candidates(
        database,
        semantic_pool,
        dimensions=len(query_vector),
    )
    try:
        ranked_ids, updates, scores = rank_embedding_candidates(
            captured,
            [work_item_id for work_item_id, _score in lexical_rows],
            query_vector,
            embedder,
        )
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
            selections.append(SearchSelection(by_id[root_id], member_id, scores[member_id]))
        elif member_id in by_id:
            selections.append(SearchSelection(by_id[member_id], member_id, scores[member_id]))
    total = len(selections)
    page = selections[filters.offset : filters.offset + filters.limit]
    pointers = {
        item.id: WorkIdentityPointer.model_validate(item)
        for item in all_visible
    }
    result = _page(database, project_id, filters, page, pointers, total, as_of=as_of)
    # Keep page evidence on the original read-only snapshot while ranking. No row
    # or advisory locks are held; shared inference admission bounds this work.
    database.commit()
    result.semantic.cache_refresh = refresh_cache(
        "work_semantic", bool(updates), lambda: persist_embedding_updates(database, updates))
    return result


def _semantic_unavailable(exc: Exception) -> ApplicationError:
    logger.error("Semantic search failed (%s)", type(exc).__name__)
    return semantic_unavailable(
        inference_failure_reason(exc))
