"""A work item's append-only history: checkpoints and events.

Neither is ever updated. The route surface offers no edit, PostgreSQL triggers
refuse one, and a correction is a new ``context`` checkpoint. Both appends
deliberately leave the work item's version alone, so independent appenders
never contend with each other or with identity edits. Clients may append only
``progress`` events; every authoritative event type is written by a service
inside the mutation that proves it.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.application.state import api_key_of
from mnemonic_api.database import Database
from mnemonic_api.models import Checkpoint
from mnemonic_api.schemas import (
    CheckpointCreate,
    CheckpointListQuery,
    CheckpointRead,
    Page,
    ProgressEventCreate,
    WorkEventListQuery,
    WorkEventPage,
    WorkEventRead,
)
from mnemonic_api.services.work_context import checkpoint_read
from mnemonic_api.services.work_events import append_progress_event, list_work_events
from mnemonic_api.services.work_items import append_checkpoint_record, require_work_item

router = APIRouter()


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    response_model=Page[CheckpointRead],
)
def list_checkpoints(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[CheckpointListQuery, Query()],
    database: Database,
) -> Page[CheckpointRead]:
    work_item = require_work_item(database, project_id, work_item_id)
    condition = Checkpoint.work_item_id == work_item.id
    total = database.scalar(select(func.count()).select_from(Checkpoint).where(condition)) or 0
    if filters.order == "newest":
        ordering = (Checkpoint.created_at.desc(), Checkpoint.id.desc())
    else:
        ordering = (Checkpoint.created_at, Checkpoint.id)
    checkpoints = database.scalars(
        select(Checkpoint)
        .where(condition)
        .order_by(*ordering)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return Page(
        items=[checkpoint_read(checkpoint) for checkpoint in checkpoints],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/checkpoints",
    response_model=CheckpointRead,
    status_code=201,
)
def add_checkpoint(
    project_id: UUID,
    work_item_id: UUID,
    payload: CheckpointCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: CheckpointCreate) -> CheckpointRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        checkpoint = append_checkpoint_record(database, work_item, domain_payload)
        database.refresh(checkpoint)
        return checkpoint_read(checkpoint)

    return run_registered_mutation(
        "add_checkpoint",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/events",
    response_model=WorkEventPage,
)
def get_work_events(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[WorkEventListQuery, Query()],
    database: Database,
) -> WorkEventPage:
    return list_work_events(database, project_id, work_item_id, filters)


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/events",
    response_model=WorkEventRead,
    status_code=201,
)
def append_event(
    project_id: UUID,
    work_item_id: UUID,
    payload: ProgressEventCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: ProgressEventCreate) -> WorkEventRead:
        return append_progress_event(
            database,
            project_id,
            work_item_id,
            domain_payload,
            bearer_key=api_key_of(request),
        )

    return run_registered_mutation(
        "append_event",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )
