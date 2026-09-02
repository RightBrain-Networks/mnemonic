"""``GET /projects/{id}/work-items``: filter, match, rank, and page work items.

Three views share one filter set. ``roots`` is the hierarchy service's page of
root work items. ``full`` and ``minimal`` run the search below; ``full`` also
decorates each summary with its ancestor path, which ``minimal`` skips because
agent callers pay for every byte.

The search is one SQL statement unless ``semantic=true``. Semantic search reads
the lexical ranking and the whole filtered candidate set, ranks them together
in Python with the embedder, and pages that list. It owns the only commit in a
read route: the derived embedding cache, which is disposable and never decides
identity or edges.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.application.state import embedder_of
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    HierarchySummary,
    Page,
    WorkItemListQuery,
    WorkSummary,
    WorkSummaryMinimal,
)
from mnemonic_api.semantic import Embedder, hybrid_rank
from mnemonic_api.services.hierarchy import ancestor_paths, hierarchy_page
from mnemonic_api.services.work_context import minimal_work_summaries, work_summaries
from mnemonic_api.services.work_items import require_project

logger = logging.getLogger(__name__)
router = APIRouter()

SORT_ORDERINGS = {
    "updated": (WorkItem.updated_at.desc(), WorkItem.id.desc()),
    "created": (WorkItem.created_at.desc(), WorkItem.id.desc()),
    "priority": (WorkItem.priority.desc(), WorkItem.updated_at.desc(), WorkItem.id.desc()),
}
# ts_rank_cd normalization 32 divides each rank by itself plus one, keeping
# work and checkpoint scores comparable in [0, 1).
TS_RANK_NORMALIZATION = 32
# Substring matching covers identity and provenance text that full-text search
# tokenizes poorly: UUIDs, session IDs, URLs, branch names, commit hashes.
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


@router.get(
    "/projects/{project_id}/work-items",
    response_model=Page[WorkSummary | HierarchySummary | WorkSummaryMinimal],
)
def search_work(
    project_id: UUID,
    filters: Annotated[WorkItemListQuery, Query()],
    request: Request,
    database: Database,
) -> Page[WorkSummary | HierarchySummary | WorkSummaryMinimal]:
    if filters.view == "roots":
        roots, total = hierarchy_page(database, project_id, filters)
        return Page(items=roots, total=total, limit=filters.limit, offset=filters.offset)
    work_items, total = search_work_rows(database, project_id, filters, embedder_of(request))
    if filters.view == "minimal":
        items = minimal_work_summaries(database, work_items)
    else:
        items = _summaries_with_ancestry(database, project_id, work_items)
    return Page(items=items, total=total, limit=filters.limit, offset=filters.offset)


def search_work_rows(
    database: Session,
    project_id: UUID,
    filters: WorkItemListQuery,
    embedder: Embedder,
) -> tuple[list[WorkItem], int]:
    """The requested page of matching rows plus the total, in the requested order."""
    require_project(database, project_id)
    conditions = [
        WorkItem.project_id == project_id,
        WorkItem.deleted_at.is_(None),
        *status_conditions(filters.status),
        *provenance_conditions(filters),
    ]
    ordering = SORT_ORDERINGS[filters.sort]
    query = (filters.q or "").strip()
    if not query:
        return _lexical_page(database, conditions, ordering, filters)
    match = lexical_match(query)
    if filters.semantic:
        return _semantic_page(database, conditions, match, ordering, filters, embedder)
    return _lexical_page(
        database, [*conditions, match.condition], (match.rank, *ordering), filters
    )


def status_conditions(status: str) -> list[ColumnElement[bool]]:
    """Translate the caller's status word into a persisted status plus lease facts.

    ``active`` and ``dropped`` are never stored. Both are ``pending`` rows whose
    retained lease is, at database time, unexpired or expired; a bare
    ``pending`` therefore means pending with no retained lease at all. The
    hierarchy service states the same rule in raw SQL; keep the two in step.
    """
    if status == "all":
        return []
    if status == "active":
        unexpired = WorkLease.expires_at > func.clock_timestamp()
        return [WorkItem.status == "pending", _lease_exists(unexpired)]
    if status == "dropped":
        expired = WorkLease.expires_at <= func.clock_timestamp()
        return [WorkItem.status == "pending", _lease_exists(expired)]
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
    """Require one checkpoint in the item's history that carries every requested fact."""
    on_checkpoint: list[ColumnElement[bool]] = []
    if filters.tag is not None:
        on_checkpoint.append(_tag_condition(filters.tag))
    if filters.source_client is not None:
        on_checkpoint.append(Checkpoint.source_client == filters.source_client)
    if filters.source_session_id is not None:
        on_checkpoint.append(Checkpoint.source_session_id == filters.source_session_id)
    if not on_checkpoint:
        return []
    return [_checkpoint_exists(*on_checkpoint)]


def _tag_condition(tag: str) -> ColumnElement[bool]:
    checkpoint_tag = func.unnest(Checkpoint.tags).column_valued("checkpoint_tag")
    return or_(
        # Keep the indexed normalized-data fast path while allowing exact
        # migrations that preserved historical tag case.
        Checkpoint.tags.contains([tag]),
        select(1).where(func.lower(checkpoint_tag) == tag).exists(),
    )


def _checkpoint_exists(*conditions: ColumnElement[bool]) -> ColumnElement[bool]:
    return (
        select(Checkpoint.id)
        .where(Checkpoint.work_item_id == WorkItem.id, *conditions)
        .exists()
    )


@dataclass(frozen=True)
class LexicalMatch:
    """PostgreSQL full-text plus substring matching for one query string."""

    query: str
    condition: ColumnElement[bool]
    rank: ColumnElement[Any]  # descending: best full-text rank on the item or its checkpoints


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
    rank = func.greatest(
        func.ts_rank_cd(WorkItem.search_vector, terms, TS_RANK_NORMALIZATION),
        func.coalesce(best_checkpoint_rank, 0.0),
    ).desc()
    return LexicalMatch(query=query, condition=condition, rank=rank)


def _lexical_page(
    database: Session,
    conditions: Sequence[ColumnElement[bool]],
    ordering: Sequence[ColumnElement[Any]],
    filters: WorkItemListQuery,
) -> tuple[list[WorkItem], int]:
    total = database.scalar(select(func.count()).select_from(WorkItem).where(*conditions)) or 0
    rows = database.scalars(
        select(WorkItem)
        .where(*conditions)
        .order_by(*ordering)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(rows), total


def _semantic_page(
    database: Session,
    conditions: Sequence[ColumnElement[bool]],
    match: LexicalMatch,
    ordering: Sequence[ColumnElement[Any]],
    filters: WorkItemListQuery,
    embedder: Embedder,
) -> tuple[list[WorkItem], int]:
    lexical_ids = list(
        database.scalars(
            select(WorkItem.id)
            .where(*conditions, match.condition)
            .order_by(match.rank, *ordering)
        )
    )
    candidates = list(database.scalars(select(WorkItem).where(*conditions).order_by(*ordering)))
    try:
        ranked = hybrid_rank(database, candidates, lexical_ids, match.query, embedder)
        # The derived embedding cache owns an explicit search-only boundary.
        database.commit()
    except Exception as exc:
        database.rollback()
        logger.error("Semantic search failed (%s)", type(exc).__name__)
        raise ApplicationError(
            503,
            "semantic_unavailable",
            "Semantic search is unavailable. Turn it off to use lexical search.",
        ) from None
    return ranked[filters.offset : filters.offset + filters.limit], len(ranked)


def _summaries_with_ancestry(
    database: Session, project_id: UUID, work_items: Sequence[WorkItem]
) -> list[WorkSummary]:
    summaries = work_summaries(database, work_items)
    paths, truncated = ancestor_paths(
        database, project_id, [work_item.id for work_item in work_items]
    )
    for summary in summaries:
        summary.ancestor_path = paths.get(summary.work_item.id, [])
        summary.ancestor_path_truncated = summary.work_item.id in truncated
    return summaries
