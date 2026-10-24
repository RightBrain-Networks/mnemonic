"""Summarize declared root causes without guessing how unlabelled rows relate."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from mnemonic_api.impact_finding_schemas import ImpactFindingSummary
from mnemonic_api.models import DecisionChangeAssessment, DecisionImpactDisposition


def findings_for_latest(rows: Sequence[DecisionImpactDisposition]) -> ImpactFindingSummary:
    findings = [row for row in rows if row.disposition == "change-required"]
    return ImpactFindingSummary(
        named_finding_count=len({row.finding_id for row in findings if row.finding_id is not None}),
        unlabelled_change_required_count=sum(row.finding_id is None for row in findings),
        change_required_row_count=len(findings),
    )


def change_findings(database: Session, change_id: UUID) -> ImpactFindingSummary:
    latest = select(
        DecisionImpactDisposition.disposition, DecisionImpactDisposition.finding_id,
    ).join(DecisionChangeAssessment, DecisionChangeAssessment.assessment_id
           == DecisionImpactDisposition.assessment_id).where(
        DecisionChangeAssessment.change_id == change_id,
    ).distinct(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
    ).order_by(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
        DecisionImpactDisposition.version.desc(),
    ).subquery()
    named, unlabelled, total = database.execute(select(
        func.count(distinct(latest.c.finding_id)),
        func.count().filter(latest.c.finding_id.is_(None)), func.count(),
    ).where(latest.c.disposition == "change-required")).one()
    return ImpactFindingSummary(named_finding_count=named,
                                unlabelled_change_required_count=unlabelled,
                                change_required_row_count=total)
