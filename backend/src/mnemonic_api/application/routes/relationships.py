"""Typed edges between work items.

Only ``blocks`` affects readiness; ``parent-child``, ``discovered-from``,
``duplicate-of``, and ``related`` describe. No edge is ever inferred from search
similarity or checkpoint prose. Adding an edge that already exists or removing
one that is already gone succeeds without applying anything; the result's
``created`` or ``removed`` flag says which, and the registry reads it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database
from mnemonic_api.schemas import (
    AdjacentRelationshipRead,
    Page,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipListQuery,
    RelationshipRemovalCreate,
    RelationshipRemovalResult,
)
from mnemonic_api.services.relationships import (
    add_relationship_record,
    list_adjacent_relationships,
    relationship_edge,
    remove_relationship_record,
    require_relationship,
)

router = APIRouter()


@router.post("/projects/{project_id}/relationships", response_model=RelationshipCreationResult)
def add_relationship(
    project_id: UUID,
    payload: RelationshipCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: RelationshipCreate) -> RelationshipCreationResult:
        return add_relationship_record(database, project_id, domain_payload)

    return run_registered_mutation(
        "add_relationship",
        request=request,
        database=database,
        project_id=project_id,
        target={},
        payload=payload,
        execute=execute,
    )


@router.get(
    "/projects/{project_id}/relationships/{relationship_id}",
    response_model=RelationshipEdgeRead,
)
def get_relationship(
    project_id: UUID,
    relationship_id: UUID,
    database: Database,
) -> RelationshipEdgeRead:
    return relationship_edge(require_relationship(database, project_id, relationship_id))


@router.delete(
    "/projects/{project_id}/relationships/{relationship_id}",
    response_model=RelationshipRemovalResult,
)
def remove_relationship(
    project_id: UUID,
    relationship_id: UUID,
    request: Request,
    database: Database,
    payload: RelationshipRemovalCreate | None = None,
) -> JSONResponse:
    # The body is optional: it carries only an actor and an operation ID.
    def execute(domain_payload: RelationshipRemovalCreate) -> RelationshipRemovalResult:
        return remove_relationship_record(
            database, project_id, relationship_id, domain_payload.actor
        )

    return run_registered_mutation(
        "remove_relationship",
        request=request,
        database=database,
        project_id=project_id,
        target={"relationship_id": relationship_id},
        payload=payload or RelationshipRemovalCreate(),
        execute=execute,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/relationships",
    response_model=Page[AdjacentRelationshipRead],
)
def list_relationships(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[RelationshipListQuery, Query()],
    database: Database,
) -> Page[AdjacentRelationshipRead]:
    items, total = list_adjacent_relationships(database, project_id, work_item_id, filters)
    return Page(items=items, total=total, limit=filters.limit, offset=filters.offset)
