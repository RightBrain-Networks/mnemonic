"""Bound explicit multi-project discovery before materializing source corpora."""

from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Artifact, ArtifactExtraction, WorkItem

MAX_DOCUMENTS = 10_000
MAX_METADATA_BYTES = 32_000_000
MAX_ARTIFACT_CONTENT_BYTES = 128 * 1024 * 1024


def _capacity() -> ApplicationError:
    return ApplicationError(
        503, "multi_project_search_capacity",
        "Selected projects exceed search capacity; narrow the project or source selection.",
    )


def check_work_scope(database: Session, projects) -> None:
    metadata_bytes = (
        func.octet_length(WorkItem.title) + func.octet_length(WorkItem.summary)
        + func.coalesce(func.octet_length(cast(WorkItem.external_references, Text)), 0) + 640
    )
    count, size = database.execute(select(func.count(), func.sum(metadata_bytes))
        .select_from(WorkItem).where(
            WorkItem.project_id.in_(projects), WorkItem.deleted_at.is_(None),
        )).one()
    if count > MAX_DOCUMENTS or (size or 0) > MAX_METADATA_BYTES:
        raise _capacity()


def check_artifact_metadata(database: Session, clauses) -> None:
    metadata_bytes = (
        func.octet_length(Artifact.filename) + func.octet_length(Artifact.description)
        + func.coalesce(func.octet_length(Artifact.mime_type), 0)
        + func.coalesce(func.octet_length(Artifact.created_by_agent_session_id), 0)
        + func.coalesce(func.octet_length(Artifact.created_by_client), 0)
        + func.coalesce(
            func.octet_length(cast(ArtifactExtraction.extracted_metadata, Text)), 0,
        ) + 256
    )
    count, size = database.execute(select(func.count(), func.sum(metadata_bytes))
        .select_from(Artifact).outerjoin(ArtifactExtraction,
            (ArtifactExtraction.artifact_id == Artifact.id)
            & (ArtifactExtraction.revision == Artifact.revision)).where(*clauses)).one()
    if count > MAX_DOCUMENTS or (size or 0) > MAX_METADATA_BYTES:
        raise _capacity()


def check_artifact_content(database: Session, corpus, approved) -> None:
    allowed = [artifact.id for artifact, extraction in corpus
               if artifact.deleted_at is None
               and (not artifact.sensitive or artifact.id in approved)
               and extraction is not None and extraction.status == "ready"]
    if not allowed:
        return
    size = database.scalar(select(func.sum(func.octet_length(ArtifactExtraction.normalized_text)))
        .join(Artifact, (ArtifactExtraction.artifact_id == Artifact.id)
              & (ArtifactExtraction.revision == Artifact.revision))
        .where(Artifact.id.in_(allowed))) or 0
    if size > MAX_ARTIFACT_CONTENT_BYTES:
        raise _capacity()
