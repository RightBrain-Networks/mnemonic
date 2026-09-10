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

from sqlalchemy import Text, and_, cast, exists, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_schemas import (
    ArtifactActor,
    ArtifactAuditRead,
    ArtifactExtractionRead,
    ArtifactExtractionStatus,
    ArtifactHistory,
    ArtifactHistoryQuery,
    ArtifactListQuery,
    ArtifactPage,
    ArtifactRead,
    ArtifactRevisionRead,
    ArtifactTextQuery,
    ArtifactTextRead,
    ArtifactUpdateMetadata,
    ArtifactUploadMetadata,
)
from mnemonic_api.artifact_storage import ArtifactStorage, StagedArtifact, UnsafeArtifactPath
from mnemonic_api.errors import ApplicationError, client_operation_conflict, conflict, not_found
from mnemonic_api.models import (
    Artifact,
    ArtifactAudit,
    ArtifactExtraction,
    ArtifactLink,
    ArtifactOperation,
    ArtifactRevision,
    ArtifactWorkLink,
    WorkItem,
)
from mnemonic_api.services.artifact_approvals import require_sensitive_access
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

type ArtifactOperationKind = Literal["upload", "replace", "delete", "update"]

logger = logging.getLogger(__name__)
_RECOVERY_ERRORS = (ApplicationError, OSError, SQLAlchemyError, UnsafeArtifactPath)


@dataclass(frozen=True)
class ArtifactMutation:
    project_id: UUID
    artifact_id: UUID
    client_operation_id: UUID
    kind: ArtifactOperationKind
    metadata: ArtifactUploadMetadata | ArtifactUpdateMetadata | ArtifactActor
    expected_revision: int | None = None
    staged: StagedArtifact | None = None
    content_sha256: str | None = None
    content_size_bytes: int | None = None

    def fingerprint(self) -> str:
        metadata = self.metadata.model_dump(mode="json")
        if isinstance(self.metadata, ArtifactUploadMetadata):
            # Preserve fingerprints of permanent receipts issued before these fields existed.
            if metadata["sensitive"] is None:
                metadata.pop("sensitive")
            if not metadata["related_artifact_ids"]:
                metadata.pop("related_artifact_ids")
        body = {
            "kind": self.kind,
            "artifact_id": str(self.artifact_id),
            "metadata": metadata,
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
        if key not in {
            "related_work_item_ids", "related_artifact_ids", "content_available", "extraction",
        }
    }
    return ArtifactRead(
        **fields,
        related_work_item_ids=[
            value for value in linked if value != artifact.originating_work_item_id
        ],
        related_artifact_ids=related_artifact_ids(database, artifact.id),
        content_available=artifact.revision > 0 and artifact.deleted_at is None,
        extraction=extraction_read(database, artifact.id, artifact.revision).model_copy(
            update={"metadata": {}} if artifact.sensitive else {},
        ),
    )


def extraction_read(database: Session, artifact_id: UUID, revision: int) -> ArtifactExtractionRead:
    extraction = database.get(ArtifactExtraction, (artifact_id, revision), populate_existing=True)
    if extraction is None:
        return ArtifactExtractionRead()
    return ArtifactExtractionRead.model_validate({
        "status": extraction.status,
        "metadata": extraction.extracted_metadata,
        "truncated": extraction.truncated,
        "error_code": extraction.error_code,
        "extracted_at": extraction.extracted_at,
    })


def related_artifact_ids(database: Session, artifact_id: UUID) -> list[UUID]:
    rows = database.execute(
        select(ArtifactLink.artifact_id, ArtifactLink.related_artifact_id).where(or_(
            ArtifactLink.artifact_id == artifact_id,
            ArtifactLink.related_artifact_id == artifact_id,
        ))
    )
    return sorted((right if left == artifact_id else left for left, right in rows), key=str)


def _validate_work_links(
    database: Session, project_id: UUID, ids: set[UUID],
) -> None:
    found = set(database.scalars(select(WorkItem.id).where(
        WorkItem.project_id == project_id, WorkItem.id.in_(ids), WorkItem.deleted_at.is_(None),
    )))
    if ids != found:
        raise not_found(
            "artifact_work_item_not_found", "A linked work item is not in this project.",
        )


def _add_work_links(
    database: Session, artifact: Artifact, ids: set[UUID],
) -> None:
    _validate_work_links(database, artifact.project_id, ids)
    if artifact.originating_work_item_id is not None:
        ids.add(artifact.originating_work_item_id)
    existing = set(database.scalars(select(ArtifactWorkLink.work_item_id).where(
        ArtifactWorkLink.artifact_id == artifact.id,
    )))
    if len((ids | existing) - {artifact.originating_work_item_id}) > 50:
        raise ApplicationError(
            422, "artifact_link_limit", "An artifact supports at most 50 related work links.",
        )
    for work_id in sorted(ids - existing, key=str):
        database.add(ArtifactWorkLink(artifact_id=artifact.id, work_item_id=work_id))


def _add_artifact_links(
    database: Session, artifact: Artifact, ids: set[UUID], actor: ArtifactActor,
) -> None:
    if artifact.id in ids:
        raise ApplicationError(422, "artifact_self_link", "An artifact cannot link to itself.")
    targets = list(database.scalars(select(Artifact).where(
        Artifact.id.in_(ids), Artifact.project_id == artifact.project_id,
        Artifact.deleted_at.is_(None), Artifact.revision > 0, ~_has_pending_operation(),
    )))
    if {target.id for target in targets} != ids:
        raise not_found(
            "artifact_related_artifact_not_found",
            "A related artifact is not available in this project.",
        )
    existing = set(related_artifact_ids(database, artifact.id))
    if len(existing | ids) > 50:
        raise ApplicationError(422, "artifact_link_limit", "An artifact supports at most 50 links.")
    for target in targets:
        if target.id not in existing:
            _add_artifact_pair(database, artifact, target, actor)


def _add_artifact_pair(
    database: Session, artifact: Artifact, target: Artifact, actor: ArtifactActor,
) -> None:
    if len(related_artifact_ids(database, target.id)) >= 50:
        raise ApplicationError(
            422, "artifact_link_limit", "A related artifact already has 50 artifact links.",
        )
    left, right = sorted((artifact.id, target.id), key=str)
    database.add(ArtifactLink(
        artifact_id=left, related_artifact_id=right, project_id=artifact.project_id,
    ))
    _audit(database, target, "linked", actor, {"related_artifact_id": str(artifact.id)})


def _add_links(
    database: Session, artifact: Artifact,
    metadata: ArtifactUploadMetadata | ArtifactUpdateMetadata,
) -> None:
    work_ids = set(metadata.related_work_item_ids or [])
    if isinstance(metadata, ArtifactUploadMetadata) and metadata.work_item_id is not None:
        work_ids.add(metadata.work_item_id)
    _add_work_links(database, artifact, work_ids)
    _add_artifact_links(database, artifact, set(metadata.related_artifact_ids or []), metadata)


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
    if isinstance(mutation.metadata, (ArtifactUploadMetadata, ArtifactUpdateMetadata)):
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
        sensitive=metadata.sensitive or False,
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


def _audit(
    database: Session, artifact: Artifact, action: str, actor: ArtifactActor,
    details: dict[str, Any] | None = None,
) -> None:
    database.add(
        ArtifactAudit(
            artifact_id=artifact.id,
            revision=artifact.revision,
            action=action,
            details=details or {},
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
    if metadata.sensitive is not None:
        artifact.sensitive = metadata.sensitive
    artifact.modified_at = datetime.now(UTC)
    _record_revision(database, artifact, metadata)
    _audit(database, artifact, "uploaded" if operation.kind == "upload" else "replaced", metadata)


def _record_revision(database: Session, artifact: Artifact, actor: ArtifactActor) -> None:
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
            agent_session_id=actor.agent_session_id,
            actor_client=actor.actor_client,
            related_work_item_ids=[str(value) for value in read.related_work_item_ids],
            related_artifact_ids=[str(value) for value in read.related_artifact_ids],
            sensitive=artifact.sensitive,
        )
    )
    database.flush()


def _publish_metadata_revision(
    database: Session, artifact: Artifact, operation: ArtifactOperation,
) -> None:
    metadata = ArtifactUpdateMetadata.model_validate(operation.intent["metadata"])
    if metadata.description is not None:
        artifact.description = metadata.description
    if metadata.sensitive is not None:
        artifact.sensitive = metadata.sensitive
    artifact.revision += 1
    artifact.modified_at = datetime.now(UTC)
    _record_revision(database, artifact, metadata)
    _audit(database, artifact, "metadata_updated", metadata, {
        "sensitive": artifact.sensitive,
        "related_work_item_ids": [str(value) for value in metadata.related_work_item_ids or []],
        "related_artifact_ids": [str(value) for value in metadata.related_artifact_ids or []],
    })


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
    elif operation.kind == "update":
        _publish_metadata_revision(database, artifact, operation)
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


def _extraction_metadata_match(artifact_id, query: str, revision=None):
    clauses = [
        ArtifactExtraction.artifact_id == artifact_id,
        cast(ArtifactExtraction.extracted_metadata, Text).icontains(query, autoescape=True),
    ]
    if revision is not None:
        clauses.append(ArtifactExtraction.revision == revision)
    historical = aliased(ArtifactRevision)
    return exists(select(ArtifactExtraction.artifact_id).join(historical, and_(
        historical.artifact_id == ArtifactExtraction.artifact_id,
        historical.revision == ArtifactExtraction.revision,
        historical.sensitive.is_(False),
    )).where(*clauses))


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
        clauses.append(or_(
            _metadata_match(Artifact, filters.q),
            (Artifact.sensitive.is_(False) & _extraction_metadata_match(Artifact.id, filters.q)),
            *historical,
        ))
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
    artifact = require_artifact(database, project_id, artifact_id)
    pages: dict[str, Any] = {}
    for name, model, schema, order in (
        ("revisions", ArtifactRevision, ArtifactRevisionRead, ArtifactRevision.revision.desc()),
        ("audit", ArtifactAudit, ArtifactAuditRead, ArtifactAudit.id.desc()),
    ):
        clauses = [model.artifact_id == artifact_id]
        if filters.q:
            match = _metadata_match(model, filters.q)
            if model is ArtifactRevision and not artifact.sensitive:
                match = or_(match, _extraction_metadata_match(
                    ArtifactRevision.artifact_id, filters.q, ArtifactRevision.revision,
                ))
            clauses.append(match)
        total = database.scalar(select(func.count()).select_from(model).where(*clauses)) or 0
        rows = database.scalars(
            select(model)
            .where(*clauses)
            .order_by(order)
            .limit(filters.limit)
            .offset(filters.offset)
        ).all()
        pages[name] = {
            "items": [_history_read(database, schema, row, artifact.sensitive) for row in rows],
            "total": total,
            "limit": filters.limit,
            "offset": filters.offset,
        }
    return ArtifactHistory(**pages)


def _history_read(database: Session, schema, row, sensitive: bool):
    result = schema.model_validate(row)
    if isinstance(result, ArtifactRevisionRead):
        result.extraction = extraction_read(database, row.artifact_id, row.revision)
        if sensitive or result.sensitive:
            result.extraction.metadata = {}
    return result


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
    *, access: ArtifactAccessRequest | None = None, human_dashboard: bool = False,
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
        require_sensitive_access(
            database, artifact, "download",
            access or ArtifactAccessRequest(**actor.model_dump()),
            {"expected_revision": expected_revision}, human_dashboard=human_dashboard,
        )
        content = storage.open(artifact.relative_path)
        try:
            _audit(database, artifact, "downloaded", actor)
            result = artifact_read(database, artifact)
            database.commit()
            return result, content
        except BaseException:
            content.close()
            raise


def _artifact_text_page(
    database: Session, artifact: Artifact, filters: ArtifactTextQuery,
) -> ArtifactTextRead:
    # PostgreSQL slices Unicode characters, matching Python code-point offsets.
    # Neither the full normalized text nor extracted metadata leaves the database.
    text_column = func.coalesce(ArtifactExtraction.normalized_text, "")
    row = database.execute(select(
        ArtifactExtraction.status,
        ArtifactExtraction.truncated,
        ArtifactExtraction.error_code,
        ArtifactExtraction.extracted_at,
        func.substring(text_column, filters.offset + 1, filters.limit).label("text"),
        func.char_length(text_column).label("total_chars"),
    ).where(
        ArtifactExtraction.artifact_id == artifact.id,
        ArtifactExtraction.revision == artifact.revision,
    )).mappings().one_or_none()
    extraction = ArtifactExtractionStatus() if row is None else ArtifactExtractionStatus(
        **{key: row[key] for key in ArtifactExtractionStatus.model_fields},
    )
    ready = row is not None and extraction.status == "ready"
    text_page = row["text"] if ready else None
    total_chars = row["total_chars"] if ready else None
    next_offset = None
    if total_chars is not None and filters.offset + filters.limit < total_chars:
        next_offset = filters.offset + filters.limit
    return ArtifactTextRead(
        project_id=artifact.project_id,
        artifact_id=artifact.id,
        revision=artifact.revision,
        sha256=artifact.sha256,
        extraction=extraction,
        text=text_page,
        offset=filters.offset,
        limit=filters.limit,
        total_chars=total_chars,
        next_offset=next_offset,
    )


def read_artifact_text(
    database: Session,
    storage: ArtifactStorage,
    project_id: UUID,
    artifact_id: UUID,
    filters: ArtifactTextQuery,
    *, access: ArtifactAccessRequest | None = None, human_dashboard: bool = False,
) -> ArtifactTextRead:
    """Read a bounded page from one current revision without adding download history."""
    recover_artifact(database, storage, project_id, artifact_id)
    with project_mutation(database, project_id, protected=True, domain_seconds=120):
        artifact = require_artifact(database, project_id, artifact_id)
        database.refresh(artifact)
        pending = _pending_operation(database, artifact.id)
        if pending is not None:
            _finish_intent(database, storage, pending)
        if artifact.deleted_at is not None:
            raise ApplicationError(410, "artifact_deleted", "Artifact content has been deleted.")
        if filters.expected_revision != artifact.revision:
            raise conflict("artifact_revision_conflict", "The artifact revision changed.")
        require_sensitive_access(
            database, artifact, "text", access or ArtifactAccessRequest(),
            filters.model_dump(mode="json"), human_dashboard=human_dashboard,
        )
        # Preserve the same current-content availability boundary as binary reads.
        with storage.open(artifact.relative_path):
            result = _artifact_text_page(database, artifact, filters)
        database.commit()
        return result
