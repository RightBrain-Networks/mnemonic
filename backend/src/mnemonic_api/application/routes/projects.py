"""Projects: the scope every other lookup is confined to.

Project administration belongs to the human dashboard. These writes are not
receipt-protected, so the broadcast middleware falls back to method and path
when deciding whether dashboards need an invalidation.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mnemonic_api.database import Database, database_sqlstate
from mnemonic_api.errors import conflict
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
    database.add(project)
    try:
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
    project = require_project(database, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    project.updated_at = datetime.now(UTC)
    database.commit()
    database.refresh(project)
    return project


@router.get("/projects/{project_id}/settings", response_model=ProjectSettingsRead)
def get_project_settings(project_id: UUID, database: Database) -> ProjectSettingsRead:
    require_project(database, project_id)
    settings = database.get(ProjectSettings, project_id)
    return ProjectSettingsRead(
        project_id=project_id,
        recall_pointer_template=(
            settings.recall_pointer_template if settings is not None else None
        ),
    )


@router.patch("/projects/{project_id}/settings", response_model=ProjectSettingsRead)
def update_project_settings(
    project_id: UUID,
    payload: ProjectSettingsPatch,
    database: Database,
) -> ProjectSettingsRead:
    # A null template clears the row; the project row lock serializes concurrent edits.
    require_project(database, project_id, lock=True)
    settings = database.get(ProjectSettings, project_id)
    template = payload.recall_pointer_template
    if template is None:
        if settings is not None:
            database.delete(settings)
    elif settings is None:
        database.add(ProjectSettings(project_id=project_id, recall_pointer_template=template))
    else:
        settings.recall_pointer_template = template
    database.commit()
    return ProjectSettingsRead(project_id=project_id, recall_pointer_template=template)
