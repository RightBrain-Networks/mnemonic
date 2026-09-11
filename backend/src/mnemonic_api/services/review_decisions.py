"""Human dispositions of a review episode without rewriting agent review results."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import CodeReview, WorkAgentFollowUp, WorkItem, WorkLease
from mnemonic_api.schemas import WorkItemPatch
from mnemonic_api.services.code_review_records import require_episode
from mnemonic_api.services.job_completion_reports import prepare_closeout_report
from mnemonic_api.services.work_events import _event, database_now


def disposition(resource: CodeReview | WorkAgentFollowUp) -> str:
    return resource.human_decisions[-1]["status"] if resource.human_decisions else "to-review"


def _require_human_intent(database: Session, payload: WorkItemPatch) -> None:
    if (
        payload.actor is None
        or payload.actor.actor_client != "dashboard"
        or payload.actor.actor_model is not None
    ):
        raise ApplicationError(
            422, "review_decision_requires_human", "A dashboard human is required."
        )
    if not database.info.get("client_operation_keyed"):
        raise ApplicationError(
            422, "client_operation_id_required", "An operation UUID is required."
        )
    if payload.model_fields_set - {
        "expected_version",
        "actor",
        "client_operation_id",
        "review_decision",
    }:
        raise ApplicationError(422, "review_decision_invalid", "Submit the review decision alone.")


def record_human_review_decision(
    database: Session,
    work: WorkItem,
    payload: WorkItemPatch,
) -> None:
    _require_human_intent(database, payload)
    decision = payload.review_decision
    assert decision is not None and payload.actor is not None
    resource = database.scalar(
        select(CodeReview)
        .where(
            CodeReview.id == decision.resource_id,
            CodeReview.work_item_id == work.id,
            CodeReview.state == "requested",
        )
        .with_for_update()
    ) or database.scalar(
        select(WorkAgentFollowUp)
        .where(
            WorkAgentFollowUp.id == decision.resource_id,
            WorkAgentFollowUp.work_item_id == work.id,
            WorkAgentFollowUp.state == "pending",
        )
        .with_for_update()
    )
    if resource is None:
        raise conflict("code_review_changed", "The review episode is no longer open.")
    assert resource.completion_checkpoint_id is not None
    require_episode(database, work, resource.completion_checkpoint_id)
    if len(resource.human_decisions) != decision.expected_decision_version:
        raise conflict("code_review_changed", "The human review decision changed. Refresh first.")
    if disposition(resource) == decision.status:
        raise conflict("review_decision_unchanged", "The review already has this status.")
    if decision.status in {"done", "wont-do", "promoted"}:
        prepare_closeout_report(database, work, decision.job_completion_report)
    elif decision.job_completion_report is not None:
        raise ApplicationError(
            422,
            "job_completion_report_not_applicable",
            "Only a terminal review decision takes a report.",
        )
    lease = database.scalar(
        select(WorkLease)
        .where(
            WorkLease.work_item_id == work.id,
        )
        .with_for_update()
    )
    if lease is not None:
        from mnemonic_api.services.leases import release_lease_record

        release_lease_record(database, work, lease.lease_token, payload.actor)
    work.version += 1
    work.updated_at = database_now(database)
    database.flush()
    version = len(resource.human_decisions) + 1
    event = _event(
        project_id=work.project_id,
        work_item_id=work.id,
        event_type="progress",
        actor=payload.actor,
        created_at=work.updated_at,
        body=f"A person set the code review to {decision.status}.",
        metadata={
            "review_resource_id": str(resource.id),
            "review_status": decision.status,
            "decision_version": version,
            "work_version": work.version,
        },
    )
    database.add(event)
    database.flush()
    resource.human_decisions = [
        *resource.human_decisions,
        {
            "version": version,
            "status": decision.status,
            **payload.actor.model_dump(mode="json"),
            "event_id": str(event.id),
            "work_version": work.version,
            "created_at": work.updated_at.isoformat(),
            "job_completion_report": (
                decision.job_completion_report.model_dump(mode="json")
                if decision.job_completion_report
                else None
            ),
        },
    ]
    database.flush()


def decision_result(database: Session, payload: WorkItemPatch) -> dict:
    decision = payload.review_decision
    assert decision is not None
    resource = database.get(CodeReview, decision.resource_id) or database.get(
        WorkAgentFollowUp, decision.resource_id
    )
    assert resource is not None
    return {**resource.human_decisions[-1], "resource_id": resource.id}
