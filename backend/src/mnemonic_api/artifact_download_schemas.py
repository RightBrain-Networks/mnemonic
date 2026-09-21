"""One-use download capabilities issued only to authenticated clients."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.schemas import APIModel


class ArtifactDownloadRequest(ArtifactAccessRequest):
    expected_revision: int = Field(ge=1)
    agent_session_id: str = Field(min_length=1, max_length=200)
    actor_client: str = Field(min_length=1, max_length=80)


class ArtifactDownloadGrant(APIModel):
    project_id: UUID
    artifact_id: UUID
    revision: int = Field(ge=1)
    size_bytes: int = Field(ge=0, le=1024 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    download_token: str = Field(repr=False, pattern=r"^[A-Za-z0-9_-]{43}$")
    expires_at: datetime
