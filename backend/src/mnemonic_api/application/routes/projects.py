"""Projects: the scope every other lookup is confined to.

Project administration belongs to the human dashboard. These writes are not
receipt-protected, so the broadcast middleware falls back to method and path
when deciding whether dashboards need an invalidation.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mnemonic_api.application.guards import reject_empty_read_request
from mnemonic_api.database import Database, database_sqlstate
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.job_report_defaults import DEFAULT_JOB_COMPLETION_REPORT_PROMPT
from mnemonic_api.models import Project, ProjectSettings
from mnemonic_api.schemas import (
    Page,
    ProjectCreate,
    ProjectListQuery,
    ProjectPatch,
    ProjectRead,
    ProjectSettingsPatch,
    ProjectSettingsRead,
)
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

router = APIRouter()
UNIQUE_VIOLATION = "23505"  # PostgreSQL SQLSTATE


@router.get("/projects", response_model=Page[ProjectRead])
def list_projects(
    filters: Annotated[ProjectListQuery, Query()], database: Database
) -> Page[ProjectRead]:
    total = database.scalar(select(func.count()).select_from(Project)) or 0
    projects = database.scalars(
        select(Project)
        .order_by(func.lower(Project.name), Project.id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return Page(
        items=[ProjectRead.model_validate(project) for project in projects],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.post("/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, database: Database) -> Project:
    project = Project(**payload.model_dump())
    try:
        with project_mutation(database, None):
            database.add(project)
            database.commit()
    except IntegrityError as exc:
        database.rollback()
        if database_sqlstate(exc) == UNIQUE_VIOLATION:
            raise conflict("slug_conflict", "A project with that slug exists.") from None
        raise
    database.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, database: Database) -> Project:
    return require_project(database, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: UUID, payload: ProjectPatch, database: Database) -> Project:
    try:
        with project_mutation(database, project_id):
            project = require_project(database, project_id)
            changes = payload.model_dump(exclude_unset=True)
            changed = any(getattr(project, field) != value for field, value in changes.items())
            for field, value in changes.items():
                setattr(project, field, value)
            if changed:
                project.updated_at = datetime.now(UTC)
            database.commit()
    except IntegrityError as exc:
        database.rollback()
        if database_sqlstate(exc) == UNIQUE_VIOLATION:
            raise conflict("slug_conflict", "A project with that slug exists.") from None
        raise
    database.refresh(project)
    return project


@router.get(
    "/projects/{project_id}/settings",
    response_model=ProjectSettingsRead,
    dependencies=[Depends(reject_empty_read_request)],
)
def get_project_settings(project_id: UUID, database: Database) -> ProjectSettingsRead:
    require_project(database, project_id)
    settings = database.get(ProjectSettings, project_id)
    if settings is None:
        raise ApplicationError(
            503, "project_settings_unavailable", "Project settings are unavailable."
        )
    return settings_read(settings)


def settings_read(settings: ProjectSettings) -> ProjectSettingsRead:
    return ProjectSettingsRead(
        project_id=settings.project_id,
        recall_pointer_template=settings.recall_pointer_template,
        job_completion_report_prompt=settings.job_completion_report_prompt,
        revision=str(settings.revision),
    )


@router.patch("/projects/{project_id}/settings", response_model=ProjectSettingsRead)
def update_project_settings(
    project_id: UUID,
    payload: ProjectSettingsPatch,
    database: Database,
) -> ProjectSettingsRead:
    with project_mutation(database, project_id):
        settings = database.get(ProjectSettings, project_id)
        if settings is None:
            raise ApplicationError(
                503, "project_settings_unavailable", "Project settings are unavailable."
            )
        if int(payload.expected_revision) != settings.revision:
            raise conflict(
                "project_settings_changed", "Project settings changed. Reload before saving."
            )
        changed = False
        for field in payload.model_fields_set - {"expected_revision"}:
            value = getattr(payload, field)
            if field == "job_completion_report_prompt" and value is None:
                value = DEFAULT_JOB_COMPLETION_REPORT_PROMPT
            if getattr(settings, field) != value:
                changed = True
                setattr(settings, field, value)
        if changed:
            if settings.revision == 2**63 - 1:
                raise ApplicationError(
                    503, "project_settings_unavailable", "Project settings revision is exhausted."
                )
            settings.revision += 1
        database.commit()
        return settings_read(settings)
