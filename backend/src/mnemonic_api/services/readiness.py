"""Canonical readiness projection, predicates, and ready-work discovery."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, false, func, literal_column, select, text, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from mnemonic_api.errors import conflict
from mnemonic_api.models import WorkItem, WorkLease, WorkRelationship
from mnemonic_api.schemas import (
    LeasePublic,
    Readiness,
    ReadyWorkListQuery,
    ReadyWorkPage,
    WorkItemRead,
    WorkSummaryMinimal,
)


def readiness(
    work_item: WorkItem | WorkItemRead,
    active_lease: WorkLease | LeasePublic | None = None,
    unresolved_blocker_count: int = 0,
    has_dropped_lease: bool = False,
) -> Readiness:
    """Project lifecycle, blocker, and lease facts with fixed display precedence."""
    terminal = work_item.status in {"done", "wont-do", "promoted"}
    lease_public = LeasePublic.model_validate(active_lease) if active_lease is not None else None
    has_active_lease = lease_public is not None
    is_blocked = unresolved_blocker_count > 0
    if work_item.status != "pending":
        display_state = work_item.status
    elif is_blocked:
        display_state = "blocked"
    elif has_active_lease:
        display_state = "active"
    elif has_dropped_lease:
        display_state = "dropped"
    else:
        display_state = "pending"
    return Readiness(
        lifecycle_status=work_item.status,
        is_terminal=terminal,
        has_active_lease=has_active_lease,
        has_dropped_lease=has_dropped_lease,
        active_lease=lease_public,
        unresolved_blocker_count=unresolved_blocker_count,
        is_blocked=is_blocked,
        is_ready=work_item.status == "pending" and not has_active_lease and not is_blocked,
        display_state=display_state,
    )


def unresolved_blocker_counts(database: Session, work_item_ids: Sequence[UUID]) -> dict[UUID, int]:
    """Count direct incoming blockers whose source has not reached done."""
    if not work_item_ids:
        return {}
    source = WorkItem.__table__.alias("blocker_source")
    target = WorkItem.__table__.alias("blocked_target")
    rows = database.execute(
        select(WorkRelationship.target_work_item_id, func.count())
        .join(
            source,
            and_(
                source.c.id == WorkRelationship.source_work_item_id,
                source.c.project_id == WorkRelationship.project_id,
            ),
        )
        .join(
            target,
            and_(
                target.c.id == WorkRelationship.target_work_item_id,
                target.c.project_id == WorkRelationship.project_id,
            ),
        )
        .where(
            WorkRelationship.relationship_type == "blocks",
            target.c.id.in_(work_item_ids),
            source.c.status != "done",
        )
        .group_by(WorkRelationship.target_work_item_id)
    )
    return {work_item_id: int(count) for work_item_id, count in rows}


def unresolved_blocker_count(database: Session, work_item_id: UUID) -> int:
    return unresolved_blocker_counts(database, [work_item_id]).get(work_item_id, 0)


def require_unblocked(database: Session, work_item_id: UUID) -> None:
    if unresolved_blocker_count(database, work_item_id):
        raise conflict("work_blocked", "This work item has an unresolved blocker.")


def gate_eligibility_clause(work_item_id: ColumnElement[UUID]) -> ColumnElement[bool]:
    """Phase 7 extension seam; no gate model exists in Phases 4–5."""
    del work_item_id
    return true()


@dataclass(frozen=True)
class EligibilityClauses:
    """Composable facts shared by listing and fresh claim validation."""

    is_pending: ColumnElement[bool]
    has_unresolved_blocker: ColumnElement[bool]
    has_active_lease: ColumnElement[bool]
    gate_eligible: ColumnElement[bool]

    def eligible(self, *, include_active_lease: bool = True) -> ColumnElement[bool]:
        clauses = [self.is_pending, ~self.has_unresolved_blocker, self.gate_eligible]
        if include_active_lease:
            clauses.append(~self.has_active_lease)
        return and_(*clauses)


def eligibility_clauses(
    work_item_id: ColumnElement[UUID],
    work_item_project_id: ColumnElement[UUID],
    work_item_status: ColumnElement[str],
    database_time: ColumnElement[datetime],
    *,
    correlate_from=None,
) -> EligibilityClauses:
    """Build readiness facts from composable work columns at one database time."""
    blocker_edge = WorkRelationship.__table__.alias("eligibility_blocker_edge")
    blocker_source = WorkItem.__table__.alias("eligibility_blocker_source")
    retained_lease = WorkLease.__table__.alias("eligibility_retained_lease")
    blocker_lookup = (
        select(true())
        .select_from(
            blocker_edge.join(
                blocker_source,
                and_(
                    blocker_source.c.project_id == blocker_edge.c.project_id,
                    blocker_source.c.id == blocker_edge.c.source_work_item_id,
                ),
            )
        )
        .where(
            blocker_edge.c.project_id == work_item_project_id,
            blocker_edge.c.relationship_type == "blocks",
            blocker_edge.c.target_work_item_id == work_item_id,
            blocker_source.c.status != "done",
        )
        .limit(1)
    )
    lease_lookup = (
        select(true())
        .select_from(retained_lease)
        .where(
            retained_lease.c.work_item_id == work_item_id,
            retained_lease.c.expires_at > database_time,
        )
        .limit(1)
    )
    if correlate_from is not None:
        blocker_lookup = blocker_lookup.correlate(correlate_from)
        lease_lookup = lease_lookup.correlate(correlate_from)
    return EligibilityClauses(
        is_pending=work_item_status == "pending",
        has_unresolved_blocker=func.coalesce(blocker_lookup.scalar_subquery(), false()),
        has_active_lease=func.coalesce(lease_lookup.scalar_subquery(), false()),
        gate_eligible=gate_eligibility_clause(work_item_id),
    )


def require_fresh_claim_eligible(database: Session, work_item: WorkItem) -> None:
    """Re-evaluate canonical non-lease eligibility after replay decisions."""
    work_table = WorkItem.__table__
    clauses = eligibility_clauses(
        work_table.c.id,
        work_table.c.project_id,
        work_table.c.status,
        func.clock_timestamp(),
        correlate_from=work_table,
    )
    row = (
        database.execute(
            select(
                clauses.eligible(include_active_lease=False).label("eligible"),
                clauses.has_unresolved_blocker.label("has_unresolved_blocker"),
            ).where(work_table.c.id == work_item.id)
        )
        .mappings()
        .one()
    )
    if row["eligible"]:
        return
    if row["has_unresolved_blocker"]:
        raise conflict("work_blocked", "This work item has an unresolved blocker.")
    raise conflict("work_not_eligible", "This work item is not eligible for a fresh claim.")


def ready_work_page(
    database: Session,
    project_id: UUID,
    filters: ReadyWorkListQuery,
) -> ReadyWorkPage:
    """Return one deterministic pointer-only ready page from one SQL statement."""
    from mnemonic_api.services.work_items import require_project, require_work_item

    if filters.parent_work_item_id is None:
        require_project(database, project_id)
    else:
        require_work_item(database, project_id, filters.parent_work_item_id)

    canonical_eligibility = eligibility_clauses(
        literal_column("work_item.id"),
        literal_column("work_item.project_id"),
        literal_column("work_item.status"),
        literal_column("database_time.now"),
    ).eligible()
    eligibility_sql = str(
        canonical_eligibility.compile(
            dialect=database.get_bind().dialect,
            compile_kwargs={"literal_binds": True},
        )
    )
    tag_predicate = ""
    if filters.tag is not None:
        tag_predicate = """
                  AND EXISTS (
                      SELECT 1
                      FROM checkpoints AS tagged_checkpoint
                      WHERE tagged_checkpoint.work_item_id = work_item.id
                        AND mnemonic_normalized_tags(tagged_checkpoint.tags)
                            @> mnemonic_normalized_tags(
                                ARRAY[CAST(:tag AS varchar)]::varchar[]
                            )
                  )
        """
    parent_predicate = ""
    if filters.parent_work_item_id is not None:
        parent_predicate = """
                  AND COALESCE((
                      SELECT parent_edge.source_work_item_id = :parent_work_item_id
                      FROM work_relationships AS parent_edge
                      WHERE parent_edge.project_id = work_item.project_id
                        AND parent_edge.relationship_type = 'parent-child'
                        AND parent_edge.target_work_item_id = work_item.id
                      LIMIT 1
                  ), false)
        """

    row = (
        database.execute(
            text(
                f"""
            WITH database_time AS MATERIALIZED (
                SELECT clock_timestamp() AS now
            ),
            eligible AS MATERIALIZED (
                SELECT
                    work_item.id,
                    work_item.title,
                    work_item.status,
                    work_item.priority,
                    work_item.version,
                    work_item.created_at,
                    work_item.updated_at
                FROM work_items AS work_item
                CROSS JOIN database_time
                WHERE work_item.project_id = :project_id
                  AND work_item.deleted_at IS NULL
                  AND work_item.priority >= :min_priority
                  AND {eligibility_sql}
                  {tag_predicate}
                  {parent_predicate}
                ORDER BY work_item.priority DESC, work_item.created_at, work_item.id
            ),
            paged AS MATERIALIZED (
                SELECT *
                FROM eligible
                ORDER BY priority DESC, created_at, id
                LIMIT :limit OFFSET :offset
            ),
            projected AS (
                SELECT
                    paged.*,
                    EXISTS (
                        SELECT 1
                        FROM work_leases AS dropped_lease
                        WHERE dropped_lease.work_item_id = paged.id
                          AND dropped_lease.expires_at <= (
                              SELECT database_time.now FROM database_time
                          )
                    ) AS has_dropped_lease,
                    (
                        SELECT count(*)
                        FROM checkpoints AS checkpoint_count
                        WHERE checkpoint_count.work_item_id = paged.id
                    ) AS checkpoint_count
                FROM paged
            )
            SELECT
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'work_item', jsonb_build_object(
                                'id', projected.id,
                                'title', projected.title,
                                'status', projected.status,
                                'priority', projected.priority,
                                'version', projected.version,
                                'updated_at', projected.updated_at
                            ),
                            'checkpoint_count', projected.checkpoint_count,
                            'display_state', CASE
                                WHEN projected.has_dropped_lease THEN 'dropped'
                                ELSE 'pending'
                            END
                        )
                        ORDER BY projected.priority DESC, projected.created_at, projected.id
                    ),
                    '[]'::jsonb
                ) AS items,
                (SELECT count(*) FROM eligible) AS total
            FROM projected
            """
            ),
            {
                "project_id": project_id,
                "min_priority": filters.min_priority,
                "tag": filters.tag,
                "parent_work_item_id": filters.parent_work_item_id,
                "limit": filters.limit,
                "offset": filters.offset,
            },
        )
        .mappings()
        .one()
    )
    return ReadyWorkPage(
        items=[WorkSummaryMinimal.model_validate(item) for item in row["items"]],
        total=int(row["total"]),
        limit=filters.limit,
        offset=filters.offset,
    )
