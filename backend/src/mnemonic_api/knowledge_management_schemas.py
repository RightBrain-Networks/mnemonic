"""Human-only knowledge deletion with an exact reviewed graph snapshot."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints

from mnemonic_api.knowledge_schemas import DecisionIdentifier, RevisionNumber
from mnemonic_api.schemas import APIModel

PreviewToken = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class KnowledgeDeletionPreview(APIModel):
    """Relationship totals include removable edges and retained active review obligations."""
    project_id: UUID
    domain_id: UUID
    decision_id: DecisionIdentifier | None = None
    document_count: int
    relationship_count: int
    removed_relationship_count: int
    affected_review_count: int
    relationships: dict[str, int]
    expected_version: int | None = None
    expected_revision: int | None = None
    preview_token: PreviewToken
    blocked_reason: str | None = None


class KnowledgeDeletionRequest(APIModel):
    expected_preview: PreviewToken
    acknowledge_relationships: StrictBool


class DomainDeletionRequest(KnowledgeDeletionRequest):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class DomainDeletion(DomainDeletionRequest):
    client_operation_id: UUID


class DecisionDeletionRequest(KnowledgeDeletionRequest):
    expected_revision: RevisionNumber


class DecisionDeletion(DecisionDeletionRequest):
    client_operation_id: UUID


class KnowledgeDeletionRead(APIModel):
    project_id: UUID
    domain_id: UUID
    document_id: UUID | None = None
    decision_id: DecisionIdentifier | None = None
    deleted_at: datetime
    document_count: int
    relationship_count: int
    removed_relationship_count: int
    affected_review_count: int
    relationships: dict[str, int]
    preview_token: PreviewToken
