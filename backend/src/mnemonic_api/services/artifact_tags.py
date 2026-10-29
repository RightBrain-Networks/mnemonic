"""Current artifact labels share the project catalog and retain revision history."""

from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_schemas import ArtifactUpdateMetadata
from mnemonic_api.errors import conflict
from mnemonic_api.models import Artifact, ArtifactOperation


def artifact_tag_condition(tags: list[str], mode: str):
    return Artifact.tags.contains(tags) if mode == "and" else Artifact.tags.overlap(tags)


def change_artifact_assignments(
    database: Session, project_id: UUID, name: str, replacement: str | None,
) -> None:
    affected = select(Artifact.id).where(
        Artifact.project_id == project_id, Artifact.deleted_at.is_(None),
        Artifact.tags.contains([name]),
    )
    pending = database.scalar(select(ArtifactOperation.id).where(
        ArtifactOperation.project_id == project_id, ArtifactOperation.state == "pending",
        or_(ArtifactOperation.artifact_id.in_(affected),
            ArtifactOperation.intent["metadata"]["tags"].contains([name])),
    ).limit(1))
    if pending is not None:
        raise conflict("artifact_tags_busy",
                       "A file update is still pending. Open the artifact library, then retry.")
    items = database.scalars(select(Artifact).where(Artifact.id.in_(affected))
                            .order_by(Artifact.id).with_for_update()).all()
    for item in items:
        values = [replacement if tag == name else tag for tag in item.tags or []]
        _record_assignment(database, item, [tag for tag in values if tag is not None] or None)


def _record_assignment(database: Session, artifact: Artifact, tags: list[str] | None) -> None:
    from mnemonic_api.services.artifacts import (
        ArtifactMutation,
        _intent_metadata,
        _publish_metadata_revision,
        artifact_read,
    )
    from mnemonic_api.services.work_events import database_now

    # The parent catalog receipt commits these child revisions atomically. The normal
    # metadata intent also lets the extraction trigger reuse unchanged current content.
    metadata = ArtifactUpdateMetadata(client_operation_id=uuid4(),
        expected_revision=artifact.revision, actor_client="dashboard", tags=tags)
    mutation = ArtifactMutation(project_id=artifact.project_id, artifact_id=artifact.id,
        client_operation_id=metadata.client_operation_id, kind="update", metadata=metadata,
        expected_revision=artifact.revision)
    operation = ArtifactOperation(project_id=artifact.project_id, artifact_id=artifact.id,
        client_operation_id=metadata.client_operation_id, kind="update",
        fingerprint=mutation.fingerprint(),
        intent={"metadata": _intent_metadata(metadata), "staged": None})
    database.add(operation)
    database.flush()
    _publish_metadata_revision(database, artifact, operation)
    # Read the clock and the response body before completing the operation: a
    # query between the terminal assignments would autoflush a completed row
    # that still has no completed_at, which artifact_operations_check rejects.
    completed = database_now(database)
    operation.response_body = artifact_read(database, artifact).model_dump(mode="json")
    operation.state = "completed"
    operation.completed_at = completed
    database.flush()
