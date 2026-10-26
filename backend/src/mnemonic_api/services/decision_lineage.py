"""Read durable change membership and live work facts from the caller's coherent snapshot."""

from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from mnemonic_api.decision_lineage_schemas import (
    DecisionLineagePage,
    DecisionLineageWork,
    LineageFollowUp,
    LineageOrigins,
    LineageRemediationOrigin,
    LineageReview,
    LineageWorkPointer,
)
from mnemonic_api.models import (
    CodeReview,
    CodeReviewRemediation,
    DecisionChange,
    DecisionChangeWork,
    DecisionRevisionChange,
    WorkAgentFollowUp,
    WorkItem,
    WorkRelationship,
)
from mnemonic_api.services.precedence import dependency_issues
from mnemonic_api.services.readiness import readiness, readiness_inputs, review_statuses
from mnemonic_api.services.review_decisions import disposition

ReviewStatus = Literal["to-review", "deferred", "done", "wont-do", "promoted"]


def _discovery_origins(database: Session, ids: list[UUID]) -> dict[UUID, LineageOrigins]:
    ranked = select(
        WorkRelationship.source_work_item_id.label("member_id"),
        WorkRelationship.target_work_item_id.label("origin_id"),
        func.row_number().over(
            partition_by=WorkRelationship.source_work_item_id,
            order_by=WorkRelationship.target_work_item_id,
        ).label("position"),
        func.count().over(partition_by=WorkRelationship.source_work_item_id).label("total"),
    ).where(
        WorkRelationship.source_work_item_id.in_(ids),
        WorkRelationship.relationship_type == "discovered-from",
    ).subquery()
    rows = database.execute(select(ranked, WorkItem.project_id).join(
        WorkItem, WorkItem.id == ranked.c.origin_id,
    ).where(ranked.c.position <= 20).order_by(ranked.c.member_id, ranked.c.position))
    result: dict[UUID, LineageOrigins] = {}
    for row in rows:
        item = result.setdefault(row.member_id, LineageOrigins(
            items=[], total=0, omitted_count=0,
        ))
        item.items.append(LineageWorkPointer(work_item_id=row.origin_id, project_id=row.project_id))
        item.total = row.total
        item.omitted_count = row.total - len(item.items)
    return result


def _review_context(database: Session, ids: list[UUID]) -> tuple[
    dict[UUID, LineageReview], dict[UUID, LineageFollowUp],
]:
    reviews = database.scalars(select(CodeReview).where(CodeReview.work_item_id.in_(ids))
        .distinct(CodeReview.work_item_id)
        .order_by(CodeReview.work_item_id, CodeReview.created_sequence.desc()))
    follow_ups = database.scalars(select(WorkAgentFollowUp).where(
        WorkAgentFollowUp.work_item_id.in_(ids),
    ).distinct(WorkAgentFollowUp.work_item_id)
        .order_by(WorkAgentFollowUp.work_item_id, WorkAgentFollowUp.created_sequence.desc()))
    return (
        {row.work_item_id: LineageReview(
            review_id=row.id,
            state=cast(Literal["requested", "completed", "superseded"], row.state),
            status=cast(ReviewStatus, disposition(row)) if row.state == "requested" else None,
        ) for row in reviews},
        {row.work_item_id: LineageFollowUp(
            follow_up_id=row.id,
            state=cast(Literal["pending", "answered", "superseded"], row.state),
            status=cast(ReviewStatus, disposition(row)) if row.state == "pending" else None,
        ) for row in follow_ups},
    )


def _remediation_origins(
    database: Session, ids: list[UUID],
) -> dict[UUID, LineageRemediationOrigin]:
    source = aliased(WorkItem)
    rows = database.execute(select(CodeReviewRemediation, source.project_id).join(
        source, source.id == CodeReviewRemediation.source_work_item_id,
    ).where(CodeReviewRemediation.remediation_work_item_id.in_(ids)))
    return {row.remediation_work_item_id: LineageRemediationOrigin(
        review_id=row.review_id, source_work_item_id=row.source_work_item_id,
        source_project_id=project_id, depth=row.depth,
    ) for row, project_id in rows}


def _members(database: Session, change: DecisionChange, works: list[WorkItem]) -> list[
    DecisionLineageWork
]:
    ids = [work.id for work in works]
    blockers, gates, leases, dropped, canonical = readiness_inputs(database, ids)
    statuses = review_statuses(database, ids)
    issues = dependency_issues(database, ids)
    origins = _discovery_origins(database, ids)
    reviews, follow_ups = _review_context(database, ids)
    remediations = _remediation_origins(database, ids)
    sources = set(database.scalars(select(DecisionRevisionChange.source_work_item_id).where(
        DecisionRevisionChange.change_id == change.id,
        DecisionRevisionChange.source_work_item_id.in_(ids),
    )))
    return [DecisionLineageWork(
        work_item_id=work.id, project_id=work.project_id, title=work.title, status=work.status,
        deleted_at=work.deleted_at, canonical_work_item_id=canonical.get(work.id, work.id),
        readiness=None if work.deleted_at is not None else readiness(
            work, leases.get(work.id), blockers.get(work.id, 0), work.id in dropped,
            gates.get(work.id, 0), canonical_work_item_id=canonical.get(work.id, work.id),
            review_status=statuses.get(work.id), dependency_issue=issues.get(work.id),
        ),
        is_decision_source=work.id in sources,
        discovered_from=origins.get(work.id, LineageOrigins(items=[], total=0, omitted_count=0)),
        code_review=reviews.get(work.id), review_follow_up=follow_ups.get(work.id),
        remediation_origin=remediations.get(work.id),
    ) for work in works]


def lineage_page(database: Session, change: DecisionChange, limit: int, offset: int) -> (
    DecisionLineagePage
):
    query = select(WorkItem).join(DecisionChangeWork,
        DecisionChangeWork.work_item_id == WorkItem.id).where(
        DecisionChangeWork.change_id == change.id,
    )
    if change.review_work_item_id is not None:
        query = query.where(WorkItem.id != change.review_work_item_id)
    total = database.scalar(select(func.count()).select_from(query.subquery())) or 0
    works = list(database.scalars(query.order_by(
        DecisionChangeWork.created_at, WorkItem.id,
    ).offset(offset).limit(limit)))
    items = _members(database, change, works) if works else []
    following = offset + len(items)
    return DecisionLineagePage(items=items, total=total, limit=limit, offset=offset,
                              next_offset=following if following < total else None)
