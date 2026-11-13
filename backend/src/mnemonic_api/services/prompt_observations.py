"""Observe file-backed prompt hashes without copying prompt bodies into activity."""

from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mnemonic_api.models import ProjectPromptObservation


def observe_prompt(database: Session, project_id: UUID, prompt_id: str, sha256: str) -> None:
    """Caller holds the project lock; the first observation establishes a baseline."""
    statement = insert(ProjectPromptObservation).values(
        project_id=project_id, prompt_id=prompt_id, sha256=sha256,
    )
    database.execute(statement.on_conflict_do_update(
        index_elements=[ProjectPromptObservation.project_id, ProjectPromptObservation.prompt_id],
        set_={"sha256": statement.excluded.sha256, "observed_at": func.clock_timestamp()},
        where=ProjectPromptObservation.sha256 != statement.excluded.sha256,
    ))
