"""Explicit rubric reads and revision-checked human settings edits."""

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import undefer

from mnemonic_api.application.guards import reject_empty_read_request
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import ProjectSettings
from mnemonic_api.phase12_schemas import PositiveRevision
from mnemonic_api.schemas import APIModel, Prompt
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

router = APIRouter()


class PriorityRubricRead(APIModel):
    project_id: UUID
    content: Prompt
    revision: PositiveRevision


class PriorityRubricPatch(APIModel):
    content: Prompt
    expected_revision: PositiveRevision


def _settings(project_id: UUID, database: Database) -> ProjectSettings:
    require_project(database, project_id)
    settings = database.scalar(select(ProjectSettings).where(
        ProjectSettings.project_id == project_id,
    ).options(undefer(ProjectSettings.priority_rubric)))
    if settings is None:
        raise ApplicationError(503, "project_settings_unavailable", "Settings are unavailable.")
    return settings


def _read(settings: ProjectSettings) -> PriorityRubricRead:
    return PriorityRubricRead(
        project_id=settings.project_id, content=settings.priority_rubric,
        revision=str(settings.revision),
    )


@router.get(
    "/projects/{project_id}/priority-rubric", response_model=PriorityRubricRead,
    dependencies=[Depends(reject_empty_read_request)],
)
def get_priority_rubric(project_id: UUID, database: Database) -> PriorityRubricRead:
    return _read(_settings(project_id, database))


@router.patch("/projects/{project_id}/priority-rubric", response_model=PriorityRubricRead)
def update_priority_rubric(
    project_id: UUID, payload: PriorityRubricPatch, database: Database,
) -> PriorityRubricRead:
    with project_mutation(database, project_id):
        settings = _settings(project_id, database)
        if int(payload.expected_revision) != settings.revision:
            raise conflict("priority_rubric_changed", "Settings changed. Review the saved rubric.")
        if settings.priority_rubric != payload.content:
            if settings.revision == 2**63 - 1:
                raise ApplicationError(503, "project_settings_unavailable",
                                       "Settings revision is exhausted.")
            settings.priority_rubric = payload.content
            settings.revision += 1
        database.commit()
        return _read(settings)
