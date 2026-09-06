"""Immutable work-event construction, public progress append, and bounded reads."""

from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from mnemonic_api.database import rows_affected
from mnemonic_api.errors import ApplicationError, not_found
from mnemonic_api.models import (
    Checkpoint,
    WorkEvent,
    WorkGate,
    WorkItem,
    WorkItemMove,
    WorkRelationship,
)
from mnemonic_api.schemas import (
    MutationActor,
    ProgressEventCreate,
    WorkEventListQuery,
    WorkEventPage,
    WorkEventRead,
)


def database_now(database: Session) -> datetime:
    return database.execute(select(func.clock_timestamp())).scalar_one()


def utc_iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def actor_fields(actor: MutationActor | None) -> dict[str, str | None]:
    if actor is None:
        return {
            "actor_kind": "unattributed",
            "actor_client": None,
            "actor_session_id": None,
            "actor_model": None,
        }
    return {
        "actor_kind": "client",
        "actor_client": actor.actor_client,
        "actor_session_id": actor.actor_session_id,
        "actor_model": actor.actor_model,
    }


def source_actor(client: str, session_id: str, model: str | None) -> MutationActor:
    return MutationActor(
        actor_client=client,
        actor_session_id=session_id,
        actor_model=model,
    )


def _event(
    *,
    project_id: UUID,
    work_item_id: UUID,
    event_type: str,
    actor: MutationActor | None,
    created_at: datetime,
    metadata: dict[str, JsonValue],
    body: str | None = None,
    checkpoint_id: UUID | None = None,
    lease_generation_id: UUID | None = None,
    lease_release_id: UUID | None = None,
    relationship: WorkRelationship | None = None,
    gate_id: UUID | None = None,
    created_for_duplicate_merge_id: UUID | None = None,
    work_duplicate_merge_id: UUID | None = None,
    work_move_id: UUID | None = None,
) -> WorkEvent:
    values: dict[str, Any] = {
        "project_id": project_id,
        "work_item_id": work_item_id,
        "event_type": event_type,
        **actor_fields(actor),
        "body": body,
        "checkpoint_id": checkpoint_id,
        "lease_generation_id": lease_generation_id,
        "lease_release_id": lease_release_id,
        "gate_id": gate_id,
        "created_for_duplicate_merge_id": created_for_duplicate_merge_id,
        "work_duplicate_merge_id": work_duplicate_merge_id,
        "work_move_id": work_move_id,
        "event_metadata": metadata,
        "created_at": created_at,
        "origin": "live",
    }
    if relationship is not None:
        values.update(
            relationship_id=relationship.id,
            relationship_source_work_item_id=relationship.source_work_item_id,
            relationship_target_work_item_id=relationship.target_work_item_id,
            relationship_context_checkpoint_work_item_id=(
                relationship.context_checkpoint_work_item_id
            ),
            relationship_context_checkpoint_id=relationship.context_checkpoint_id,
        )
    return WorkEvent(**values)


def stage_human_attention_requested(
    database: Session,
    gate: WorkGate,
) -> WorkEvent:
    event = _event(
        project_id=gate.project_id,
        work_item_id=gate.work_item_id,
        event_type="human_attention_requested",
        actor=source_actor(
            gate.requested_by_client,
            gate.requested_by_session_id,
            gate.requested_by_model,
        ),
        created_at=gate.created_at,
        body=gate.question,
        gate_id=gate.id,
        metadata={"gate_id": str(gate.id), "gate_type": "human"},
    )
    database.add(event)
    return event


def stage_human_attention_resolved(
    database: Session,
    gate: WorkGate,
) -> WorkEvent:
    assert gate.resolved_at is not None
    assert gate.resolution is not None
    assert gate.resolved_by_client is not None
    assert gate.resolved_by_session_id is not None
    event = _event(
        project_id=gate.project_id,
        work_item_id=gate.work_item_id,
        event_type="human_attention_resolved",
        actor=source_actor(
            gate.resolved_by_client,
            gate.resolved_by_session_id,
            gate.resolved_by_model,
        ),
        created_at=gate.resolved_at,
        body=gate.resolution,
        gate_id=gate.id,
        metadata={"gate_id": str(gate.id), "gate_type": "human"},
    )
    database.add(event)
    return event


def stage_work_created(
    database: Session,
    work_item: WorkItem,
    checkpoint: Checkpoint,
) -> WorkEvent:
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="work_created",
        actor=source_actor(
            checkpoint.source_client,
            checkpoint.source_session_id,
            checkpoint.source_model,
        ),
        created_at=work_item.created_at,
        checkpoint_id=checkpoint.id,
        metadata={
            "initial": {
                "title": work_item.title,
                "summary": work_item.summary,
                "status": work_item.status,
                "priority": work_item.priority,
                "version": work_item.version,
                **({"external_references": deepcopy(work_item.external_references)}
                   if work_item.external_references else {}),
            }
        },
    )
    database.add(event)
    return event


def stage_checkpoint_added(
    database: Session,
    work_item: WorkItem,
    checkpoint: Checkpoint,
) -> WorkEvent:
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="checkpoint_added",
        actor=source_actor(
            checkpoint.source_client,
            checkpoint.source_session_id,
            checkpoint.source_model,
        ),
        created_at=checkpoint.created_at,
        checkpoint_id=checkpoint.id,
        metadata={"checkpoint_kind": checkpoint.kind},
    )
    database.add(event)
    return event


def stage_work_completed(
    database: Session,
    work_item: WorkItem,
    checkpoint: Checkpoint,
    *,
    from_status: str,
) -> WorkEvent:
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="work_completed",
        actor=source_actor(
            checkpoint.source_client,
            checkpoint.source_session_id,
            checkpoint.source_model,
        ),
        created_at=checkpoint.created_at,
        checkpoint_id=checkpoint.id,
        metadata={
            "from_status": from_status,
            "to_status": "done",
            "work_version": work_item.version,
        },
    )
    database.add(event)
    return event


def stage_work_changed(
    database: Session,
    work_item: WorkItem,
    *,
    before: dict[str, Any],
    requested_fields: Iterable[str],
    actor: MutationActor | None,
    created_at: datetime,
) -> WorkEvent:
    fields = sorted(set(requested_fields))
    changes = {
        field: {"before": before[field], "after": deepcopy(getattr(work_item, field))}
        for field in fields
    }
    old_status = str(before["status"])
    status_requested = "status" in changes
    status_changed = status_requested and old_status != work_item.status
    if status_changed and work_item.status == "pending":
        event_type = "work_reopened"
    elif status_changed:
        event_type = "work_status_changed"
    else:
        event_type = "work_updated"
    metadata: dict[str, JsonValue] = {
        "changes": changes,
        "work_version": work_item.version,
    }
    if status_changed:
        metadata.update(from_status=old_status, to_status=work_item.status)
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type=event_type,
        actor=actor,
        created_at=created_at,
        metadata=metadata,
    )
    database.add(event)
    return event


def stage_work_deleted(
    database: Session,
    work_item: WorkItem,
    *,
    actor: MutationActor | None,
) -> WorkEvent:
    assert work_item.deleted_at is not None
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="work_deleted",
        actor=actor,
        created_at=work_item.deleted_at,
        metadata={
            "final_status": work_item.status,
            "final_version": work_item.version,
        },
    )
    database.add(event)
    return event


def stage_work_claimed(
    database: Session,
    work_item: WorkItem,
    *,
    holder_client: str,
    holder_session_id: str,
    lease_generation_id: UUID,
    acquired_at: datetime,
    expires_at: datetime,
    code_review_id: UUID | None = None,
    mode: str | None = None,
) -> WorkEvent:
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="work_claimed",
        actor=source_actor(
            holder_client,
            holder_session_id,
            None,
        ),
        created_at=acquired_at,
        lease_generation_id=lease_generation_id,
        metadata={"expires_at": utc_iso(expires_at), **({"purpose": "code_review",
                  "code_review_id": str(code_review_id), "mode": mode} if code_review_id else {})},
    )
    event.code_review_id = code_review_id
    database.add(event)
    return event


def stage_work_released(
    database: Session,
    work_item: WorkItem,
    *,
    lease_generation_id: UUID,
    lease_release_id: UUID,
    lease_holder_client: str,
    lease_holder_session_id: str,
    actor: MutationActor | None,
    created_at: datetime,
    code_review_id: UUID | None = None,
    mode: str | None = None,
) -> WorkEvent:
    metadata: dict[str, JsonValue]
    if lease_holder_client.strip() and lease_holder_session_id.strip():
        metadata = {
            "lease_holder_kind": "client",
            "lease_holder_client": lease_holder_client,
            "lease_holder_session_id": lease_holder_session_id,
        }
    else:
        metadata = {"lease_holder_kind": "unattributed"}
    if code_review_id is not None:
        metadata.update(purpose="code_review", code_review_id=str(code_review_id), mode=mode)
    event = _event(
        project_id=work_item.project_id,
        work_item_id=work_item.id,
        event_type="work_released",
        actor=actor,
        created_at=created_at,
        lease_generation_id=lease_generation_id,
        lease_release_id=lease_release_id,
        metadata=metadata,
    )
    event.code_review_id = code_review_id
    database.add(event)
    return event


def stage_relationship_events(
    database: Session,
    relationship: WorkRelationship,
    *,
    action: str,
    actor: MutationActor | None,
    created_at: datetime,
    created_for_duplicate_merge_id: UUID | None = None,
) -> list[WorkEvent]:
    if action not in {"added", "removed"}:
        raise ValueError("Relationship event action must be added or removed")
    family = "dependency" if relationship.relationship_type == "blocks" else "relationship"
    event_type = f"{family}_{action}"
    events = [
        _event(
            project_id=relationship.project_id,
            work_item_id=work_item_id,
            event_type=event_type,
            actor=actor,
            created_at=created_at,
            metadata={"relationship_type": relationship.relationship_type},
            relationship=relationship,
            created_for_duplicate_merge_id=created_for_duplicate_merge_id,
        )
        for work_item_id in (
            relationship.source_work_item_id,
            relationship.target_work_item_id,
        )
    ]
    database.add_all(events)
    return events


def stage_work_merged_events(
    database: Session,
    *,
    merge_id: UUID,
    project_id: UUID,
    source_work_item_id: UUID,
    destination_work_item_id: UUID,
    source_work_version: int,
    destination_work_version: int,
    rationale: str,
    actor: MutationActor,
    created_at: datetime,
) -> list[WorkEvent]:
    """Stage the source-role event first and destination-role event second."""
    metadata = {
        "merge_id": str(merge_id),
        "source_work_item_id": str(source_work_item_id),
        "destination_work_item_id": str(destination_work_item_id),
        "source_work_version": source_work_version,
        "destination_work_version": destination_work_version,
    }
    events = [
        _event(
            project_id=project_id,
            work_item_id=work_item_id,
            event_type="work_merged",
            actor=actor,
            created_at=created_at,
            body=rationale,
            metadata={**metadata, "role": role},
            work_duplicate_merge_id=merge_id,
        )
        for role, work_item_id in (
            ("source", source_work_item_id),
            ("destination", destination_work_item_id),
        )
    ]
    database.add_all(events)
    return events


def stage_work_moved_events(
    database: Session,
    move: WorkItemMove,
    *,
    actor: MutationActor | None,
) -> list[WorkEvent]:
    """Stage the source-project witness first and target-project witness second."""
    metadata = {
        "move_id": str(move.id),
        "source_project_id": str(move.source_project_id),
        "target_project_id": str(move.target_project_id),
        "work_version": move.resulting_work_version,
    }
    events = [
        _event(
            project_id=project_id,
            work_item_id=move.work_item_id,
            event_type="work_moved",
            actor=actor,
            created_at=move.created_at,
            metadata={**metadata, "role": role},
            work_move_id=move.id,
        )
        for role, project_id in (
            ("source", move.source_project_id),
            ("target", move.target_project_id),
        )
    ]
    database.add_all(events)
    return events


def _progress_strings(
    payload: ProgressEventCreate,
) -> Iterable[tuple[str, str]]:
    yield "actor.actor_client", payload.actor.actor_client
    yield "actor.actor_session_id", payload.actor.actor_session_id
    if payload.actor.actor_model is not None:
        yield "actor.actor_model", payload.actor.actor_model
    yield "body", payload.body

    def walk(item: JsonValue) -> Iterable[tuple[str, str]]:
        if isinstance(item, str):
            yield "metadata.value", item
        elif isinstance(item, list):
            for child in item:
                yield from walk(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                yield "metadata.key", key
                yield from walk(child)

    yield from walk(payload.metadata)


def reject_known_secret_echo(
    payload: ProgressEventCreate,
    *,
    bearer_key: str,
) -> None:
    secrets = [bearer_key]
    if payload.lease_token is not None:
        secrets.append(payload.lease_token)
    locations = sorted(
        {
            location
            for location, value in _progress_strings(payload)
            if any(secret and secret in value for secret in secrets)
        }
    )
    if locations:
        raise ApplicationError(
            422,
            "event_secret_echo",
            "Progress content contains a request credential or capability.",
            context={"fields": locations},
        )


def append_progress_event(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    payload: ProgressEventCreate,
    *,
    bearer_key: str,
) -> WorkEventRead:
    """Lock work first, validate any capability, then update activity and append."""
    from mnemonic_api.services.leases import validate_optional_lease_token
    from mnemonic_api.services.work_items import require_work_item

    reject_known_secret_echo(payload, bearer_key=bearer_key)
    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    mutation_time = database_now(database)
    validate_optional_lease_token(
        database,
        work_item.id,
        payload.lease_token,
        lock=payload.lease_token is not None,
    )
    activity_update = database.execute(
        update(WorkItem)
        .where(
            WorkItem.id == work_item.id,
            WorkItem.project_id == project_id,
            WorkItem.deleted_at.is_(None),
        )
        .values(updated_at=func.greatest(WorkItem.updated_at, mutation_time))
        .execution_options(synchronize_session=False)
    )
    if rows_affected(activity_update) != 1:
        raise not_found("work_item_not_found", "Work item not found.")
    event = _event(
        project_id=project_id,
        work_item_id=work_item_id,
        event_type="progress",
        actor=payload.actor,
        created_at=mutation_time,
        body=payload.body,
        metadata=payload.metadata,
    )
    database.add(event)
    database.flush()
    return work_event_read(event)


def work_event_read(event: WorkEvent) -> WorkEventRead:
    relationship_direction = None
    counterpart_work_item_id = None
    if event.relationship_id is not None:
        if event.event_metadata["relationship_type"] == "related":
            relationship_direction = "undirected"
        elif event.relationship_target_work_item_id == event.work_item_id:
            relationship_direction = "incoming"
        else:
            relationship_direction = "outgoing"
        counterpart_work_item_id = (
            event.relationship_target_work_item_id
            if event.relationship_source_work_item_id == event.work_item_id
            else event.relationship_source_work_item_id
        )
    return WorkEventRead.model_validate(
        {
            **{
                field: getattr(event, field)
                for field in ("code_review_id", "work_follow_up_id",
                              "work_follow_up_answer_id", "code_review_result_id")
                if getattr(event, field) is not None
            },
            "id": event.id,
            "project_id": event.project_id,
            "work_item_id": event.work_item_id,
            "event_type": event.event_type,
            "actor_kind": event.actor_kind,
            "actor_client": event.actor_client,
            "actor_session_id": event.actor_session_id,
            "actor_model": event.actor_model,
            "body": event.body,
            "checkpoint_id": event.checkpoint_id,
            "lease_generation_id": event.lease_generation_id,
            "lease_release_id": event.lease_release_id,
            "relationship_id": event.relationship_id,
            "relationship_source_work_item_id": event.relationship_source_work_item_id,
            "relationship_target_work_item_id": event.relationship_target_work_item_id,
            "relationship_context_checkpoint_work_item_id": (
                event.relationship_context_checkpoint_work_item_id
            ),
            "relationship_context_checkpoint_id": event.relationship_context_checkpoint_id,
            "relationship_direction": relationship_direction,
            "counterpart_work_item_id": counterpart_work_item_id,
            "metadata_version": event.metadata_version,
            "metadata": event.event_metadata,
            "origin": event.origin,
            "created_at": event.created_at,
        }
    )


def list_work_events(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    filters: WorkEventListQuery,
) -> WorkEventPage:
    ordering = "created_at DESC, id DESC" if filters.order == "newest" else "created_at, id"
    type_predicate = ""
    if filters.event_type is not None:
        type_predicate = "AND event.event_type = :event_type"
    row = (
        database.execute(
            text(
                f"""
            WITH visible_work AS MATERIALIZED (
                SELECT id
                FROM work_items
                WHERE project_id = :project_id
                  AND id = :work_item_id
                  AND deleted_at IS NULL
            ),
            paged AS MATERIALIZED (
                SELECT
                    event.id,
                    event.project_id,
                    event.work_item_id,
                    event.event_type,
                    event.actor_kind,
                    event.actor_client,
                    event.actor_session_id,
                    event.actor_model,
                    event.body,
                    event.checkpoint_id,
                    event.lease_generation_id,
                    event.lease_release_id,
                    event.code_review_id, event.work_follow_up_id,
                    event.work_follow_up_answer_id, event.code_review_result_id,
                    event.relationship_id,
                    event.relationship_source_work_item_id,
                    event.relationship_target_work_item_id,
                    event.relationship_context_checkpoint_work_item_id,
                    event.relationship_context_checkpoint_id,
                    event.metadata_version,
                    event.metadata,
                    event.origin,
                    event.created_at
                FROM visible_work
                JOIN work_events AS event ON event.work_item_id = visible_work.id
                WHERE true
                  {type_predicate}
                ORDER BY {ordering}
                LIMIT :limit OFFSET :offset
            ),
            projected AS (
                SELECT
                    paged.id,
                    paged.project_id,
                    paged.work_item_id,
                    paged.event_type,
                    paged.actor_kind,
                    paged.actor_client,
                    paged.actor_session_id,
                    paged.actor_model,
                    paged.body,
                    paged.checkpoint_id,
                    paged.lease_generation_id,
                    paged.lease_release_id,
                    paged.code_review_id, paged.work_follow_up_id,
                    paged.work_follow_up_answer_id, paged.code_review_result_id,
                    paged.relationship_id,
                    paged.relationship_source_work_item_id,
                    paged.relationship_target_work_item_id,
                    paged.relationship_context_checkpoint_work_item_id,
                    paged.relationship_context_checkpoint_id,
                    paged.metadata_version,
                    paged.metadata,
                    paged.origin,
                    paged.created_at,
                    CASE
                        WHEN paged.relationship_id IS NULL THEN NULL
                        WHEN paged.metadata->>'relationship_type' = 'related'
                            THEN 'undirected'
                        WHEN paged.relationship_target_work_item_id = paged.work_item_id
                            THEN 'incoming'
                        ELSE 'outgoing'
                    END AS relationship_direction,
                    CASE
                        WHEN paged.relationship_id IS NULL THEN NULL
                        WHEN paged.relationship_source_work_item_id = paged.work_item_id
                            THEN paged.relationship_target_work_item_id
                        ELSE paged.relationship_source_work_item_id
                    END AS counterpart_work_item_id
                FROM paged
            )
            SELECT
                EXISTS (SELECT 1 FROM visible_work) AS work_exists,
                (
                    SELECT count(*)
                    FROM work_events AS event
                    JOIN visible_work ON visible_work.id = event.work_item_id
                    WHERE true
                      {type_predicate}
                ) AS total,
                COALESCE(
                    (
                        SELECT origin = 'backfill'
                        FROM work_events AS creation
                        WHERE creation.work_item_id = :work_item_id
                          AND creation.event_type = 'work_created'
                    ),
                    false
                ) AS history_incomplete,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'id', projected.id,
                                'project_id', projected.project_id,
                                'work_item_id', projected.work_item_id,
                                'event_type', projected.event_type,
                                'actor_kind', projected.actor_kind,
                                'actor_client', projected.actor_client,
                                'actor_session_id', projected.actor_session_id,
                                'actor_model', projected.actor_model,
                                'body', projected.body,
                                'checkpoint_id', projected.checkpoint_id,
                                'lease_generation_id', projected.lease_generation_id,
                                'lease_release_id', projected.lease_release_id,
                                'relationship_id', projected.relationship_id,
                                'relationship_source_work_item_id',
                                    projected.relationship_source_work_item_id,
                                'relationship_target_work_item_id',
                                    projected.relationship_target_work_item_id,
                                'relationship_context_checkpoint_work_item_id',
                                    projected.relationship_context_checkpoint_work_item_id,
                                'relationship_context_checkpoint_id',
                                    projected.relationship_context_checkpoint_id,
                                'relationship_direction', projected.relationship_direction,
                                'counterpart_work_item_id',
                                    projected.counterpart_work_item_id,
                                'metadata_version', projected.metadata_version,
                                'metadata', projected.metadata,
                                'origin', projected.origin,
                                'created_at', projected.created_at
                            )
                            ORDER BY {ordering}
                        )
                        FROM projected
                    ),
                    '[]'::jsonb
                ) AS items
            """
            ),
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "event_type": filters.event_type,
                "limit": filters.limit,
                "offset": filters.offset,
            },
        )
        .mappings()
        .one()
    )
    if not row["work_exists"]:
        from mnemonic_api.services.work_items import missing_work_item

        raise missing_work_item(database, project_id)
    return WorkEventPage(
        items=[WorkEventRead.model_validate(item) for item in row["items"]],
        total=int(row["total"]),
        limit=filters.limit,
        offset=filters.offset,
        pre_phase5_history_may_be_incomplete=bool(row["history_incomplete"]),
    )
