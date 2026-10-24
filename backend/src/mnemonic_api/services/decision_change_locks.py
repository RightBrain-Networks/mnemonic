"""Discover and fence every project touched by decision review scope mutations."""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import client_operation_unavailable
from mnemonic_api.models import (
    DecisionChange,
    DecisionChangeAssessment,
    DecisionChangeWork,
    DecisionRevisionChange,
    WorkItem,
)
from mnemonic_api.schemas import APIModel

KINDS = frozenset({
    "put_decision", "update_decision_change", "request_human_input", "resolve_human_input",
    "assess_decision_impact", "assess_decision_impact_batch",
})


@dataclass(frozen=True)
class DecisionMutationScope:
    project_ids: frozenset[UUID]
    witnesses: tuple[tuple[str, ...], ...]


def _identities(*values: object) -> set[UUID]:
    return {value for value in values if isinstance(value, UUID)}


def _sources(kind: str, target: Mapping[str, UUID], payload: APIModel) -> set[UUID]:
    if kind in {"request_human_input", "resolve_human_input"}:
        return _identities(target.get("work_item_id"))
    return _identities(getattr(payload, "source_work_item_id", None),
                       getattr(payload, "work_item_id", None))


def _memberships(
    database: Session, work_ids: set[UUID], payload: APIModel, target: Mapping[str, UUID],
) -> tuple[set[UUID], list[tuple[str, ...]]]:
    declaration = getattr(payload, "change", None)
    change_ids = _identities(getattr(payload, "change_id", None),
                             getattr(declaration, "change_id", None))
    witnesses = []
    for work_id, change_id in database.execute(select(
        DecisionChangeWork.work_item_id, DecisionChangeWork.change_id,
    ).where(DecisionChangeWork.work_item_id.in_(work_ids))):
        change_ids.add(change_id)
        witnesses.append(("lineage", str(work_id), str(change_id)))
    assessments = {
        *getattr(payload, "assessment_ids", ()), *_identities(target.get("assessment_id")),
    }
    for assessment_id, change_id in database.execute(select(
        DecisionChangeAssessment.assessment_id, DecisionChangeAssessment.change_id,
    ).where(DecisionChangeAssessment.assessment_id.in_(assessments))):
        change_ids.add(change_id)
        witnesses.append(("assessment", str(assessment_id), str(change_id)))
    return change_ids, witnesses


def discover_scope(
    database: Session, kind: str, target: Mapping[str, UUID], payload: APIModel,
) -> DecisionMutationScope | None:
    if kind not in KINDS:
        return None
    work_ids = _sources(kind, target, payload)
    change_ids, witnesses = _memberships(database, work_ids, payload, target)
    held_sources = database.scalars(select(DecisionRevisionChange.source_work_item_id).where(
        DecisionRevisionChange.change_id.in_(change_ids),
        DecisionRevisionChange.requires_admission.is_(True),
        DecisionRevisionChange.source_work_item_id.is_not(None),
    ))
    work_ids.update(identity for identity in held_sources if identity is not None)
    projects = set()
    for work_id, owner in database.execute(select(WorkItem.id, WorkItem.project_id).where(
        WorkItem.id.in_(work_ids),
    )):
        projects.add(owner)
        witnesses.append(("source", str(work_id), str(owner)))
    for change_id, owner, driver, driver_project in database.execute(select(
        DecisionChange.id, DecisionChange.project_id, DecisionChange.review_work_item_id,
        WorkItem.project_id,
    ).outerjoin(WorkItem, WorkItem.id == DecisionChange.review_work_item_id).where(
        DecisionChange.id.in_(change_ids),
    )):
        projects.add(owner)
        if driver_project is not None:
            projects.add(driver_project)
        witnesses.append(("change", str(change_id), str(owner), str(driver), str(driver_project)))
    return DecisionMutationScope(frozenset(projects), tuple(sorted(witnesses)))


def require_unchanged_scope(
    database: Session, scope: DecisionMutationScope, kind: str,
    target: Mapping[str, UUID], payload: APIModel,
) -> None:
    # Project locks and the topology lock now fence this read through commit.
    # A move or new adoption between discovery and locking requires exact retry.
    if discover_scope(database, kind, target, payload) != scope:
        raise client_operation_unavailable()
