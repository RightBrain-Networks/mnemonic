"""Record a future-work exception without erasing discovery or existing lineage."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import WorkChangeInheritanceExclusion, WorkItem
from mnemonic_api.schemas import WorkItemCreate


def stage_inheritance_exclusion(
    database: Session, work: WorkItem, payload: WorkItemCreate,
    endpoints: dict[UUID, WorkItem],
) -> None:
    reason = payload.decision_change_inheritance_reason
    if payload.inherit_decision_change:
        if reason is not None:
            raise ApplicationError(422, "invalid_decision_change_inheritance",
                                   "An inheritance reason requires inherit_decision_change=false.")
        return
    origin = payload.discovered_from_work_item_id
    if not isinstance(origin, UUID) or reason is None:
        raise ApplicationError(422, "invalid_decision_change_inheritance",
                               "Disabling inheritance requires a declared origin and a reason.")
    selected = [edge.context_checkpoint_id for edge in payload.initial_relationships
                if edge.type == "discovered-from" and edge.other_work_item_id == origin]
    context_id = min(selected, key=str) if selected else endpoints[origin].initial_checkpoint_id
    receipt_id = database.info.get("client_operation_receipt_id")
    if not isinstance(receipt_id, int):
        raise ApplicationError(422, "client_operation_id_required",
                               "An operation UUID is required.")
    database.add(WorkChangeInheritanceExclusion(
        work_item_id=work.id, origin_work_item_id=origin,
        origin_context_checkpoint_id=context_id,
        reason=reason, creation_receipt_id=receipt_id, created_at=work.created_at,
    ))
    database.flush()
    database.expire(work, ["decision_change_inheritance_exclusion"])


def excludes_inheritance(database: Session, work_id: UUID, source_id: UUID) -> bool:
    return database.scalar(select(WorkChangeInheritanceExclusion.work_item_id).where(
        WorkChangeInheritanceExclusion.work_item_id == work_id,
        WorkChangeInheritanceExclusion.origin_work_item_id == source_id,
    )) is not None
