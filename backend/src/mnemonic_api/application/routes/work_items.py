"""Work items: identity, lifecycle, bounded recall, and the ready queue.

The status column holds only what a person decided: ``pending``, ``deferred``,
``done``, ``wont-do``, ``promoted``. Active, dropped, blocked, and waiting are
derived at read time from leases, ``blocks`` edges, and human gates, so nothing
here writes them. Identity and lifecycle edits carry the version last read.
The receipt-protected writes run under ``mutations.run_registered_mutation``
and contribute only their domain work.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.mutations import run_registered_mutation
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.schemas import (
    ChildrenListQuery,
    CompletionCheckpointRead,
    HierarchySummary,
    Page,
    ReadyWorkListQuery,
    ReadyWorkPage,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkContext,
    WorkContextQuery,
    WorkCreation,
    WorkDeferralCreate,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkItemCreate,
    WorkItemDetailRead,
    WorkItemPatch,
    WorkItemRead,
    WorkMoveCreate,
    WorkMoveRead,
    WorkUpdateRead,
)
from mnemonic_api.services.completion_evidence import hydrate_completion_evidence
from mnemonic_api.services.duplicates import work_item_detail
from mnemonic_api.services.hierarchy import hierarchy_page
from mnemonic_api.services.job_completion_reports import closeout_report
from mnemonic_api.services.readiness import ready_work_page
from mnemonic_api.services.relationships import relationship_edge
from mnemonic_api.services.work_context import assemble_work_context, checkpoint_read
from mnemonic_api.services.work_items import (
    complete_work_record,
    create_work_records,
    defer_work_record,
    delete_work_record,
    move_work_record,
    require_work_item,
    update_work_record,
)

router = APIRouter()


@router.post("/projects/{project_id}/work-items", response_model=WorkCreation, status_code=201)
def create_work(
    project_id: UUID,
    payload: WorkItemCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    # The item, its first checkpoint, and any initial edges share one transaction.
    def execute(domain_payload: WorkItemCreate) -> WorkCreation:
        work_item, checkpoint, relationships = create_work_records(
            database, project_id, domain_payload
        )
        database.refresh(work_item)
        database.refresh(checkpoint)
        for relationship in relationships:
            database.refresh(relationship)
        return WorkCreation(
            work_item=WorkItemRead.model_validate(work_item),
            initial_checkpoint=checkpoint_read(checkpoint),
            initial_relationships=[relationship_edge(item) for item in relationships],
        )

    return run_registered_mutation(
        "create_work",
        request=request,
        database=database,
        project_id=project_id,
        target={},
        payload=payload,
        execute=execute,
    )


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}",
    response_model=WorkItemDetailRead,
)
def get_work(project_id: UUID, work_item_id: UUID, database: Database) -> WorkItemDetailRead:
    begin_coherent_read(database)
    work_item = require_work_item(database, project_id, work_item_id)
    return work_item_detail(database, project_id, work_item)


@router.patch("/projects/{project_id}/work-items/{work_item_id}", response_model=WorkUpdateRead)
def update_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkItemPatch,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: WorkItemPatch) -> WorkUpdateRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        update_work_record(database, work_item, domain_payload)
        database.refresh(work_item)
        data = WorkItemRead.model_validate(work_item).model_dump()
        report = closeout_report(database, work_item)
        if report is not None:
            data["job_completion_report"] = report
        return WorkUpdateRead(**data)

    return run_registered_mutation(
        "update_work",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.post("/projects/{project_id}/work-items/{work_item_id}/defer", response_model=WorkItemRead)
def defer_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkDeferralCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    """Human dashboard action; intentionally absent from the agent MCP surface."""

    def execute(domain_payload: WorkDeferralCreate) -> WorkItemRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        defer_work_record(database, work_item, domain_payload)
        database.refresh(work_item)
        return WorkItemRead.model_validate(work_item)

    return run_registered_mutation(
        "defer_work",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/move",
    response_model=WorkMoveRead,
)
def move_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkMoveCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    """Human dashboard action; intentionally absent from the agent MCP surface."""

    def execute(domain_payload: WorkMoveCreate) -> WorkMoveRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        move = move_work_record(database, work_item, project_id, domain_payload)
        database.refresh(work_item)
        return WorkMoveRead(
            source_project_id=move.source_project_id,
            target_project_id=move.target_project_id,
            preserved_status=move.preserved_status,
            work_item=WorkItemRead.model_validate(work_item),
        )

    return run_registered_mutation(
        "move_work",
        request=request,
        database=database,
        project_id=project_id,
        additional_project_ids=(payload.target_project_id,),
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/complete",
    response_model=WorkCompletionRead,
)
def complete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkCompletionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: WorkCompletionCreate) -> WorkCompletionRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        checkpoint = complete_work_record(
            database,
            work_item,
            domain_payload.expected_version,
            domain_payload.checkpoint,
            domain_payload.lease_token,
            domain_payload.completion_evidence,
            domain_payload.job_completion_report,
        )
        database.refresh(work_item)
        database.refresh(checkpoint)
        response_fields = {}
        report = closeout_report(database, work_item)
        if report is not None:
            response_fields["job_completion_report"] = report
        evidence = hydrate_completion_evidence(database, checkpoint)
        if evidence is not None:
            response_fields["completion_evidence"] = evidence
        return WorkCompletionRead(
            work_item=WorkItemRead.model_validate(work_item),
            checkpoint=CompletionCheckpointRead.model_validate(checkpoint),
            **response_fields,
        )

    return run_registered_mutation(
        "complete_work",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.post(
    "/projects/{project_id}/work-items/{work_item_id}/delete",
    response_model=WorkDeletionRead,
)
def delete_work(
    project_id: UUID,
    work_item_id: UUID,
    payload: WorkDeletionCreate,
    request: Request,
    database: Database,
) -> JSONResponse:
    def execute(domain_payload: WorkDeletionCreate) -> WorkDeletionRead:
        work_item = require_work_item(database, project_id, work_item_id, lock=True)
        delete_work_record(
            database,
            work_item,
            domain_payload.expected_version,
            domain_payload.lease_token,
            domain_payload.actor,
        )
        return WorkDeletionRead(
            project_id=project_id, work_item_id=work_item_id, version=work_item.version
        )

    return run_registered_mutation(
        "delete_work",
        request=request,
        database=database,
        project_id=project_id,
        target={"work_item_id": work_item_id},
        payload=payload,
        execute=execute,
    )


@router.get("/projects/{project_id}/work-items/{work_item_id}/context", response_model=WorkContext)
def recall_work(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[WorkContextQuery, Query()],
    database: Database,
) -> WorkContext:
    # Bounded read-only context; recalling work is never authority to execute it.
    return assemble_work_context(
        database,
        project_id,
        work_item_id,
        filters.recent_limit,
        filters.recent_event_limit,
    )


@router.get("/projects/{project_id}/ready-work", response_model=ReadyWorkPage)
def list_ready_work(
    project_id: UUID,
    filters: Annotated[ReadyWorkListQuery, Query()],
    database: Database,
) -> ReadyWorkPage:
    """List advisory ready pointers; claim-time validation remains authoritative."""
    return ready_work_page(database, project_id, filters)


@router.get(
    "/projects/{project_id}/work-items/{work_item_id}/children",
    response_model=Page[HierarchySummary],
)
def list_children(
    project_id: UUID,
    work_item_id: UUID,
    filters: Annotated[ChildrenListQuery, Query()],
    database: Database,
) -> Page[HierarchySummary]:
    items, total = hierarchy_page(
        database, project_id, filters, parent_work_item_id=work_item_id
    )
    return Page(items=items, total=total, limit=filters.limit, offset=filters.offset)
