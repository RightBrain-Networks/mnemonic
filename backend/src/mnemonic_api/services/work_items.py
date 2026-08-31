"""Canonical work/checkpoint mutations without transaction-boundary commits."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict, not_found
from mnemonic_api.models import Checkpoint, Project, WorkItem
from mnemonic_api.schemas import (
    CheckpointCreate,
    CompletionCheckpointCreate,
    WorkItemCreate,
    WorkItemPatch,
)
from mnemonic_api.services.leases import (
    consume_lease_for_terminal_mutation,
    validate_optional_lease_token,
)


def require_project(database: Session, project_id: UUID) -> Project:
    project = database.get(Project, project_id)
    if project is None:
        raise not_found("project_not_found", "Project not found.")
    return project


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
        raise not_found("work_item_not_found", "Work item not found.")
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
    values = payload.model_dump(exclude={"kind", "lease_token"})
    return Checkpoint(work_item_id=work_item_id, kind=kind, **values)


def create_work_records(
    database: Session, project_id: UUID, payload: WorkItemCreate
) -> tuple[WorkItem, Checkpoint]:
    """Stage one work item and its required initial context in the same transaction."""
    require_project(database, project_id)
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
    # Explicitly stage the parent first. The deferred work->initial-checkpoint
    # constraint permits this order, while the immediate checkpoint->work FK
    # requires it. A single outer transaction still commits both or neither.
    database.add(work_item)
    database.flush()
    database.add(checkpoint)
    database.flush()
    return work_item, checkpoint


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
    if activity_update.rowcount != 1:
        raise not_found("work_item_not_found", "Work item not found.")
    database.flush()
    return checkpoint


def update_work_record(database: Session, work_item: WorkItem, payload: WorkItemPatch) -> None:
    require_version(work_item, payload.expected_version)
    changes = payload.model_dump(
        exclude_unset=True, exclude={"expected_version", "lease_token"}
    )
    requested_status = changes.get("status")
    if requested_status is not None and requested_status != work_item.status:
        allowed = {
            "open": {"wont-do", "promoted"},
            "wont-do": {"open"},
            "promoted": {"open"},
            "done": {"open"},
        }
        if requested_status not in allowed[work_item.status]:
            raise conflict(
                "invalid_status_transition",
                "That lifecycle transition is not allowed.",
            )
    terminal_transition = (
        requested_status in {"wont-do", "promoted"}
        and requested_status != work_item.status
    )
    if terminal_transition:
        consume_lease_for_terminal_mutation(database, work_item.id, payload.lease_token)
    else:
        validate_optional_lease_token(
            database, work_item.id, payload.lease_token, lock=True
        )
    for field, value in changes.items():
        setattr(work_item, field, value)
    work_item.version += 1
    work_item.updated_at = datetime.now(UTC)


def complete_work_record(
    database: Session,
    work_item: WorkItem,
    expected_version: int,
    payload: CompletionCheckpointCreate,
    lease_token: str | None = None,
) -> Checkpoint:
    if work_item.status != "open":
        raise conflict("work_not_open", "Only open work can be completed.")
    require_version(work_item, expected_version)
    consume_lease_for_terminal_mutation(database, work_item.id, lease_token)
    checkpoint = _checkpoint(work_item.id, payload, kind="completion")
    database.add(checkpoint)
    work_item.status = "done"
    work_item.version += 1
    work_item.updated_at = datetime.now(UTC)
    database.flush()
    return checkpoint


def delete_work_record(
    database: Session,
    work_item: WorkItem,
    expected_version: int,
    lease_token: str | None = None,
) -> None:
    require_version(work_item, expected_version)
    consume_lease_for_terminal_mutation(database, work_item.id, lease_token)
    now = datetime.now(UTC)
    work_item.deleted_at = now
    work_item.updated_at = now
    work_item.version += 1
