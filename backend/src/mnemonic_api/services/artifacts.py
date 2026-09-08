"""Recoverable filesystem mutations and searchable, append-only artifact metadata.

The first commit records a complete intent. The second transaction atomically
publishes or unlinks bytes, appends history, and completes its receipt. Every
artifact entry point resumes committed intents before exposing content. A crash
after publication is recovered by verifying the current file against the intent;
old bytes are never copied aside, put in a database, or kept as a revision.
"""

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, BinaryIO, Literal
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from mnemonic_api.artifact_schemas import (
    ArtifactActor,
    ArtifactAuditRead,
    ArtifactHistory,
    ArtifactHistoryQuery,
    ArtifactListQuery,
    ArtifactPage,
    ArtifactRead,
    ArtifactRevisionRead,
    ArtifactUploadMetadata,
)
from mnemonic_api.artifact_storage import ArtifactStorage, StagedArtifact, UnsafeArtifactPath
from mnemonic_api.errors import ApplicationError, client_operation_conflict, conflict, not_found
from mnemonic_api.models import (
    Artifact,
    ArtifactAudit,
    ArtifactOperation,
    ArtifactRevision,
    ArtifactWorkLink,
    WorkItem,
)
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

type ArtifactOperationKind = Literal["upload", "replace", "delete"]

logger = logging.getLogger(__name__)
_RECOVERY_ERRORS = (ApplicationError, OSError, SQLAlchemyError, UnsafeArtifactPath)


@dataclass(frozen=True)
class ArtifactMutation:
    project_id: UUID
    artifact_id: UUID
    client_operation_id: UUID
    kind: ArtifactOperationKind
    metadata: ArtifactUploadMetadata | ArtifactActor
    expected_revision: int | None = None
    staged: StagedArtifact | None = None
    content_sha256: str | None = None
    content_size_bytes: int | None = None

    def fingerprint(self) -> str:
        body = {
            "kind": self.kind,
            "artifact_id": str(self.artifact_id),
            "metadata": self.metadata.model_dump(mode="json"),
            "expected_revision": self.expected_revision,
            "sha256": self.staged.sha256 if self.staged else self.content_sha256,
            "size_bytes": self.staged.size_bytes if self.staged else self.content_size_bytes,
        }
        return hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


def require_artifact(database: Session, project_id: UUID, artifact_id: UUID) -> Artifact:
    artifact = database.scalar(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.id == artifact_id)
    )
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found in this project.")
    return artifact


def artifact_read(database: Session, artifact: Artifact) -> ArtifactRead:
    linked = list(
        database.scalars(
            select(ArtifactWorkLink.work_item_id)
            .where(ArtifactWorkLink.artifact_id == artifact.id)
            .order_by(ArtifactWorkLink.work_item_id)
        )
    )
    fields = {
        key: getattr(artifact, key)
        for key in ArtifactRead.model_fields
        if key not in {"related_work_item_ids", "content_available"}
    }
    return ArtifactRead(
        **fields,
        related_work_item_ids=[
            value for value in linked if value != artifact.originating_work_item_id
        ],
        content_available=artifact.revision > 0 and artifact.deleted_at is None,
    )


def _validate_links(database: Session, project_id: UUID, metadata: ArtifactUploadMetadata) -> None:
    ids = set(metadata.related_work_item_ids)
    if metadata.work_item_id is not None:
        ids.add(metadata.work_item_id)
    found = set(
        database.scalars(
            select(WorkItem.id).where(
                WorkItem.project_id == project_id,
                WorkItem.id.in_(ids),
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    if ids != found:
        raise not_found(
            "artifact_work_item_not_found", "A linked work item is not in this project."
        )


def _add_links(database: Session, artifact: Artifact, metadata: ArtifactUploadMetadata) -> None:
    ids = set(metadata.related_work_item_ids)
    if artifact.originating_work_item_id is not None:
        ids.add(artifact.originating_work_item_id)
    existing = set(
        database.scalars(
            select(ArtifactWorkLink.work_item_id).where(ArtifactWorkLink.artifact_id == artifact.id)
        )
    )
    if len((ids | existing) - {artifact.originating_work_item_id}) > 50:
        raise ApplicationError(
            422, "artifact_link_limit", "An artifact supports at most 50 related links."
        )
    for work_id in sorted(ids - existing, key=str):
        database.add(ArtifactWorkLink(artifact_id=artifact.id, work_item_id=work_id))


def _validate_mutation(database: Session, artifact: Artifact, mutation: ArtifactMutation) -> None:
    if artifact.deleted_at is not None:
        raise ApplicationError(410, "artifact_deleted", "Artifact content has been deleted.")
    if mutation.kind != "upload" and artifact.revision != mutation.expected_revision:
        raise conflict(
            "artifact_revision_conflict", "The artifact revision changed. Reload metadata."
        )
    if isinstance(mutation.metadata, ArtifactUploadMetadata):
        if mutation.metadata.filename != artifact.filename:
            raise conflict(
                "artifact_filename_immutable", "Replacement must keep the original name."
            )
        origin = mutation.metadata.work_item_id
        if origin is not None and origin != artifact.originating_work_item_id:
            raise conflict("artifact_origin_immutable", "The originating work item cannot change.")
        _validate_links(database, artifact.project_id, mutation.metadata)
        _add_links(database, artifact, mutation.metadata)


def _create_identity(database: Session, mutation: ArtifactMutation) -> Artifact:
    metadata = mutation.metadata
    if not isinstance(metadata, ArtifactUploadMetadata) or mutation.staged is None:
        raise ValueError("Upload requires staged content and upload metadata")
    artifact = Artifact(
        id=mutation.artifact_id,
        project_id=mutation.project_id,
        filename=metadata.filename,
        description=metadata.description or "",
        relative_path=mutation.staged.relative_path,
        created_by_agent_session_id=metadata.agent_session_id,
        created_by_client=metadata.actor_client,
        originating_work_item_id=metadata.work_item_id,
    )
    database.add(artifact)
    database.flush()
    return artifact


def _existing_operation(database: Session, mutation: ArtifactMutation) -> ArtifactOperation | None:
    return database.scalar(
        select(ArtifactOperation).where(
            ArtifactOperation.project_id == mutation.project_id,
            ArtifactOperation.client_operation_id == mutation.client_operation_id,
        )
    )


def _pending_operation(database: Session, artifact_id: UUID) -> ArtifactOperation | None:
    return database.scalar(
        select(ArtifactOperation).where(
            ArtifactOperation.artifact_id == artifact_id, ArtifactOperation.state == "pending"
        )
    )


def prepare_upload_replay(database: Session, mutation: ArtifactMutation) -> tuple[str, int] | None:
    """Bind replay metadata before fresh upload limits or filesystem staging."""
    require_project(database, mutation.project_id)
    operation = _existing_operation(database, mutation)
    if operation is None:
        return None
    staged = operation.intent.get("staged")
    if not staged:
        raise client_operation_conflict()
    candidate = replace(
        mutation, content_sha256=staged["sha256"], content_size_bytes=staged["size_bytes"]
    )
    if candidate.fingerprint() != operation.fingerprint:
        raise client_operation_conflict()
    return staged["sha256"], staged["size_bytes"]


def replay_artifact_upload(
    database: Session, storage: ArtifactStorage, mutation: ArtifactMutation
) -> tuple[dict[str, Any], bool]:
    """Replay verified bytes without staging or requiring writable current content storage."""
    with project_mutation(database, mutation.project_id, protected=True, domain_seconds=120):
        operation = _existing_operation(database, mutation)
        if operation is None or operation.fingerprint != mutation.fingerprint():
            raise client_operation_conflict()
        result = _finish_intent(database, storage, operation)
        database.commit()
        return result, True


def _audit(database: Session, artifact: Artifact, action: str, actor: ArtifactActor) -> None:
    database.add(
        ArtifactAudit(
            artifact_id=artifact.id,
            revision=artifact.revision,
            action=action,
            filename=artifact.filename,
            description=artifact.description,
            agent_session_id=actor.agent_session_id,
            actor_client=actor.actor_client,
        )
    )


def _publish_revision(
    database: Session, storage: ArtifactStorage, artifact: Artifact, operation: ArtifactOperation
) -> None:
    staged = StagedArtifact(**operation.intent["staged"])
    storage.publish(staged)
    metadata = ArtifactUploadMetadata.model_validate(operation.intent["metadata"])
    artifact.revision += 1
    artifact.size_bytes = staged.size_bytes
    artifact.sha256 = staged.sha256
    artifact.mime_type = staged.mime_type
    if metadata.description is not None:
        artifact.description = metadata.description
    artifact.modified_at = datetime.now(UTC)
    read = artifact_read(database, artifact)
    database.add(
        ArtifactRevision(
            artifact_id=artifact.id,
            revision=artifact.revision,
            filename=artifact.filename,
            description=artifact.description,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            mime_type=artifact.mime_type,
            agent_session_id=metadata.agent_session_id,
            actor_client=metadata.actor_client,
            related_work_item_ids=[str(value) for value in read.related_work_item_ids],
        )
    )
    database.flush()
    _audit(database, artifact, "uploaded" if operation.kind == "upload" else "replaced", metadata)


def _finish_intent(
    database: Session, storage: ArtifactStorage, operation: ArtifactOperation
) -> dict[str, Any]:
    if operation.state == "completed":
        if operation.response_body is None:
            raise RuntimeError("A completed artifact receipt must contain its response")
        return operation.response_body
    artifact = require_artifact(database, operation.project_id, operation.artifact_id)
    if operation.kind == "delete":
        storage.delete(artifact.relative_path)
        actor = ArtifactActor.model_validate(operation.intent["metadata"])
        deleted_at = datetime.now(UTC)
        artifact.deleted_at = deleted_at
        artifact.modified_at = deleted_at
        _audit(database, artifact, "deleted", actor)
    else:
        _publish_revision(database, storage, artifact, operation)
    database.flush()
    response = artifact_read(database, artifact).model_dump(mode="json")
    operation.state = "completed"
    operation.response_body = response
    operation.completed_at = datetime.now(UTC)
    database.flush()
    return response


def _reserve_intent(
    database: Session, storage: ArtifactStorage, mutation: ArtifactMutation
) -> tuple[ArtifactOperation, bool]:
    existing = _existing_operation(database, mutation)
    if existing is not None:
        if existing.fingerprint != mutation.fingerprint():
            raise client_operation_conflict()
        _finish_intent(database, storage, existing)
        return existing, True
    if mutation.kind == "upload":
        artifact = _create_identity(database, mutation)
    else:
        artifact = require_artifact(database, mutation.project_id, mutation.artifact_id)
        pending = _pending_operation(database, artifact.id)
        if pending is not None:
            _finish_intent(database, storage, pending)
    _validate_mutation(database, artifact, mutation)
    operation = ArtifactOperation(
        project_id=mutation.project_id,
        artifact_id=artifact.id,
        client_operation_id=mutation.client_operation_id,
        kind=mutation.kind,
        fingerprint=mutation.fingerprint(),
        intent={
            "metadata": mutation.metadata.model_dump(mode="json"),
            "staged": asdict(mutation.staged) if mutation.staged else None,
        },
    )
    database.add(operation)
    database.flush()
    return operation, False


def mutate_artifact(
    database: Session, storage: ArtifactStorage, mutation: ArtifactMutation
) -> tuple[dict[str, Any], bool]:
    """Reserve durably, publish atomically, then return or replay a permanent receipt."""
    commit_attempted = False
    replayed = False
    try:
        with project_mutation(database, mutation.project_id, protected=True, domain_seconds=120):
            operation, replayed = _reserve_intent(database, storage, mutation)
            commit_attempted = True
            database.commit()
        if replayed:
            if mutation.staged is not None:
                storage.discard(mutation.staged)
            return _finish_intent(database, storage, operation), True
        with project_mutation(database, mutation.project_id, protected=True, domain_seconds=120):
            # Reload because another worker may have recovered the committed intent.
            database.refresh(operation)
            response = _finish_intent(database, storage, operation)
            database.commit()
            return response, False
    except BaseException:
        # A failed/ambiguous commit may own this stage. Never discard durable work.
        if not commit_attempted and mutation.staged is not None:
            storage.discard(mutation.staged)
        raise


def recover_artifact(
    database: Session, storage: ArtifactStorage, project_id: UUID, artifact_id: UUID
) -> None:
    """Recover one artifact, committing its progress independently of other intents."""
    with project_mutation(database, project_id, protected=True, domain_seconds=120):
        require_artifact(database, project_id, artifact_id)
        operation = _pending_operation(database, artifact_id)
        if operation is not None:
            _finish_intent(database, storage, operation)
        database.commit()


def _rollback_recovery(database: Session) -> None:
    try:
        database.rollback()
    except SQLAlchemyError:
        database.invalidate()


def recover_project_artifacts(
    database: Session, storage: ArtifactStorage, project_id: UUID
) -> None:
    """Attempt each pending artifact without letting one failure block healthy reads."""
    require_project(database, project_id)
    artifact_ids = list(database.scalars(
        select(ArtifactOperation.artifact_id)
        .where(ArtifactOperation.project_id == project_id, ArtifactOperation.state == "pending")
        .order_by(ArtifactOperation.created_at)
    ))
    database.commit()
    for artifact_id in artifact_ids:
        try:
            recover_artifact(database, storage, project_id, artifact_id)
        except _RECOVERY_ERRORS as error:
            _rollback_recovery(database)
            logger.warning(
                "Artifact recovery unavailable for artifact %s (%s)",
                artifact_id, type(error).__name__,
            )


def recover_all_artifacts(database: Session, storage: ArtifactStorage) -> None:
    projects = list(
        database.scalars(
            select(ArtifactOperation.project_id)
            .where(ArtifactOperation.state == "pending")
            .distinct()
        )
    )
    database.commit()
    for project_id in projects:
        try:
            recover_project_artifacts(database, storage, project_id)
        except _RECOVERY_ERRORS as error:
            _rollback_recovery(database)
            logger.warning(
                "Artifact recovery unavailable for project %s (%s)",
                project_id, type(error).__name__,
            )


def _has_pending_operation():
    return exists(
        select(ArtifactOperation.id).where(
            ArtifactOperation.artifact_id == Artifact.id, ArtifactOperation.state == "pending"
        )
    )


def _metadata_match(model: Any, query: str):
    columns = [model.filename, model.description]
    if model in (Artifact, ArtifactRevision):
        columns.extend([model.mime_type, model.sha256])
    if model is Artifact:
        columns.extend([model.created_by_agent_session_id, model.created_by_client])
    else:
        columns.extend([model.agent_session_id, model.actor_client])
    if model is ArtifactAudit:
        columns.append(model.action)
    return or_(*(column.icontains(query, autoescape=True) for column in columns))


def list_artifacts(
    database: Session, project_id: UUID, filters: ArtifactListQuery
) -> ArtifactPage[ArtifactRead]:
    require_project(database, project_id)
    clauses = [
        Artifact.project_id == project_id, Artifact.revision > 0, ~_has_pending_operation(),
    ]
    if not filters.include_deleted:
        clauses.append(Artifact.deleted_at.is_(None))
    if filters.work_item_id is not None:
        clauses.append(
            exists(
                select(ArtifactWorkLink.artifact_id).where(
                    ArtifactWorkLink.artifact_id == Artifact.id,
                    ArtifactWorkLink.work_item_id == filters.work_item_id,
                )
            )
        )
    if filters.q:
        historical = [
            exists(
                select(model.artifact_id).where(
                    model.artifact_id == Artifact.id, _metadata_match(model, filters.q)
                )
            )
            for model in (ArtifactRevision, ArtifactAudit)
        ]
        clauses.append(or_(_metadata_match(Artifact, filters.q), *historical))
    total = database.scalar(select(func.count()).select_from(Artifact).where(*clauses)) or 0
    column = getattr(Artifact, filters.sort)
    ordering = column.desc() if filters.order == "desc" else column.asc()
    items = database.scalars(
        select(Artifact)
        .where(*clauses)
        .order_by(ordering, Artifact.id)
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    return ArtifactPage(
        items=[artifact_read(database, item) for item in items],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def artifact_history(
    database: Session, project_id: UUID, artifact_id: UUID, filters: ArtifactHistoryQuery
) -> ArtifactHistory:
    require_artifact(database, project_id, artifact_id)
    pages: dict[str, Any] = {}
    for name, model, schema, order in (
        ("revisions", ArtifactRevision, ArtifactRevisionRead, ArtifactRevision.revision.desc()),
        ("audit", ArtifactAudit, ArtifactAuditRead, ArtifactAudit.id.desc()),
    ):
        clauses = [model.artifact_id == artifact_id]
        if filters.q:
            clauses.append(_metadata_match(model, filters.q))
        total = database.scalar(select(func.count()).select_from(model).where(*clauses)) or 0
        rows = database.scalars(
            select(model)
            .where(*clauses)
            .order_by(order)
            .limit(filters.limit)
            .offset(filters.offset)
        ).all()
        pages[name] = {
            "items": [schema.model_validate(row) for row in rows],
            "total": total,
            "limit": filters.limit,
            "offset": filters.offset,
        }
    return ArtifactHistory(**pages)


def work_artifacts(
    database: Session, project_id: UUID, work_item_id: UUID
) -> tuple[list[ArtifactRead], int]:
    clauses = [
        ArtifactWorkLink.work_item_id == work_item_id,
        Artifact.project_id == project_id,
        Artifact.deleted_at.is_(None),
        Artifact.revision > 0,
        ~_has_pending_operation(),
    ]
    query = select(Artifact).join(ArtifactWorkLink).where(*clauses)
    total = database.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = database.scalars(query.order_by(Artifact.modified_at.desc(), Artifact.id).limit(20))
    return [artifact_read(database, item) for item in items], total


def open_artifact(
    database: Session,
    storage: ArtifactStorage,
    project_id: UUID,
    artifact_id: UUID,
    expected_revision: int | None,
    actor: ArtifactActor,
) -> tuple[ArtifactRead, BinaryIO]:
    with project_mutation(database, project_id, protected=True, domain_seconds=120):
        artifact = require_artifact(database, project_id, artifact_id)
        pending = _pending_operation(database, artifact.id)
        if pending is not None:
            _finish_intent(database, storage, pending)
        if artifact.deleted_at is not None:
            raise ApplicationError(410, "artifact_deleted", "Artifact content has been deleted.")
        if expected_revision is not None and expected_revision != artifact.revision:
            raise conflict("artifact_revision_conflict", "The artifact revision changed.")
        content = storage.open(artifact.relative_path)
        try:
            _audit(database, artifact, "downloaded", actor)
            result = artifact_read(database, artifact)
            database.commit()
            return result, content
        except BaseException:
            content.close()
            raise


def search_artifact_contents() -> None:
    """Reserved extension point. Untrusted artifact contents are not parsed or indexed."""
    raise ApplicationError(501, "unimplemented", "Artifact content search is unimplemented.")
