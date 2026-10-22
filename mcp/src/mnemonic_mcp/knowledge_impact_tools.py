"""Decision dependency traversal and immutable impact reassessment records."""

from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StrictInt

from .api import MnemonicAPI
from .impact_models import (
    Disposition,
    ImpactActor,
    ImpactAssessmentDetail,
    ImpactAssessmentRead,
    ImpactDispositionCreate,
    ImpactDispositionRead,
    ImpactRead,
    Reason,
    Reference,
)
from .knowledge_models import DecisionIdentifier, DecisionReference, KnowledgePage, RevisionNumber
from .knowledge_tools import _READ, _WRITE, Limit, Offset, _read, _validated, _write


def _reference_matches(actual: str, requested: str, revision: int | None = None) -> bool:
    parts = requested.split("-")
    stable = "-".join(parts[:2])
    number = int(parts[2]) if len(parts) == 3 else revision
    return actual.startswith(stable + "-") and (
        number is None or actual == f"{stable}-{number}"
    )


def _impact_matches(
    result: ImpactRead, project_id: UUID, reference: str, revision: int | None,
    max_depth: int, limit: int, offset: int,
) -> bool:
    return (
        result.project_id == project_id
        and _reference_matches(result.origin_reference, reference, revision)
        and (result.limit, result.offset, result.coverage.max_depth) == (limit, offset, max_depth)
        and all(item.path and item.path[0] == result.origin_reference for item in result.items)
    )


def register_impact_tools(server: FastMCP, api: MnemonicAPI) -> None:
    _register_impact_reads(server, api)
    _register_assessment_reads(server, api)
    _register_assessment_writes(server, api)


def _register_impact_reads(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_impact(
        project_id: UUID, decision_id: DecisionReference, revision: RevisionNumber | None = None,
        max_depth: Annotated[StrictInt, Field(ge=1, le=16)] = 8,
        limit: Limit = 50, offset: Offset = 0,
    ) -> ImpactRead:
        """Trace reverse declared dependencies from an exact decision revision to downstream decisions, recorded commits, work, and artifacts. Current revision is the default; retain origin_reference. Follow next_offset and inspect coverage/evidence_truncated. Each candidate carries one shortest explanatory path; related-only candidates have unknown dependency semantics. Bounded traversal and recorded provenance cannot prove all affected code is known. This is a read, not authorization to revise decisions or implement changes."""
        return await _read(
            api, f"projects/{project_id}/knowledge/decisions/{decision_id}/impact", ImpactRead,
            lambda result: _impact_matches(
                result, project_id, decision_id, revision, max_depth, limit, offset,
            ),
            {"max_depth": max_depth, "limit": limit, "offset": offset,
             **({"revision": revision} if revision is not None else {})},
        )


def _register_assessment_reads(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_assessments(
        project_id: UUID, decision_id: DecisionIdentifier | None = None,
        limit: Limit = 50, offset: Offset = 0,
    ) -> KnowledgePage[ImpactAssessmentRead]:
        """List durable impact assessments created by decision revisions or supersession. Optionally select a stable decision ID. Each episode pins before/after revisions, a bounded diff, captured candidates and coverage. Page all results; get exact candidate pages with decision_assessment_get. An assessment records a need to inspect consequences, not automatic invalidation or permission to perform remediation."""
        return await _read(
            api, f"projects/{project_id}/knowledge/assessments", KnowledgePage[ImpactAssessmentRead],
            lambda page: page.limit == limit and page.offset == offset
            and all(item.project_id == project_id for item in page.items),
            {"limit": limit, "offset": offset,
             **({"decision_id": decision_id} if decision_id is not None else {})},
        )

    @server.tool(annotations=_READ)
    async def decision_assessment_get(
        project_id: UUID, assessment_id: UUID, limit: Limit = 50, offset: Offset = 0,
    ) -> ImpactAssessmentDetail:
        """Read one captured decision-change assessment, a page of candidates and their latest dispositions. Follow next_offset; read version before appending a disposition. Inspect exact before/after text when diff.truncated and scope when evidence_truncated. Candidate snapshots retain the evidence known at the change; later registrations require a fresh decision_impact read. Earlier dispositions remain available through decision_assessment_history. Neither open nor reviewed candidates grant execution authority."""
        return await _read(
            api, f"projects/{project_id}/knowledge/assessments/{assessment_id}",
            ImpactAssessmentDetail,
            lambda item: (item.project_id, item.id, item.limit, item.offset)
            == (project_id, assessment_id, limit, offset),
            {"limit": limit, "offset": offset},
        )

    @server.tool(annotations=_READ)
    async def decision_assessment_history(
        project_id: UUID, assessment_id: UUID, limit: Limit = 50, offset: Offset = 0,
    ) -> KnowledgePage[ImpactDispositionRead]:
        """Page the immutable disposition history of one impact assessment. Later dispositions do not erase earlier reasons or remediation links. Follow all pages and reread assessment version if concurrent writes matter; a historical disposition is evidence, not current authority or proof that remediation was performed."""
        return await _read(
            api, f"projects/{project_id}/knowledge/assessments/{assessment_id}/dispositions",
            KnowledgePage[ImpactDispositionRead],
            lambda page: page.limit == limit and page.offset == offset and all(
                (item.project_id, item.assessment_id) == (project_id, assessment_id)
                for item in page.items
            ),
            {"limit": limit, "offset": offset},
        )


def _register_assessment_writes(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_WRITE)
    async def decision_assessment_append(
        project_id: UUID, assessment_id: UUID, candidate_reference: Reference,
        expected_version: Annotated[StrictInt, Field(ge=0)], disposition: Disposition,
        reason: Reason, actor: ImpactActor, client_operation_id: UUID,
        remediation_work_item_id: UUID | None = None,
    ) -> ImpactDispositionRead:
        """Append an evidenced assessment for one exact captured candidate revision: still-valid, change-required, not-affected, or resolved. Supply the current assessment expected_version, a concrete reason, and remediation work ID when applicable. Inspect evidence before deciding; resolved must reflect completed authorized remediation. This never changes document lifecycle, completes work, resolves a human gate, or grants authority. Retain the operation ID and every argument for an exact uncertain retry. A definitive version conflict requires rereading before a new intent."""
        intent = _validated(ImpactDispositionCreate, {
            "candidate_reference": candidate_reference, "expected_version": expected_version,
            "disposition": disposition, "reason": reason,
            "remediation_work_item_id": remediation_work_item_id,
            "actor": actor,
            "client_operation_id": client_operation_id,
        })
        return await _write(
            api, "POST", f"projects/{project_id}/knowledge/assessments/{assessment_id}/dispositions",
            ImpactDispositionRead, intent,
            lambda item: (item.project_id, item.assessment_id, item.version)
            == (project_id, assessment_id, expected_version + 1)
            and item.actor == intent.actor
            and all(getattr(item, key) == value for key, value in intent.model_dump(
                exclude={"client_operation_id", "expected_version", "actor"},
            ).items()),
            expected_status=201,
        )
