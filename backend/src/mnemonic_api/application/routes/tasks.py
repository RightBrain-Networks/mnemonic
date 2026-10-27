"""Read-only task surfaces for the dashboard."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from mnemonic_api.application.guards import transport_rejection
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.services.tasks import task_page
from mnemonic_api.task_schemas import TaskListQuery, TaskPage

router = APIRouter()


async def validate_task_query(request: Request) -> None:
    names = [name for name, _ in request.query_params.multi_items() if name != "tags"]
    if len(names) != len(set(names)):
        raise transport_rejection("query", None, "Only tags accepts repeated query values.")
    async for chunk in request.stream():
        if chunk:
            raise transport_rejection("body", None, "A request body is not accepted for this read.")


@router.get("/projects/{project_id}/tasks", response_model=TaskPage,
            dependencies=[Depends(validate_task_query)])
def list_tasks(
    project_id: UUID, database: Database, filters: Annotated[TaskListQuery, Query()],
) -> TaskPage:
    begin_coherent_read(database)
    return task_page(database, project_id, filters)
