"""Dashboard-only deletion and file transfers; no machine router registration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from mnemonic_api.application.browser_file_transport import ClosingStreamingResponse
from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.knowledge_management_schemas import (
    DecisionDeletion,
    DomainDeletion,
    KnowledgeDeletionPreview,
    KnowledgeDeletionRead,
)
from mnemonic_api.knowledge_schemas import DecisionIdentifier
from mnemonic_api.services import knowledge, knowledge_deletion, knowledge_exports

router = APIRouter()
_PREFIX = "/projects/{project_id}/knowledge"


@router.get(_PREFIX + "/domains/{domain_id}/deletion-preview")
def preview_knowledge_domain_deletion(project_id: UUID, domain_id: UUID,
                                      database: Database) -> KnowledgeDeletionPreview:
    begin_coherent_read(database)
    return knowledge_deletion.deletion_preview(
        database, knowledge.require_domain(database, project_id, domain_id),
    )


@router.get(_PREFIX + "/decisions/{decision_id}/deletion-preview")
def preview_knowledge_decision_deletion(project_id: UUID, decision_id: DecisionIdentifier,
                                        database: Database) -> KnowledgeDeletionPreview:
    begin_coherent_read(database)
    document, domain, _ = knowledge.require_document(database, project_id, decision_id)
    return knowledge_deletion.deletion_preview(database, domain, document)


@router.post(_PREFIX + "/domains/{domain_id}/delete", response_model=KnowledgeDeletionRead)
def delete_knowledge_domain(project_id: UUID, domain_id: UUID, payload: DomainDeletion,
                            request: Request, database: Database) -> JSONResponse:
    return run_registered_mutation(
        "delete_knowledge_domain", request=request, database=database, project_id=project_id,
        target={"domain_id": domain_id}, payload=payload,
        execute=lambda body: knowledge_deletion.delete_domain(
            database, project_id, domain_id, body,
        ),
    )


@router.post(_PREFIX + "/decisions/{decision_id}/delete", response_model=KnowledgeDeletionRead)
def delete_knowledge_decision(project_id: UUID, decision_id: DecisionIdentifier,
                              payload: DecisionDeletion, request: Request,
                              database: Database) -> JSONResponse:
    document_id = knowledge.lookup_document(database, project_id, decision_id)[0].id
    return run_registered_mutation(
        "delete_decision", request=request, database=database, project_id=project_id,
        target={"document_id": document_id}, payload=payload,
        execute=lambda body: knowledge_deletion.delete_document(
            database, project_id, document_id, body,
        ),
    )


@router.get(_PREFIX + "/domains/{domain_id}/export")
def export_knowledge_domain(project_id: UUID, domain_id: UUID,
                            database: Database) -> ClosingStreamingResponse:
    begin_coherent_read(database)
    domain, output = knowledge_exports.export_domain(database, project_id, domain_id)
    output.seek(0, 2)
    size = output.tell()
    output.seek(0)
    return ClosingStreamingResponse(
        iter(lambda: output.read(64 * 1024), b""), background=BackgroundTask(output.close),
        media_type="application/gzip", headers={
            "Content-Disposition": f'attachment; filename="{domain.prefix}-decisions.tar.gz"',
            "Content-Length": str(size), "Cache-Control": "no-store",
        },
    )


@router.get(_PREFIX + "/decisions/{decision_id}/download")
def download_knowledge_decision(project_id: UUID, decision_id: DecisionIdentifier,
                                database: Database,
                                revision: Annotated[int | None, Query(ge=0)] = None) -> Response:
    begin_coherent_read(database)
    document, _, _ = knowledge.require_document(database, project_id, decision_id)
    content = knowledge.require_revision(database, document, revision)
    suffix = ".md" if content.media_type == "text/markdown" else ".txt"
    return Response(content.content.encode("utf-8"), media_type=content.media_type, headers={
        "Content-Disposition": f'attachment; filename="{decision_id}{suffix}"',
        "Cache-Control": "no-store",
    })
