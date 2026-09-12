"""Project prompt configuration and nonrecursive, context-aware macro expansion."""

import json
import os
import re
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import CodeReview, CodeReviewScope, ProjectSettings
from mnemonic_api.prompt_storage import PromptStorage, validate_prompt
from mnemonic_api.services.work_items import require_project, require_work_item

MACROS = {
    "$COMPLETION_ACTION": "Completed or marked Done, when asking a review recommendation.",
    "$WORK_ITEM_TITLE": "The work item's title; when work context is available.",
    "$WORK_ITEM_SUMMARY": "The work item's summary; when work context is available.",
    "$WORK_ITEM_STATUS": "The work item's lifecycle status.",
    "$WORK_ITEM_PRIORITY": "The work item's numeric priority; when work context is available.",
    "$PROJECT_ID": "The project's unique ID.",
    "$PROJECT_NAME": "The project's name.",
    "$PROJECT_SLUG": "The project's URL-safe slug.",
    "$WORK_ITEM_ID": "The work item's unique ID.",
    "$PROJECT_DESCRIPTION": "The project description.",
    "$PROJECT_REPOSITORY_URL": "The project repository URL.",
    "$WORK_ITEM_VERSION": "The current work version.",
    "$LEASE_DEFAULT_MINUTES": "The current project default lease duration in minutes.",
    "$LEASE_MINIMUM_MINUTES": "The current project minimum lease duration in minutes.",
    "$LEASE_MAXIMUM_MINUTES": "The current project maximum lease duration in minutes.",
    "$CODE_REVIEW_ID": "The current review episode's unique ID, when available.",
    "$REVIEW_VERSION": "The current review episode version, when available.",
    "$REVIEW_SCOPE_SHA256": "The pinned review scope hash, when available.",
    "$REVIEW_ROUTING": "JSON containing review routing IDs, version, and scope hash.",
    "$REVIEW_SCOPE": "JSON containing only pinned repository locators and Git scope.",
    "$REVIEW_RESULT_ID": "The completed review result ID when creating remediation.",
    "$REVIEW_FINDINGS": "All findings, as a checklist, when creating remediation.",
}
_MACRO = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


def storage_for(database: Session | None = None) -> PromptStorage:
    root = database.info.get("prompt_root") if database is not None else None
    return PromptStorage(
        root or Path(os.environ.get("MNEMONIC_PROMPT_ROOT", "/var/lib/mnemonic/prompts"))
    )


def settings_prompt(settings: ProjectSettings, prompt_id: str) -> str:
    file = storage_for(object_session(settings)).read(settings.project_id, prompt_id)
    field = (
        "recall_pointer_sha256"
        if prompt_id == "recall-pointer"
        else "job_completion_report_prompt_sha256"
    )
    if file.revision != getattr(settings, field):
        raise conflict("project_settings_changed", "Prompt changed. Reload current settings.")
    return file.content


def expand_macros(template: str, values: dict[str, str]) -> str:
    """Insert literal values once; inserted text can never become another macro."""
    return _MACRO.sub(lambda match: values.get(match[0], match[0]), template)


def synchronize_settings(database: Session, settings: ProjectSettings) -> None:
    """Caller holds the project lock; external file edits advance the existing revision."""
    storage = storage_for(database)
    values = {
        "recall_pointer_sha256": storage.read(settings.project_id, "recall-pointer").revision,
        "job_completion_report_prompt_sha256": storage.read(
            settings.project_id, "job-completion-report"
        ).revision,
    }
    if all(getattr(settings, key) == value for key, value in values.items()):
        return
    if settings.revision == 2**63 - 1:
        raise ApplicationError(503, "project_settings_unavailable", "Settings revision exhausted.")
    for key, value in values.items():
        setattr(settings, key, value)
    settings.revision += 1
    database.flush()


def _review_values(
    database: Session,
    project_id: UUID,
    work_item_id: UUID | None,
    review_id: UUID | None,
    *,
    completed: bool = False,
) -> dict[str, str]:
    query = select(CodeReview).where(
        CodeReview.project_id == project_id,
        CodeReview.state.in_(("requested", "completed") if completed else ("requested",)),
    )
    if review_id is not None:
        query = query.where(CodeReview.id == review_id)
    elif work_item_id is None:
        return {}
    if work_item_id is not None:
        query = query.where(CodeReview.work_item_id == work_item_id)
    review = database.scalar(query.order_by(CodeReview.created_at.desc()).limit(1))
    if review is None:
        if review_id is not None:
            raise conflict(
                "code_review_changed", "Review changed. Refresh before copying its prompt."
            )
        return {}
    from mnemonic_api.services.code_review_records import require_requested

    work = require_work_item(database, project_id, review.work_item_id)
    try:
        if not completed or review.state != "completed":
            require_requested(database, work, review)
    except ApplicationError:
        if review_id is not None:
            raise
        return {}
    scope = database.get(CodeReviewScope, review.id)
    if scope is None:
        raise ApplicationError(503, "prompt_unavailable", "Review scope is unavailable.")
    allowed = {
        "repository_key",
        "repository_url",
        "checkout_path",
        "object_format",
        "base_commit",
        "head_commit",
    }
    repositories = [
        {key: value for key, value in row.items() if key in allowed} for row in scope.repositories
    ]
    routing = {
        "project_id": str(project_id),
        "work_item_id": str(review.work_item_id),
        "code_review_id": str(review.id),
        "review_version": review.version,
        "scope_sha256": review.scope_sha256,
        "prompt_version": 1,
    }
    return {
        "$CODE_REVIEW_ID": str(review.id),
        "$WORK_ITEM_ID": str(review.work_item_id),
        "$REVIEW_VERSION": str(review.version),
        "$REVIEW_SCOPE_SHA256": review.scope_sha256,
        "$REVIEW_ROUTING": json.dumps(routing, indent=2),
        "$REVIEW_SCOPE": json.dumps({"repositories": repositories}, indent=2),
    }


def _work_values(database: Session, project_id: UUID, work_id: UUID) -> dict[str, str]:
    work = require_work_item(database, project_id, work_id)
    return {
        "$WORK_ITEM_ID": str(work.id),
        "$WORK_ITEM_TITLE": work.title,
        "$WORK_ITEM_SUMMARY": work.summary,
        "$WORK_ITEM_STATUS": work.status,
        "$WORK_ITEM_PRIORITY": str(work.priority),
        "$WORK_ITEM_VERSION": str(work.version),
    }


def render_prompt(
    database: Session,
    project_id: UUID,
    prompt_id: str,
    *,
    work_item_id: UUID | None = None,
    code_review_id: UUID | None = None,
    extra: dict[str, str] | None = None,
    template: str | None = None,
) -> str:
    project = require_project(database, project_id)
    values = {"$PROJECT_ID": str(project_id)}
    values.update(
        {
            "$PROJECT_NAME": project.name,
            "$PROJECT_SLUG": project.slug,
            "$PROJECT_DESCRIPTION": project.description,
            "$PROJECT_REPOSITORY_URL": project.repository_url or "",
        }
    )
    settings = database.get(ProjectSettings, project_id)
    if settings is not None:
        values.update(
            {
                f"$LEASE_{name.upper()}_MINUTES": str(getattr(settings, f"lease_{name}_minutes"))
                for name in ("default", "minimum", "maximum")
            }
        )
    review_values = _review_values(
        database,
        project_id,
        work_item_id,
        code_review_id,
        completed=prompt_id == "review-remediation",
    )
    values.update(review_values)
    if work_item_id is None and "$WORK_ITEM_ID" in review_values:
        work_item_id = UUID(review_values["$WORK_ITEM_ID"])
    if work_item_id is not None:
        values.update(_work_values(database, project_id, work_item_id))
    if prompt_id == "cold-code-review" and "$CODE_REVIEW_ID" not in values:
        raise conflict("code_review_changed", "A current review is required for a cold prompt.")
    values.update(extra or {})
    body = (
        template
        if template is not None
        else storage_for(database).read(project_id, prompt_id).content
    )
    rendered = expand_macros(body, values)
    if len(rendered) > 100_000 or len(rendered.encode()) > 400_000:
        raise ApplicationError(422, "prompt_render_too_large", "Expanded prompt exceeds its limit.")
    if prompt_id == "review-recommendation":
        try:
            validate_prompt(prompt_id, rendered)
        except ValueError:
            raise ApplicationError(
                422, "prompt_render_too_large", "Expanded review recommendation exceeds its limits."
            ) from None
    return rendered
