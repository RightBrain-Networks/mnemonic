"""Reverse commit provenance and append-only merge reconciliation."""

from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import StrictBool

from .api import MnemonicAPI
from .knowledge_models import DecisionScopeRead, KnowledgeModel, KnowledgePage
from .knowledge_tools import _READ, _WRITE, Limit, Offset, _read, _validated, _write
from .provenance_models import CommitHash, RepositoryIdentity
from .repository_identity import canonical_repository


class ScopeReconcile(KnowledgeModel):
    repository: RepositoryIdentity
    original_commit_hash: CommitHash
    merged_commit_hash: CommitHash
    client_operation_id: UUID


class ScopeReconcileResult(KnowledgeModel):
    project_id: UUID
    repository: RepositoryIdentity
    original_commit_hash: CommitHash
    merged_commit_hash: CommitHash
    scope_ids: list[UUID]
    created: StrictBool


def register_provenance_tools(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_commit_lookup(
        project_id: UUID, repository: RepositoryIdentity, commit_hash: CommitHash,
        limit: Limit = 50, offset: Offset = 0,
    ) -> KnowledgePage[DecisionScopeRead]:
        """Find exact decision revisions recorded for a repository and full commit hash, including reconciled merge hashes. Follow every page. Supported GitHub slugs/HTTP(S) URLs match case-insensitively; results retain original stored spelling. Other identities match exactly; missing identity never matches. Empty results mean no registered provenance, not no decision influence. Mnemonic does not fetch Git; inspect the repository before claiming a commit or mapping is verified."""
        return await _read(
            api, f"projects/{project_id}/knowledge/commits", KnowledgePage[DecisionScopeRead],
            lambda page: page.limit == limit and page.offset == offset and all(
                item.project_id == project_id
                and canonical_repository(item.repository) == canonical_repository(repository)
                and commit_hash in {item.commit_hash, *item.merged_commit_hashes}
                for item in page.items
            ),
            {"repository": repository, "commit_hash": commit_hash,
             "limit": limit, "offset": offset},
        )

    @server.tool(annotations=_WRITE)
    async def decision_scope_reconcile(
        project_id: UUID, repository: RepositoryIdentity,
        original_commit_hash: CommitHash, merged_commit_hash: CommitHash,
        client_operation_id: UUID,
    ) -> ScopeReconcileResult:
        """Append a verified original-to-merged commit mapping to existing decision scope records in one repository. Inspect actual merge/rebase/squash evidence first; never guess a hash or infer equivalence from similar text. Use the exact stored repository spelling returned by lookup; reconciliation does not expand aliases. Original scope remains immutable. The server records the declaration without fetching Git. Omit client_operation_id for automatic minting; retain it and every exact argument for an uncertain retry, then reconcile using decision_commit_lookup."""
        intent = _validated(ScopeReconcile, {
            "repository": repository, "original_commit_hash": original_commit_hash,
            "merged_commit_hash": merged_commit_hash, "client_operation_id": client_operation_id,
        })
        return await _write(
            api, "POST", f"projects/{project_id}/knowledge/commits/reconcile",
            ScopeReconcileResult, intent,
            lambda result: result.project_id == project_id
            and result.repository == intent.repository
            and result.original_commit_hash == intent.original_commit_hash
            and result.merged_commit_hash == intent.merged_commit_hash
            and len(result.scope_ids) == len(set(result.scope_ids)),
        )
