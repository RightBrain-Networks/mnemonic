"""Repository lookup and immutable reconciliation of original and merged commits."""

from uuid import UUID

from mnemonic_api.provenance_schemas import CommitHash, RepositoryIdentity
from mnemonic_api.schemas import APIModel


class CommitReconcileRequest(APIModel):
    repository: RepositoryIdentity
    original_commit_hash: CommitHash
    merged_commit_hash: CommitHash


class CommitReconcileCreate(CommitReconcileRequest):
    client_operation_id: UUID


class CommitReconcileResult(CommitReconcileRequest):
    project_id: UUID
    scope_ids: list[UUID]
    created: bool
