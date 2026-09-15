"""Human review requests and first-claim preparation of an unscoped request."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.code_review_schemas import (
    CodeReviewHandoffInput,
    ManualReviewRequest,
    scope_hash,
)
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import (
    Checkpoint,
    CodeReview,
    CodeReviewHandoff,
    CodeReviewScope,
    ProjectSettings,
    WorkAgentFollowUp,
    WorkCompletionReviewPolicy,
    WorkEvent,
    WorkItem,
    WorkLease,
)
from mnemonic_api.schemas import MutationActor, WorkClaimCreate, WorkItemPatch
from mnemonic_api.services.code_review_records import bind_created_event, stage_review_event
from mnemonic_api.services.work_events import _event, database_now


def _require_request(database: Session, work: WorkItem, payload: WorkItemPatch) -> None:
    actor = payload.actor
    if actor is None or actor.actor_client != "dashboard" or actor.actor_model is not None:
        raise ApplicationError(
            422, "review_request_requires_human", "A dashboard human is required."
        )
    if not database.info.get("client_operation_keyed"):
        raise ApplicationError(
            422, "client_operation_id_required", "An operation UUID is required."
        )
    if payload.model_fields_set - {
        "expected_version",
        "actor",
        "client_operation_id",
        "request_code_review",
    }:
        raise ApplicationError(422, "review_request_invalid", "Submit the review request alone.")
    if work.remediation_depth >= 2:
        raise conflict("code_review_depth_forbidden", "Further remediation cannot be reviewed.")
    settings = database.get(ProjectSettings, work.project_id)
    assert settings is not None
    if work.remediation_depth and not settings.allow_remediation_code_reviews:
        raise conflict("code_review_remediation_disabled", "Enable remediation reviews first.")
    if work.manual_review_request is not None:
        raise conflict("code_review_already_requested", "This work is already marked for review.")


def request_manual_review(database: Session, work: WorkItem, payload: WorkItemPatch) -> None:
    _require_request(database, work, payload)
    if work.status == "done":
        require_unreviewed_episode(database, work)
    assert payload.actor is not None
    now, request_id = database_now(database), uuid4()
    work.version += 1
    work.updated_at = now
    database.flush()
    event = _event(
        project_id=work.project_id,
        work_item_id=work.id,
        event_type="progress",
        actor=payload.actor,
        body="A human operator requested a code review when this work is Done.",
        metadata={"manual_review_request_id": str(request_id), "work_version": work.version},
        created_at=now,
    )
    database.add(event)
    database.flush()
    work.manual_review_request = ManualReviewRequest(
        id=request_id,
        **payload.actor.model_dump(),
        created_at=now,
        event_id=str(event.id),
        work_version=work.version,
        priority=work.priority,
    ).model_dump(mode="json")
    database.flush()
    if work.status == "done":
        create_manual_review(database, work)


def require_unreviewed_episode(database: Session, work: WorkItem) -> Checkpoint:
    checkpoint = database.scalar(
        select(Checkpoint).where(
            Checkpoint.work_item_id == work.id,
            Checkpoint.kind == "completion",
            Checkpoint.completion_generation == work.completion_generation,
        )
    )
    if checkpoint is None:
        raise conflict("completion_episode_unsealed", "The Done completion is unavailable.")
    if database.scalar(
        select(CodeReview.id).where(
            CodeReview.completion_checkpoint_id == checkpoint.id,
        )
    ) or database.scalar(
        select(WorkAgentFollowUp.id).where(
            WorkAgentFollowUp.completion_checkpoint_id == checkpoint.id,
            WorkAgentFollowUp.state == "pending",
        )
    ):
        raise conflict("code_review_already_requested", "This completion already has a review.")
    return checkpoint


def create_manual_review(
    database: Session,
    work: WorkItem,
    handoff: CodeReviewHandoffInput | None = None,
) -> CodeReview:
    checkpoint = require_unreviewed_episode(database, work)
    event = database.scalar(
        select(WorkEvent).where(
            WorkEvent.work_item_id == work.id,
            WorkEvent.event_type == "work_completed",
            WorkEvent.checkpoint_id == checkpoint.id,
        )
    )
    if event is None:
        raise conflict("completion_episode_unsealed", "The completion event is unavailable.")
    policy = database.scalar(
        select(WorkCompletionReviewPolicy).where(
            WorkCompletionReviewPolicy.completion_checkpoint_id == checkpoint.id,
        )
    )
    request = work.manual_review_request
    assert request is not None
    actor = MutationActor(
        **{
            key: request[key]
            for key in (
                "actor_client",
                "actor_session_id",
                "actor_model",
            )
        }
    )
    review = CodeReview(
        id=uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        completion_checkpoint_id=checkpoint.id,
        completion_event_id=event.id,
        policy_decision_id=policy.id if policy else None,
        answer_id=None,
        request_reason="manual",
        manual_request=request,
        schema_version=1,
        version=1,
        state="requested",
        requesting_client=actor.actor_client,
        requesting_session_id=actor.actor_session_id,
        requesting_model=None,
        scope_sha256=scope_hash(handoff.scope) if handoff else None,
        created_at=database_now(database),
    )
    database.add(review)
    database.flush()
    if handoff is not None:
        database.add(
            CodeReviewScope(
                review_id=review.id,
                project_id=work.project_id,
                work_item_id=work.id,
                repositories=handoff.scope.model_dump(mode="json")["repositories"],
            )
        )
        database.add(
            CodeReviewHandoff(
                review_id=review.id,
                project_id=work.project_id,
                work_item_id=work.id,
                **handoff.handoff.model_dump(mode="json"),
            )
        )
        database.flush()
    created = stage_review_event(
        database, work, "code_review_requested", actor, code_review_id=review.id
    )
    bind_created_event(database, review, created)
    return review


def require_claim_scope(database: Session, review: CodeReview, payload: WorkClaimCreate) -> None:
    handoff = payload.code_review_handoff
    if review.scope_sha256 is None:
        if handoff is None or payload.mode != "warm":
            raise ApplicationError(
                422,
                "code_review_scope_required",
                "Prepare this manual review with a handoff and warm claim.",
            )
        return
    preparation = review.scope_preparation
    same_claim = preparation and preparation["claim_request_id"] == str(payload.claim_request_id)
    if not same_claim:
        if handoff is not None:
            raise ApplicationError(
                422,
                "code_review_scope_already_pinned",
                "This review already has an immutable scope; omit the handoff.",
            )
        return
    scope = database.get(CodeReviewScope, review.id)
    notes = database.get(CodeReviewHandoff, review.id)
    assert scope is not None and notes is not None
    if (
        handoff is None
        or handoff.scope.model_dump(mode="json")["repositories"] != scope.repositories
        or any(getattr(notes, key) != value for key, value in handoff.handoff.model_dump().items())
    ):
        raise conflict("claim_request_mismatch", "Retry the exact original review handoff.")


def seal_claim_scope(database: Session, lease: WorkLease, payload: WorkClaimCreate) -> None:
    if payload.code_review_handoff is None:
        return
    review = database.get(CodeReview, lease.code_review_id)
    assert review is not None
    if review.scope_sha256 is not None:
        return
    handoff = payload.code_review_handoff
    database.add(
        CodeReviewScope(
            review_id=review.id,
            project_id=review.project_id,
            work_item_id=review.work_item_id,
            repositories=handoff.scope.model_dump(mode="json")["repositories"],
        )
    )
    database.add(
        CodeReviewHandoff(
            review_id=review.id,
            project_id=review.project_id,
            work_item_id=review.work_item_id,
            **handoff.handoff.model_dump(mode="json"),
        )
    )
    database.flush()
    event_id = database.scalar(
        select(WorkEvent.id).where(
            WorkEvent.lease_generation_id == lease.lease_generation_id,
            WorkEvent.event_type == "work_claimed",
        )
    )
    review.scope_sha256 = scope_hash(handoff.scope)
    review.scope_preparation = {
        "claim_request_id": str(lease.claim_request_id),
        "event_id": str(event_id),
    }
    database.flush()
