"""Shared exact-ownership reads and immutable event projection for reviews."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.code_review_schemas import (
    CodeReviewRead,
    CodeReviewRemediationRead,
    CodeReviewResultInput,
    CodeReviewResultRead,
    ReviewPolicyRead,
    WorkFollowUpAnswerRead,
    WorkFollowUpRead,
)
from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import (
    Checkpoint,
    CodeReview,
    CodeReviewFinding,
    CodeReviewResult,
    ProjectActivity,
    WorkAgentFollowUp,
    WorkEvent,
    WorkItem,
)
from mnemonic_api.schemas import MutationActor
from mnemonic_api.services.work_events import actor_fields, database_now


def wire[T: BaseModel](model: type[T], row: Any, **extra: Any) -> T:
    data = {name: getattr(row, name) for name in model.model_fields if name not in extra}
    for name in (
        "completion_event_id",
        "trigger_event_id",
        "created_event_id",
        "created_sequence",
        "settings_revision",
        "superseded_by_event_id",
        "claim_event_id",
    ):
        if name in data and data[name] is not None:
            data[name] = str(data[name])
    return model.model_validate({**data, **extra})


def policy_read(row: Any) -> ReviewPolicyRead:
    return wire(ReviewPolicyRead, row)


def review_read(row: CodeReview) -> CodeReviewRead:
    return wire(CodeReviewRead, row)


def follow_up_read(row: WorkAgentFollowUp) -> WorkFollowUpRead:
    return wire(WorkFollowUpRead, row)


def answer_read(row: Any) -> WorkFollowUpAnswerRead:
    return wire(WorkFollowUpAnswerRead, row)


def remediation_read(row: Any) -> CodeReviewRemediationRead:
    return wire(CodeReviewRemediationRead, row)


def result_read(database: Session, row: CodeReviewResult) -> CodeReviewResultRead:
    findings = list(
        database.scalars(
            select(CodeReviewFinding.data)
            .where(
                CodeReviewFinding.result_id == row.id,
            )
            .order_by(CodeReviewFinding.position)
        )
    )
    return wire(CodeReviewResultRead, row, findings=findings)


def require_review(
    database: Session, project_id: UUID, work_id: UUID, review_id: UUID, *, lock: bool = False
) -> CodeReview:
    query = select(CodeReview).where(
        CodeReview.id == review_id,
        CodeReview.project_id == project_id,
        CodeReview.work_item_id == work_id,
    )
    row = database.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found("code_review_not_found", "Review not found in this work item.")
    return row


def require_follow_up(
    database: Session, project_id: UUID, work_id: UUID, follow_up_id: UUID, *, lock: bool = False
) -> WorkAgentFollowUp:
    query = select(WorkAgentFollowUp).where(
        WorkAgentFollowUp.id == follow_up_id,
        WorkAgentFollowUp.project_id == project_id,
        WorkAgentFollowUp.work_item_id == work_id,
    )
    row = database.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise not_found("work_follow_up_not_found", "Follow-up not found in this work item.")
    return row


def require_episode(database: Session, work: WorkItem, checkpoint_id: UUID) -> None:
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work)
    checkpoint = database.get(Checkpoint, checkpoint_id)
    if (
        work.deleted_at is not None
        or work.status != "done"
        or checkpoint is None
        or checkpoint.work_item_id != work.id
        or checkpoint.completion_generation != work.completion_generation
    ):
        raise conflict("code_review_superseded", "The implementation episode is no longer current.")
    if work.remediation_depth >= 2:
        raise conflict("code_review_depth_forbidden", "Further remediation cannot be reviewed.")


def require_requested(database: Session, work: WorkItem, review: CodeReview) -> None:
    if review.state == "completed":
        raise conflict("code_review_already_completed", "This review already has a result.")
    if review.state != "requested":
        raise conflict("code_review_superseded", "This review was superseded.")
    require_episode(database, work, review.completion_checkpoint_id)


def stage_review_event(
    database: Session, work: WorkItem, kind: str, actor: MutationActor, **refs: UUID
) -> WorkEvent:
    event = WorkEvent(
        project_id=work.project_id,
        work_item_id=work.id,
        event_type=kind,
        **actor_fields(actor),
        body=None,
        event_metadata={key: str(value) for key, value in refs.items()},
        created_at=database_now(database),
        origin="live",
        **refs,
    )
    database.add(event)
    database.flush()
    return event


def bind_created_event(database: Session, row: Any, event: WorkEvent) -> None:
    row.created_event_id = event.id
    if hasattr(row, "created_sequence"):
        row.created_sequence = database.scalar(
            select(ProjectActivity.sequence).where(
                ProjectActivity.work_event_id == event.id,
            )
        )
    database.flush()


def bounded[T: BaseModel](value: T, maximum: int) -> T:
    if len(value.model_dump_json().encode()) > maximum:
        raise ApplicationError(503, "code_review_unavailable", "Review response exceeds its bound.")
    return value


def result_input(row: CodeReviewResultRead) -> CodeReviewResultInput:
    return CodeReviewResultInput.model_validate(
        row.model_dump(
            include=set(CodeReviewResultInput.model_fields),
        )
    )
