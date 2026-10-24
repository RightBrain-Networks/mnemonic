"""Explicit author declarations, immutable provenance, and inherited review boundaries."""


from uuid import UUID

from pydantic.experimental.missing_sentinel import MISSING
from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.decision_change_schemas import DecisionChangeDeclaration
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.knowledge_schemas import DecisionPutRequest
from mnemonic_api.models import (
    DecisionChange,
    DecisionChangeWork,
    DecisionRevision,
    DecisionRevisionChange,
    WorkItem,
)
from mnemonic_api.services.decision_changes import bind_work, closed, lock_change, require_change


def _admitted_ancestors(database: Session, change: DecisionChange) -> set[UUID]:
    result: set[UUID] = set()
    parent_id = change.parent_change_id
    while parent_id is not None and parent_id not in result:
        result.add(parent_id)
        parent = database.get(DecisionChange, parent_id)
        assert parent is not None
        if parent.review_work_item_id is not None:
            result.update(database.scalars(select(DecisionChangeWork.change_id).where(
                DecisionChangeWork.work_item_id == parent.review_work_item_id,
                DecisionChangeWork.created_at <= change.created_at,
            )))
        held_sources = select(DecisionRevisionChange.source_work_item_id).where(
            DecisionRevisionChange.change_id == parent.id,
            DecisionRevisionChange.requires_admission.is_(True),
            DecisionRevisionChange.created_at <= change.created_at,
        )
        result.update(database.scalars(select(DecisionChangeWork.change_id).where(
            DecisionChangeWork.work_item_id.in_(held_sources),
            DecisionChangeWork.created_at <= change.created_at,
        )))
        parent_id = parent.parent_change_id
    return result


def _needs_admission(
    database: Session, change: DecisionChange, inherited: list[DecisionChange],
) -> bool:
    admitted = _admitted_ancestors(database, change)
    return any(item.id not in admitted and (
        closed(database, item) or item.project_id != change.project_id
    ) for item in inherited)


def _require_inherited_boundary(
    database: Session, change: DecisionChange, inherited: list[DecisionChange],
) -> None:
    if _needs_admission(database, change, inherited):
        raise conflict("decision_change_closed",
                       "An inherited review cycle is closed; explicit human admission is required.")


def _leaf_changes(database: Session, inherited: list[DecisionChange]) -> list[DecisionChange]:
    parents = {item.parent_change_id for item in inherited}
    for item in inherited:
        parents.update(_admitted_ancestors(database, item) - {item.id})
    for item in inherited:
        if item.id not in parents and item.review_work_item_id is not None:
            parents.update(database.scalars(select(DecisionChangeWork.change_id).where(
                DecisionChangeWork.work_item_id == item.review_work_item_id,
                DecisionChangeWork.change_id != item.id,
            )))
    return [item for item in inherited if item.id not in parents]


def _source(database: Session, payload: DecisionPutRequest) -> WorkItem | None:
    identity = payload.source_work_item_id
    if identity is MISSING or identity is None:
        return None
    row = database.get(WorkItem, identity)
    if row is None or row.deleted_at is not None:
        raise ApplicationError(422, "decision_change_source_invalid", "Source work must exist.")
    return row


def _lineage(database: Session, source: WorkItem | None) -> list[DecisionChange]:
    if source is None:
        return []
    return list(database.scalars(select(DecisionChange).join(
        DecisionChangeWork, DecisionChangeWork.change_id == DecisionChange.id,
    ).where(DecisionChangeWork.work_item_id == source.id).order_by(DecisionChange.id)))


def _select_change(
    database: Session, revision: DecisionRevision, declaration: DecisionChangeDeclaration,
    source: WorkItem | None, *, hold_closed: bool,
) -> DecisionChange | None:
    inherited = _lineage(database, source)
    if declaration.change_id is not None:
        change = require_change(database, revision.project_id, declaration.change_id)
        allowed = {item.id for item in inherited}
        if inherited and change.id not in allowed and change.parent_change_id not in allowed:
            raise conflict("decision_change_lineage_conflict",
                           "Review-derived work must retain its change or an admitted successor.")
        if declaration.kind != "maintenance" and not hold_closed:
            _require_inherited_boundary(database, change, inherited)
        return lock_change(database, change)
    if declaration.kind == "maintenance":
        return None
    local = [item for item in inherited if item.project_id == revision.project_id]
    leaves = _leaf_changes(database, local)
    if (local and not leaves) or len(leaves) > 1 or (inherited and not local and not hold_closed):
        raise conflict("decision_change_lineage_conflict",
                       "Select the governing change explicitly; inherited scope is ambiguous.")
    if leaves:
        if not hold_closed:
            _require_inherited_boundary(database, leaves[0], inherited)
        return lock_change(database, leaves[0])
    change = DecisionChange(project_id=revision.project_id, origin_revision_id=revision.id,
                            scope_version=0, acknowledged_scope_version=0)
    database.add(change)
    database.flush()
    return change


def _governing(
    database: Session, revision: DecisionRevision, declaration: DecisionChangeDeclaration,
    change: DecisionChange | None,
) -> DecisionRevision | None:
    if declaration.governing_reference is None:
        return None
    from mnemonic_api.services import knowledge

    document, _, pinned = knowledge.require_document(
        database, revision.project_id, declaration.governing_reference,
    )
    governing = knowledge.require_revision(database, document, pinned)
    classification = database.get(DecisionRevisionChange, governing.id)
    assert change is not None
    if governing.id == revision.id or not (
        change.origin_revision_id == governing.id
        or (classification is not None and classification.change_id == change.id)
    ):
        raise ApplicationError(
            422, "decision_change_governance_invalid",
            "The exact governing revision must already belong to the declared change.",
        )
    return governing


def classify_revision(
    database: Session, revision: DecisionRevision, payload: DecisionPutRequest,
    *, hold_closed: bool = False,
) -> tuple[DecisionChange | None, DecisionRevision | None]:
    declaration = payload.change or DecisionChangeDeclaration()
    source = _source(database, payload)
    change = _select_change(database, revision, declaration, source, hold_closed=hold_closed)
    governing = _governing(database, revision, declaration, change)
    if change is not None and change.origin_revision_id is None:
        change.origin_revision_id = revision.id
    database.add(DecisionRevisionChange(
        revision_id=revision.id, change_id=change.id if change else None,
        kind=declaration.kind, reason=declaration.reason,
        governing_revision_id=governing.id if governing else None,
        source_work_item_id=source.id if source else None,
        requires_admission=bool(hold_closed and change is not None
                                and _needs_admission(database, change, _lineage(database, source))
                                and declaration.kind == "substantive"),
    ))
    if source is not None and change is not None:
        bind_work(database, source.id, change.id)
    database.flush()
    return change, governing
