"""Atomic completion policy, typed follow-up answers and review/remediation lifecycle."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.code_review_schemas import (
    CodeReviewHandoffInput,
    CodeReviewHandoffNotes,
    CodeReviewScopeInput,
    review_policy,
    scope_hash,
)
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import (
    CodeReview,
    CodeReviewFinding,
    CodeReviewHandoff,
    CodeReviewRemediation,
    CodeReviewResult,
    CodeReviewScope,
    ProjectSettings,
    WorkAgentFollowUp,
    WorkAgentFollowUpAnswer,
    WorkCompletionReviewPolicy,
    WorkEvent,
    WorkItem,
    WorkLease,
)
from mnemonic_api.schemas import (
    CodeReviewCompletionRead,
    CodeReviewCompletionRequest,
    InitialCheckpointCreate,
    MutationActor,
    WorkCreation,
    WorkFollowUpResponseRequest,
    WorkFollowUpResponseResult,
    WorkItemCreate,
    WorkItemPatch,
    WorkItemRead,
)
from mnemonic_api.services.code_review_records import (
    answer_read,
    bind_created_event,
    bounded,
    follow_up_read,
    policy_read,
    remediation_read,
    require_episode,
    require_follow_up,
    require_requested,
    require_review,
    result_read,
    review_read,
    stage_review_event,
)
from mnemonic_api.services.work_events import database_now

SUPERSESSION_FIELDS = frozenset(
    {
        "supersede_code_review_id",
        "expected_code_review_version",
        "supersede_follow_up_id",
        "expected_follow_up_version",
    }
)
QUESTION = (
    "Do you recommend an adversarial code review of the work you just completed? "
    "Answer yes or no and give a concise reason. Consider complexity, application-wide changes, "
    "rework of faulty code, security or other critical behavior, and mistakes encountered. "
    "A comprehensive review already completed in this session, trivial changes, an owner's "
    "request for no review, or well-supported confidence may justify no. These examples are "
    "not exhaustive. If yes, provide the exact Git scope and a reviewer handoff describing "
    "decisions and reasons, concerns, and implementation or testing traps."
)


def prepare_review_policy(
    work: WorkItem, settings: ProjectSettings, handoff: CodeReviewHandoffInput | None
) -> str:
    decision = review_policy(
        work.priority,
        settings.code_review_required_min_priority,
        settings.code_review_optional_min_priority,
        settings.allow_remediation_code_reviews,
        work.remediation_depth,
    )
    if decision == "mandatory" and handoff is None:
        raise ApplicationError(
            422, "code_review_handoff_required", "Mandatory review needs a handoff."
        )
    if decision != "mandatory" and handoff is not None:
        raise ApplicationError(
            422,
            "code_review_handoff_not_applicable",
            "This closeout does not accept a mandatory review handoff.",
        )
    return decision


def _create_review(
    database: Session,
    work: WorkItem,
    policy: WorkCompletionReviewPolicy,
    handoff: CodeReviewHandoffInput,
    actor: MutationActor,
    answer: WorkAgentFollowUpAnswer | None = None,
) -> CodeReview:
    review = CodeReview(
        id=answer.code_review_id if answer else uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        completion_checkpoint_id=policy.completion_checkpoint_id,
        completion_event_id=policy.completion_event_id,
        policy_decision_id=policy.id,
        answer_id=answer.id if answer else None,
        request_reason="recommended" if answer else "mandatory",
        schema_version=1,
        version=1,
        state="requested",
        requesting_client=actor.actor_client,
        requesting_session_id=actor.actor_session_id,
        requesting_model=actor.actor_model,
        scope_sha256=scope_hash(handoff.scope),
        created_at=database_now(database),
    )
    database.add(review)
    database.flush()
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
    event = stage_review_event(
        database, work, "code_review_requested", actor, code_review_id=review.id
    )
    bind_created_event(database, review, event)
    return review


def seal_review_policy(
    database: Session,
    work: WorkItem,
    event: WorkEvent,
    settings: ProjectSettings,
    handoff: CodeReviewHandoffInput | None,
    decision: str,
    actor: MutationActor,
) -> None:
    policy = WorkCompletionReviewPolicy(
        id=uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        completion_checkpoint_id=event.checkpoint_id,
        completion_event_id=event.id,
        settings_revision=settings.revision,
        required_min_priority=settings.code_review_required_min_priority,
        optional_min_priority=settings.code_review_optional_min_priority,
        allow_remediation_code_reviews=settings.allow_remediation_code_reviews,
        priority_at_closeout=work.priority,
        remediation_depth=work.remediation_depth,
        decision=decision,
        created_at=event.created_at,
    )
    database.add(policy)
    database.flush()
    if decision == "mandatory":
        assert handoff is not None
        _create_review(database, work, policy, handoff, actor)
    elif decision == "ask_recommendation":
        _create_question(database, work, policy, actor)


def _create_question(
    database: Session, work: WorkItem, policy: WorkCompletionReviewPolicy, actor: MutationActor
) -> None:
    human = actor.actor_client == "dashboard" and actor.actor_model is None
    question = WorkAgentFollowUp(
        id=uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        trigger_event_id=policy.completion_event_id,
        completion_checkpoint_id=policy.completion_checkpoint_id,
        kind="code_review_recommendation",
        schema_version=1,
        version=1,
        audience="origin_human" if human else "origin_agent",
        question=QUESTION.replace("work you just completed", "work you just marked Done")
        if human
        else QUESTION,
        allowed_answers=["yes", "no"],
        required_answer_fields=["recommend_review", "rationale"],
        origin_client=actor.actor_client,
        origin_session_id=actor.actor_session_id,
        origin_model=actor.actor_model,
        kind_data={"policy_decision_id": str(policy.id)},
        state="pending",
        created_at=database_now(database),
    )
    database.add(question)
    database.flush()
    event = stage_review_event(
        database, work, "work_follow_up_requested", actor, work_follow_up_id=question.id
    )
    bind_created_event(database, question, event)


def completion_review_fields(database: Session, checkpoint_id: UUID) -> dict[str, Any]:
    policy = database.scalar(
        select(WorkCompletionReviewPolicy).where(
            WorkCompletionReviewPolicy.completion_checkpoint_id == checkpoint_id,
        )
    )
    if policy is None:
        return {}
    values: dict[str, Any] = {"review_policy_decision": policy_read(policy)}
    if policy.decision == "mandatory":
        review = database.scalar(
            select(CodeReview).where(CodeReview.policy_decision_id == policy.id)
        )
        assert review is not None
        values["code_review_request"] = review_read(review)
        scope = database.get(CodeReviewScope, review.id)
        notes = database.get(CodeReviewHandoff, review.id)
        assert scope is not None and notes is not None
        values["code_review_handoff"] = CodeReviewHandoffInput(
            scope=CodeReviewScopeInput(repositories=scope.repositories),
            handoff=CodeReviewHandoffNotes.model_validate(notes),
        )
    elif policy.decision == "ask_recommendation":
        question = database.scalar(
            select(WorkAgentFollowUp).where(
                WorkAgentFollowUp.completion_checkpoint_id == checkpoint_id,
            )
        )
        assert question is not None
        values["agent_follow_ups"] = [follow_up_read(question)]
    return values


def answer_follow_up(
    database: Session, work: WorkItem, follow_up_id: UUID, payload: WorkFollowUpResponseRequest
) -> WorkFollowUpResponseResult:
    question = require_follow_up(database, work.project_id, work.id, follow_up_id, lock=True)
    if question.state != "pending" or question.version != payload.expected_follow_up_version:
        code = (
            "work_follow_up_superseded"
            if question.state == "superseded"
            else "work_follow_up_changed"
        )
        raise conflict(code, "The follow-up is no longer the pending version you observed.")
    if question.kind != "code_review_recommendation" or question.completion_checkpoint_id is None:
        raise ApplicationError(422, "work_follow_up_answer_invalid", "Unsupported follow-up kind.")
    require_episode(database, work, question.completion_checkpoint_id)
    if (payload.actor.actor_client, payload.actor.actor_session_id) != (
        question.origin_client,
        question.origin_session_id,
    ):
        raise conflict("work_follow_up_origin_mismatch", "Only the originating session may answer.")
    policy = database.get(
        WorkCompletionReviewPolicy, UUID(question.kind_data["policy_decision_id"])
    )
    assert policy is not None
    answer = WorkAgentFollowUpAnswer(
        id=uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        follow_up_id=question.id,
        recommend_review=payload.answer.recommend_review,
        rationale=payload.answer.rationale,
        **payload.actor.model_dump(),
        code_review_id=uuid4() if payload.answer.recommend_review else None,
        created_at=database_now(database),
    )
    database.add(answer)
    database.flush()
    review = None
    if payload.answer.recommend_review:
        assert payload.answer.code_review_handoff is not None
        review = _create_review(
            database, work, policy, payload.answer.code_review_handoff, payload.actor, answer
        )
    event = stage_review_event(
        database,
        work,
        "work_follow_up_answered",
        payload.actor,
        work_follow_up_id=question.id,
        work_follow_up_answer_id=answer.id,
    )
    bind_created_event(database, answer, event)
    question.state, question.answer_id = "answered", answer.id
    question.version += 1
    database.flush()
    fields = {"code_review_request": review_read(review),
              "code_review_handoff": payload.answer.code_review_handoff} if review else {}
    return WorkFollowUpResponseResult(
        follow_up=follow_up_read(question), answer=answer_read(answer), **fields
    )


def require_no_review_obligation(database: Session, work_id: UUID) -> None:
    review = database.scalar(
        select(CodeReview.id)
        .where(
            CodeReview.work_item_id == work_id,
            CodeReview.state == "requested",
        )
        .limit(1)
    )
    question = database.scalar(
        select(WorkAgentFollowUp.id)
        .where(
            WorkAgentFollowUp.work_item_id == work_id,
            WorkAgentFollowUp.state == "pending",
        )
        .limit(1)
    )
    if review is not None or question is not None:
        raise conflict(
            "code_review_obligation_outstanding", "Reopen explicitly to supersede review work."
        )


def supersede_for_reopen(database: Session, work: WorkItem, payload: WorkItemPatch) -> None:
    review = database.scalar(
        select(CodeReview)
        .where(
            CodeReview.work_item_id == work.id,
            CodeReview.state == "requested",
        )
        .with_for_update()
    )
    question = database.scalar(
        select(WorkAgentFollowUp)
        .where(
            WorkAgentFollowUp.work_item_id == work.id,
            WorkAgentFollowUp.state == "pending",
        )
        .with_for_update()
    )
    supplied = bool(payload.model_fields_set & SUPERSESSION_FIELDS)
    if review is None and question is None:
        if supplied:
            raise conflict(
                "code_review_changed", "No matching outstanding review obligation exists."
            )
        return
    if payload.status != "pending" or work.status != "done" or payload.actor is None:
        if supplied or payload.status == "pending":
            raise conflict(
                "code_review_obligation_outstanding", "Explicit reopen intent is required."
            )
        return
    if payload.lease_token is not None:
        raise conflict("lease_purpose_mismatch", "Review capabilities cannot authorize reopening.")
    if payload.client_operation_id is None:
        # The registered wrapper removes this control only for distinct domain models;
        # update_work retains its existing WorkItemPatch domain model.
        if not database.info.get("client_operation_keyed"):
            raise conflict(
                "code_review_obligation_outstanding", "Supersession needs an operation UUID."
            )
    if review is not None:
        _supersede_review(database, work, review, payload)
    if question is not None:
        _supersede_question(database, work, question, payload)


def _supersede_review(
    database: Session, work: WorkItem, review: CodeReview, payload: WorkItemPatch
) -> None:
    if (
        payload.supersede_code_review_id != review.id
        or payload.expected_code_review_version != review.version
        or payload.supersede_follow_up_id is not None
    ):
        raise conflict(
            "code_review_changed", "Explicit current review identity is required to reopen."
        )
    assert payload.actor is not None
    event = stage_review_event(
        database, work, "code_review_superseded", payload.actor, code_review_id=review.id
    )
    review.state, review.superseded_by_event_id = "superseded", event.id
    review.version += 1
    lease = database.scalar(
        select(WorkLease).where(WorkLease.work_item_id == work.id).with_for_update()
    )
    if lease is not None:
        from mnemonic_api.services.leases import release_lease_record

        release_lease_record(database, work, lease.lease_token, payload.actor)
    database.flush()


def _supersede_question(
    database: Session, work: WorkItem, question: WorkAgentFollowUp, payload: WorkItemPatch
) -> None:
    if (
        payload.supersede_follow_up_id != question.id
        or payload.expected_follow_up_version != question.version
        or payload.supersede_code_review_id is not None
    ):
        raise conflict(
            "work_follow_up_changed", "Exact pending question identity is required to reopen."
        )
    assert payload.actor is not None
    event = stage_review_event(
        database, work, "work_follow_up_superseded", payload.actor, work_follow_up_id=question.id
    )
    question.state, question.superseded_by_event_id = "superseded", event.id
    question.version += 1
    database.flush()


def _review_lease(
    database: Session, work: WorkItem, review: CodeReview, payload: CodeReviewCompletionRequest
) -> tuple[WorkLease, WorkEvent]:
    from mnemonic_api.services.leases import _expired, _same_token, _token_mismatch

    lease = database.scalar(
        select(WorkLease).where(WorkLease.work_item_id == work.id).with_for_update()
    )
    if lease is None or not _same_token(payload.lease_token, lease.lease_token):
        _token_mismatch()
    assert lease is not None
    if lease.expires_at <= database_now(database):
        _expired()
    if (
        lease.purpose != "code_review"
        or lease.code_review_id != review.id
        or lease.mode != payload.result.mode
        or (lease.holder_client, lease.holder_session_id)
        != (payload.actor.actor_client, payload.actor.actor_session_id)
    ):
        raise conflict("lease_purpose_mismatch", "A matching reviewer capability is required.")
    claim = database.scalar(
        select(WorkEvent).where(
            WorkEvent.work_item_id == work.id,
            WorkEvent.event_type == "work_claimed",
            WorkEvent.lease_generation_id == lease.lease_generation_id,
        )
    )
    if claim is None or claim.code_review_id != review.id:
        raise conflict("lease_purpose_mismatch", "Review claim provenance is unavailable.")
    return lease, claim


def complete_review(
    database: Session, work: WorkItem, review_id: UUID, payload: CodeReviewCompletionRequest
) -> CodeReviewCompletionRead:
    review = require_review(database, work.project_id, work.id, review_id, lock=True)
    require_requested(database, work, review)
    if review.version != payload.expected_review_version:
        raise conflict("code_review_changed", "Review version changed.")
    _require_review_scope(database, review, payload)
    lease, claim = _review_lease(database, work, review, payload)
    result = CodeReviewResult(
        id=uuid4(),
        project_id=work.project_id,
        work_item_id=work.id,
        review_id=review.id,
        mode=payload.result.mode,
        scope_sha256=review.scope_sha256,
        summary=payload.result.summary,
        coverage=[r.model_dump(mode="json") for r in payload.result.coverage],
        limitations=payload.result.limitations,
        findings_count=len(payload.result.findings),
        **payload.actor.model_dump(),
        lease_generation_id=lease.lease_generation_id,
        claim_event_id=claim.id,
        created_at=database_now(database),
    )
    database.add(result)
    database.flush()
    for position, finding in enumerate(payload.result.findings):
        database.add(
            CodeReviewFinding(
                result_id=result.id,
                position=position,
                finding_key=finding.finding_key,
                data=finding.model_dump(mode="json"),
            )
        )
    database.flush()
    association, created = _create_remediation(database, work, review, result, payload)
    event = stage_review_event(
        database,
        work,
        "code_review_completed",
        payload.actor,
        code_review_id=review.id,
        code_review_result_id=result.id,
    )
    bind_created_event(database, result, event)
    review.state, review.result_id = "completed", result.id
    review.version += 1
    from mnemonic_api.services.leases import release_lease_record

    release_lease_record(database, work, payload.lease_token, payload.actor)
    database.flush()
    return bounded(
        CodeReviewCompletionRead(
            review=review_read(review),
            result=result_read(database, result),
            remediation=remediation_read(association) if association else None,
            remediation_work=created,
        ),
        1048576,
    )


def _require_review_scope(
    database: Session, review: CodeReview, payload: CodeReviewCompletionRequest
) -> None:
    scope = database.get(CodeReviewScope, review.id)
    assert scope is not None
    expected = [
        (r["repository_key"], r["base_commit"], r["head_commit"]) for r in scope.repositories
    ]
    actual = [(r.repository_key, r.base_commit, r.head_commit) for r in payload.result.coverage]
    if payload.scope_sha256 != review.scope_sha256:
        raise ApplicationError(
            422, "code_review_scope_mismatch", "Scope hash does not match this review."
        )
    if actual != expected:
        raise ApplicationError(
            422, "code_review_coverage_incomplete", "Coverage must match every pinned range."
        )


def _create_remediation(
    database: Session,
    work: WorkItem,
    review: CodeReview,
    result: CodeReviewResult,
    payload: CodeReviewCompletionRequest,
) -> tuple[CodeReviewRemediation | None, WorkCreation | None]:
    if not payload.result.findings:
        return None, None
    from mnemonic_api.services.relationships import relationship_edge, stage_relationship_locked
    from mnemonic_api.services.work_context import checkpoint_read
    from mnemonic_api.services.work_events import stage_relationship_events
    from mnemonic_api.services.work_items import create_work_records

    policy = database.get(WorkCompletionReviewPolicy, review.policy_decision_id)
    assert policy is not None
    association_id = uuid4()
    initial = InitialCheckpointCreate(
        prompt=_remediation_prompt(result.id, payload),
        source_client=payload.actor.actor_client,
        source_session_id=payload.actor.actor_session_id,
        source_model=payload.actor.actor_model,
    )
    created_work, checkpoint, _ = create_work_records(
        database,
        work.project_id,
        WorkItemCreate(
            title=("Remediate review: " + work.title)[:200],
            summary="Fix all actionable findings from code review " + str(review.id) + ".",
            priority=policy.priority_at_closeout,
            initial_checkpoint=initial,
        ),
        remediation_id=association_id,
        remediation_depth=work.remediation_depth + 1,
    )
    edge, _ = stage_relationship_locked(
        database,
        project_id=work.project_id,
        relationship_type="discovered-from",
        source_work_item_id=created_work.id,
        target_work_item_id=work.id,
        created_by_client=payload.actor.actor_client,
        created_by_session_id=payload.actor.actor_session_id,
        created_by_model=payload.actor.actor_model,
        context_checkpoint_id=review.completion_checkpoint_id,
        locked_work_items={work.id: work, created_work.id: created_work},
    )
    stage_relationship_events(
        database, edge, action="added", actor=payload.actor, created_at=edge.created_at
    )
    parent = (
        database.get(CodeReviewRemediation, work.remediation_id) if work.remediation_id else None
    )
    association = CodeReviewRemediation(
        id=association_id,
        project_id=work.project_id,
        review_id=review.id,
        result_id=result.id,
        source_work_item_id=work.id,
        completion_checkpoint_id=review.completion_checkpoint_id,
        remediation_work_item_id=created_work.id,
        relationship_id=edge.id,
        parent_remediation_id=work.remediation_id,
        root_work_item_id=parent.root_work_item_id if parent else work.id,
        depth=work.remediation_depth + 1,
        created_at=database_now(database),
    )
    database.add(association)
    database.flush()
    return association, WorkCreation(
        work_item=WorkItemRead.model_validate(created_work),
        initial_checkpoint=checkpoint_read(checkpoint),
        initial_relationships=[relationship_edge(edge)],
    )


def _remediation_prompt(result_id: UUID, payload: CodeReviewCompletionRequest) -> str:
    chunks = [
        f"Remediate all findings from code review result {result_id}.",
        "Keep all findings in this one work item. Record progress by finding key.",
    ]
    for finding in payload.result.findings:
        chunks.append(f"\n- [ ] {finding.finding_key} ({finding.severity}): {finding.title}")
        for field, value in finding.model_dump(mode="json").items():
            if field not in {"finding_key", "severity", "title"} and value is not None:
                chunks.append(f"  {field}: {value}")
    return "\n".join(chunks)
