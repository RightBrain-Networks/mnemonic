"""Human prompt editing and agent-facing project template rendering."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field, StringConstraints

from mnemonic_api.application.guards import reject_empty_read_request
from mnemonic_api.database import Database
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ProjectSettings
from mnemonic_api.prompt_storage import PROMPTS, PromptFile, validate_prompt
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.prompts import MACROS, render_prompt, storage_for, synchronize_settings

router = APIRouter()
Digest = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$")]


class PromptMetadataRead(APIModel):
    id: str
    name: str
    description: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime
    revision: Digest


class PromptRead(PromptMetadataRead):
    content: str


class PromptMacroRead(APIModel):
    macro: str
    description: str


class PromptPage(APIModel):
    items: list[PromptMetadataRead]
    macros: list[PromptMacroRead]


class PromptUpdate(APIModel):
    content: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100_000)]
    expected_revision: Digest


class PromptRenderRequest(APIModel):
    work_item_id: UUID | None = None
    code_review_id: UUID | None = None


class PromptRenderRead(APIModel):
    content: str = Field(max_length=100_000)


def _read(prompt_id: str, file: PromptFile) -> PromptRead:
    name, description = PROMPTS[prompt_id]
    return PromptRead(
        id=prompt_id,
        name=name,
        description=description,
        content=file.content,
        size_bytes=file.size_bytes,
        created_at=file.created_at,
        updated_at=file.updated_at,
        revision=file.revision,
    )


def _sync(database: Database, project_id: UUID) -> None:
    settings = database.get(ProjectSettings, project_id)
    if settings is not None:
        synchronize_settings(database, settings)


@router.get(
    "/projects/{project_id}/prompts",
    response_model=PromptPage,
    dependencies=[Depends(reject_empty_read_request)],
)
def list_prompts(project_id: UUID, database: Database) -> PromptPage:
    with project_mutation(database, project_id):
        items = [_read(key, storage_for(database).read(project_id, key)) for key in PROMPTS]
        _sync(database, project_id)
        database.commit()
    return PromptPage(
        items=[PromptMetadataRead(**item.model_dump(exclude={"content"})) for item in items],
        macros=[
            PromptMacroRead(macro=macro, description=description)
            for macro, description in MACROS.items()
        ],
    )


@router.get(
    "/projects/{project_id}/prompts/{prompt_id}",
    response_model=PromptRead,
    dependencies=[Depends(reject_empty_read_request)],
)
def get_prompt(project_id: UUID, prompt_id: str, database: Database) -> PromptRead:
    with project_mutation(database, project_id):
        result = _read(prompt_id, storage_for(database).read(project_id, prompt_id))
        _sync(database, project_id)
        database.commit()
    return result


@router.put("/projects/{project_id}/prompts/{prompt_id}", response_model=PromptRead)
def update_prompt(
    project_id: UUID,
    prompt_id: str,
    payload: PromptUpdate,
    database: Database,
) -> PromptRead:
    try:
        validate_prompt(prompt_id, payload.content)
    except ValueError:
        raise ApplicationError(
            422, "invalid_prompt", "Prompt text exceeds its limits or is invalid."
        ) from None
    with project_mutation(database, project_id):
        result = storage_for(database).write(
            project_id, prompt_id, payload.content, payload.expected_revision
        )
        _sync(database, project_id)
        database.commit()
    return _read(prompt_id, result)


@router.post(
    "/projects/{project_id}/prompts/{prompt_id}/render", response_model=PromptRenderRead,
    openapi_extra={"x-mnemonic-effect": "safe_read"},
)
def render_project_prompt(
    project_id: UUID,
    prompt_id: str,
    payload: PromptRenderRequest,
    database: Database,
) -> PromptRenderRead:
    return PromptRenderRead(
        content=render_prompt(
            database,
            project_id,
            prompt_id,
            work_item_id=payload.work_item_id,
            code_review_id=payload.code_review_id,
        )
    )
