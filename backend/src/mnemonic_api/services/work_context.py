"""Canonical projections for bounded context and work summaries."""

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
    LeasePublic,
    Readiness,
    WorkContext,
    WorkItemPointer,
    WorkItemRead,
    WorkSummary,
    WorkSummaryMinimal,
)


def checkpoint_read(checkpoint: Checkpoint | dict[str, Any]) -> CheckpointRead:
    return CheckpointRead.model_validate(checkpoint)


def checkpoint_pointer(checkpoint: Checkpoint) -> CheckpointPointer:
    return CheckpointPointer.model_validate(checkpoint)


def readiness(
    work_item: WorkItem | WorkItemRead,
    active_lease: WorkLease | LeasePublic | None = None,
    unresolved_blocker_count: int = 0,
) -> Readiness:
    terminal = work_item.status != "open"
    lease_public = (
        LeasePublic.model_validate(active_lease) if active_lease is not None else None
    )
    has_active_lease = lease_public is not None
    is_blocked = unresolved_blocker_count > 0
    if terminal:
        display_state = work_item.status
    elif is_blocked:
        display_state = "blocked"
    elif has_active_lease:
        display_state = "active"
    else:
        display_state = "ready"
    return Readiness(
        lifecycle_status=work_item.status,
        is_terminal=terminal,
        has_active_lease=has_active_lease,
        active_lease=lease_public,
        unresolved_blocker_count=unresolved_blocker_count,
        is_blocked=is_blocked,
        is_ready=not terminal and not has_active_lease and not is_blocked,
        display_state=display_state,
    )


def _summary_inputs(
    database: Session, ids: list[UUID]
) -> tuple[dict[UUID, int], dict[UUID, int], dict[UUID, WorkLease]]:
    """Checkpoint counts, unresolved blockers, and active leases for one page."""
    from mnemonic_api.services.relationships import unresolved_blocker_counts

    blocker_counts = unresolved_blocker_counts(database, ids)
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
    return counts, blocker_counts, active_leases


def minimal_work_summaries(
    database: Session, work_items: Sequence[WorkItem]
) -> list[WorkSummaryMinimal]:
    """Pointer-only summaries for callers on a context budget. No checkpoint pointer."""
    if not work_items:
        return []
    ids = [work_item.id for work_item in work_items]
    counts, blocker_counts, active_leases = _summary_inputs(database, ids)
    return [
        WorkSummaryMinimal(
            work_item=WorkItemPointer.model_validate(work_item),
            checkpoint_count=counts[work_item.id],
            display_state=readiness(
                work_item,
                active_leases.get(work_item.id),
                blocker_counts.get(work_item.id, 0),
            ).display_state,
        )
        for work_item in work_items
    ]


def work_summaries(database: Session, work_items: Sequence[WorkItem]) -> list[WorkSummary]:
    if not work_items:
        return []
    ids = [work_item.id for work_item in work_items]
    counts, blocker_counts, active_leases = _summary_inputs(database, ids)
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
            readiness=readiness(
                work_item,
                active_leases.get(work_item.id),
                blocker_counts.get(work_item.id, 0),
            ),
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
            ),
            adjacent_base AS (
                SELECT
                    relationship.*,
                    CASE
                        WHEN relationship.relationship_type = 'related' THEN 'undirected'
                        WHEN relationship.target_work_item_id = w.id THEN 'incoming'
                        ELSE 'outgoing'
                    END AS relative_direction,
                    CASE
                        WHEN relationship.source_work_item_id = w.id
                        THEN relationship.target_work_item_id
                        ELSE relationship.source_work_item_id
                    END AS counterpart_id
                FROM chosen AS w
                JOIN work_relationships AS relationship
                  ON relationship.project_id = w.project_id
                 AND (
                    relationship.source_work_item_id = w.id
                    OR relationship.target_work_item_id = w.id
                 )
            ),
            adjacent_limited AS (
                SELECT *
                FROM (
                    SELECT
                        adjacent_base.*,
                        row_number() OVER (
                            PARTITION BY adjacent_base.relative_direction
                            ORDER BY adjacent_base.created_at, adjacent_base.id
                        ) AS direction_rank
                    FROM adjacent_base
                ) AS ranked
                WHERE ranked.direction_rank <= 50
            ),
            adjacent_rows AS (
                SELECT
                    relationship.*,
                    counterpart.title AS counterpart_title,
                    counterpart.status AS counterpart_status,
                    CASE
                        WHEN counterpart_lease.work_item_id IS NULL THEN NULL
                        ELSE jsonb_build_object(
                            'holder_client', counterpart_lease.holder_client,
                            'holder_session_id', counterpart_lease.holder_session_id,
                            'acquired_at', counterpart_lease.acquired_at,
                            'renewed_at', counterpart_lease.renewed_at,
                            'expires_at', counterpart_lease.expires_at
                        )
                    END AS counterpart_active_lease,
                    (
                        SELECT count(*)
                        FROM work_relationships AS blocker_edge
                        JOIN work_items AS blocker_source
                          ON blocker_source.id = blocker_edge.source_work_item_id
                        WHERE blocker_edge.relationship_type = 'blocks'
                          AND blocker_edge.project_id = relationship.project_id
                          AND blocker_edge.target_work_item_id = counterpart.id
                          AND blocker_source.status <> 'done'
                    ) AS counterpart_blocker_count
                FROM adjacent_limited AS relationship
                JOIN work_items AS counterpart
                  ON counterpart.id = relationship.counterpart_id
                 AND counterpart.deleted_at IS NULL
                CROSS JOIN database_time
                LEFT JOIN work_leases AS counterpart_lease
                  ON counterpart_lease.work_item_id = counterpart.id
                 AND counterpart_lease.expires_at > database_time.now
            ),
            adjacent_projected AS (
                SELECT
                    adjacent.*,
                    jsonb_build_object(
                        'relationship', jsonb_build_object(
                            'id', adjacent.id,
                            'project_id', adjacent.project_id,
                            'relationship_type', adjacent.relationship_type,
                            'source_work_item_id', adjacent.source_work_item_id,
                            'target_work_item_id', adjacent.target_work_item_id,
                            'context_checkpoint_work_item_id',
                                adjacent.context_checkpoint_work_item_id,
                            'context_checkpoint_id', adjacent.context_checkpoint_id,
                            'created_by_client', adjacent.created_by_client,
                            'created_by_session_id', adjacent.created_by_session_id,
                            'created_by_model', adjacent.created_by_model,
                            'created_at', adjacent.created_at
                        ),
                        'relative_to_work_item_id', :work_item_id,
                        'direction', adjacent.relative_direction,
                        'counterpart', jsonb_build_object(
                            'id', adjacent.counterpart_id,
                            'title', adjacent.counterpart_title,
                            'status', adjacent.counterpart_status,
                            'readiness', jsonb_build_object(
                                'lifecycle_status', adjacent.counterpart_status,
                                'is_terminal', adjacent.counterpart_status <> 'open',
                                'has_active_lease',
                                    adjacent.counterpart_active_lease IS NOT NULL,
                                'active_lease', adjacent.counterpart_active_lease,
                                'unresolved_blocker_count',
                                    adjacent.counterpart_blocker_count,
                                'is_blocked', adjacent.counterpart_blocker_count > 0,
                                'is_ready', adjacent.counterpart_status = 'open'
                                    AND adjacent.counterpart_active_lease IS NULL
                                    AND adjacent.counterpart_blocker_count = 0,
                                'display_state', CASE
                                    WHEN adjacent.counterpart_status <> 'open'
                                        THEN adjacent.counterpart_status
                                    WHEN adjacent.counterpart_blocker_count > 0 THEN 'blocked'
                                    WHEN adjacent.counterpart_active_lease IS NOT NULL THEN 'active'
                                    ELSE 'ready'
                                END
                            )
                        )
                    ) AS edge_json
                FROM adjacent_rows AS adjacent
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
                END AS active_lease,
                (
                    SELECT count(*)
                    FROM work_relationships AS blocker_edge
                    JOIN work_items AS blocker_source
                      ON blocker_source.id = blocker_edge.source_work_item_id
                    WHERE blocker_edge.relationship_type = 'blocks'
                      AND blocker_edge.project_id = w.project_id
                      AND blocker_edge.target_work_item_id = w.id
                      AND blocker_source.status <> 'done'
                ) AS unresolved_blocker_count,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            limited.edge_json ORDER BY limited.created_at, limited.id
                        )
                        FROM (
                            SELECT edge_json, created_at, id
                            FROM adjacent_projected
                            WHERE relative_direction = 'incoming'
                            ORDER BY created_at, id
                            LIMIT 50
                        ) AS limited
                    ),
                    '[]'::jsonb
                ) AS incoming_relationships,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            limited.edge_json ORDER BY limited.created_at, limited.id
                        )
                        FROM (
                            SELECT edge_json, created_at, id
                            FROM adjacent_projected
                            WHERE relative_direction = 'outgoing'
                            ORDER BY created_at, id
                            LIMIT 50
                        ) AS limited
                    ),
                    '[]'::jsonb
                ) AS outgoing_relationships,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            limited.edge_json ORDER BY limited.created_at, limited.id
                        )
                        FROM (
                            SELECT edge_json, created_at, id
                            FROM adjacent_projected
                            WHERE relative_direction = 'undirected'
                            ORDER BY created_at, id
                            LIMIT 50
                        ) AS limited
                    ),
                    '[]'::jsonb
                ) AS undirected_relationships,
                (
                    SELECT jsonb_build_object(
                        'incoming', count(*) FILTER (
                            WHERE relative_direction = 'incoming'
                        ),
                        'outgoing', count(*) FILTER (
                            WHERE relative_direction = 'outgoing'
                        ),
                        'undirected', count(*) FILTER (
                            WHERE relative_direction = 'undirected'
                        ),
                        'total', count(*)
                    )
                    FROM adjacent_base
                ) AS relationship_counts
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
    blocker_count = int(row["unresolved_blocker_count"])
    materialized_ids = {initial.id, current.id, *(item.id for item in recent)}
    total = int(row["checkpoint_total"])
    # One checkpoint body per payload: when the newest context checkpoint is the
    # initial one, the client reads initial_checkpoint instead of a second copy.
    current_is_initial = current.id == initial.id
    return WorkContext(
        work_item=work_item,
        initial_checkpoint=initial,
        current_context=None if current_is_initial else current,
        current_context_is_initial=current_is_initial,
        recent_checkpoints=recent,
        checkpoint_total=total,
        omitted_checkpoint_count=total - len(materialized_ids),
        readiness=readiness(work_item, active_lease, blocker_count),
        incoming_relationships=row["incoming_relationships"],
        outgoing_relationships=row["outgoing_relationships"],
        undirected_relationships=row["undirected_relationships"],
        relationship_counts=row["relationship_counts"],
    )
