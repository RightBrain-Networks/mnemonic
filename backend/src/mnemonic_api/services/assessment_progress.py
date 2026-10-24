"""Renew only the implementation capability of an assessment's current review driver."""

from secrets import compare_digest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import DecisionChange, DecisionChangeAssessment, WorkItem, WorkLease
from mnemonic_api.services.duplicates import require_canonical_work_item
from mnemonic_api.services.leases import validate_optional_lease_token
from mnemonic_api.services.work_items import require_work_item


def renew_assessment_progress(
    database: Session, assessment_id: UUID, lease_token: str | None,
) -> UUID | None:
    if lease_token is None:
        return
    # The mutation's discovered project/topology fences protect all current drivers,
    # including moved work and an episode adopted by more than one investigation.
    drivers = database.execute(select(WorkItem, WorkLease).join(
        WorkLease, WorkLease.work_item_id == WorkItem.id,
    ).join(DecisionChange, DecisionChange.review_work_item_id == WorkItem.id).join(
        DecisionChangeAssessment, DecisionChangeAssessment.change_id == DecisionChange.id,
    ).where(DecisionChangeAssessment.assessment_id == assessment_id))
    for work, lease in drivers:
        if compare_digest(lease_token.encode(), lease.lease_token.encode()):
            require_work_item(database, work.project_id, work.id, lock=True)
            validate_optional_lease_token(
                database, work.id, lease_token, renew_for_progress=True,
            )
            require_canonical_work_item(database, work)
            if work.status != "pending":
                raise conflict("decision_change_closed", "The associated review is not active.")
            return work.id
    raise conflict("lease_token_mismatch", "Supply the associated review's active capability.")
