"""Count captured obligations and report scope growth without exposing review context."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionChange, DecisionChangeAssessment, WorkLease
from mnemonic_api.review_scope_schemas import (
    ExtendedDecisionReview,
    ReviewScopeChanged,
    ReviewScopeHolder,
)


def captured_counts(database: Session, assessment_ids: list[UUID]) -> tuple[int, int, int]:
    """Count occurrence rows, distinct revision references, and distinct stable decision IDs."""
    if not assessment_ids:
        return 0, 0, 0
    row = database.execute(text("""
        SELECT count(*),count(DISTINCT candidate->>'reference'),
               count(DISTINCT candidate->>'decision_id')
        FROM decision_impact_assessments assessment,
             LATERAL jsonb_array_elements(assessment.candidates) candidate
        WHERE assessment.id=ANY(CAST(:ids AS uuid[])) AND (
            candidate->>'relationship' IN ('origin','depends-on') OR
            (candidate->>'relationship'='related-only'
             AND candidate->>'related_direction'='incoming'))
    """), {"ids": assessment_ids}).one()
    return row[0], row[1], row[2]


def scope_notice(
    database: Session, change: DecisionChange, old_version: int,
    assessment_ids: list[UUID],
) -> ReviewScopeChanged:
    assert change.review_work_item_id is not None
    added = change.scope_version - old_version
    counts = captured_counts(database, assessment_ids) if len(assessment_ids) == added else (
        None, None, None
    )
    return ReviewScopeChanged(
        change_id=change.id, project_id=change.project_id,
        review_work_item_id=change.review_work_item_id,
        old_scope_version=old_version, new_scope_version=change.scope_version,
        episodes_added=added, captured_code_impact_rows_added=counts[0],
        distinct_references_in_added_rows=counts[1], distinct_decisions_in_added_rows=counts[2],
    )


def active_holder(database: Session, work_id: UUID) -> WorkLease | None:
    return database.scalar(select(WorkLease).where(
        WorkLease.work_item_id == work_id, WorkLease.expires_at > func.now(),
    ))


def pending_scope_notice(database: Session, work_id: UUID) -> ReviewScopeChanged | None:
    lease = active_holder(database, work_id)
    if lease is not None and lease.purpose == "code_review":
        return None
    change = database.scalar(select(DecisionChange).where(
        DecisionChange.review_work_item_id == work_id,
        DecisionChange.scope_version > DecisionChange.acknowledged_scope_version,
    ))
    if change is None:
        return None
    from mnemonic_api.services.decision_changes import closed

    if closed(database, change):
        return None
    bindings = select(DecisionChangeAssessment.assessment_id).where(
        DecisionChangeAssessment.change_id == change.id,
    )
    if change.acknowledged_scope_version:
        bindings = bindings.where(DecisionChangeAssessment.attachment_scope_version
                                  > change.acknowledged_scope_version)
    return scope_notice(database, change, change.acknowledged_scope_version,
                        list(database.scalars(bindings)))


def extended_review(database: Session, notice: ReviewScopeChanged) -> ExtendedDecisionReview:
    lease = active_holder(database, notice.review_work_item_id)
    return ExtendedDecisionReview(
        review_scope_changed=notice,
        holder=ReviewScopeHolder(
            holder_client=lease.holder_client, holder_session_id=lease.holder_session_id,
            expires_at=lease.expires_at,
        ) if lease is not None and lease.purpose == "implementation" else None,
    )
