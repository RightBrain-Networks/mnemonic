"""Authoritative duplicate forest resolution and irreversible merge execution."""

from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import (
    client_operation_secret_echo,
    conflict,
    duplicate_context_changed,
    duplicate_depth_exceeded,
    duplicate_destination_not_canonical,
    duplicate_graph_invalid,
    duplicate_self,
    duplicate_source_gate_unresolved,
    duplicate_structural_relationships,
    work_already_duplicate,
    work_duplicate,
)
from mnemonic_api.models import (
    Checkpoint,
    WorkDuplicateMerge,
    WorkEvent,
    WorkGate,
    WorkItem,
    WorkLease,
    WorkRelationship,
)
from mnemonic_api.schemas import (
    CanonicalWorkProjection,
    DuplicateMergeEligibility,
    MergeReviewRevision,
    MutationActor,
    WorkIdentityPointer,
    WorkItemDetailRead,
    WorkItemRead,
    WorkMergeRead,
    WorkMergeRequest,
    WorkMergeResult,
)
from mnemonic_api.services.leases import consume_lease_for_terminal_mutation
from mnemonic_api.services.relationships import (
    lock_endpoint_work_items,
    lock_project_graph,
    relationship_edge,
    stage_merge_relationship_locked,
)
from mnemonic_api.services.work_events import (
    database_now,
    source_actor,
    stage_relationship_events,
    stage_work_merged_events,
    work_event_read,
)

MAX_CANONICAL_DEPTH = 50
MAX_CONTEXT_MEMBERS = 20


@dataclass(frozen=True)
class MergeEdge:
    destination_id: UUID
    sequence: int


@dataclass(frozen=True)
class CanonicalGraph:
    edges: dict[UUID, MergeEdge]
    incoming: dict[UUID, tuple[UUID, ...]]
    pointers: dict[UUID, WorkIdentityPointer]


@dataclass(frozen=True)
class CanonicalGroupSnapshot:
    """Validated root membership without repeatedly traversing reverse subtrees."""

    root_by_member: dict[UUID, UUID]
    duplicate_member_count_by_root: dict[UUID, int]


def _identity_pointer(work_item: WorkItem | WorkItemRead) -> WorkIdentityPointer:
    return WorkIdentityPointer(id=work_item.id, title=work_item.title, status=work_item.status)


def _load_graph(
    database: Session,
    project_id: UUID,
    extra_work_items: Sequence[WorkItem | WorkItemRead] = (),
) -> CanonicalGraph:
    rows = list(
        database.execute(
            select(
                WorkDuplicateMerge.source_work_item_id,
                WorkDuplicateMerge.destination_work_item_id,
                WorkDuplicateMerge.merge_sequence,
            )
            .where(WorkDuplicateMerge.project_id == project_id)
            .order_by(WorkDuplicateMerge.merge_sequence, WorkDuplicateMerge.id)
        )
    )
    edges: dict[UUID, MergeEdge] = {}
    incoming_lists: dict[UUID, list[UUID]] = defaultdict(list)
    ids = {item.id for item in extra_work_items}
    for source_id, destination_id, sequence in rows:
        if source_id in edges or source_id == destination_id:
            raise duplicate_graph_invalid()
        edges[source_id] = MergeEdge(destination_id, int(sequence))
        incoming_lists[destination_id].append(source_id)
        ids.update((source_id, destination_id))
    work_items = list(database.scalars(select(WorkItem).where(WorkItem.id.in_(ids)))) if ids else []
    pointers = {
        item.id: _identity_pointer(item)
        for item in work_items
        if item.project_id == project_id and item.deleted_at is None
    }
    pointers.update({item.id: _identity_pointer(item) for item in extra_work_items})
    if any(
        source not in pointers or edge.destination_id not in pointers
        for source, edge in edges.items()
    ):
        raise duplicate_graph_invalid()
    incoming = {
        destination: tuple(
            sorted(sources, key=lambda source: (edges[source].sequence, str(source)))
        )
        for destination, sources in incoming_lists.items()
    }
    graph = CanonicalGraph(edges=edges, incoming=incoming, pointers=pointers)
    for source_id in graph.edges:
        _canonical_path(graph, source_id)
    return graph


def _canonical_path(graph: CanonicalGraph, requested_id: UUID) -> tuple[UUID, ...]:
    path: list[UUID] = []
    visited = {requested_id}
    current_id = requested_id
    while current_id in graph.edges:
        destination_id = graph.edges[current_id].destination_id
        if destination_id in visited or destination_id not in graph.pointers:
            raise duplicate_graph_invalid()
        path.append(destination_id)
        if len(path) > MAX_CANONICAL_DEPTH:
            raise duplicate_graph_invalid()
        visited.add(destination_id)
        current_id = destination_id
    return tuple(path)


def _member_ids(graph: CanonicalGraph, root_id: UUID) -> tuple[UUID, ...]:
    members: list[UUID] = []
    seen = {root_id}
    queue: deque[tuple[UUID, int]] = deque([(root_id, 0)])
    while queue:
        destination_id, depth = queue.popleft()
        for source_id in graph.incoming.get(destination_id, ()):
            if source_id in seen or depth + 1 > MAX_CANONICAL_DEPTH:
                raise duplicate_graph_invalid()
            seen.add(source_id)
            members.append(source_id)
            queue.append((source_id, depth + 1))
    return tuple(sorted(members, key=lambda item: (graph.edges[item].sequence, str(item))))


def canonical_projection(
    database: Session,
    project_id: UUID,
    work_item: WorkItem | WorkItemRead,
    *,
    graph: CanonicalGraph | None = None,
) -> CanonicalWorkProjection:
    snapshot = graph or _load_graph(database, project_id, [work_item])
    if work_item.id not in snapshot.pointers:
        snapshot.pointers[work_item.id] = _identity_pointer(work_item)
    path_ids = _canonical_path(snapshot, work_item.id)
    root_id = path_ids[-1] if path_ids else work_item.id
    members = _member_ids(snapshot, root_id)
    path = [snapshot.pointers[item_id] for item_id in path_ids]
    return CanonicalWorkProjection(
        is_duplicate=bool(path),
        direct_destination=path[0] if path else None,
        canonical_work_item=snapshot.pointers[root_id],
        path=path,
        duplicate_member_count=len(members),
    )


def canonical_projections(
    database: Session,
    project_id: UUID,
    work_items: Sequence[WorkItem],
) -> dict[UUID, CanonicalWorkProjection]:
    graph = _load_graph(database, project_id, work_items)
    return {
        work_item.id: canonical_projection(database, project_id, work_item, graph=graph)
        for work_item in work_items
    }


def canonical_group_snapshot(
    database: Session,
    project_id: UUID,
    work_items: Sequence[WorkItem],
) -> CanonicalGroupSnapshot:
    graph = _load_graph(database, project_id, work_items)
    root_by_member: dict[UUID, UUID] = {}
    member_counts: dict[UUID, int] = defaultdict(int)
    for work_item in work_items:
        path = _canonical_path(graph, work_item.id)
        root_id = path[-1] if path else work_item.id
        root_by_member[work_item.id] = root_id
        member_counts.setdefault(root_id, 0)
        if work_item.id != root_id:
            member_counts[root_id] += 1
    return CanonicalGroupSnapshot(
        root_by_member=root_by_member,
        duplicate_member_count_by_root=dict(member_counts),
    )


def canonical_work_item_ids(
    database: Session,
    work_item_ids: Sequence[UUID],
) -> dict[UUID, UUID]:
    if not work_item_ids:
        return {}
    work_items = list(
        database.scalars(
            select(WorkItem).where(
                WorkItem.id.in_(set(work_item_ids)),
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    by_project: dict[UUID, list[WorkItem]] = defaultdict(list)
    for work_item in work_items:
        by_project[work_item.project_id].append(work_item)
    canonical: dict[UUID, UUID] = {}
    for project_id, project_items in by_project.items():
        projections = canonical_projections(database, project_id, project_items)
        canonical.update(
            {
                work_item.id: projections[work_item.id].canonical_work_item.id
                for work_item in project_items
            }
        )
    return canonical


def validate_project_duplicate_graph(database: Session, project_id: UUID) -> None:
    """Fail closed before a public response relies on canonical identity."""
    _load_graph(database, project_id)


def work_item_detail(
    database: Session,
    project_id: UUID,
    work_item: WorkItem,
) -> WorkItemDetailRead:
    from mnemonic_api.services.code_review_reads import review_context

    return WorkItemDetailRead(
        code_review_context=review_context(database, work_item.id),
        work_item=WorkItemRead.model_validate(work_item),
        canonical=canonical_projection(database, project_id, work_item),
    )


def is_duplicate_work_item(database: Session, work_item_id: UUID) -> bool:
    return database.scalar(
        select(WorkDuplicateMerge.id)
        .where(WorkDuplicateMerge.source_work_item_id == work_item_id)
        .limit(1)
    ) is not None


def require_canonical_work_item(database: Session, work_item: WorkItem) -> None:
    direct = database.scalar(
        select(WorkDuplicateMerge.destination_work_item_id).where(
            WorkDuplicateMerge.project_id == work_item.project_id,
            WorkDuplicateMerge.source_work_item_id == work_item.id,
        )
    )
    if direct is None:
        return
    projection = canonical_projection(database, work_item.project_id, work_item)
    raise work_duplicate(projection.canonical_work_item.id)


def require_no_duplicate_membership(database: Session, work_item: WorkItem) -> None:
    membership = database.scalar(
        select(WorkDuplicateMerge.id)
        .where(
            WorkDuplicateMerge.project_id == work_item.project_id,
            or_(
                WorkDuplicateMerge.source_work_item_id == work_item.id,
                WorkDuplicateMerge.destination_work_item_id == work_item.id,
            ),
        )
        .limit(1)
    )
    if membership is not None:
        raise conflict(
            "work_move_duplicate_membership",
            "Duplicate-group work cannot move between projects.",
        )


def merge_review_revisions(
    database: Session,
    work_item_ids: Iterable[UUID],
) -> dict[UUID, MergeReviewRevision]:
    ids = set(work_item_ids)
    if not ids:
        return {}
    current_checkpoint = (
        select(Checkpoint.id)
        .where(Checkpoint.work_item_id == WorkItem.id, Checkpoint.kind == "context")
        .order_by(Checkpoint.created_at.desc(), Checkpoint.id.desc())
        .limit(1)
        .correlate(WorkItem)
        .scalar_subquery()
    )
    event_count = (
        select(func.count())
        .select_from(WorkEvent)
        .where(WorkEvent.work_item_id == WorkItem.id)
        .correlate(WorkItem)
        .scalar_subquery()
    )
    rows = database.execute(
        select(
            WorkItem.id,
            WorkItem.version,
            current_checkpoint.label("context_checkpoint_id"),
            event_count.label("work_event_count"),
        ).where(WorkItem.id.in_(ids))
    )
    revisions: dict[UUID, MergeReviewRevision] = {}
    for row in rows:
        if row.context_checkpoint_id is None or int(row.work_event_count) < 1:
            raise duplicate_graph_invalid()
        revisions[row.id] = MergeReviewRevision(
            work_version=row.version,
            context_checkpoint_id=row.context_checkpoint_id,
            work_event_count=int(row.work_event_count),
        )
    if set(revisions) != ids:
        raise duplicate_graph_invalid()
    return revisions


def merge_review_revision(database: Session, work_item_id: UUID) -> MergeReviewRevision:
    return merge_review_revisions(database, [work_item_id])[work_item_id]


def duplicate_members_for_context(
    database: Session,
    project_id: UUID,
    work_item: WorkItem | WorkItemRead,
) -> tuple[CanonicalWorkProjection, list[WorkIdentityPointer], int]:
    graph = _load_graph(database, project_id, [work_item])
    projection = canonical_projection(database, project_id, work_item, graph=graph)
    root_id = projection.canonical_work_item.id
    member_ids = list(_member_ids(graph, root_id))
    if projection.is_duplicate:
        member_ids.remove(work_item.id)
        member_ids.insert(0, work_item.id)
    members = [graph.pointers[item_id] for item_id in member_ids[:MAX_CONTEXT_MEMBERS]]
    return projection, members, len(member_ids)


def duplicate_merge_eligibility(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    *,
    as_of: datetime | None = None,
) -> DuplicateMergeEligibility:
    rows = dict(
        database.execute(
            select(WorkRelationship.relationship_type, func.count())
            .where(
                WorkRelationship.project_id == project_id,
                WorkRelationship.relationship_type.in_(("blocks", "parent-child")),
                or_(
                    WorkRelationship.source_work_item_id == work_item_id,
                    WorkRelationship.target_work_item_id == work_item_id,
                ),
            )
            .group_by(WorkRelationship.relationship_type)
        ).tuples().all()
    )
    has_gate = database.scalar(
        select(WorkGate.id)
        .where(WorkGate.work_item_id == work_item_id, WorkGate.resolved_at.is_(None))
        .limit(1)
    ) is not None
    lease = database.get(WorkLease, work_item_id)
    now = as_of or database_now(database)
    lease_state = "none" if lease is None else ("active" if lease.expires_at > now else "expired")
    return DuplicateMergeEligibility(
        incident_blocks_count=int(rows.get("blocks", 0)),
        incident_parent_child_count=int(rows.get("parent-child", 0)),
        has_unresolved_gate=has_gate,
        source_lease_state=lease_state,
    )


def _uuid_spellings(value: UUID) -> frozenset[str]:
    canonical = str(value)
    urn = f"urn:uuid:{canonical}"
    return frozenset(
        {
            canonical,
            value.hex,
            urn,
            "{" + canonical + "}",
        }
    )


def reject_merge_secret_echo(
    payload: WorkMergeRequest,
    *,
    bearer_key: str,
    client_operation_id: UUID,
) -> None:
    exact_controls = {bearer_key}
    if payload.lease_token is not None:
        exact_controls.add(payload.lease_token)
    operation_spellings = _uuid_spellings(client_operation_id)
    durable_values = (
        payload.rationale,
        payload.merged_by_client,
        payload.merged_by_session_id,
        payload.merged_by_model or "",
    )
    if any(
        any(control and control in value for control in exact_controls)
        or any(spelling in value.casefold() for spelling in operation_spellings)
        for value in durable_values
    ):
        raise client_operation_secret_echo()


def _require_merge_preconditions(
    database: Session,
    source: WorkItem,
    destination: WorkItem,
    payload: WorkMergeRequest,
    graph: CanonicalGraph,
) -> None:
    from mnemonic_api.services.code_reviews import require_no_review_obligation

    if source.remediation_depth or destination.remediation_depth:
        raise conflict("code_review_provenance_merge_forbidden", "Remediation cannot be merged.")
    require_no_review_obligation(database, source.id)
    require_no_review_obligation(database, destination.id)
    if source.id in graph.edges:
        raise work_already_duplicate()
    if destination.id in graph.edges:
        raise duplicate_destination_not_canonical()
    source_depth = max(
        (len(_canonical_path(graph, member_id)) for member_id in _member_ids(graph, source.id)),
        default=0,
    )
    destination_depth = max(
        (
            len(_canonical_path(graph, member_id))
            for member_id in _member_ids(graph, destination.id)
        ),
        default=0,
    )
    if source_depth + 1 > MAX_CANONICAL_DEPTH or destination_depth > MAX_CANONICAL_DEPTH:
        raise duplicate_depth_exceeded()
    current = merge_review_revisions(database, (source.id, destination.id))
    if (
        current[source.id] != payload.reviewed_source_revision
        or current[destination.id] != payload.reviewed_destination_revision
    ):
        raise duplicate_context_changed()
    eligibility = duplicate_merge_eligibility(database, source.project_id, source.id)
    if eligibility.has_unresolved_gate:
        raise duplicate_source_gate_unresolved()
    if eligibility.incident_blocks_count or eligibility.incident_parent_child_count:
        raise duplicate_structural_relationships()


def _merge_read(merge: WorkDuplicateMerge) -> WorkMergeRead:
    return WorkMergeRead(
        id=merge.id,
        merge_sequence=merge.merge_sequence,
        project_id=merge.project_id,
        source_work_item_id=merge.source_work_item_id,
        destination_work_item_id=merge.destination_work_item_id,
        duplicate_relationship_id=merge.duplicate_relationship_id,
        reviewed_source_revision=MergeReviewRevision(
            work_version=merge.reviewed_source_work_version,
            context_checkpoint_id=merge.reviewed_source_context_checkpoint_id,
            work_event_count=merge.reviewed_source_work_event_count,
        ),
        reviewed_destination_revision=MergeReviewRevision(
            work_version=merge.reviewed_destination_work_version,
            context_checkpoint_id=merge.reviewed_destination_context_checkpoint_id,
            work_event_count=merge.reviewed_destination_work_event_count,
        ),
        resulting_source_work_version=merge.resulting_source_work_version,
        resulting_destination_work_version=merge.resulting_destination_work_version,
        rationale=merge.rationale,
        merged_by_client=merge.merged_by_client,
        merged_by_session_id=merge.merged_by_session_id,
        merged_by_model=merge.merged_by_model,
        created_at=merge.created_at,
    )


def merge_work_records(
    database: Session,
    project_id: UUID,
    source_work_item_id: UUID,
    payload: WorkMergeRequest,
) -> WorkMergeResult:
    lock_project_graph(database, project_id)
    locked = lock_endpoint_work_items(
        database,
        project_id,
        (source_work_item_id, payload.destination_work_item_id),
    )
    source = locked[source_work_item_id]
    destination = locked[payload.destination_work_item_id]
    if source.id == destination.id:
        raise duplicate_self()
    graph = _load_graph(database, project_id, tuple(locked.values()))
    _require_merge_preconditions(database, source, destination, payload, graph)
    consume_lease_for_terminal_mutation(database, source.id, payload.lease_token)

    merge_id = uuid4()
    merge_time = database_now(database)
    relationship, relationship_created = stage_merge_relationship_locked(
        database,
        relationship_id=uuid4(),
        merge_id=merge_id,
        project_id=project_id,
        source_work_item_id=source.id,
        destination_work_item_id=destination.id,
        created_by_client=payload.merged_by_client,
        created_by_session_id=payload.merged_by_session_id,
        created_by_model=payload.merged_by_model,
        created_at=merge_time,
        locked_work_items=locked,
    )
    source.version += 1
    destination.version += 1
    source.updated_at = merge_time
    destination.updated_at = merge_time
    database.flush()
    merge = WorkDuplicateMerge(
        id=merge_id,
        project_id=project_id,
        source_work_item_id=source.id,
        destination_work_item_id=destination.id,
        duplicate_relationship_id=relationship.id,
        duplicate_relationship_type="duplicate-of",
        reviewed_source_work_version=payload.reviewed_source_revision.work_version,
        reviewed_source_context_checkpoint_id=payload.reviewed_source_revision.context_checkpoint_id,
        reviewed_source_work_event_count=payload.reviewed_source_revision.work_event_count,
        reviewed_destination_work_version=payload.reviewed_destination_revision.work_version,
        reviewed_destination_context_checkpoint_id=(
            payload.reviewed_destination_revision.context_checkpoint_id
        ),
        reviewed_destination_work_event_count=payload.reviewed_destination_revision.work_event_count,
        resulting_source_work_version=source.version,
        resulting_destination_work_version=destination.version,
        rationale=payload.rationale,
        merged_by_client=payload.merged_by_client,
        merged_by_session_id=payload.merged_by_session_id,
        merged_by_model=payload.merged_by_model,
        created_at=merge_time,
    )
    database.add(merge)
    database.flush()
    actor: MutationActor = source_actor(
        payload.merged_by_client,
        payload.merged_by_session_id,
        payload.merged_by_model,
    )
    relationship_events = (
        stage_relationship_events(
            database,
            relationship,
            action="added",
            actor=actor,
            created_at=merge_time,
            created_for_duplicate_merge_id=merge_id,
        )
        if relationship_created
        else []
    )
    merge_events = stage_work_merged_events(
        database,
        merge_id=merge_id,
        project_id=project_id,
        source_work_item_id=source.id,
        destination_work_item_id=destination.id,
        source_work_version=source.version,
        destination_work_version=destination.version,
        rationale=payload.rationale,
        actor=actor,
        created_at=merge_time,
    )
    database.flush()
    pointer = _identity_pointer(destination)
    return WorkMergeResult(
        merge=_merge_read(merge),
        source_work_item=WorkItemRead.model_validate(source),
        destination_work_item=WorkItemRead.model_validate(destination),
        direct_destination=pointer,
        canonical_work_item=pointer,
        supporting_relationship_created=relationship_created,
        supporting_relationship=relationship_edge(relationship),
        relationship_events=[work_event_read(event) for event in relationship_events],
        merge_events=[work_event_read(event) for event in merge_events],
    )
