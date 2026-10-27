"""Tag catalog reads for agents; mutations are registered only on the human browser router."""
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database
from mnemonic_api.services import tags
from mnemonic_api.services.work_items import require_project
from mnemonic_api.tag_schemas import TagCatalog, TagCreate, TagDelete, TagOrder, TagPatch

router = APIRouter()
browser_mutations = APIRouter()


@router.get("/projects/{project_id}/tags", response_model=TagCatalog)
def list_tags(project_id: UUID, database: Database) -> TagCatalog:
    require_project(database, project_id)
    return tags.catalog(database, project_id)


@browser_mutations.post("/projects/{project_id}/tags", response_model=TagCatalog, status_code=201)
def create_tag(project_id: UUID, payload: TagCreate, request: Request,
               database: Database) -> JSONResponse:
    return run_registered_mutation("create_tag", request=request, database=database,
        project_id=project_id, target={}, payload=payload,
        execute=lambda body: tags.create_tag(database, project_id, body))


@browser_mutations.patch("/projects/{project_id}/tags/{tag_id:uuid}", response_model=TagCatalog)
def update_tag(project_id: UUID, tag_id: UUID, payload: TagPatch, request: Request,
               database: Database) -> JSONResponse:
    return run_registered_mutation("update_tag", request=request, database=database,
        project_id=project_id, target={"tag_id": tag_id}, payload=payload,
        execute=lambda body: tags.update_tag(database, project_id, tag_id, body))


@browser_mutations.delete("/projects/{project_id}/tags/{tag_id:uuid}", response_model=TagCatalog)
def delete_tag(project_id: UUID, tag_id: UUID, payload: TagDelete, request: Request,
               database: Database) -> JSONResponse:
    return run_registered_mutation("delete_tag", request=request, database=database,
        project_id=project_id, target={"tag_id": tag_id}, payload=payload,
        execute=lambda body: tags.delete_tag(database, project_id, tag_id))


@browser_mutations.put("/projects/{project_id}/tags/order", response_model=TagCatalog)
def reorder_tags(project_id: UUID, payload: TagOrder, request: Request,
                 database: Database) -> JSONResponse:
    return run_registered_mutation("reorder_tags", request=request, database=database,
        project_id=project_id, target={}, payload=payload,
        execute=lambda body: tags.reorder_tags(database, project_id, body))
