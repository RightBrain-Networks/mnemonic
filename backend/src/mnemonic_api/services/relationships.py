"""Concurrency-safe project graph mutations and adjacency projections."""

from collections.abc import Iterable, Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.errors import conflict, duplicate_merge_required, not_found
from mnemonic_api.external_references import ExternalReference
from mnemonic_api.models import (
    Checkpoint,
    Project,
    WorkItem,
    WorkRelationship,
)
from mnemonic_api.schemas import (
    AdjacentRelationshipRead,
    MutationActor,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipListQuery,
    RelationshipRemovalResult,
    WorkPointer,
)
from mnemonic_api.services.readiness import (
    readiness,
    readiness_inputs,
)
from mnemonic_api.services.work_events import database_now, source_actor, stage_relationship_events

CYCLE_TYPES = frozenset({"blocks", "parent-child"})


def lock_project_graph(database: Session, project_id: UUID) -> Project:
    """Serialize every graph write in a project before endpoint locks are taken."""
    project = database.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise not_found("project_not_found", "Project not found.")
    return project


def lock_endpoint_work_items(
    database: Session,
    project_id: UUID,
    work_item_ids: Iterable[UUID],
) -> dict[UUID, WorkItem]:
    ids = sorted(set(work_item_ids))
    if not ids:
        return {}
    work_items = list(
        database.scalars(
            select(WorkItem)
            .where(
                WorkItem.project_id == project_id,
                WorkItem.id.in_(ids),
                WorkItem.deleted_at.is_(None),
            )
            .order_by(WorkItem.id)
            .with_for_update()
        )
    )
    if len(work_items) != len(ids):
        raise not_found("work_item_not_found", "Work item not found.")
    return {work_item.id: work_item for work_item in work_items}


def normalize_endpoints(
    relationship_type: str, source_work_item_id: UUID, target_work_item_id: UUID
) -> tuple[UUID, UUID]:
    if source_work_item_id == target_work_item_id:
        raise conflict(
            "relationship_self_edge",
            "A relationship cannot connect a work item to itself.",
        )
    if relationship_type == "related" and target_work_item_id < source_work_item_id:
        return target_work_item_id, source_work_item_id
    return source_work_item_id, target_work_item_id


def relationship_edge(relationship: WorkRelationship) -> RelationshipEdgeRead:
    return RelationshipEdgeRead.model_validate(relationship)


def require_no_relationships(database: Session, project_id: UUID, work_item_id: UUID) -> None:
    exists = database.scalar(
        select(WorkRelationship.id)
        .where(
            WorkRelationship.project_id == project_id,
            or_(
                WorkRelationship.source_work_item_id == work_item_id,
                WorkRelationship.target_work_item_id == work_item_id,
            ),
        )
        .limit(1)
    )
    if exists is not None:
        raise conflict(
            "active_relationships",
            "Remove this work item's relationships before deleting it.",
        )


def _context_owner(
    database: Session,
    context_checkpoint_id: UUID | None,
    source_work_item_id: UUID,
    target_work_item_id: UUID,
) -> UUID | None:
    if context_checkpoint_id is None:
        return None
    owner = database.scalar(
        select(Checkpoint.work_item_id).where(
            Checkpoint.id == context_checkpoint_id,
            Checkpoint.work_item_id.in_([source_work_item_id, target_work_item_id]),
        )
    )
    if owner is None:
        raise not_found(
            "checkpoint_not_found",
            "The relationship context checkpoint was not found on either endpoint.",
        )
    return owner


def _would_create_cycle(
    database: Session,
    project_id: UUID,
    relationship_type: str,
    source_work_item_id: UUID,
    target_work_item_id: UUID,
) -> bool:
    if relationship_type not in CYCLE_TYPES:
        return False
    return bool(
        database.scalar(
            text(
                """
                WITH RECURSIVE reachable(work_item_id) AS (
                    SELECT target_work_item_id
                    FROM work_relationships
                    WHERE project_id = :project_id
                      AND relationship_type = :relationship_type
                      AND source_work_item_id = :target_work_item_id
                    UNION
                    SELECT edge.target_work_item_id
                    FROM work_relationships AS edge
                    JOIN reachable
                      ON reachable.work_item_id = edge.source_work_item_id
                    WHERE edge.project_id = :project_id
                      AND edge.relationship_type = :relationship_type
                )
                SELECT EXISTS (
                    SELECT 1
                    FROM reachable
                    WHERE work_item_id = :source_work_item_id
                )
                """
            ),
            {
                "project_id": project_id,
                "relationship_type": relationship_type,
                "source_work_item_id": source_work_item_id,
                "target_work_item_id": target_work_item_id,
            },
        )
    )


def stage_relationship_locked(
    database: Session,
    *,
    project_id: UUID,
    relationship_type: str,
    source_work_item_id: UUID,
    target_work_item_id: UUID,
    created_by_client: str,
    created_by_session_id: str,
    created_by_model: str | None,
    context_checkpoint_id: UUID | None,
    locked_work_items: dict[UUID, WorkItem],
) -> tuple[WorkRelationship, bool]:
    """Validate and stage an edge after the project and endpoints are locked."""
    source_work_item_id, target_work_item_id = normalize_endpoints(
        relationship_type, source_work_item_id, target_work_item_id
    )
    if source_work_item_id not in locked_work_items or target_work_item_id not in locked_work_items:
        raise not_found("work_item_not_found", "Work item not found.")

    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, locked_work_items[source_work_item_id])
    require_canonical_work_item(database, locked_work_items[target_work_item_id])
    if relationship_type == "duplicate-of":
        raise duplicate_merge_required()

    existing = database.scalar(
        select(WorkRelationship).where(
            WorkRelationship.project_id == project_id,
            WorkRelationship.relationship_type == relationship_type,
            WorkRelationship.source_work_item_id == source_work_item_id,
            WorkRelationship.target_work_item_id == target_work_item_id,
        )
    )
    if existing is not None:
        return existing, False

    if relationship_type == "parent-child":
        current_parent = database.scalar(
            select(WorkRelationship.id).where(
                WorkRelationship.project_id == project_id,
                WorkRelationship.relationship_type == "parent-child",
                WorkRelationship.target_work_item_id == target_work_item_id,
            )
        )
        if current_parent is not None:
            raise conflict(
                "parent_already_set",
                "This work item already has a parent.",
            )

    context_owner = _context_owner(
        database,
        context_checkpoint_id,
        source_work_item_id,
        target_work_item_id,
    )
    if relationship_type == "discovered-from":
        if context_checkpoint_id is None:
            raise conflict(
                "relationship_context_required",
                "discovered-from requires an originating checkpoint.",
            )
        if context_owner != target_work_item_id:
            raise conflict(
                "relationship_context_invalid",
                "Discovery context must belong to the originating target work item.",
            )

    if _would_create_cycle(
        database,
        project_id,
        relationship_type,
        source_work_item_id,
        target_work_item_id,
    ):
        raise conflict(
            "relationship_cycle",
            "That relationship would create a cycle.",
        )

    relationship = WorkRelationship(
        project_id=project_id,
        relationship_type=relationship_type,
        source_work_item_id=source_work_item_id,
        target_work_item_id=target_work_item_id,
        context_checkpoint_work_item_id=context_owner,
        context_checkpoint_id=context_checkpoint_id,
        created_by_client=created_by_client,
        created_by_session_id=created_by_session_id,
        created_by_model=created_by_model,
    )
    database.add(relationship)
    database.flush()
    return relationship, True


def stage_merge_relationship_locked(
    database: Session,
    *,
    relationship_id: UUID,
    merge_id: UUID,
    project_id: UUID,
    source_work_item_id: UUID,
    destination_work_item_id: UUID,
    created_by_client: str,
    created_by_session_id: str,
    created_by_model: str | None,
    created_at: datetime,
    locked_work_items: dict[UUID, WorkItem],
) -> tuple[WorkRelationship, bool]:
    """Reuse or stage the exact merge witness after graph/endpoints are already locked."""
    if set((source_work_item_id, destination_work_item_id)) - set(locked_work_items):
        raise not_found("work_item_not_found", "Work item not found.")
    existing = database.scalar(
        select(WorkRelationship)
        .where(
            WorkRelationship.project_id == project_id,
            WorkRelationship.relationship_type == "duplicate-of",
            WorkRelationship.source_work_item_id == source_work_item_id,
            WorkRelationship.target_work_item_id == destination_work_item_id,
        )
        .with_for_update()
    )
    if existing is not None:
        return existing, False
    relationship = WorkRelationship(
        id=relationship_id,
        project_id=project_id,
        relationship_type="duplicate-of",
        source_work_item_id=source_work_item_id,
        target_work_item_id=destination_work_item_id,
        context_checkpoint_work_item_id=None,
        context_checkpoint_id=None,
        created_by_client=created_by_client,
        created_by_session_id=created_by_session_id,
        created_by_model=created_by_model,
        created_for_duplicate_merge_id=merge_id,
        created_at=created_at,
    )
    database.add(relationship)
    database.flush()
    return relationship, True


def add_relationship_record(
    database: Session,
    project_id: UUID,
    payload: RelationshipCreate,
) -> RelationshipCreationResult:
    lock_project_graph(database, project_id)
    source_id, target_id = normalize_endpoints(
        payload.relationship_type,
        payload.source_work_item_id,
        payload.target_work_item_id,
    )
    locked = lock_endpoint_work_items(database, project_id, [source_id, target_id])
    relationship, created = stage_relationship_locked(
        database,
        project_id=project_id,
        relationship_type=payload.relationship_type,
        source_work_item_id=source_id,
        target_work_item_id=target_id,
        created_by_client=payload.created_by_client,
        created_by_session_id=payload.created_by_session_id,
        created_by_model=payload.created_by_model,
        context_checkpoint_id=payload.context_checkpoint_id,
        locked_work_items=locked,
    )
    if created:
        actor = source_actor(
            relationship.created_by_client,
            relationship.created_by_session_id,
            relationship.created_by_model,
        )
        stage_relationship_events(
            database,
            relationship,
            action="added",
            actor=actor,
            created_at=relationship.created_at,
        )
        database.flush()
    return RelationshipCreationResult(
        relationship=relationship_edge(relationship),
        created=created,
    )


def require_relationship(
    database: Session, project_id: UUID, relationship_id: UUID
) -> WorkRelationship:
    relationship = database.scalar(
        select(WorkRelationship).where(
            WorkRelationship.id == relationship_id,
            WorkRelationship.project_id == project_id,
        )
    )
    if relationship is None:
        raise not_found("relationship_not_found", "Relationship not found.")
    return relationship


def remove_relationship_record(
    database: Session,
    project_id: UUID,
    relationship_id: UUID,
    actor: MutationActor | None = None,
) -> RelationshipRemovalResult:
    lock_project_graph(database, project_id)
    relationship = database.scalar(
        select(WorkRelationship).where(
            WorkRelationship.id == relationship_id,
            WorkRelationship.project_id == project_id,
        )
    )
    if relationship is None:
        return RelationshipRemovalResult(
            project_id=project_id,
            relationship_id=relationship_id,
            removed=False,
        )
    lock_endpoint_work_items(
        database,
        project_id,
        [relationship.source_work_item_id, relationship.target_work_item_id],
    )
    from mnemonic_api.services.duplicates import is_duplicate_work_item

    if is_duplicate_work_item(database, relationship.source_work_item_id) or is_duplicate_work_item(
        database, relationship.target_work_item_id
    ):
        from mnemonic_api.errors import duplicate_relationship_frozen

        raise duplicate_relationship_frozen()
    relationship = database.scalar(
        select(WorkRelationship)
        .where(
            WorkRelationship.id == relationship_id,
            WorkRelationship.project_id == project_id,
        )
        .with_for_update()
    )
    if relationship is None:
        return RelationshipRemovalResult(
            project_id=project_id,
            relationship_id=relationship_id,
            removed=False,
        )
    mutation_time = database_now(database)
    stage_relationship_events(
        database,
        relationship,
        action="removed",
        actor=actor,
        created_at=mutation_time,
    )
    database.flush()
    database.delete(relationship)
    database.flush()
    return RelationshipRemovalResult(
        project_id=project_id,
        relationship_id=relationship_id,
        removed=True,
    )


def _work_pointers(
    database: Session,
    work_item_ids: Sequence[UUID],
    *,
    as_of: datetime | None = None,
) -> dict[UUID, WorkPointer]:
    if not work_item_ids:
        return {}
    work_items = list(
        database.scalars(
            select(WorkItem).where(
                WorkItem.id.in_(work_item_ids),
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    (
        blocker_counts,
        gate_counts,
        active_leases,
        dropped_lease_ids,
        canonical_ids,
    ) = readiness_inputs(database, work_item_ids, as_of=as_of)
    return {
        work_item.id: WorkPointer(
            id=work_item.id,
            title=work_item.title,
            external_references=[ExternalReference.model_validate(item)
                                 for item in work_item.external_references],
            status=work_item.status,
            readiness=readiness(
                work_item,
                active_leases.get(work_item.id),
                blocker_counts.get(work_item.id, 0),
                work_item.id in dropped_lease_ids,
                gate_counts.get(work_item.id, 0),
                canonical_work_item_id=canonical_ids.get(work_item.id, work_item.id),
            ),
        )
        for work_item in work_items
    }


def adjacent_relationship(
    relationship: WorkRelationship,
    relative_to_work_item_id: UUID,
    counterpart: WorkPointer,
) -> AdjacentRelationshipRead:
    if relationship.relationship_type == "related":
        direction = "undirected"
    elif relationship.target_work_item_id == relative_to_work_item_id:
        direction = "incoming"
    else:
        direction = "outgoing"
    return AdjacentRelationshipRead(
        relationship=relationship_edge(relationship),
        relative_to_work_item_id=relative_to_work_item_id,
        direction=direction,
        counterpart=counterpart,
    )


def list_adjacent_relationships(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    filters: RelationshipListQuery,
) -> tuple[list[AdjacentRelationshipRead], int]:
    from mnemonic_api.services.work_items import require_work_item

    begin_coherent_read(database)
    require_work_item(database, project_id, work_item_id)
    as_of = database.scalar(select(func.transaction_timestamp()))
    if as_of is None:
        raise RuntimeError("Database did not provide a transaction timestamp")
    if filters.direction == "incoming":
        adjacency = (
            WorkRelationship.target_work_item_id == work_item_id,
            WorkRelationship.relationship_type != "related",
        )
    elif filters.direction == "outgoing":
        adjacency = (
            WorkRelationship.source_work_item_id == work_item_id,
            WorkRelationship.relationship_type != "related",
        )
    elif filters.direction == "undirected":
        adjacency = (
            WorkRelationship.relationship_type == "related",
            or_(
                WorkRelationship.source_work_item_id == work_item_id,
                WorkRelationship.target_work_item_id == work_item_id,
            ),
        )
    else:
        adjacency = (
            or_(
                WorkRelationship.source_work_item_id == work_item_id,
                WorkRelationship.target_work_item_id == work_item_id,
            ),
        )
    conditions = [WorkRelationship.project_id == project_id, *adjacency]
    if filters.type is not None:
        conditions.append(WorkRelationship.relationship_type == filters.type)
    total = (
        database.scalar(select(func.count()).select_from(WorkRelationship).where(*conditions)) or 0
    )
    relationships = list(
        database.scalars(
            select(WorkRelationship)
            .where(*conditions)
            .order_by(WorkRelationship.created_at, WorkRelationship.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
    )
    counterpart_ids = [
        relationship.target_work_item_id
        if relationship.source_work_item_id == work_item_id
        else relationship.source_work_item_id
        for relationship in relationships
    ]
    pointers = _work_pointers(database, counterpart_ids, as_of=as_of)
    return [
        adjacent_relationship(
            relationship,
            work_item_id,
            pointers[
                relationship.target_work_item_id
                if relationship.source_work_item_id == work_item_id
                else relationship.source_work_item_id
            ],
        )
        for relationship in relationships
    ], int(total)
