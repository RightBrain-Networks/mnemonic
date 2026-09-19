"""Read-only task surfaces for the dashboard."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from mnemonic_api.application.guards import reject_read_body_and_duplicate_query
from mnemonic_api.database import Database, begin_coherent_read
from mnemonic_api.services.tasks import task_page
from mnemonic_api.task_schemas import TaskListQuery, TaskPage

router = APIRouter()


@router.get("/projects/{project_id}/tasks", response_model=TaskPage,
            dependencies=[Depends(reject_read_body_and_duplicate_query)])
def list_tasks(
    project_id: UUID, database: Database, filters: Annotated[TaskListQuery, Query()],
) -> TaskPage:
    begin_coherent_read(database)
    return task_page(database, project_id, filters)
