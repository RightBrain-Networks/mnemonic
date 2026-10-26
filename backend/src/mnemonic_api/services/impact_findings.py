"""Summarize declared root causes without guessing how unlabelled rows relate."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.impact_finding_schemas import ImpactFindingSummary
from mnemonic_api.models import DecisionImpactDisposition


def findings_for_latest(rows: Sequence[DecisionImpactDisposition]) -> ImpactFindingSummary:
    findings = [row for row in rows if row.disposition == "change-required"]
    return ImpactFindingSummary(
        named_finding_count=len({row.finding_id for row in findings if row.finding_id is not None}),
        unlabelled_change_required_count=sum(row.finding_id is None for row in findings),
        change_required_row_count=len(findings),
    )


def change_findings(database: Session, change_id: UUID) -> ImpactFindingSummary:
    named, unlabelled, total = database.execute(text("""
        WITH latest AS (
            SELECT DISTINCT ON (disposition.assessment_id, disposition.candidate_reference)
                disposition.assessment_id, disposition.candidate_reference,
                disposition.disposition, disposition.finding_id
            FROM decision_impact_dispositions disposition
            JOIN decision_change_assessments binding
                ON binding.assessment_id=disposition.assessment_id
            WHERE binding.change_id=:change_id
            ORDER BY disposition.assessment_id, disposition.candidate_reference,
                disposition.version DESC
        ), effective AS (
            SELECT disposition,finding_id FROM latest
            UNION ALL
            SELECT source.disposition,source.finding_id
            FROM decision_candidate_reuses reuse
            JOIN decision_change_assessments binding ON binding.assessment_id=reuse.assessment_id
            JOIN decision_impact_dispositions source ON source.id=reuse.source_disposition_id
            WHERE binding.change_id=:change_id AND NOT EXISTS (
                SELECT 1 FROM latest WHERE latest.assessment_id=reuse.assessment_id
                    AND latest.candidate_reference=reuse.candidate_reference)
        ) SELECT count(DISTINCT finding_id), count(*) FILTER (WHERE finding_id IS NULL), count(*)
          FROM effective WHERE disposition='change-required'
    """), {"change_id": change_id}).one()
    return ImpactFindingSummary(named_finding_count=named,
                                unlabelled_change_required_count=unlabelled,
                                change_required_row_count=total)
