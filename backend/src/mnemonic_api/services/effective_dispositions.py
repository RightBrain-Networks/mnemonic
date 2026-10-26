"""Read pinned prior conclusions without copying or following reference chains."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.models import DecisionCandidateReuse, DecisionImpactDisposition


@dataclass(frozen=True)
class EffectiveDisposition:
    assessment_id: UUID
    row: DecisionImpactDisposition
    reused: bool = False

    @property
    def source(self) -> dict | None:
        return ({"assessment_id": self.row.assessment_id, "disposition_id": self.row.id,
                 "disposition_version": self.row.version} if self.reused else None)


def effective_dispositions(
    database: Session, assessment_ids: Sequence[UUID],
) -> list[EffectiveDisposition]:
    if not assessment_ids:
        return []
    inherited = database.execute(select(DecisionCandidateReuse, DecisionImpactDisposition).join(
        DecisionImpactDisposition,
        DecisionImpactDisposition.id == DecisionCandidateReuse.source_disposition_id,
    ).where(DecisionCandidateReuse.assessment_id.in_(assessment_ids))).all()
    result = {(reuse.assessment_id, reuse.candidate_reference): EffectiveDisposition(
        reuse.assessment_id, row, True,
    ) for reuse, row in inherited}
    local = database.scalars(select(DecisionImpactDisposition).where(
        DecisionImpactDisposition.assessment_id.in_(assessment_ids),
    ).distinct(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
    ).order_by(
        DecisionImpactDisposition.assessment_id, DecisionImpactDisposition.candidate_reference,
        DecisionImpactDisposition.version.desc(),
    ))
    for row in local:
        result[row.assessment_id, row.candidate_reference] = EffectiveDisposition(
            row.assessment_id, row,
        )
    return [result[key] for key in sorted(result)]
