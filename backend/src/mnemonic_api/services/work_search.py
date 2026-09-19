"""Shared lexical selection and canonical work search projections."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import literal_terms
from mnemonic_api.errors import work_duplicate
from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkSearchHit,
    WorkSearchPage,
    WorkSummary,
)
from mnemonic_api.search_diagnostics import TermDiagnostic, TermMatchCounts
from mnemonic_api.search_disclosure import SearchDisclosure, WorkAppliedFilters, search_disclosure
from mnemonic_api.search_exploration import wants_diagnostics
from mnemonic_api.search_query import parse_query
from mnemonic_api.search_ranking import search_ranking
from mnemonic_api.services.compact_work import compact_work_hits
from mnemonic_api.services.hierarchy import ancestor_paths
from mnemonic_api.services.readiness import review_status_clause
from mnemonic_api.services.work_context import work_summaries
from mnemonic_api.services.work_evidence import work_match_evidence
from mnemonic_api.services.work_items import missing_work_item
from mnemonic_api.services.work_matching import lexical_match


@dataclass(frozen=True)
class SearchSelection:
    work_item: WorkItem
    matched_member_id: UUID
    score: float = 0.0


def status_conditions(
    status: str, as_of: datetime, status_scope: str = "effective",
) -> list[ColumnElement[bool]]:
    if status == "all":
        return []
    if status == "active":
        return [WorkItem.status == "pending", _lease_exists(WorkLease.expires_at > as_of)]
    if status == "dropped":
        return [WorkItem.status == "pending", _lease_exists(WorkLease.expires_at <= as_of)]
    if status == "pending":
        return [WorkItem.status == "pending", ~_lease_exists()]
    effective = WorkItem.status if status_scope == "work_item" else func.coalesce(
        review_status_clause(WorkItem.id), WorkItem.status,
    )
    return [effective == status]


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
    filters: WorkItemListQuery | None = None,
) -> list[tuple[UUID, float]]:
    if not query or not candidates:
        return []
    filters = filters or WorkItemListQuery()
    match = lexical_match(parse_query(query, filters.query_mode), filters.work_fields)
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
        return [SearchSelection(item, item.id, score_by_id[item.id]) for item in ordered]

    winner_by_root: dict[UUID, tuple[UUID, float]] = {}
    for member_id, score in lexical_rows:
        root_id = projections[member_id].canonical_work_item.id
        winner_by_root.setdefault(root_id, (member_id, score))
    eligible = [item for item in scoped if item.id in winner_by_root]
    root_scores = {item.id: winner_by_root[item.id][1] for item in eligible}
    ordered = _sort_rows(eligible, filters.sort, scores=root_scores)
    return [
        SearchSelection(item, winner_by_root[item.id][0], root_scores[item.id])
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
    database: Session, project_id: UUID, filters: WorkItemListQuery,
    selections: Sequence[SearchSelection], pointers: dict[UUID, WorkIdentityPointer],
    total: int, *, as_of: datetime,
) -> WorkSearchPage:
    work_items = [selection.work_item for selection in selections]
    evidence = work_match_evidence(
        database, [selection.matched_member_id for selection in selections], filters)
    if filters.detail == "compact":
        items = compact_work_hits(
            database, project_id, work_items, as_of=as_of,
            ranks={item.id: filters.offset + index for index, item in enumerate(work_items, 1)},
            evidence=evidence,
            matched_members={selection.work_item.id: pointers[selection.matched_member_id]
                             for selection in selections},
        )
    else:
        summaries = _summaries_with_ancestry(database, project_id, work_items, as_of=as_of)
        summary_by_id = {summary.work_item.id: summary for summary in summaries}
        items = [WorkSearchHit(
            summary=summary_by_id[selection.work_item.id],
            matched_member=pointers[selection.matched_member_id],
            **evidence[selection.matched_member_id].model_dump(),
        ) for selection in selections]
    ranking = search_ranking(filters.q, filters.query_mode, work=True, semantic=filters.semantic)
    for rank, (item, selection) in enumerate(
        zip(items, selections, strict=True), filters.offset + 1
    ):
        item.rank, item.score, item.score_type = rank, selection.score, ranking.score_type
    return WorkSearchPage(
        **ranking.model_dump(),
        work_rank_scope="work_items",
        **work_search_disclosure(project_id, filters).model_dump(),
        detail=filters.detail, items=items, total=total, limit=filters.limit, offset=filters.offset,
    )



def work_search_disclosure(project_id: UUID, filters: WorkItemListQuery) -> SearchDisclosure:
    return search_disclosure(
        project_id, filters.q, semantic=filters.semantic, query_mode=filters.query_mode,
        diagnostics=filters.diagnostics,
        work_items=WorkAppliedFilters.model_validate(
            filters.model_dump(include=set(WorkAppliedFilters.model_fields)),
        ),
    )


def work_term_diagnostics(database: Session, filters: WorkItemListQuery,
                          scoped: Sequence[WorkItem], projections: dict[UUID, Any],
                          pool: Sequence[WorkItem], total: int) -> list[TermDiagnostic]:
    if not wants_diagnostics(filters.diagnostics, filters.q, total):
        return []
    return [TermDiagnostic(term=term, matches=TermMatchCounts(work_items=len(
        _lexical_selections(scoped, filters, projections,
                            _lexical_rows(database, term, pool,
                                filters.model_copy(update={"query_mode": "terms"})), term),
    ))) for term in literal_terms(filters.q or "", fold_accents=False)]
