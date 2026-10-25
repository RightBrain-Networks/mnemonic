"""Decision text reads and receipt-protected knowledge graph mutations."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.errors import ApplicationError
from mnemonic_api.knowledge_schemas import (
    DecisionListQuery,
    DecisionPut,
    DecisionPutResult,
    DecisionRead,
    DecisionReference,
    DecisionScopeAppend,
    DecisionScopeRead,
    DecisionScopeResult,
    DecisionTextQuery,
    DecisionTextRead,
    DomainCreate,
    DomainRename,
    KnowledgeDomainRead,
    KnowledgePage,
    KnowledgePagination,
)
from mnemonic_api.services import knowledge

router = APIRouter()
_PREFIX = "/projects/{project_id}/knowledge"


@router.get(_PREFIX + "/domains", response_model=KnowledgePage[KnowledgeDomainRead])
def list_knowledge_domains(
    project_id: UUID,
    database: Database,
    filters: Annotated[KnowledgePagination, Query()],
) -> KnowledgePage[KnowledgeDomainRead]:
    begin_coherent_read(database)
    return knowledge.list_domains(database, project_id, filters)


@router.post(_PREFIX + "/domains", response_model=KnowledgeDomainRead, status_code=201)
def create_knowledge_domain(
    project_id: UUID,
    payload: DomainCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "create_knowledge_domain",
        request=request,
        database=database,
        project_id=project_id,
        target={},
        payload=payload,
        execute=lambda body: knowledge.create_domain(database, project_id, body),
    )


@router.patch(_PREFIX + "/domains/{domain_id}", response_model=KnowledgeDomainRead)
def rename_knowledge_domain(
    project_id: UUID,
    domain_id: UUID,
    payload: DomainRename,
    request: Request,
    database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "rename_knowledge_domain",
        request=request,
        database=database,
        project_id=project_id,
        target={"domain_id": domain_id},
        payload=payload,
        execute=lambda body: knowledge.rename_domain(database, project_id, domain_id, body),
    )


@router.get(_PREFIX + "/decisions", response_model=KnowledgePage[DecisionRead])
def list_knowledge_decisions(
    project_id: UUID,
    database: Database,
    filters: Annotated[DecisionListQuery, Query()],
) -> KnowledgePage[DecisionRead]:
    begin_coherent_read(database)
    return knowledge.list_decisions(database, project_id, filters)


@router.put(_PREFIX + "/decisions", response_model=DecisionPutResult)
def put_knowledge_decision(
    project_id: UUID,
    payload: DecisionPut,
    request: Request,
    database: Database,
) -> JSONResponse:
    """Unicode JSON text is encoded to UTF-8 before hashing and database persistence.

    Fresh uploads deduplicate by domain/content hash and return the retained original
    metadata. Explicit human saves always append a revision at expected_revision;
    agent updates append for changed content or metadata and retain identical repeats.
    """
    return run_registered_mutation(
        "put_decision",
        request=request,
        database=database,
        project_id=project_id,
        target={},
        payload=payload,
        execute=lambda body: knowledge.put_decision(database, project_id, body),
    )


@router.get(_PREFIX + "/decisions/{decision_id}", response_model=DecisionTextRead)
def get_knowledge_decision(
    project_id: UUID,
    decision_id: DecisionReference,
    database: Database,
    response: Response,
    filters: Annotated[DecisionTextQuery, Query()],
) -> DecisionTextRead:
    response.headers["Cache-Control"] = "no-store"
    begin_coherent_read(database)
    return knowledge.read_decision_text(database, project_id, decision_id, filters)


@router.get(
    _PREFIX + "/decisions/{decision_id}/history", response_model=KnowledgePage[DecisionRead]
)
def get_knowledge_decision_history(
    project_id: UUID,
    decision_id: DecisionReference,
    database: Database,
    filters: Annotated[KnowledgePagination, Query()],
) -> KnowledgePage[DecisionRead]:
    begin_coherent_read(database)
    return knowledge.decision_history(database, project_id, decision_id, filters)


@router.get(
    _PREFIX + "/decisions/{decision_id}/scope", response_model=KnowledgePage[DecisionScopeRead]
)
def get_knowledge_decision_scope(
    project_id: UUID,
    decision_id: DecisionReference,
    database: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    revision: Annotated[int | None, Query(ge=0)] = None,
) -> KnowledgePage[DecisionScopeRead]:
    begin_coherent_read(database)
    return knowledge.list_scope(
        database, project_id, decision_id, KnowledgePagination(limit=limit, offset=offset), revision
    )


@router.post(_PREFIX + "/decisions/{decision_id}/scope", response_model=DecisionScopeResult)
def append_knowledge_decision_scope(
    project_id: UUID,
    decision_id: DecisionReference,
    payload: DecisionScopeAppend,
    request: Request,
    database: Database,
) -> JSONResponse:
    document, domain, pinned = knowledge.lookup_document(database, project_id, decision_id)
    if pinned is not None and pinned != payload.revision:
        raise ApplicationError(422, "decision_revision_mismatch", "Revision assertions must agree.")
    return run_registered_mutation(
        "append_decision_scope",
        request=request,
        database=database,
        project_id=project_id,
        target={"document_id": document.id},
        payload=payload,
        execute=lambda body: knowledge.append_scope(database, project_id, document, domain, body),
    )
