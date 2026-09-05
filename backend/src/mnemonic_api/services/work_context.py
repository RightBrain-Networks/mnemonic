"""Canonical projections for bounded context and work summaries."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.models import Checkpoint, WorkItem, WorkLease
from mnemonic_api.schemas import (
    CheckpointPointer,
    CheckpointRead,
    HumanGateContextRevision,
    LeasePublic,
    MergeReviewRevision,
    WorkContext,
    WorkEventRead,
    WorkIdentityPointer,
    WorkItemRead,
    WorkSummary,
)
from mnemonic_api.services.readiness import (
    readiness,
    readiness_inputs,
    unresolved_blocker_count_clause,
    unresolved_gate_count_clause,
)


def checkpoint_read(checkpoint: Checkpoint | dict[str, Any]) -> CheckpointRead:
    return CheckpointRead.model_validate(checkpoint)


def checkpoint_pointer(checkpoint: Checkpoint) -> CheckpointPointer:
    return CheckpointPointer.model_validate(checkpoint)


def _summary_inputs(
    database: Session,
    ids: list[UUID],
    *,
    as_of: datetime | None = None,
) -> tuple[
    dict[UUID, int],
    dict[UUID, int],
    dict[UUID, int],
    dict[UUID, WorkLease],
    set[UUID],
    dict[UUID, UUID],
]:
    """Counts and lease facts used by bounded work-summary pages."""
    blocker_counts, gate_counts, active_leases, dropped_lease_ids, canonical_ids = readiness_inputs(
        database, ids, as_of=as_of
    )
    counts = dict(
        database.execute(
            select(Checkpoint.work_item_id, func.count())
            .where(Checkpoint.work_item_id.in_(ids))
            .group_by(Checkpoint.work_item_id)
        )
        .tuples()
        .all()
    )
    return counts, blocker_counts, gate_counts, active_leases, dropped_lease_ids, canonical_ids


def work_summaries(
    database: Session,
    work_items: Sequence[WorkItem],
    *,
    as_of: datetime | None = None,
) -> list[WorkSummary]:
    if not work_items:
        return []
    ids = [work_item.id for work_item in work_items]
    (
        counts,
        blocker_counts,
        gate_counts,
        active_leases,
        dropped_lease_ids,
        canonical_ids,
    ) = _summary_inputs(database, ids, as_of=as_of)
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
                work_item.id in dropped_lease_ids,
                gate_counts.get(work_item.id, 0),
                canonical_work_item_id=canonical_ids.get(work_item.id, work_item.id),
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
    *,
    focus_gate_id: UUID | None = None,
    coherent_read: bool = True,
) -> WorkContext:
    """Read source-owned context and canonical projections from one pinned snapshot."""
    if coherent_read:
        begin_coherent_read(database)
    dialect = database.get_bind().dialect

    def sql(expression) -> str:
        return str(
            expression.compile(
                dialect=dialect,
                compile_kwargs={"literal_binds": True},
            )
        )

    focal_blocker_count_sql = sql(
        unresolved_blocker_count_clause(
            literal_column("w.id"),
            literal_column("w.project_id"),
        )
    )
    focal_gate_count_sql = sql(
        unresolved_gate_count_clause(literal_column("w.id"))
    )
    counterpart_blocker_count_sql = sql(
        unresolved_blocker_count_clause(
            literal_column("counterpart.id"),
            literal_column("counterpart.project_id"),
        )
    )
    counterpart_gate_count_sql = sql(
        unresolved_gate_count_clause(literal_column("counterpart.id"))
    )
    row = (
        database.execute(
            text(
                f"""
            WITH selected_work AS (
                SELECT *
                FROM work_items
                WHERE id = :work_item_id
                  AND project_id = :project_id
                  AND deleted_at IS NULL
            ),
            database_time AS (
                SELECT transaction_timestamp() AS now
            ),
            chosen AS (
                SELECT
                    w.*,
                    current_checkpoint.id AS current_checkpoint_id,
                    (
                        SELECT count(*)
                        FROM work_events AS relationship_event
                        WHERE relationship_event.work_item_id = w.id
                          AND relationship_event.event_type IN (
                              'dependency_added',
                              'dependency_removed',
                              'relationship_added',
                              'relationship_removed'
                          )
                    ) AS current_relationship_event_count
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
            gate_rows AS MATERIALIZED (
                SELECT gate.*
                FROM chosen AS w
                JOIN work_gates AS gate
                  ON gate.project_id = w.project_id
                 AND gate.work_item_id = w.id
            ),
            recent_events AS MATERIALIZED (
                SELECT
                    recent_event.id,
                    recent_event.project_id,
                    recent_event.work_item_id,
                    recent_event.event_type,
                    recent_event.actor_kind,
                    recent_event.actor_client,
                    recent_event.actor_session_id,
                    recent_event.actor_model,
                    recent_event.body,
                    recent_event.checkpoint_id,
                    recent_event.lease_generation_id,
                    recent_event.lease_release_id,
                    recent_event.relationship_id,
                    recent_event.relationship_source_work_item_id,
                    recent_event.relationship_target_work_item_id,
                    recent_event.relationship_context_checkpoint_work_item_id,
                    recent_event.relationship_context_checkpoint_id,
                    recent_event.metadata_version,
                    recent_event.metadata,
                    recent_event.origin,
                    recent_event.created_at,
                    recent_event.relationship_direction,
                    recent_event.counterpart_work_item_id
                FROM chosen AS w
                CROSS JOIN LATERAL (
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
                        event.relationship_id,
                        event.relationship_source_work_item_id,
                        event.relationship_target_work_item_id,
                        event.relationship_context_checkpoint_work_item_id,
                        event.relationship_context_checkpoint_id,
                        event.metadata_version,
                        event.metadata,
                        event.origin,
                        event.created_at,
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
                WHERE ranked.direction_rank <= 100
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
                    ({counterpart_blocker_count_sql}) AS counterpart_blocker_count,
                    ({counterpart_gate_count_sql}) AS counterpart_gate_count,
                    EXISTS (
                        SELECT 1
                        FROM work_leases AS dropped_counterpart_lease
                        WHERE dropped_counterpart_lease.work_item_id = counterpart.id
                          AND dropped_counterpart_lease.expires_at <= database_time.now
                    ) AS counterpart_has_dropped_lease
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
                                'has_dropped_lease',
                                    adjacent.counterpart_has_dropped_lease,
                                'active_lease', adjacent.counterpart_active_lease,
                                'unresolved_blocker_count',
                                    adjacent.counterpart_blocker_count,
                                'unresolved_gate_count',
                                    adjacent.counterpart_gate_count
                            )
                        )
                    ) AS edge_json
                FROM adjacent_rows AS adjacent
            )
            SELECT
                jsonb_build_object(
                    'id', w.id, 'project_id', w.project_id,
                    'title', w.title, 'summary', w.summary,
                    'status', w.status, 'priority', w.priority,
                    'initial_checkpoint_id', w.initial_checkpoint_id,
                    'version', w.version, 'created_at', w.created_at, 'updated_at', w.updated_at
                )
                    AS work_item,
                jsonb_build_object(
                    'work_version', w.version,
                    'context_checkpoint_id', w.current_checkpoint_id,
                    'relationship_event_count', w.current_relationship_event_count
                ) AS current_context_revision,
                to_jsonb(initial_checkpoint) - 'search_vector'
                    - 'completion_generation' AS initial_checkpoint,
                to_jsonb(current_checkpoint) - 'search_vector'
                    - 'completion_generation' AS current_checkpoint,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            to_jsonb(recent_checkpoint) - 'search_vector'
                                - 'completion_generation'
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
                EXISTS (
                    SELECT 1
                    FROM work_leases AS dropped_lease
                    WHERE dropped_lease.work_item_id = w.id
                      AND dropped_lease.expires_at <= database_time.now
                ) AS has_dropped_lease,
                ({focal_blocker_count_sql}) AS unresolved_blocker_count,
                ({focal_gate_count_sql}) AS unresolved_gate_total,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            bounded.gate_row
                            ORDER BY bounded.attention_sequence
                        )
                        FROM (
                            SELECT
                                to_jsonb(unresolved_gate) AS gate_row,
                                unresolved_gate.attention_sequence
                            FROM gate_rows AS unresolved_gate
                            WHERE unresolved_gate.resolved_at IS NULL
                            ORDER BY
                                CASE
                                    WHEN CAST(:focus_gate_id AS uuid) IS NOT NULL
                                     AND unresolved_gate.id
                                         = CAST(:focus_gate_id AS uuid)
                                    THEN 0
                                    ELSE 1
                                END,
                                unresolved_gate.attention_sequence
                            LIMIT 20
                        ) AS bounded
                    ),
                    '[]'::jsonb
                ) AS unresolved_gates,
                (
                    CAST(:focus_gate_id AS uuid) IS NULL
                    OR EXISTS (
                        SELECT 1
                        FROM gate_rows AS focused_gate
                        WHERE focused_gate.id = CAST(:focus_gate_id AS uuid)
                          AND focused_gate.resolved_at IS NULL
                    )
                ) AS focus_gate_found,
                (
                    SELECT count(*)
                    FROM gate_rows AS resolved_gate_count
                    WHERE resolved_gate_count.resolved_at IS NOT NULL
                ) AS resolved_gate_total,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            bounded.gate_row
                            ORDER BY bounded.resolved_at DESC, bounded.id DESC
                        )
                        FROM (
                            SELECT
                                to_jsonb(resolved_gate) AS gate_row,
                                resolved_gate.resolved_at,
                                resolved_gate.id
                            FROM gate_rows AS resolved_gate
                            WHERE resolved_gate.resolved_at IS NOT NULL
                            ORDER BY resolved_gate.resolved_at DESC, resolved_gate.id DESC
                            LIMIT 20
                        ) AS bounded
                    ),
                    '[]'::jsonb
                ) AS recent_resolved_gates,
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
                            jsonb_build_object(
                                'id', recent_event.id,
                                'project_id', recent_event.project_id,
                                'work_item_id', recent_event.work_item_id,
                                'event_type', recent_event.event_type,
                                'actor_kind', recent_event.actor_kind,
                                'actor_client', recent_event.actor_client,
                                'actor_session_id', recent_event.actor_session_id,
                                'actor_model', recent_event.actor_model,
                                'body', recent_event.body,
                                'checkpoint_id', recent_event.checkpoint_id,
                                'lease_generation_id', recent_event.lease_generation_id,
                                'lease_release_id', recent_event.lease_release_id,
                                'relationship_id', recent_event.relationship_id,
                                'relationship_source_work_item_id',
                                    recent_event.relationship_source_work_item_id,
                                'relationship_target_work_item_id',
                                    recent_event.relationship_target_work_item_id,
                                'relationship_context_checkpoint_work_item_id',
                                    recent_event.relationship_context_checkpoint_work_item_id,
                                'relationship_context_checkpoint_id',
                                    recent_event.relationship_context_checkpoint_id,
                                'relationship_direction', recent_event.relationship_direction,
                                'counterpart_work_item_id', recent_event.counterpart_work_item_id,
                                'metadata_version', recent_event.metadata_version,
                                'metadata', recent_event.metadata,
                                'origin', recent_event.origin,
                                'created_at', recent_event.created_at
                            )
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
                ,database_time.now AS as_of
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
                "focus_gate_id": focus_gate_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        from mnemonic_api.services.work_items import missing_work_item

        raise missing_work_item(database, project_id)
    if not row["focus_gate_found"]:
        from mnemonic_api.errors import gate_not_found

        raise gate_not_found()

    work_item = WorkItemRead.model_validate(row["work_item"])
    initial = CheckpointRead.model_validate(row["initial_checkpoint"])
    current = CheckpointRead.model_validate(row["current_checkpoint"])
    recent = [CheckpointRead.model_validate(item) for item in row["recent_checkpoints"]]
    recent_events = [WorkEventRead.model_validate(item) for item in row["recent_events"]]
    from mnemonic_api.services.gates import human_gate_read

    current_revision = HumanGateContextRevision.model_validate(
        row["current_context_revision"]
    )
    unresolved_gates = [
        human_gate_read(item, current_revision) for item in row["unresolved_gates"]
    ]
    recent_resolved_gates = [
        human_gate_read(item, current_revision)
        for item in row["recent_resolved_gates"]
    ]
    unresolved_gate_total = int(row["unresolved_gate_total"])
    resolved_gate_total = int(row["resolved_gate_total"])
    event_total = int(row["event_total"])
    history_incomplete = bool(row["pre_phase5_history_may_be_incomplete"])
    active_lease = (
        LeasePublic.model_validate(row["active_lease"]) if row["active_lease"] is not None else None
    )
    relationship_groups = (
        row["incoming_relationships"],
        row["outgoing_relationships"],
        row["undirected_relationships"],
    )
    from mnemonic_api.services.duplicates import (
        canonical_work_item_ids,
        duplicate_members_for_context,
        duplicate_merge_eligibility,
    )

    counterpart_ids = [
        UUID(str(edge["counterpart"]["id"]))
        for relationships in relationship_groups
        for edge in relationships
    ]
    counterpart_canonical_ids = canonical_work_item_ids(database, counterpart_ids)
    for relationships in relationship_groups:
        for edge in relationships:
            counterpart = edge["counterpart"]
            counterpart_inputs = counterpart["readiness"]
            counterpart_lease = counterpart_inputs["active_lease"]
            counterpart["readiness"] = readiness(
                WorkIdentityPointer.model_validate(
                    {
                        "id": counterpart["id"],
                        "title": counterpart["title"],
                        "status": counterpart["status"],
                    }
                ),
                LeasePublic.model_validate(counterpart_lease)
                if counterpart_lease is not None
                else None,
                int(counterpart_inputs["unresolved_blocker_count"]),
                bool(counterpart_inputs["has_dropped_lease"]),
                int(counterpart_inputs["unresolved_gate_count"]),
                canonical_work_item_id=counterpart_canonical_ids.get(
                    UUID(str(counterpart["id"])), UUID(str(counterpart["id"]))
                ),
            )
    blocker_count = int(row["unresolved_blocker_count"])
    materialized_ids = {initial.id, current.id, *(item.id for item in recent)}
    total = int(row["checkpoint_total"])
    # One checkpoint body per payload: when the newest context checkpoint is the
    # initial one, the client reads initial_checkpoint instead of a second copy.
    current_is_initial = current.id == initial.id
    canonical, duplicate_members, duplicate_member_total = duplicate_members_for_context(
        database, project_id, work_item
    )
    relationship_counts = row["relationship_counts"]
    omitted_relationship_counts = {
        "incoming": int(relationship_counts["incoming"]) - len(row["incoming_relationships"]),
        "outgoing": int(relationship_counts["outgoing"]) - len(row["outgoing_relationships"]),
        "undirected": int(relationship_counts["undirected"])
        - len(row["undirected_relationships"]),
        "total": int(relationship_counts["total"])
        - sum(len(group) for group in relationship_groups),
    }
    return WorkContext(
        work_item=work_item,
        merge_review_revision=MergeReviewRevision(
            work_version=work_item.version,
            context_checkpoint_id=current.id,
            work_event_count=event_total,
        ),
        canonical=canonical,
        duplicate_members=duplicate_members,
        duplicate_member_total=duplicate_member_total,
        omitted_duplicate_member_count=duplicate_member_total - len(duplicate_members),
        initial_checkpoint=initial,
        current_context=None if current_is_initial else current,
        current_context_is_initial=current_is_initial,
        recent_checkpoints=recent,
        checkpoint_total=total,
        omitted_checkpoint_count=total - len(materialized_ids),
        readiness=readiness(
            work_item,
            active_lease,
            blocker_count,
            bool(row["has_dropped_lease"]),
            unresolved_gate_total,
            canonical_work_item_id=canonical.canonical_work_item.id,
        ),
        unresolved_gates=unresolved_gates,
        unresolved_gate_total=unresolved_gate_total,
        omitted_unresolved_gate_count=unresolved_gate_total - len(unresolved_gates),
        recent_resolved_gates=recent_resolved_gates,
        resolved_gate_total=resolved_gate_total,
        omitted_resolved_gate_count=resolved_gate_total - len(recent_resolved_gates),
        incoming_relationships=row["incoming_relationships"],
        outgoing_relationships=row["outgoing_relationships"],
        undirected_relationships=row["undirected_relationships"],
        relationship_counts=row["relationship_counts"],
        omitted_relationship_counts=omitted_relationship_counts,
        duplicate_merge_eligibility=duplicate_merge_eligibility(
            database,
            project_id,
            work_item_id,
            as_of=row["as_of"],
        ),
        recent_events=recent_events,
        event_total=event_total,
        omitted_event_count=event_total - len(recent_events),
        pre_phase5_history_may_be_incomplete=history_incomplete,
    )
