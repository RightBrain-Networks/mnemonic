"""Retain explicit author assertions that a prior supersession ruling is unchanged."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_schemas import UnchangedSupersession
from mnemonic_api.errors import ApplicationError
from mnemonic_api.knowledge_schemas import DecisionPutRequest
from mnemonic_api.models import (
    DecisionImpactAssessment,
    DecisionRevision,
    DecisionSupersessionReuse,
)
from mnemonic_api.services.decision_dependencies import superseded_ids
from mnemonic_api.services.decision_supersession_reuse import (
    continuous_declaration,
    latest_supersession,
)


def _invalid() -> ApplicationError:
    return ApplicationError(
        422, "decision_supersession_reuse_invalid",
        "Name the latest prior supersession capture for this replacement document and exact "
        "target; the target revision, extent and reason must have remained unchanged.",
    )


def _validate(
    database: Session, revision: DecisionRevision, payload: DecisionPutRequest,
    item: UnchangedSupersession,
) -> tuple[DecisionRevision, DecisionRevision]:
    from mnemonic_api.services import knowledge

    target, _, target_pin = knowledge.require_document(
        database, revision.project_id, item.target_reference,
    )
    origin = knowledge.require_revision(database, target, target_pin)
    replacement, _, replacement_pin = knowledge.require_document(
        database, revision.project_id, item.governing_reference,
    )
    governing = knowledge.require_revision(database, replacement, replacement_pin)
    identifier = item.target_reference.rsplit("-", 1)[0]
    detail = next((row for row in payload.supersession_details
                   if row.decision_id == identifier), None)
    expected_pin = detail.revision if detail is not None else target.current_revision
    prior = latest_supersession(database, revision, origin)
    if (replacement.id != revision.document_id or governing.revision >= revision.revision
            or identifier not in superseded_ids(payload) or origin.revision != expected_pin
            or prior is None or prior[0].replacement_revision_id != governing.id):
        raise _invalid()
    expected = DecisionImpactAssessment(
        origin_revision_id=origin.id, extent=detail.extent if detail else None,
        reason=detail.reason if detail else None,
    )
    if (any(getattr(prior[0], name) != getattr(expected, name)
            for name in ("origin_revision_id", "extent", "reason"))
            or not continuous_declaration(
                database, revision, origin, governing.revision, expected,
            )):
        raise _invalid()
    return origin, governing


def record_declarations(
    database: Session, revision: DecisionRevision, payload: DecisionPutRequest,
) -> None:
    for item in payload.change.unchanged_supersessions if payload.change else []:
        origin, governing = _validate(database, revision, payload, item)
        database.add(DecisionSupersessionReuse(
            project_id=revision.project_id, revision_id=revision.id,
            target_revision_id=origin.id, governing_revision_id=governing.id, reason=item.reason,
        ))


def read_declarations(database: Session, revision_id: UUID) -> list[UnchangedSupersession]:
    from mnemonic_api.services.decision_impact import _revision_reference

    rows = database.scalars(select(DecisionSupersessionReuse).where(
        DecisionSupersessionReuse.revision_id == revision_id,
    ))
    return sorted((UnchangedSupersession(
        target_reference=_revision_reference(database, row.target_revision_id),
        governing_reference=_revision_reference(database, row.governing_revision_id),
        reason=row.reason,
    ) for row in rows), key=lambda item: item.target_reference)
