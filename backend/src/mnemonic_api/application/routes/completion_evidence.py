"""Bounded, event-backed completion-evidence history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from mnemonic_api.database import Database
from mnemonic_api.errors import completion_evidence_unavailable
from mnemonic_api.schemas import (
    COMPLETION_HISTORY_MAX_BYTES,
    CompletionEvidenceListQuery,
    CompletionEvidencePage,
)
from mnemonic_api.services.completion_evidence import completion_evidence_page

router = APIRouter()


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/completion-evidence",
    response_model=CompletionEvidencePage,
)
def list_completion_evidence(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[CompletionEvidenceListQuery, Query()],
    database: Database,
) -> JSONResponse:
    page = completion_evidence_page(database, project_id, work_item_id, filters)
    response = JSONResponse(
        content=page.model_dump(mode="json"),
        headers={
            "Cache-Control": "no-store, no-transform",
            "Content-Encoding": "identity",
        },
    )
    if len(response.body) > COMPLETION_HISTORY_MAX_BYTES:
        raise completion_evidence_unavailable()
    return response
