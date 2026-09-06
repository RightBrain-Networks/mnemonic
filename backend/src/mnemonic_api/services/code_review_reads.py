"""Exact history and compact project-scoped review discovery with bounded cursors."""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, literal, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.code_review_schemas import (
    CodeReviewContext,
    CodeReviewDetail,
    CodeReviewHandoffNotes,
    CodeReviewScopeInput,
    ReviewQueuePage,
    ReviewQueueRow,
    ReviewSourceState,
    WorkFollowUpDetail,
)
from mnemonic_api.models import (
    CodeReview,
    CodeReviewHandoff,
    CodeReviewRemediation,
    CodeReviewResult,
    CodeReviewScope,
    WorkAgentFollowUp,
    WorkAgentFollowUpAnswer,
    WorkCompletionReviewPolicy,
    WorkDuplicateMerge,
    WorkItem,
    WorkLease,
)
from mnemonic_api.schemas import LeasePublic
from mnemonic_api.services.activity_cursors import cursor_base, decode_cursor, encode_cursor
from mnemonic_api.services.code_review_records import (
    answer_read,
    bounded,
    follow_up_read,
    policy_read,
    remediation_read,
    require_follow_up,
    require_review,
    result_read,
    review_read,
)
from mnemonic_api.services.project_activity import activity_head
from mnemonic_api.services.work_events import database_now


def source_state(work: WorkItem) -> ReviewSourceState:
    return ReviewSourceState(
        work_item_id=work.id,
        title=work.title,
        status=work.status,
        deleted=work.deleted_at is not None,
    )


def review_detail(
    database: Session, project_id: UUID, work_id: UUID, review_id: UUID
) -> CodeReviewDetail:
    review = require_review(database, project_id, work_id, review_id)
    policy = database.get(WorkCompletionReviewPolicy, review.policy_decision_id)
    scope = database.get(CodeReviewScope, review.id)
    handoff = database.get(CodeReviewHandoff, review.id)
    work = database.get(WorkItem, work_id)
    assert policy is not None and scope is not None and handoff is not None and work is not None
    result = database.get(CodeReviewResult, review.result_id) if review.result_id else None
    association = database.scalar(
        select(CodeReviewRemediation).where(
            CodeReviewRemediation.review_id == review.id,
        )
    )
    return bounded(
        CodeReviewDetail(
            review=review_read(review),
            policy_decision=policy_read(policy),
            scope=CodeReviewScopeInput(repositories=scope.repositories),
            handoff=CodeReviewHandoffNotes.model_validate(handoff),
            result=result_read(database, result) if result else None,
            remediation=remediation_read(association) if association else None,
            source_work_state=source_state(work),
        ),
        786432,
    )


def follow_up_detail(
    database: Session, project_id: UUID, work_id: UUID, follow_up_id: UUID
) -> WorkFollowUpDetail:
    question = require_follow_up(database, project_id, work_id, follow_up_id)
    answer = (
        database.get(WorkAgentFollowUpAnswer, question.answer_id) if question.answer_id else None
    )
    review = (
        database.get(CodeReview, answer.code_review_id)
        if answer and answer.code_review_id
        else None
    )
    work = database.get(WorkItem, work_id)
    assert work is not None
    return bounded(
        WorkFollowUpDetail(
            follow_up=follow_up_read(question),
            answer=answer_read(answer) if answer else None,
            code_review=review_read(review) if review else None,
            source_work_state=source_state(work),
        ),
        65536,
    )


def review_context(database: Session, work_id: UUID) -> CodeReviewContext:
    work = database.get(WorkItem, work_id)
    assert work is not None
    review = database.scalar(
        select(CodeReview).where(
            CodeReview.work_item_id == work.id,
            CodeReview.state == "requested",
        )
    )
    question = database.scalar(
        select(WorkAgentFollowUp).where(
            WorkAgentFollowUp.work_item_id == work.id,
            WorkAgentFollowUp.state == "pending",
        )
    )
    origin = (
        database.get(CodeReviewRemediation, work.remediation_id) if work.remediation_id else None
    )
    return CodeReviewContext(
        remediation_depth=work.remediation_depth,
        current_review=review_read(review) if review else None,
        pending_follow_up=follow_up_read(question) if question else None,
        remediation_origin=remediation_read(origin) if origin else None,
    )


def queue_page(
    database: Session,
    project_id: UUID,
    *,
    kind: str,
    state: str,
    availability: str,
    work_item_id: UUID | None,
    after: str | None,
    limit: int,
) -> ReviewQueuePage:
    head = activity_head(database, project_id)
    scope = {
        "state": state,
        "availability": availability,
        "work_item_id": str(work_item_id) if work_item_id else None,
    }
    upper, last = head.last_sequence, head.last_sequence + 1
    if after is not None:
        cursor = decode_cursor(after, head, kind, scope)
        upper, last = int(cursor["upper"]), int(cursor["last"])
    model = CodeReview if kind == "code_reviews" else WorkAgentFollowUp
    now = database_now(database)
    query = (
        select(model, WorkItem, WorkLease, CodeReviewRemediation.remediation_work_item_id)
        .join(WorkItem, WorkItem.id == model.work_item_id)
        .outerjoin(
            WorkLease,
            WorkLease.work_item_id == WorkItem.id,
        )
        .outerjoin(
            CodeReviewRemediation,
            and_(literal(kind == "code_reviews"), CodeReviewRemediation.review_id == model.id),
        )
        .where(
            model.project_id == project_id,
            model.created_sequence <= upper,
            model.created_sequence < last,
        )
    )
    if state != "all":
        query = query.where(model.state == state)
    if work_item_id is not None:
        query = query.where(model.work_item_id == work_item_id)
    if availability == "unclaimed":
        query = query.where(
            model.state == "requested",
            WorkItem.status == "done",
            WorkItem.deleted_at.is_(None),
            WorkItem.id.not_in(select(WorkDuplicateMerge.source_work_item_id)),
            or_(WorkLease.work_item_id.is_(None), WorkLease.expires_at <= now),
        )
    rows = database.execute(query.order_by(model.created_sequence.desc()).limit(limit + 1)).all()
    items = [
        _queue_row(resource, work, lease, child, now)
        for resource, work, lease, child in rows[:limit]
    ]
    next_last = int(items[-1].created_sequence) if items else min(last, upper)
    return bounded(
        ReviewQueuePage(
            project_id=project_id,
            items=items,
            has_more=len(rows) > limit,
            next_cursor=encode_cursor(
                {**cursor_base(head, kind, **scope), "upper": str(upper), "last": str(next_last)}
            ),
        ),
        524288,
    )


def _queue_row(
    resource: Any,
    work: WorkItem,
    lease: WorkLease | None,
    child: UUID | None,
    now: Any,
) -> ReviewQueueRow:
    active = lease is not None and lease.expires_at > now
    review = isinstance(resource, CodeReview)
    matching_lease = (
        active
        and review
        and resource.state == "requested"
        and lease is not None
        and lease.purpose == "code_review"
        and lease.code_review_id == resource.id
    )
    return bounded(
        ReviewQueueRow(
            id=resource.id,
            project_id=work.project_id,
            work_item_id=work.id,
            title=work.title,
            work_status=work.status,
            state=resource.state,
            version=resource.version,
            created_sequence=str(resource.created_sequence),
            created_at=resource.created_at,
            request_reason=resource.request_reason if review else None,
            kind=None if review else resource.kind,
            remediation_depth=work.remediation_depth,
            review_available=review and resource.state == "requested" and not active,
            result_id=resource.result_id if review else None,
            remediation_work_item_id=child,
            lease=(
                LeasePublic.model_validate(lease).model_dump(mode="json")
                if matching_lease
                else None
            ),
        ),
        8192,
    )
