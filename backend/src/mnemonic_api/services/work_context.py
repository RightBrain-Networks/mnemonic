"""Canonical projections for bounded context, summaries, and legacy aliases."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mnemonic_api.errors import not_found
from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    CheckpointPointer,
    CheckpointRead,
    HandoffCommentRead,
    HandoffRead,
    HandoffSummary,
    LeasePublic,
    Readiness,
    WorkContext,
    WorkItemRead,
    WorkSummary,
)


def checkpoint_read(checkpoint: Checkpoint | dict[str, Any]) -> CheckpointRead:
    return CheckpointRead.model_validate(checkpoint)


def checkpoint_pointer(checkpoint: Checkpoint) -> CheckpointPointer:
    return CheckpointPointer.model_validate(checkpoint)


def readiness(
    work_item: WorkItem | WorkItemRead, active_lease: WorkLease | LeasePublic | None = None
) -> Readiness:
    terminal = work_item.status != "open"
    lease_public = (
        LeasePublic.model_validate(active_lease) if active_lease is not None else None
    )
    has_active_lease = lease_public is not None
    if terminal:
        display_state = work_item.status
    elif has_active_lease:
        display_state = "active"
    else:
        display_state = "ready"
    return Readiness(
        lifecycle_status=work_item.status,
        is_terminal=terminal,
        has_active_lease=has_active_lease,
        active_lease=lease_public,
        is_ready=not terminal and not has_active_lease,
        display_state=display_state,
    )


def initial_checkpoint(database: Session, work_item: WorkItem) -> Checkpoint:
    checkpoint = database.scalar(
        select(Checkpoint).where(
            Checkpoint.work_item_id == work_item.id,
            Checkpoint.id == work_item.initial_checkpoint_id,
        )
    )
    if checkpoint is None:  # Protected by a deferred database foreign key.
        raise RuntimeError("Work item is missing its initial checkpoint")
    return checkpoint


def work_summaries(database: Session, work_items: Sequence[WorkItem]) -> list[WorkSummary]:
    if not work_items:
        return []
    ids = [work_item.id for work_item in work_items]
    counts = dict(
        database.execute(
            select(Checkpoint.work_item_id, func.count())
            .where(Checkpoint.work_item_id.in_(ids))
            .group_by(Checkpoint.work_item_id)
        ).all()
    )
    active_leases = {
        lease.work_item_id: lease
        for lease in database.scalars(
            select(WorkLease).where(
                WorkLease.work_item_id.in_(ids),
                WorkLease.expires_at > func.clock_timestamp(),
            )
        )
    }
    current_contexts = {
        checkpoint.work_item_id: checkpoint
        for checkpoint in database.scalars(
            select(Checkpoint)
            .distinct(Checkpoint.work_item_id)
            .where(Checkpoint.work_item_id.in_(ids), Checkpoint.kind == "context")
            .order_by(
                Checkpoint.work_item_id,
                Checkpoint.created_at.desc(),
                Checkpoint.id.desc(),
            )
        )
    }
    return [
        WorkSummary(
            work_item=WorkItemRead.model_validate(work_item),
            checkpoint_count=counts[work_item.id],
            current_context=checkpoint_pointer(current_contexts[work_item.id]),
            readiness=readiness(work_item, active_leases.get(work_item.id)),
        )
        for work_item in work_items
    ]


def assemble_work_context(
    database: Session, project_id: UUID, work_item_id: UUID, recent_limit: int
) -> WorkContext:
    """Read all bounded context components from one READ COMMITTED statement."""
    row = database.execute(
        text(
            """
            WITH selected_work AS (
                SELECT *
                FROM work_items
                WHERE id = :work_item_id
                  AND project_id = :project_id
                  AND deleted_at IS NULL
            ),
            database_time AS (
                SELECT clock_timestamp() AS now
            ),
            chosen AS (
                SELECT
                    w.*,
                    current_checkpoint.id AS current_checkpoint_id
                FROM selected_work AS w
                JOIN LATERAL (
                    SELECT checkpoint.id
                    FROM checkpoints AS checkpoint
                    WHERE checkpoint.work_item_id = w.id
                      AND checkpoint.kind = 'context'
                    ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
                    LIMIT 1
                ) AS current_checkpoint ON TRUE
            ),
            recent AS (
                SELECT checkpoint.*
                FROM checkpoints AS checkpoint
                JOIN chosen AS w ON w.id = checkpoint.work_item_id
                WHERE checkpoint.id <> w.initial_checkpoint_id
                  AND checkpoint.id <> w.current_checkpoint_id
                ORDER BY checkpoint.created_at DESC, checkpoint.id DESC
                LIMIT :recent_limit
            )
            SELECT
                to_jsonb(w) - 'deleted_at' - 'search_vector' - 'current_checkpoint_id'
                    AS work_item,
                to_jsonb(initial_checkpoint) - 'search_vector' AS initial_checkpoint,
                to_jsonb(current_checkpoint) - 'search_vector' AS current_checkpoint,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            to_jsonb(recent_checkpoint) - 'search_vector'
                            ORDER BY recent_checkpoint.created_at, recent_checkpoint.id
                        )
                        FROM recent AS recent_checkpoint
                    ),
                    '[]'::jsonb
                ) AS recent_checkpoints,
                (
                    SELECT count(*)
                    FROM checkpoints AS checkpoint_count
                    WHERE checkpoint_count.work_item_id = w.id
                ) AS checkpoint_total
                ,CASE
                    WHEN active_lease.work_item_id IS NULL THEN NULL
                    ELSE jsonb_build_object(
                        'holder_client', active_lease.holder_client,
                        'holder_session_id', active_lease.holder_session_id,
                        'acquired_at', active_lease.acquired_at,
                        'renewed_at', active_lease.renewed_at,
                        'expires_at', active_lease.expires_at
                    )
                END AS active_lease
            FROM chosen AS w
            JOIN checkpoints AS initial_checkpoint
              ON initial_checkpoint.work_item_id = w.id
             AND initial_checkpoint.id = w.initial_checkpoint_id
            JOIN checkpoints AS current_checkpoint
              ON current_checkpoint.work_item_id = w.id
             AND current_checkpoint.id = w.current_checkpoint_id
            CROSS JOIN database_time
            LEFT JOIN work_leases AS active_lease
              ON active_lease.work_item_id = w.id
             AND active_lease.expires_at > database_time.now
            """
        ),
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "recent_limit": recent_limit,
        },
    ).mappings().one_or_none()
    if row is None:
        raise not_found("work_item_not_found", "Work item not found.")

    work_item = WorkItemRead.model_validate(row["work_item"])
    initial = CheckpointRead.model_validate(row["initial_checkpoint"])
    current = CheckpointRead.model_validate(row["current_checkpoint"])
    recent = [CheckpointRead.model_validate(item) for item in row["recent_checkpoints"]]
    active_lease = (
        LeasePublic.model_validate(row["active_lease"])
        if row["active_lease"] is not None
        else None
    )
    materialized_ids = {initial.id, current.id, *(item.id for item in recent)}
    total = int(row["checkpoint_total"])
    return WorkContext(
        work_item=work_item,
        initial_checkpoint=initial,
        current_context=current,
        recent_checkpoints=recent,
        checkpoint_total=total,
        omitted_checkpoint_count=total - len(materialized_ids),
        readiness=readiness(work_item, active_lease),
    )


def legacy_handoff_read(work_item: WorkItem, checkpoint: Checkpoint) -> HandoffRead:
    return HandoffRead(
        id=work_item.id,
        project_id=work_item.project_id,
        title=work_item.title,
        summary=work_item.summary,
        prompt=checkpoint.prompt,
        source_client=checkpoint.source_client,
        source_session_id=checkpoint.source_session_id,
        source_model=checkpoint.source_model,
        source_session_url=checkpoint.source_session_url,
        repository_branch=checkpoint.repository_branch,
        verified_against=checkpoint.verified_against,
        tags=checkpoint.tags,
        source_metadata=checkpoint.source_metadata,
        status=work_item.status,
        version=work_item.version,
        created_at=work_item.created_at,
        updated_at=work_item.updated_at,
    )


def legacy_handoff_summary(work_item: WorkItem, checkpoint: Checkpoint) -> HandoffSummary:
    return HandoffSummary.model_validate(legacy_handoff_read(work_item, checkpoint))


def legacy_comment_read(checkpoint: Checkpoint) -> HandoffCommentRead:
    return HandoffCommentRead(
        id=checkpoint.id,
        handoff_id=checkpoint.work_item_id,
        body=checkpoint.prompt,
        kind="work-summary" if checkpoint.kind == "completion" else "comment",
        source_client=checkpoint.source_client,
        source_session_id=checkpoint.source_session_id,
        source_model=checkpoint.source_model,
        created_at=checkpoint.created_at,
    )
