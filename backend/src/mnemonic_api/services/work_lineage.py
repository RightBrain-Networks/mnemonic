"""Turn an explicit session origin into an atomic discovery edge."""

from uuid import UUID

from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import InitialRelationshipCreate, WorkItemCreate


def creation_endpoint_ids(payload: WorkItemCreate) -> list[UUID]:
    ids = [item.other_work_item_id for item in payload.initial_relationships]
    if isinstance(payload.discovered_from_work_item_id, UUID):
        ids.append(payload.discovered_from_work_item_id)
    return ids


def creation_relationships(
    payload: WorkItemCreate, locked_work_items: dict[UUID, WorkItem],
) -> list[InitialRelationshipCreate]:
    relationships = list(payload.initial_relationships)
    origin = payload.discovered_from_work_item_id
    if isinstance(origin, UUID) and not any(
        item.type == "discovered-from" and item.other_work_item_id == origin
        for item in relationships
    ):
        relationships.append(InitialRelationshipCreate(
            type="discovered-from", direction="outgoing", other_work_item_id=origin,
            context_checkpoint_id=locked_work_items[origin].initial_checkpoint_id,
        ))
    return relationships
