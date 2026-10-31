"""Version-checked explicit adoption and acknowledgement of review scope."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_schemas import DecisionChangeRead, DecisionChangeUpdateRequest
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import (
    DecisionChange,
    DecisionChangeAssessment,
)
from mnemonic_api.services.decision_changes import (
    attach_assessments,
    bind_work,
    lock_change,
    read_change,
    require_change,
    require_open,
    scope_checkpoint,
)
from mnemonic_api.services.leases import validate_optional_lease_token


def _authorize_driver(
    database: Session, work_id: UUID, payload: DecisionChangeUpdateRequest,
) -> None:
    if payload.actor_client != "dashboard" and payload.lease_token is None:
        raise ApplicationError(422, "lease_token_required",
                               "Claim the review before changing scope.")
    validate_optional_lease_token(database, work_id, payload.lease_token, lock=True)


def _adopt(
    database: Session, project_id: UUID, payload: DecisionChangeUpdateRequest,
) -> DecisionChange:
    from mnemonic_api.services.decision_impact import require_assessment
    from mnemonic_api.services.duplicates import require_canonical_work_item
    from mnemonic_api.services.work_items import require_version, require_work_item

    assert payload.work_item_id is not None and payload.expected_work_version is not None
    work = require_work_item(database, project_id, payload.work_item_id, lock=True)
    require_version(work, payload.expected_work_version)
    require_canonical_work_item(database, work)
    if work.status != "pending":
        raise conflict("decision_change_adoption_conflict", "Adopt into pending ordinary work.")
    _authorize_driver(database, work.id, payload)
    change = database.scalar(select(DecisionChange).where(
        DecisionChange.review_work_item_id == work.id,
    ))
    episodes = [require_assessment(database, project_id, identity)
                for identity in payload.assessment_ids]
    owners = list(database.scalars(select(DecisionChange).join(DecisionChangeAssessment).where(
        DecisionChangeAssessment.assessment_id.in_(payload.assessment_ids),
    ).distinct()))
    for owner in owners:
        bind_work(database, work.id, owner.id)
    if change is None and len(owners) == 1 and owners[0].review_work_item_id is None:
        change = lock_change(database, owners[0])
        change.review_work_item_id = work.id
    if change is None:
        change = DecisionChange(
            project_id=project_id, origin_revision_id=work.decision_review_revision_id,
            review_work_item_id=work.id, scope_version=0, acknowledged_scope_version=0,
        )
        database.add(change)
        database.flush()
    else:
        change = lock_change(database, change)
    bind_work(database, work.id, change.id)
    attach_assessments(database, change, episodes)
    change.updated_at = datetime.now(UTC)
    scope_checkpoint(
        database, change,
        f"Explicitly adopted decision impact review scope {change.id}, version "
        f"{change.scope_version}. Read decision_change_get and page decision_assessments with "
        "this change_id; investigate every required candidate, record dispositions, and "
        "acknowledge the current scope before Done. Investigation only: create separate work "
        "for remediation and retain this work as its discovered-from origin.",
    )
    return change


def update_change(
    database: Session, project_id: UUID, payload: DecisionChangeUpdateRequest,
) -> DecisionChangeRead:
    if payload.action == "admit":
        raise ApplicationError(422, "decision_change_admission_retired",
                               "Review cycles are created automatically; admission is no longer "
                               "supported.")
    if payload.action == "adopt":
        change = _adopt(database, project_id, payload)
    else:
        assert payload.change_id is not None
        change = lock_change(database, require_change(database, project_id, payload.change_id))
        if payload.expected_scope_version != change.scope_version:
            raise conflict("decision_change_scope_conflict",
                           "Read the current scope before acting.")
        require_open(database, change)
        if change.review_work_item_id is None:
            raise conflict("decision_change_adoption_conflict", "This change has no review.")
        _authorize_driver(database, change.review_work_item_id, payload)
        change.acknowledged_scope_version = change.scope_version
        change.updated_at = datetime.now(UTC)
        scope_checkpoint(database, change,
                         f"Review scope {change.scope_version} acknowledged by "
                         f"{payload.actor_client} session {payload.actor_session_id}.")
    database.flush()
    return read_change(database, change)
