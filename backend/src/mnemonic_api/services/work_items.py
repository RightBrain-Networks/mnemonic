"""Canonical work/checkpoint mutations without transaction-boundary commits."""

from copy import deepcopy
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mnemonic_api.database import rows_affected
from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import Checkpoint, Project, WorkItem, WorkItemMove, WorkRelationship
from mnemonic_api.phase12_schemas import JobCompletionReportInput
from mnemonic_api.schemas import (
    CheckpointCreate,
    CompletionCheckpointCreate,
    CompletionEvidenceInput,
    MutationActor,
    WorkDeferralCreate,
    WorkItemCreate,
    WorkItemPatch,
    WorkMoveCreate,
)
from mnemonic_api.services.leases import (
    consume_lease_for_terminal_mutation,
    require_no_active_lease,
    require_no_active_lease_for_move,
    validate_optional_lease_token,
)
from mnemonic_api.services.readiness import require_no_unresolved_gates, require_unblocked
from mnemonic_api.services.relationships import (
    lock_endpoint_work_items,
    lock_project_graph,
    require_no_relationships,
    require_no_relationships_for_move,
    stage_relationship_locked,
)
from mnemonic_api.services.work_events import (
    actor_fields,
    database_now,
    source_actor,
    stage_checkpoint_added,
    stage_relationship_events,
    stage_work_changed,
    stage_work_completed,
    stage_work_created,
    stage_work_deleted,
    stage_work_moved_events,
)


def require_project(database: Session, project_id: UUID, *, lock: bool = False) -> Project:
    project = (
        database.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if lock
        else database.get(Project, project_id)
    )
    if project is None:
        raise not_found("project_not_found", "Project not found.")
    return project


def missing_work_item(database: Session, project_id: UUID) -> ApplicationError:
    """Name the ID that was actually wrong.

    A work-item lookup matches id and project_id together, so a miss alone does
    not say which one was bad — and the two have different recoveries. Resolve
    that with one extra query, taken only on a path that has already failed.
    """
    if database.get(Project, project_id) is None:
        return not_found("project_not_found", "Project not found.")
    return not_found("work_item_not_found", "Work item not found in this project.")


def require_work_item(
    database: Session, project_id: UUID, work_item_id: UUID, *, lock: bool = False
) -> WorkItem:
    statement = select(WorkItem).where(
        WorkItem.id == work_item_id,
        WorkItem.project_id == project_id,
        WorkItem.deleted_at.is_(None),
    )
    if lock:
        statement = statement.with_for_update()
    work_item = database.scalar(statement)
    if work_item is None:
        raise missing_work_item(database, project_id)
    return work_item


def require_version(work_item: WorkItem, expected_version: int) -> None:
    if work_item.version != expected_version:
        raise conflict(
            "version_conflict",
            "This work item changed. Recall it again before editing or deleting.",
        )


def _checkpoint(
    work_item_id: UUID,
    payload: CheckpointCreate | CompletionCheckpointCreate,
    *,
    kind: str,
) -> Checkpoint:
    values = payload.model_dump(exclude={"kind", "lease_token", "client_operation_id"})
    return Checkpoint(work_item_id=work_item_id, kind=kind, **values)


def create_work_records(
    database: Session, project_id: UUID, payload: WorkItemCreate, *,
    remediation_id: UUID | None = None, remediation_depth: int = 0,
) -> tuple[WorkItem, Checkpoint, list[WorkRelationship]]:
    """Stage required work, context, and requested graph facts in one transaction."""
    if payload.status != "pending":
        raise ApplicationError(422, "initial_status_must_be_pending", "New work must be pending.")
    if payload.initial_relationships:
        lock_project_graph(database, project_id)
        locked_work_items = lock_endpoint_work_items(
            database,
            project_id,
            [item.other_work_item_id for item in payload.initial_relationships],
        )
    else:
        require_project(database, project_id)
        locked_work_items = {}
    work_item_id = uuid4()
    initial_checkpoint_id = uuid4()
    work_item = WorkItem(
        id=work_item_id,
        project_id=project_id,
        title=payload.title,
        summary=payload.summary,
        external_references=[item.model_dump(mode="json") for item in payload.external_references],
        priority=payload.priority,
        status=payload.status,
        initial_checkpoint_id=initial_checkpoint_id,
        remediation_id=remediation_id,
        remediation_depth=remediation_depth,
    )
    checkpoint = Checkpoint(
        id=initial_checkpoint_id,
        work_item_id=work_item_id,
        kind="context",
        **payload.initial_checkpoint.model_dump(),
    )
    database.add(work_item)
    database.flush()
    database.add(checkpoint)
    database.flush()

    relationships: list[WorkRelationship] = []
    if payload.initial_relationships:
        locked_work_items[work_item.id] = work_item
        seen_relationship_ids: set[UUID] = set()
        ordered = sorted(
            payload.initial_relationships,
            key=lambda item: (
                item.type,
                "outgoing" if item.type == "related" else item.direction,
                str(item.other_work_item_id),
                str(item.context_checkpoint_id or ""),
            ),
        )
        for item in ordered:
            source_id, target_id = (
                (work_item.id, item.other_work_item_id)
                if item.direction == "outgoing"
                else (item.other_work_item_id, work_item.id)
            )
            relationship, _ = stage_relationship_locked(
                database,
                project_id=project_id,
                relationship_type=item.type,
                source_work_item_id=source_id,
                target_work_item_id=target_id,
                created_by_client=payload.initial_checkpoint.source_client,
                created_by_session_id=payload.initial_checkpoint.source_session_id,
                created_by_model=payload.initial_checkpoint.source_model,
                context_checkpoint_id=item.context_checkpoint_id,
                locked_work_items=locked_work_items,
            )
            if relationship.id not in seen_relationship_ids:
                relationships.append(relationship)
                seen_relationship_ids.add(relationship.id)
    stage_work_created(database, work_item, checkpoint)
    relationship_actor = source_actor(
        checkpoint.source_client,
        checkpoint.source_session_id,
        checkpoint.source_model,
    )
    for relationship in relationships:
        stage_relationship_events(
            database,
            relationship,
            action="added",
            actor=relationship_actor,
            created_at=relationship.created_at,
        )
    database.flush()
    return work_item, checkpoint, relationships


def append_checkpoint_record(
    database: Session, work_item: WorkItem, payload: CheckpointCreate
) -> Checkpoint:
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    # Token-bearing routes lock the work row before entering this helper. Lock
    # the retained lease here to preserve work -> lease order through commit.
    validate_optional_lease_token(
        database,
        work_item.id,
        payload.lease_token,
        lock=payload.lease_token is not None,
    )
    checkpoint = _checkpoint(work_item.id, payload, kind=payload.kind)
    database.add(checkpoint)
    activity_update = database.execute(
        update(WorkItem)
        .where(WorkItem.id == work_item.id, WorkItem.deleted_at.is_(None))
        .values(updated_at=func.greatest(WorkItem.updated_at, func.clock_timestamp()))
        .execution_options(synchronize_session=False)
    )
    if rows_affected(activity_update) != 1:
        raise not_found("work_item_not_found", "Work item not found.")
    database.flush()
    stage_checkpoint_added(database, work_item, checkpoint)
    database.flush()
    return checkpoint


def require_sealed_completion_episode(database: Session, work_item: WorkItem) -> None:
    """Refuse to leave ``done`` without the sealed episode the database demands.

    ``completion_episode_departure_guard`` enforces this, but a trigger raising
    reaches the caller as a 503 ``database_unavailable`` -- a transient-sounding
    answer for a permanently true condition. Asking the database's own predicate
    first turns it into a 409 naming the condition, and cannot drift from the
    guard because it is the same function.

    Work completed before ``0010_work_events`` is the case that reaches this:
    it owns no completion episode at all. The predicate is total -- a missing or
    ambiguous episode returns false rather than raising -- so an unsealed
    episode of any shape answers here rather than at the trigger.
    """

    sealed = database.scalar(
        select(
            func.mnemonic_completion_episode_is_sealed(
                work_item.id, work_item.completion_generation
            )
        )
    )
    if sealed is not True:
        from mnemonic_api.errors import completion_episode_unsealed

        raise completion_episode_unsealed()


def require_sealed_closeout_report(database: Session, work_item: WorkItem) -> None:
    slot = work_item.last_reportable_closeout_version
    if slot is None:
        if work_item.status in {"done", "wont-do", "promoted"}:
            from mnemonic_api.errors import closeout_report_unsealed

            raise closeout_report_unsealed()
        return
    sealed = database.scalar(
        select(func.mnemonic_job_report_slot_sealed(work_item.id, slot))
    )
    if sealed is not True:
        from mnemonic_api.errors import closeout_report_unsealed

        raise closeout_report_unsealed()


def update_work_record(database: Session, work_item: WorkItem, payload: WorkItemPatch) -> None:
    from mnemonic_api.services.code_reviews import SUPERSESSION_FIELDS, supersede_for_reopen
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    require_version(work_item, payload.expected_version)
    supersede_for_reopen(database, work_item, payload)
    changes = payload.model_dump(
        mode="json",
        exclude_unset=True,
        exclude={"expected_version", "lease_token", "actor", "client_operation_id",
                 "job_completion_report", *SUPERSESSION_FIELDS},
    )
    before = {
        field: deepcopy(getattr(work_item, field))
        for field in ("title", "summary", "priority", "status", "external_references")
    }
    requested_status = changes.get("status")
    if requested_status is not None and requested_status != work_item.status:
        allowed = {
            "pending": {"wont-do", "promoted"},
            "deferred": {"pending"},
            "wont-do": {"pending"},
            "promoted": {"pending"},
            "done": {"pending"},
        }
        if requested_status not in allowed[work_item.status]:
            raise conflict(
                "invalid_status_transition",
                "That lifecycle transition is not allowed.",
            )
        if work_item.status == "done":
            require_sealed_completion_episode(database, work_item)
    terminal_transition = (
        requested_status in {"done", "wont-do", "promoted"}
        and requested_status != work_item.status
    )
    from mnemonic_api.services.job_completion_reports import (
        prepare_closeout_report,
        seal_closeout_report,
    )

    report_id = uuid4() if terminal_transition else None
    report_settings = None
    if terminal_transition:
        report_settings = prepare_closeout_report(
            database, work_item, payload.job_completion_report
        )
    elif payload.job_completion_report is not None:
        raise ApplicationError(422, "job_completion_report_not_applicable",
                               "Reports belong only to an actual closeout transition.")
    if terminal_transition:
        require_no_unresolved_gates(database, work_item.id)
        consume_lease_for_terminal_mutation(database, work_item.id, payload.lease_token)
    else:
        validate_optional_lease_token(database, work_item.id, payload.lease_token, lock=True)
    mutation_time = database_now(database)
    for field, value in changes.items():
        setattr(work_item, field, value)
    work_item.version += 1
    work_item.updated_at = mutation_time
    database.flush()
    event = stage_work_changed(
        database,
        work_item,
        before=before,
        requested_fields=changes,
        actor=payload.actor,
        created_at=mutation_time,
    )
    event.job_completion_report_id = report_id
    database.flush()
    if report_id is not None:
        assert payload.job_completion_report is not None and report_settings is not None
        assert payload.actor is not None
        seal_closeout_report(database, work_item, event, report_id, payload.job_completion_report,
                             report_settings, payload.actor)


def defer_work_record(
    database: Session,
    work_item: WorkItem,
    payload: WorkDeferralCreate,
) -> None:
    """Apply the dashboard-only deferral transition without displacing active work."""
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    require_version(work_item, payload.expected_version)
    if work_item.status != "pending":
        raise conflict(
            "invalid_status_transition",
            "Only pending work can be deferred.",
        )
    require_no_active_lease(database, work_item.id)
    before = {
        field: deepcopy(getattr(work_item, field))
        for field in ("title", "summary", "priority", "status", "external_references")
    }
    mutation_time = database_now(database)
    work_item.status = "deferred"
    work_item.version += 1
    work_item.updated_at = mutation_time
    database.flush()
    stage_work_changed(
        database,
        work_item,
        before=before,
        requested_fields={"status"},
        actor=payload.actor,
        created_at=mutation_time,
    )
    database.flush()


def complete_work_record(
    database: Session,
    work_item: WorkItem,
    expected_version: int,
    payload: CompletionCheckpointCreate,
    lease_token: str | None = None,
    completion_evidence: CompletionEvidenceInput | None = None,
    job_completion_report: JobCompletionReportInput | None = None,
    code_review_handoff=None,
) -> Checkpoint:
    from mnemonic_api.services.completion_evidence import (
        hydrate_completion_evidence,
        insert_completion_evidence,
    )
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    if work_item.status != "pending":
        raise conflict("work_not_pending", "Only pending work can be completed.")
    require_version(work_item, expected_version)
    from mnemonic_api.services.job_completion_reports import (
        prepare_closeout_report,
        seal_closeout_report,
    )

    report_settings = prepare_closeout_report(database, work_item, job_completion_report)
    from mnemonic_api.services.code_reviews import prepare_review_policy, seal_review_policy

    review_decision = prepare_review_policy(work_item, report_settings, code_review_handoff)
    report_id = uuid4()
    require_unblocked(database, work_item.id)
    require_no_unresolved_gates(database, work_item.id)
    consume_lease_for_terminal_mutation(database, work_item.id, lease_token)
    mutation_time = database_now(database)
    checkpoint = _checkpoint(work_item.id, payload, kind="completion")
    database.add(checkpoint)
    database.flush()
    inserted_evidence = insert_completion_evidence(
        database,
        work_item,
        checkpoint,
        completion_evidence,
    )
    work_item.status = "done"
    work_item.version += 1
    work_item.updated_at = mutation_time
    database.flush()
    event = stage_work_completed(
        database,
        work_item,
        checkpoint,
        from_status="pending",
    )
    event.job_completion_report_id = report_id
    database.flush()
    assert job_completion_report is not None
    seal_closeout_report(database, work_item, event, report_id, job_completion_report,
        report_settings, source_actor(payload.source_client, payload.source_session_id,
                                      payload.source_model), checkpoint.id)
    seal_review_policy(database, work_item, event, report_settings, code_review_handoff,
        review_decision, source_actor(payload.source_client, payload.source_session_id,
                                      payload.source_model))
    sealed_evidence = hydrate_completion_evidence(database, checkpoint)
    if sealed_evidence != inserted_evidence:
        from mnemonic_api.errors import completion_evidence_unavailable

        raise completion_evidence_unavailable()
    return checkpoint


def delete_work_record(
    database: Session,
    work_item: WorkItem,
    expected_version: int,
    lease_token: str | None = None,
    actor: MutationActor | None = None,
) -> None:
    from mnemonic_api.services.code_reviews import require_no_review_obligation
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    require_version(work_item, expected_version)
    require_no_review_obligation(database, work_item.id)
    require_no_relationships(database, work_item.project_id, work_item.id)
    require_no_unresolved_gates(database, work_item.id)
    consume_lease_for_terminal_mutation(database, work_item.id, lease_token)
    mutation_time = database_now(database)
    work_item.deleted_at = mutation_time
    work_item.updated_at = mutation_time
    work_item.version += 1
    database.flush()
    stage_work_deleted(database, work_item, actor=actor)
    database.flush()


def move_work_record(
    database: Session,
    work_item: WorkItem,
    source_project_id: UUID,
    payload: WorkMoveCreate,
) -> WorkItemMove:
    """Move one stable work identity while leaving immutable facts at their origins."""
    from mnemonic_api.services.duplicates import (
        require_canonical_work_item,
        require_no_duplicate_membership,
    )

    if payload.target_project_id == source_project_id:
        raise conflict(
            "work_move_same_project",
            "The work item is already in the target project.",
        )
    require_canonical_work_item(database, work_item)
    require_version(work_item, payload.expected_version)
    require_no_duplicate_membership(database, work_item)
    require_no_relationships_for_move(database, source_project_id, work_item.id)
    require_no_unresolved_gates(database, work_item.id)
    require_no_active_lease_for_move(database, work_item.id)
    if work_item.status == "done":
        require_sealed_completion_episode(database, work_item)
    require_sealed_closeout_report(database, work_item)

    mutation_time = database_now(database)
    move = WorkItemMove(
        id=uuid4(),
        work_item_id=work_item.id,
        source_project_id=source_project_id,
        target_project_id=payload.target_project_id,
        source_work_version=work_item.version,
        resulting_work_version=work_item.version + 1,
        preserved_status=work_item.status,
        created_at=mutation_time,
        **actor_fields(payload.actor),
    )
    database.add(move)
    database.flush()

    work_item.project_id = payload.target_project_id
    work_item.version = move.resulting_work_version
    work_item.updated_at = mutation_time
    database.flush()
    stage_work_moved_events(database, move, actor=payload.actor)
    database.flush()
    return move
