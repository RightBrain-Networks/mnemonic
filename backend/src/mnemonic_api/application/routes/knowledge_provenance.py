"""Search registered citations by original or merged repository commit."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.knowledge_schemas import DecisionScopeRead, KnowledgePage, KnowledgePagination
from mnemonic_api.provenance_api import CommitReconcileCreate, CommitReconcileResult
from mnemonic_api.provenance_schemas import CommitHash, RepositoryIdentity
from mnemonic_api.services.decision_provenance import lookup_commit, reconcile_commit

router = APIRouter()
_PREFIX = "/projects/{project_id}/knowledge/commits"


@router.get(_PREFIX, response_model=KnowledgePage[DecisionScopeRead])
def decision_commit_lookup(
    project_id: UUID, database: Database, repository: RepositoryIdentity, commit_hash: CommitHash,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> KnowledgePage[DecisionScopeRead]:
    begin_coherent_read(database)
    return lookup_commit(database, project_id, repository, commit_hash,
                         KnowledgePagination(limit=limit, offset=offset))


@router.post(_PREFIX + "/reconcile", response_model=CommitReconcileResult)
def decision_scope_reconcile(
    project_id: UUID, payload: CommitReconcileCreate, request: Request, database: Database,
) -> JSONResponse:
    return run_registered_mutation(
        "reconcile_decision_scope", request=request, database=database, project_id=project_id,
        target={}, payload=payload,
        execute=lambda body: reconcile_commit(database, project_id, body),
    )
