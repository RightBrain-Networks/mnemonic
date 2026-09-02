"""Human gates: durable questions that only a person resolves.

An agent may request a gate and read gates; resolution arrives through the
dashboard's REST call below and never through the MCP surface. Both writes
reject request-known secrets before reserving a receipt, so credential or
capability material can never become durable question or answer text. Every
resolution names the exact work, checkpoint, and relationship revision that
was reviewed; reads expose that revision with backend-computed drift facts.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import api_key_of
from mnemonic_api.database import Database
from mnemonic_api.schemas import (
    HumanAttentionListQuery,
    HumanAttentionPage,
    HumanGateListQuery,
    HumanGatePage,
    HumanGateRead,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    WorkContext,
    WorkContextQuery,
)
from mnemonic_api.services.gates import (
    list_human_attention,
    list_work_gates,
    reject_gate_secret_echo,
    request_human_gate,
    resolve_human_gate,
)
from mnemonic_api.services.work_context import assemble_work_context

router = APIRouter()


@router.get("/projects/{project_id}/human-attention", response_model=HumanAttentionPage)
def get_human_attention(
    project_id: UUID,
    filters: Annotated[HumanAttentionListQuery, Query()],
    database: Database,
) -> HumanAttentionPage:
    return list_human_attention(database, project_id, filters)


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/gates",
    response_model=HumanGatePage,
)
def get_work_gates(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[HumanGateListQuery, Query()],
    database: Database,
) -> HumanGatePage:
    return list_work_gates(database, project_id, work_item_id, filters)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/gates",
    response_model=HumanGateRead,
    status_code=201,
)
def create_human_gate(
    project_id: UUID,
    work_item_id: UUID,
    payload: HumanGateRequestCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    reject_gate_secret_echo(payload, known_secret_values=(api_key_of(request),))

    def execute(domain_payload: HumanGateRequestCreate) -> HumanGateRead:
        return request_human_gate(database, project_id, work_item_id, domain_payload)

    return run_registered_mutation(
        "request_human_input",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/resolve",
    response_model=HumanGateRead,
)
def resolve_human_gate_route(
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    payload: HumanGateResolutionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    reject_gate_secret_echo(payload, known_secret_values=(api_key_of(request),))

    def execute(domain_payload: HumanGateResolutionCreate) -> HumanGateRead:
        return resolve_human_gate(database, project_id, work_item_id, gate_id, domain_payload)

    return run_registered_mutation(
        "resolve_human_input",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id, "gate_id": gate_id},
        payload=payload,
        execute=execute,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/context",
    response_model=WorkContext,
)
def review_human_gate_context(
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    filters: Annotated[WorkContextQuery, Query()],
    database: Database,
) -> WorkContext:
    # The same bounded context as recall, focused on the gate under review.
    return assemble_work_context(
        database,
        project_id,
        work_item_id,
        filters.recent_limit,
        filters.recent_event_limit,
        focus_gate_id=gate_id,
    )
