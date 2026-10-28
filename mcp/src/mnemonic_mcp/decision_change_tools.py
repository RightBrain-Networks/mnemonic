"""Read and acknowledge bounded review scope; adoption keeps one native driver."""

from typing import Annotated, Literal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StrictInt

from .api import MnemonicAPI
from .decision_change_models import DecisionChangeRead, DecisionChangeUpdate
from .knowledge_tools import _READ, _WRITE, Session, _read, _validated, _write


def register_decision_change_tools(server: FastMCP, api: MnemonicAPI) -> None:
    from .decision_candidate_tools import register_decision_candidate_tools

    register_decision_candidate_tools(server, api)

    @server.tool(annotations=_READ)
    async def decision_change_get(
        project_id: UUID, change_id: UUID,
        lineage_limit: Annotated[StrictInt, Field(ge=1, le=100)] = 20,
        lineage_offset: Annotated[StrictInt, Field(ge=0)] = 0,
    ) -> DecisionChangeRead:
        """Read a substantive change's native review driver, scope version and acknowledged version. lineage_work pages other durable member work, including source implementations and code-review remediation, with safe current readiness, lease and factual origin pointers; follow next_offset using lineage_offset. Members can belong to another project, be deleted or be duplicate aliases; follow the returned identities deliberately. Membership alone does not establish relevance. Inspect relevant open work before dispositions and recheck before completion. This visibility does not block review readiness. Page decision_assessments(change_id=...) for current obligations, including revisions added while the review is active. Reading or acknowledging scope does not dispose candidates or grant execution authority. A closed driver requires explicit human dashboard admission before a new substantive review cycle."""
        params: dict[str, object] = (
            {"lineage_limit": lineage_limit, "lineage_offset": lineage_offset}
            if (lineage_limit, lineage_offset) != (20, 0) else {}
        )
        return await _read(
            api, f"projects/{project_id}/knowledge/changes/{change_id}", DecisionChangeRead,
            lambda item: (item.project_id, item.id) == (project_id, change_id) and (
                (item.lineage_work is None and not params) or item.lineage_work is not None and
                (item.lineage_work.limit, item.lineage_work.offset) == (lineage_limit, lineage_offset)
            ), params,
        )

    @server.tool(annotations=_WRITE)
    async def decision_change_update(
        project_id: UUID, action: Literal["adopt", "acknowledge"], client_operation_id: UUID,
        actor_client: Annotated[str, Field(min_length=1, max_length=64)],
        actor_session_id: Session,
        actor_model: Annotated[str, Field(min_length=1, max_length=200)] | None = None,
        change_id: UUID | None = None, work_item_id: UUID | None = None,
        expected_work_version: Annotated[StrictInt, Field(ge=1)] | None = None,
        expected_scope_version: Annotated[StrictInt, Field(ge=0)] | None = None,
        assessment_ids: Annotated[list[UUID], Field(max_length=100)] = [],  # noqa: B006
        lease_token: Annotated[str, Field(min_length=1, max_length=200)] | None = None,
    ) -> DecisionChangeRead:
        """Adopt assessment episodes into one existing ordinary pending work item, or acknowledge a change's current review scope before Done. adopt requires work_item_id, expected_work_version and 1–100 unique assessment_ids; acknowledge requires change_id and expected_scope_version. Use truthful actor identity and the active work lease token when applicable. Adoption is explicit; it never merges historical work or copies dispositions. Acknowledge only after inspecting all attached assessments: it does not replace required dispositions or remediation links. Scope growth invalidates an earlier acknowledgement and keeps review_scope_changed notices pending on status/renew/token-bearing progress responses. Read the detailed growth checkpoint; metadata-only notices do not replace assessment reads. Agents cannot admit a new cycle after closure; ask the owner using the human gate workflow. Omit client_operation_id on fresh calls, retain the returned UUID and every argument for an exact uncertain retry."""
        intent = _validated(DecisionChangeUpdate, {
            "action": action, "client_operation_id": client_operation_id,
            "actor_client": actor_client, "actor_session_id": actor_session_id,
            "actor_model": actor_model, "change_id": change_id, "work_item_id": work_item_id,
            "expected_work_version": expected_work_version,
            "expected_scope_version": expected_scope_version, "assessment_ids": assessment_ids,
            "lease_token": lease_token,
        })
        return await _write(
            api, "POST", f"projects/{project_id}/knowledge/changes", DecisionChangeRead, intent,
            lambda item: item.project_id == project_id and (
                item.review_work_item_id == work_item_id
                and item.assessment_count >= len(assessment_ids) if action == "adopt" else
                item.id == change_id and item.scope_version == expected_scope_version
                and item.acknowledged_scope_version == expected_scope_version
            ),
        )
