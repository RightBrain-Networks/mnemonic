"""Durable single-use grants, including across MCP replicas and restarts."""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_download_schemas import ArtifactDownloadGrant, ArtifactDownloadRequest
from mnemonic_api.artifact_schemas import ArtifactActor
from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import Artifact, ArtifactDownloadCapability
from mnemonic_api.services.artifact_approvals import require_sensitive_access
from mnemonic_api.services.project_mutations import project_mutation


def issue_download(
    database: Session, project_id: UUID, artifact_id: UUID, access: ArtifactDownloadRequest,
) -> ArtifactDownloadGrant:
    from mnemonic_api.services.artifacts import require_artifact

    with project_mutation(database, project_id, protected=True):
        artifact = require_artifact(database, project_id, artifact_id)
        if artifact.deleted_at is not None:
            raise ApplicationError(410, "artifact_deleted", "Artifact bytes have been deleted.")
        if artifact.revision != access.expected_revision:
            raise conflict("artifact_revision_conflict", "The artifact revision changed.",
                           context={"current_revision": artifact.revision})
        require_sensitive_access(
            database, artifact, "download", access,
            {"expected_revision": access.expected_revision}, audit_read=False,
        )
        token = secrets.token_urlsafe(32)
        now = cast(datetime, database.scalar(select(func.clock_timestamp())))
        expires = now + timedelta(minutes=5)
        database.add(ArtifactDownloadCapability(
            token_hash=hashlib.sha256(token.encode()).hexdigest(), artifact_id=artifact.id,
            revision=artifact.revision, agent_session_id=access.agent_session_id,
            actor_client=access.actor_client, expires_at=expires,
        ))
        result = ArtifactDownloadGrant(
            project_id=project_id, artifact_id=artifact_id, revision=artifact.revision,
            size_bytes=artifact.size_bytes, sha256=artifact.sha256,
            download_token=token, expires_at=expires,
        )
        database.commit()
        return result


def consume_download(database: Session, artifact: Artifact, token: str) -> ArtifactActor:
    """Caller holds the project lock; consumption commits with the pinned file open."""
    grant = database.scalar(select(ArtifactDownloadCapability).where(
        ArtifactDownloadCapability.token_hash == hashlib.sha256(token.encode()).hexdigest(),
        ArtifactDownloadCapability.artifact_id == artifact.id,
        ArtifactDownloadCapability.revision == artifact.revision,
        ArtifactDownloadCapability.expires_at > func.clock_timestamp(),
        ArtifactDownloadCapability.consumed_at.is_(None),
    ).with_for_update())
    if grant is None:
        raise ApplicationError(401, "artifact_download_grant_invalid",
                               "Download grant expired, consumed, or invalid.")
    grant.consumed_at = database.scalar(select(func.clock_timestamp()))
    return ArtifactActor(agent_session_id=grant.agent_session_id, actor_client=grant.actor_client)
