"""Concurrency-safe project graph mutations and bounded relationship projections."""

from collections.abc import Iterable, Sequence
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict, not_found
from mnemonic_api.models import (
    Checkpoint,
    Project,
    WorkItem,
    WorkLease,
    WorkRelationship,
)
from mnemonic_api.schemas import (
    AdjacentRelationshipRead,
    ChildrenListQuery,
    HierarchySummary,
    MutationActor,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipListQuery,
    RelationshipRemovalResult,
    WorkIdentityPointer,
    WorkItemListQuery,
    WorkPointer,
)
from mnemonic_api.services.readiness import (
    readiness,
    unresolved_blocker_counts,
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


def _work_pointers(database: Session, work_item_ids: Sequence[UUID]) -> dict[UUID, WorkPointer]:
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
    active_leases = {
        lease.work_item_id: lease
        for lease in database.scalars(
            select(WorkLease).where(
                WorkLease.work_item_id.in_(work_item_ids),
                WorkLease.expires_at > func.clock_timestamp(),
            )
        )
    }
    blocker_counts = unresolved_blocker_counts(database, work_item_ids)
    return {
        work_item.id: WorkPointer(
            id=work_item.id,
            title=work_item.title,
            status=work_item.status,
            readiness=readiness(
                work_item,
                active_leases.get(work_item.id),
                blocker_counts.get(work_item.id, 0),
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

    require_work_item(database, project_id, work_item_id)
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
    pointers = _work_pointers(database, counterpart_ids)
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


def _hierarchy_match_sql(
    filters: WorkItemListQuery | ChildrenListQuery,
) -> tuple[str, dict[str, object]]:
    conditions = ["candidate.deleted_at IS NULL"]
    parameters: dict[str, object] = {}
    if filters.status == "active":
        conditions.append(
            "candidate.status = 'open' AND EXISTS ("
            "SELECT 1 FROM work_leases AS filter_lease "
            "WHERE filter_lease.work_item_id = candidate.id "
            "AND filter_lease.expires_at > clock_timestamp()"
            ")"
        )
    elif filters.status == "dropped":
        conditions.append(
            "candidate.status = 'open' AND EXISTS ("
            "SELECT 1 FROM work_leases AS filter_lease "
            "WHERE filter_lease.work_item_id = candidate.id "
            "AND filter_lease.expires_at <= clock_timestamp()"
            ")"
        )
    elif filters.status != "all":
        conditions.append("candidate.status = :filter_status")
        parameters["filter_status"] = filters.status

    checkpoint_conditions: list[str] = []
    if filters.tag is not None:
        checkpoint_conditions.append(
            "EXISTS ("
            "SELECT 1 FROM unnest(filter_checkpoint.tags) AS candidate_tag(value) "
            "WHERE lower(candidate_tag.value) = :filter_tag"
            ")"
        )
        parameters["filter_tag"] = filters.tag
    if filters.source_client is not None:
        checkpoint_conditions.append("filter_checkpoint.source_client = :filter_source_client")
        parameters["filter_source_client"] = filters.source_client
    if filters.source_session_id is not None:
        checkpoint_conditions.append(
            "filter_checkpoint.source_session_id = :filter_source_session_id"
        )
        parameters["filter_source_session_id"] = filters.source_session_id
    if checkpoint_conditions:
        conditions.append(
            "EXISTS (SELECT 1 FROM checkpoints AS filter_checkpoint "
            "WHERE filter_checkpoint.work_item_id = candidate.id AND "
            + " AND ".join(checkpoint_conditions)
            + ")"
        )
    return " AND ".join(conditions), parameters


def hierarchy_page(
    database: Session,
    project_id: UUID,
    filters: WorkItemListQuery | ChildrenListQuery,
    *,
    parent_work_item_id: UUID | None = None,
) -> tuple[list[HierarchySummary], int]:
    """Return qualifying roots/direct branches with any-depth filter metadata."""
    from mnemonic_api.services.work_context import work_summaries
    from mnemonic_api.services.work_items import require_project, require_work_item

    if parent_work_item_id is None:
        require_project(database, project_id)
        candidate_sql = """
            SELECT root.id
            FROM work_items AS root
            WHERE root.project_id = :project_id
              AND root.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM work_relationships AS parent_edge
                  WHERE parent_edge.project_id = :project_id
                    AND parent_edge.relationship_type = 'parent-child'
                    AND parent_edge.target_work_item_id = root.id
              )
        """
    else:
        require_work_item(database, project_id, parent_work_item_id)
        candidate_sql = """
            SELECT child.id
            FROM work_relationships AS child_edge
            JOIN work_items AS child
              ON child.id = child_edge.target_work_item_id
             AND child.project_id = :project_id
             AND child.deleted_at IS NULL
            WHERE child_edge.project_id = :project_id
              AND child_edge.relationship_type = 'parent-child'
              AND child_edge.source_work_item_id = :parent_work_item_id
        """

    root_ordering = {
        "updated": "root.updated_at DESC, root.id DESC",
        "created": "root.created_at DESC, root.id DESC",
        "priority": "root.priority DESC, root.updated_at DESC, root.id DESC",
    }[filters.sort]
    page_ordering = {
        "updated": "paged.updated_at DESC, paged.root_id DESC",
        "created": "paged.created_at DESC, paged.root_id DESC",
        "priority": "paged.priority DESC, paged.updated_at DESC, paged.root_id DESC",
    }[filters.sort]
    match_sql, match_parameters = _hierarchy_match_sql(filters)
    row = (
        database.execute(
            text(
                f"""
            WITH RECURSIVE candidate_roots AS (
                {candidate_sql}
            ),
            subtree(root_id, work_item_id) AS (
                SELECT id, id
                FROM candidate_roots
                UNION
                SELECT subtree.root_id, child_edge.target_work_item_id
                FROM subtree
                JOIN work_relationships AS child_edge
                  ON child_edge.project_id = :project_id
                 AND child_edge.relationship_type = 'parent-child'
                 AND child_edge.source_work_item_id = subtree.work_item_id
                JOIN work_items AS visible_child
                  ON visible_child.id = child_edge.target_work_item_id
                 AND visible_child.deleted_at IS NULL
            ),
            matches AS (
                SELECT candidate.id
                FROM work_items AS candidate
                WHERE candidate.project_id = :project_id
                  AND {match_sql}
            ),
            qualifying AS (
                SELECT
                    subtree.root_id,
                    bool_or(
                        subtree.work_item_id = subtree.root_id
                        AND matches.id IS NOT NULL
                    ) AS self_matches_filter,
                    bool_or(
                        subtree.work_item_id <> subtree.root_id
                        AND matches.id IS NOT NULL
                    ) AS has_matching_descendants
                FROM subtree
                LEFT JOIN matches ON matches.id = subtree.work_item_id
                GROUP BY subtree.root_id
                HAVING bool_or(matches.id IS NOT NULL)
            ),
            paged AS (
                SELECT qualifying.*, root.updated_at, root.created_at, root.priority
                FROM qualifying
                JOIN work_items AS root ON root.id = qualifying.root_id
                ORDER BY {root_ordering}
                LIMIT :limit OFFSET :offset
            )
            SELECT
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'work_item_id', paged.root_id,
                            'self_matches_filter', paged.self_matches_filter,
                            'has_matching_descendants', paged.has_matching_descendants
                        )
                        ORDER BY {page_ordering}
                    ),
                    '[]'::jsonb
                ) AS items,
                (SELECT count(*) FROM qualifying) AS total
            FROM paged
            """
            ),
            {
                "project_id": project_id,
                "parent_work_item_id": parent_work_item_id,
                "limit": filters.limit,
                "offset": filters.offset,
                **match_parameters,
            },
        )
        .mappings()
        .one()
    )
    item_rows = list(row["items"])
    ids = [UUID(str(item["work_item_id"])) for item in item_rows]
    work_by_id = {
        work_item.id: work_item
        for work_item in database.scalars(select(WorkItem).where(WorkItem.id.in_(ids)))
    }
    summaries = {
        summary.work_item.id: summary
        for summary in work_summaries(database, [work_by_id[work_item_id] for work_item_id in ids])
    }
    return [
        HierarchySummary(
            summary=summaries[UUID(str(item["work_item_id"]))],
            self_matches_filter=bool(item["self_matches_filter"]),
            has_matching_descendants=bool(item["has_matching_descendants"]),
        )
        for item in item_rows
    ], int(row["total"])


def ancestor_paths(
    database: Session, project_id: UUID, work_item_ids: Sequence[UUID]
) -> tuple[dict[UUID, list[WorkIdentityPointer]], set[UUID]]:
    if not work_item_ids:
        return {}, set()
    rows = database.execute(
        text(
            """
            WITH RECURSIVE ancestors(work_item_id, ancestor_id, depth) AS (
                SELECT edge.target_work_item_id, edge.source_work_item_id, 1
                FROM work_relationships AS edge
                WHERE edge.project_id = :project_id
                  AND edge.relationship_type = 'parent-child'
                  AND edge.target_work_item_id = ANY(CAST(:work_item_ids AS uuid[]))
                UNION ALL
                SELECT ancestors.work_item_id, edge.source_work_item_id, ancestors.depth + 1
                FROM ancestors
                JOIN work_relationships AS edge
                  ON edge.project_id = :project_id
                 AND edge.relationship_type = 'parent-child'
                 AND edge.target_work_item_id = ancestors.ancestor_id
                WHERE ancestors.depth < 51
            )
            SELECT
                ancestors.work_item_id,
                ancestors.depth,
                ancestor.id,
                ancestor.title,
                ancestor.status
            FROM ancestors
            JOIN work_items AS ancestor
              ON ancestor.id = ancestors.ancestor_id
             AND ancestor.deleted_at IS NULL
            ORDER BY ancestors.work_item_id, ancestors.depth DESC
            """
        ),
        {"project_id": project_id, "work_item_ids": list(work_item_ids)},
    ).mappings()
    paths: dict[UUID, list[WorkIdentityPointer]] = {
        work_item_id: [] for work_item_id in work_item_ids
    }
    truncated: set[UUID] = set()
    for row in rows:
        work_item_id = UUID(str(row["work_item_id"]))
        if int(row["depth"]) > 50:
            truncated.add(work_item_id)
            continue
        paths[work_item_id].append(
            WorkIdentityPointer(id=row["id"], title=row["title"], status=row["status"])
        )
    return paths, truncated
