"""Canonical-aware work search with explicit alias audit scopes."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.application.state import embedder_of
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError, work_duplicate
from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    HierarchySummary,
    Page,
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkSearchHit,
    WorkSummary,
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
from mnemonic_api.services.hierarchy import ancestor_paths, hierarchy_page
from mnemonic_api.services.work_context import work_summaries
from mnemonic_api.services.work_items import missing_work_item, require_project

logger = logging.getLogger(__name__)
router = APIRouter()

TS_RANK_NORMALIZATION = 32
WORK_TEXT_FIELDS = (WorkItem.title, WorkItem.summary, cast(WorkItem.id, String))
CHECKPOINT_TEXT_FIELDS = (
    Checkpoint.prompt,
    cast(Checkpoint.id, String),
    Checkpoint.source_client,
    Checkpoint.source_session_id,
    Checkpoint.source_model,
    Checkpoint.source_session_url,
    Checkpoint.repository_branch,
    Checkpoint.verified_against,
    func.array_to_string(Checkpoint.tags, " "),
)


@dataclass(frozen=True)
class LexicalMatch:
    condition: ColumnElement[bool]
    score: ColumnElement[Any]


@dataclass(frozen=True)
class SearchSelection:
    work_item: WorkItem
    matched_member_id: UUID


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


def status_conditions(status: str, as_of: datetime) -> list[ColumnElement[bool]]:
    if status == "all":
        return []
    if status == "active":
        return [WorkItem.status == "pending", _lease_exists(WorkLease.expires_at > as_of)]
    if status == "dropped":
        return [WorkItem.status == "pending", _lease_exists(WorkLease.expires_at <= as_of)]
    if status == "pending":
        return [WorkItem.status == "pending", ~_lease_exists()]
    return [WorkItem.status == status]


def _lease_exists(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    return (
        select(WorkLease.work_item_id)
        .where(WorkLease.work_item_id == WorkItem.id, *conditions)
        .correlate(WorkItem)
        .exists()
    )


def provenance_conditions(filters: WorkItemListQuery) -> list[ColumnElement[bool]]:
    on_checkpoint: list[ColumnElement[bool]] = []
    if filters.tag is not None:
        on_checkpoint.append(_tag_condition(filters.tag))
    if filters.source_client is not None:
        on_checkpoint.append(Checkpoint.source_client == filters.source_client)
    if filters.source_session_id is not None:
        on_checkpoint.append(Checkpoint.source_session_id == filters.source_session_id)
    return [_checkpoint_exists(*on_checkpoint)] if on_checkpoint else []


def _tag_condition(tag: str) -> ColumnElement[bool]:
    checkpoint_tag = func.unnest(Checkpoint.tags).column_valued("checkpoint_tag")
    return or_(
        Checkpoint.tags.contains([tag]),
        select(1).where(func.lower(checkpoint_tag) == tag).exists(),
    )


def _checkpoint_exists(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    return (
        select(Checkpoint.id)
        .where(Checkpoint.work_item_id == WorkItem.id, *conditions)
        .exists()
    )


def lexical_match(query: str) -> LexicalMatch:
    terms = func.plainto_tsquery("english", query)
    in_checkpoint = _checkpoint_exists(
        or_(
            Checkpoint.search_vector.bool_op("@@")(terms),
            *(field.icontains(query, autoescape=True) for field in CHECKPOINT_TEXT_FIELDS),
        )
    )
    condition = or_(
        WorkItem.search_vector.bool_op("@@")(terms),
        *(field.icontains(query, autoescape=True) for field in WORK_TEXT_FIELDS),
        in_checkpoint,
    )
    best_checkpoint_rank = (
        select(func.max(func.ts_rank_cd(Checkpoint.search_vector, terms, TS_RANK_NORMALIZATION)))
        .where(Checkpoint.work_item_id == WorkItem.id)
        .scalar_subquery()
    )
    return LexicalMatch(
        condition=condition,
        score=func.greatest(
            func.ts_rank_cd(WorkItem.search_vector, terms, TS_RANK_NORMALIZATION),
            func.coalesce(best_checkpoint_rank, 0.0),
        ),
    )


def _validate_root_filter(
    database: Session,
    project_id: UUID,
    root_id: UUID | None,
    all_visible: Sequence[WorkItem],
    projections: dict[UUID, Any],
) -> UUID | None:
    if root_id is None:
        return None
    item = next((work_item for work_item in all_visible if work_item.id == root_id), None)
    if item is None:
        raise missing_work_item(database, project_id)
    projection = projections[item.id]
    if projection.is_duplicate:
        raise work_duplicate(projection.canonical_work_item.id)
    return item.id


def _scope_rows(
    rows: Sequence[WorkItem],
    filters: WorkItemListQuery,
    projections: dict[UUID, Any],
    root_filter: UUID | None,
) -> list[WorkItem]:
    scoped: list[WorkItem] = []
    for work_item in rows:
        projection = projections[work_item.id]
        if filters.duplicate_scope == "canonical" and projection.is_duplicate:
            continue
        if filters.duplicate_scope == "aliases" and not projection.is_duplicate:
            continue
        if root_filter is not None and projection.canonical_work_item.id != root_filter:
            continue
        scoped.append(work_item)
    return scoped


def _lexical_rows(
    database: Session,
    query: str,
    candidates: Sequence[WorkItem],
) -> list[tuple[UUID, float]]:
    if not query or not candidates:
        return []
    match = lexical_match(query)
    rows = database.execute(
        select(WorkItem.id, match.score.label("score"))
        .where(WorkItem.id.in_([item.id for item in candidates]), match.condition)
        .order_by(match.score.desc(), WorkItem.updated_at.desc(), WorkItem.id)
    )
    return [(work_item_id, float(score)) for work_item_id, score in rows]


def _lexical_selections(
    scoped: Sequence[WorkItem],
    filters: WorkItemListQuery,
    projections: dict[UUID, Any],
    lexical_rows: Sequence[tuple[UUID, float]],
    query: str,
) -> list[SearchSelection]:
    if not query:
        return [
            SearchSelection(work_item=item, matched_member_id=item.id)
            for item in _sort_rows(scoped, filters.sort)
        ]
    score_by_id = dict(lexical_rows)
    if filters.duplicate_scope != "canonical":
        matches = [item for item in scoped if item.id in score_by_id]
        ordered = _sort_rows(matches, filters.sort, scores=score_by_id)
        return [SearchSelection(item, item.id) for item in ordered]

    winner_by_root: dict[UUID, tuple[UUID, float]] = {}
    for member_id, score in lexical_rows:
        root_id = projections[member_id].canonical_work_item.id
        winner_by_root.setdefault(root_id, (member_id, score))
    eligible = [item for item in scoped if item.id in winner_by_root]
    root_scores = {item.id: winner_by_root[item.id][1] for item in eligible}
    ordered = _sort_rows(eligible, filters.sort, scores=root_scores)
    return [
        SearchSelection(item, winner_by_root[item.id][0])
        for item in ordered
    ]


def _sort_rows(
    rows: Sequence[WorkItem],
    sort: str,
    *,
    scores: dict[UUID, float] | None = None,
) -> list[WorkItem]:
    def persisted_key(item: WorkItem) -> tuple[Any, ...]:
        if sort == "created":
            return item.created_at, item.id.int
        if sort == "priority":
            return item.priority, item.updated_at, item.id.int
        return item.updated_at, item.id.int

    if scores is None:
        return sorted(rows, key=persisted_key, reverse=True)
    return sorted(
        rows,
        key=lambda item: (scores[item.id], *persisted_key(item)),
        reverse=True,
    )


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


def _summaries_with_ancestry(
    database: Session,
    project_id: UUID,
    work_items: Sequence[WorkItem],
    *,
    as_of: datetime,
) -> list[WorkSummary]:
    summaries = work_summaries(database, work_items, as_of=as_of)
    paths, truncated = ancestor_paths(database, project_id, [item.id for item in work_items])
    for summary in summaries:
        summary.ancestor_path = paths.get(summary.work_item.id, [])
        summary.ancestor_path_truncated = summary.work_item.id in truncated
    return summaries


def _page(
    filters: WorkItemListQuery,
    selections: Sequence[SearchSelection],
    summaries: Sequence[WorkSummary],
    pointers: dict[UUID, WorkIdentityPointer],
    total: int,
) -> Page[WorkSearchHit | HierarchySummary]:
    summary_by_id = {summary.work_item.id: summary for summary in summaries}
    items = [
        WorkSearchHit(
            summary=summary_by_id[selection.work_item.id],
            matched_member=pointers[selection.matched_member_id],
        )
        for selection in selections
    ]
    return Page(items=items, total=total, limit=filters.limit, offset=filters.offset)


def _semantic_unavailable(exc: Exception) -> ApplicationError:
    logger.error("Semantic search failed (%s)", type(exc).__name__)
    return ApplicationError(
        503,
        "semantic_unavailable",
        "Semantic search is unavailable. Turn it off to use lexical search.",
    )
