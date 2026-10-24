"""Read grouped decision candidates while retaining each separate episode obligation."""

from uuid import UUID

from mcp.server.fastmcp import FastMCP

from .api import MnemonicAPI
from .decision_candidate_models import DecisionChangeCandidatesRead, Limit
from .input_errors import InputValidationError
from .knowledge_models import DecisionIdentifier
from .knowledge_tools import _READ, Offset, _read


def register_decision_candidate_tools(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_change_candidates(
        project_id: UUID, change_id: UUID, decision_id: DecisionIdentifier | None = None,
        limit: Limit = 20, offset: Offset = 0,
        occurrence_limit: Limit = 10, occurrence_offset: Offset = 0,
    ) -> DecisionChangeCandidatesRead:
        """Group one change's required code-impact candidates by stable decision ID without copying or coalescing dispositions. Whole-change decision_count, revision_count, occurrence_count, unassessed_count and change_required_count distinguish documents from per-episode obligations; total counts groups selected by optional decision_id. Scope and counts share one coherent snapshot. Page groups with next_offset, then each group's occurrences with next_occurrence_offset; use decision_id plus limit=1 to retrieve a single group's remaining occurrences. Each occurrence retains assessment ID/version, transition references, candidate reference, relationship/direction, and compact latest disposition identity/version/result/remediation link. Read assessment/history for evidence and reasons. captured_references contains only references in the returned occurrence page; references_complete and revision_count disclose missing references. Both limits are 1–100 and their product cannot exceed 200. Read every occurrence before deciding work is complete. Grouping does not waive per-candidate, per-episode Done requirements, classify live links, inherit judgments, or authorize changes."""
        if limit * occurrence_limit > 200:
            raise InputValidationError("Group limit multiplied by occurrence_limit must not exceed 200.")
        return await _read(
            api, f"projects/{project_id}/knowledge/changes/{change_id}/candidates",
            DecisionChangeCandidatesRead,
            lambda item: (item.project_id, item.change_id, item.decision_id, item.limit, item.offset,
                          item.occurrence_limit, item.occurrence_offset)
            == (project_id, change_id, decision_id, limit, offset,
                occurrence_limit, occurrence_offset),
            {"limit": limit, "offset": offset, "occurrence_limit": occurrence_limit,
             "occurrence_offset": occurrence_offset,
             **({"decision_id": decision_id} if decision_id is not None else {})},
        )
