"""Canonical projections for bounded context and work summaries."""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    CheckpointPointer,
    CheckpointRead,
    LeasePublic,
    WorkContext,
    WorkEventRead,
    WorkItemPointer,
    WorkItemRead,
    WorkSummary,
    WorkSummaryMinimal,
)
from mnemonic_api.services.readiness import readiness, unresolved_blocker_counts


def checkpoint_read(checkpoint: Checkpoint | dict[str, Any]) -> CheckpointRead:
    return CheckpointRead.model_validate(checkpoint)


def checkpoint_pointer(checkpoint: Checkpoint) -> CheckpointPointer:
    return CheckpointPointer.model_validate(checkpoint)


def _summary_inputs(
    database: Session, ids: list[UUID]
) -> tuple[dict[UUID, int], dict[UUID, int], dict[UUID, WorkLease]]:
    """Checkpoint counts, unresolved blockers, and active leases for one page."""
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
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    recent_limit: int,
    recent_event_limit: int = 10,
) -> WorkContext:
    """Read all bounded context components from one READ COMMITTED statement."""
    row = (
        database.execute(
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
            recent_events AS MATERIALIZED (
                SELECT recent_event.*
                FROM chosen AS w
                CROSS JOIN LATERAL (
                    SELECT
                        event.*,
                        CASE
                            WHEN event.relationship_id IS NULL THEN NULL
                            WHEN event.metadata->>'relationship_type' = 'related'
                                THEN 'undirected'
                            WHEN event.relationship_target_work_item_id = event.work_item_id
                                THEN 'incoming'
                            ELSE 'outgoing'
                        END AS relationship_direction,
                        CASE
                            WHEN event.relationship_id IS NULL THEN NULL
                            WHEN event.relationship_source_work_item_id = event.work_item_id
                                THEN event.relationship_target_work_item_id
                            ELSE event.relationship_source_work_item_id
                        END AS counterpart_work_item_id
                    FROM work_events AS event
                    WHERE event.project_id = w.project_id
                      AND event.work_item_id = w.id
                    ORDER BY event.created_at DESC, event.id DESC
                    LIMIT :recent_event_limit
                ) AS recent_event
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
                ) AS relationship_counts,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            to_jsonb(recent_event)
                            ORDER BY recent_event.created_at, recent_event.id
                        )
                        FROM recent_events AS recent_event
                    ),
                    '[]'::jsonb
                ) AS recent_events,
                (
                    SELECT count(*)
                    FROM work_events AS event_count
                    WHERE event_count.project_id = w.project_id
                      AND event_count.work_item_id = w.id
                ) AS event_total,
                COALESCE(
                    (
                        SELECT creation.origin = 'backfill'
                        FROM work_events AS creation
                        WHERE creation.project_id = w.project_id
                          AND creation.work_item_id = w.id
                          AND creation.event_type = 'work_created'
                    ),
                    false
                ) AS pre_phase5_history_may_be_incomplete
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
                "recent_event_limit": recent_event_limit,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        from mnemonic_api.services.work_items import missing_work_item

        raise missing_work_item(database, project_id)

    work_item = WorkItemRead.model_validate(row["work_item"])
    initial = CheckpointRead.model_validate(row["initial_checkpoint"])
    current = CheckpointRead.model_validate(row["current_checkpoint"])
    recent = [CheckpointRead.model_validate(item) for item in row["recent_checkpoints"]]
    recent_events = [WorkEventRead.model_validate(item) for item in row["recent_events"]]
    event_total = int(row["event_total"])
    history_incomplete = bool(row["pre_phase5_history_may_be_incomplete"])
    active_lease = (
        LeasePublic.model_validate(row["active_lease"]) if row["active_lease"] is not None else None
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
        recent_events=recent_events,
        event_total=event_total,
        omitted_event_count=event_total - len(recent_events),
        pre_phase5_history_may_be_incomplete=history_incomplete,
    )
