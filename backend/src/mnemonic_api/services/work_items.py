"""Canonical work/checkpoint mutations without transaction-boundary commits."""

from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mnemonic_api.database import rows_affected
from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import Checkpoint, Project, WorkItem, WorkRelationship
from mnemonic_api.schemas import (
    CheckpointCreate,
    CompletionCheckpointCreate,
    MutationActor,
    WorkDeferralCreate,
    WorkItemCreate,
    WorkItemPatch,
)
from mnemonic_api.services.leases import (
    consume_lease_for_terminal_mutation,
    require_no_active_lease,
    validate_optional_lease_token,
)
from mnemonic_api.services.readiness import require_no_unresolved_gates, require_unblocked
from mnemonic_api.services.relationships import (
    lock_endpoint_work_items,
    lock_project_graph,
    require_no_relationships,
    stage_relationship_locked,
)
from mnemonic_api.services.work_events import (
    database_now,
    source_actor,
    stage_checkpoint_added,
    stage_relationship_events,
    stage_work_changed,
    stage_work_completed,
    stage_work_created,
    stage_work_deleted,
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
    database: Session, project_id: UUID, payload: WorkItemCreate
) -> tuple[WorkItem, Checkpoint, list[WorkRelationship]]:
    """Stage required work, context, and requested graph facts in one transaction."""
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
        priority=payload.priority,
        status=payload.status,
        initial_checkpoint_id=initial_checkpoint_id,
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


def update_work_record(database: Session, work_item: WorkItem, payload: WorkItemPatch) -> None:
    require_version(work_item, payload.expected_version)
    changes = payload.model_dump(
        exclude_unset=True,
        exclude={"expected_version", "lease_token", "actor", "client_operation_id"},
    )
    before = {
        field: getattr(work_item, field) for field in ("title", "summary", "priority", "status")
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
    terminal_transition = (
        requested_status in {"done", "wont-do", "promoted"}
        and requested_status != work_item.status
    )
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
    stage_work_changed(
        database,
        work_item,
        before=before,
        requested_fields=changes,
        actor=payload.actor,
        created_at=mutation_time,
    )
    database.flush()


def defer_work_record(
    database: Session,
    work_item: WorkItem,
    payload: WorkDeferralCreate,
) -> None:
    """Apply the dashboard-only deferral transition without displacing active work."""
    require_version(work_item, payload.expected_version)
    if work_item.status != "pending":
        raise conflict(
            "invalid_status_transition",
            "Only pending work can be deferred.",
        )
    require_no_active_lease(database, work_item.id)
    before = {
        field: getattr(work_item, field) for field in ("title", "summary", "priority", "status")
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
) -> Checkpoint:
    if work_item.status != "pending":
        raise conflict("work_not_pending", "Only pending work can be completed.")
    require_version(work_item, expected_version)
    require_unblocked(database, work_item.id)
    require_no_unresolved_gates(database, work_item.id)
    consume_lease_for_terminal_mutation(database, work_item.id, lease_token)
    mutation_time = database_now(database)
    checkpoint = _checkpoint(work_item.id, payload, kind="completion")
    database.add(checkpoint)
    work_item.status = "done"
    work_item.version += 1
    work_item.updated_at = mutation_time
    database.flush()
    stage_work_completed(
        database,
        work_item,
        checkpoint,
        from_status="pending",
    )
    database.flush()
    return checkpoint


def delete_work_record(
    database: Session,
    work_item: WorkItem,
    expected_version: int,
    lease_token: str | None = None,
    actor: MutationActor | None = None,
) -> None:
    require_version(work_item, expected_version)
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
