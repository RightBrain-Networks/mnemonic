"""Decision dependency traversal and immutable impact reassessment records."""

from typing import Annotated
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StrictBool, StrictInt
from pydantic.experimental.missing_sentinel import MISSING

from .api import MnemonicAPI
from .impact_models import (
    Disposition,
    FindingIdentifier,
    ImpactActor,
    ImpactAssessmentDetail,
    ImpactAssessmentRead,
    ImpactCandidateRead,
    ImpactDetail,
    ImpactDispositionBatchCreate,
    ImpactDispositionBatchRead,
    ImpactDispositionCreate,
    ImpactDispositionItem,
    ImpactDispositionRead,
    ImpactKind,
    ImpactProjection,
    ImpactRead,
    ImpactScopes,
    Reason,
    Reference,
    RemediationTarget,
    effective_projection,
)
from .knowledge_models import DecisionIdentifier, DecisionReference, KnowledgePage, RevisionNumber
from .knowledge_tools import _READ, _WRITE, Limit, Offset, _read, _validated, _write
from .server import LeaseTokenInput


def _reference_matches(actual: str, requested: str, revision: int | None = None) -> bool:
    parts = requested.split("-")
    stable = "-".join(parts[:2])
    number = int(parts[2]) if len(parts) == 3 else revision
    return actual.startswith(stable + "-") and (
        number is None or actual == f"{stable}-{number}"
    )


def _excerpts_match(candidates: list[ImpactCandidateRead], requested: bool) -> bool:
    return all((item.citation_excerpts is not MISSING) == requested for item in candidates)


def _impact_matches(
    result: ImpactRead, project_id: UUID, reference: str, revision: int | None,
    max_depth: int, limit: int, offset: int, kind: ImpactKind, options: ImpactProjection,
) -> bool:
    return (
        result.project_id == project_id
        and effective_projection(result.projection) == options
        and result.kind == kind
        and _reference_matches(result.origin_reference, reference, revision)
        and (result.limit, result.offset, result.coverage.max_depth) == (limit, offset, max_depth)
        and all(item.path and item.path[0] == result.origin_reference for item in result.items)
    )


def register_impact_tools(server: FastMCP, api: MnemonicAPI) -> None:
    _register_impact_reads(server, api)
    _register_assessment_reads(server, api)
    _register_assessment_writes(server, api)
    _register_assessment_batch(server, api)


def _register_impact_reads(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_impact(
        project_id: UUID, decision_id: DecisionReference, revision: RevisionNumber | None = None,
        max_depth: Annotated[StrictInt, Field(ge=1, le=16)] = 8,
        limit: Limit = 50, offset: Offset = 0,
        kind: ImpactKind = "code-impact",
        detail: ImpactDetail = "full", include_diff: StrictBool = True,
        include_scopes: ImpactScopes = "full",
        include_citation_excerpts: StrictBool = False,
    ) -> ImpactRead:
        """Trace reverse declared dependencies from an exact decision revision to downstream decisions, recorded commits, work, and artifacts. Current revision is the default; retain origin_reference. Follow next_offset and inspect coverage/evidence_truncated. kind=code-impact (default) returns the changed decision, reverse depends_on dependents, and records citing that trace through incoming related links (related-only, related_direction=incoming, causality unknown); kind=context separately pages prerequisites and outward references for optional contradiction checks. Each candidate carries one shortest explanatory path. candidate_count counts required code-impact records; context_count is separate. Inspect no-linked-commit warnings and revision author/current active-work context. Missing scope calls for repository commit-message, declared-file, and source-ID searches; an empty graph never proves no implementation. Bounded traversal and recorded provenance cannot prove all affected code is known. Optional detail=compact omits candidate prose/resource links and summarizes author work by IDs; include_scopes=summary returns captured counts/hashes, or none omits scopes. Full defaults preserve existing evidence. Use full reads and decision_scope to inspect omitted details. include_citation_excerpts=true adds up to three literal 400-character hints from exact pinned candidate text, with source hash and truncation. Empty excerpts do not establish irrelevance. This is a read, not authorization to revise decisions or implement changes."""
        options = ImpactProjection(detail=detail, include_diff=include_diff,
                                   include_scopes=include_scopes)
        return await _read(
            api, f"projects/{project_id}/knowledge/decisions/{decision_id}/impact", ImpactRead,
            lambda result: _impact_matches(
                result, project_id, decision_id, revision, max_depth, limit, offset, kind, options,
            ) and _excerpts_match(result.items, include_citation_excerpts),
            {**options.model_dump(exclude_defaults=True), "max_depth": max_depth, "limit": limit,
             "offset": offset, "kind": kind,
             **({"revision": revision} if revision is not None else {}),
             **({"include_citation_excerpts": True} if include_citation_excerpts else {})},
        )


def _register_assessment_reads(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_READ)
    async def decision_assessments(
        project_id: UUID, decision_id: DecisionIdentifier | None = None,
        limit: Limit = 50, offset: Offset = 0,
        change_id: UUID | None = None,
        detail: ImpactDetail = "full", include_diff: StrictBool = True,
        include_scopes: ImpactScopes = "full",
    ) -> KnowledgePage[ImpactAssessmentRead]:
        """List durable impact assessments created by decision revisions or supersession. Optionally select a stable decision ID or change_id. For a native review, filter by change_id and page every current episode; the initial prompt may precede scope growth. Each episode pins before/after revisions, a bounded diff, captured candidates and coverage. Page all results; get exact candidate pages with decision_assessment_get. Use detail=compact and include_diff=false for concise episode navigation: retain transition hashes, changed fields and coverage while omitting unified diff and author work titles. Full defaults are unchanged. An assessment records a need to inspect consequences, not automatic invalidation or permission to perform remediation."""
        options = ImpactProjection(detail=detail, include_diff=include_diff,
                                   include_scopes=include_scopes)
        return await _read(
            api, f"projects/{project_id}/knowledge/assessments", KnowledgePage[ImpactAssessmentRead],
            lambda page: page.limit == limit and page.offset == offset
            and all(item.project_id == project_id
                    and effective_projection(item.projection) == options for item in page.items),
            {**options.model_dump(exclude_defaults=True), "limit": limit, "offset": offset,
             **({"change_id": change_id} if change_id is not None else {}),
             **({"decision_id": decision_id} if decision_id is not None else {})},
        )

    @server.tool(annotations=_READ)
    async def decision_assessment_get(
        project_id: UUID, assessment_id: UUID, limit: Limit = 50, offset: Offset = 0,
        kind: ImpactKind = "code-impact",
        detail: ImpactDetail = "full", include_diff: StrictBool = True,
        include_scopes: ImpactScopes = "full",
        include_citation_excerpts: StrictBool = False,
    ) -> ImpactAssessmentDetail:
        """Read captured candidates and latest dispositions for one immutable episode. kind=code-impact (default) requires origin/reverse-dependency candidates and incoming related-only citations; related causality remains unknown. kind=context pages optional prerequisites, outward references and historical related-only rows without direction. Follow next_offset, retain version before writing, and inspect coverage/evidence_truncated. Read exact before/after text to classify rule, scope or reference changes; headings do not establish scope. Snapshots exclude later evidence: use decision_impact for a live refresh. Page earlier judgments with decision_assessment_history. detail=compact omits candidate prose/links, disposition reasons/attribution and author work titles. include_scopes=summary returns captured counts/hashes; none omits scopes. include_diff=false omits ordinary and supersession diff prose while retaining identities, hashes, bounds and truncation. Retrieve full detail/history/scope before relying on omitted evidence. include_citation_excerpts=true adds bounded literal hints from exact captured candidate text, with source hash and truncation; empty hints do not establish irrelevance. supersession_delta compares the replacing document with its previous revision and target-specific details, while the original cross-document diff remains available. Inspect supersession_extent_reason_unknown warnings and exact revisions when either comparison is truncated. Defaults remain full without excerpts. Neither an assessment nor a disposition grants execution authority."""
        options = ImpactProjection(detail=detail, include_diff=include_diff,
                                   include_scopes=include_scopes)
        return await _read(
            api, f"projects/{project_id}/knowledge/assessments/{assessment_id}",
            ImpactAssessmentDetail,
            lambda item: (item.project_id, item.id, item.limit, item.offset, item.kind)
            == (project_id, assessment_id, limit, offset, kind)
            and effective_projection(item.projection) == options
            and _excerpts_match(item.candidates, include_citation_excerpts),
            {**options.model_dump(exclude_defaults=True), "limit": limit, "offset": offset, "kind": kind,
             **({"include_citation_excerpts": True} if include_citation_excerpts else {})},
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
        finding_id: FindingIdentifier | None = None,
        remediation_target: RemediationTarget | None = None,
        lease_token: LeaseTokenInput | None = None,
    ) -> ImpactDispositionRead:
        """Append an evidenced assessment for one exact captured candidate revision: still-valid, change-required, not-affected, or resolved. Supply the current assessment expected_version, a concrete reason, and remediation work ID when applicable. Context-only verdicts are optional and may use a short truthful contradiction note; every recorded change-required result still requires tracked remediation before review Done. Optional finding_id groups the same evidenced finding using a case-sensitive ASCII key of 1–100 letters, digits, dots, colons, underscores or hyphens, starting with a letter or digit. Optional remediation_target is code, decision-record or both, only for change-required. Unlabelled rows remain unknown; finding summaries never change obligations. Inspect evidence before deciding; resolved must reflect completed authorized remediation. This never changes document lifecycle, completes work, resolves a human gate, or grants authority. Optionally supply an active implementation lease_token belonging to a current review driver associated with this episode; fresh success renews its last granted duration within current project lease bounds. Token-free writes and exact receipt replays do not renew. Unrelated and code-review capabilities are refused. Retain the operation ID and every argument for an exact uncertain retry. A definitive version conflict requires rereading before a new intent."""
        intent = _validated(ImpactDispositionCreate, {
            "candidate_reference": candidate_reference, "expected_version": expected_version,
            "disposition": disposition, "reason": reason,
            "remediation_work_item_id": remediation_work_item_id,
            "finding_id": finding_id, "remediation_target": remediation_target,
            "actor": actor,
            "client_operation_id": client_operation_id,
            "lease_token": lease_token.get_secret_value() if lease_token is not None else None,
        })
        return await _write(
            api, "POST", f"projects/{project_id}/knowledge/assessments/{assessment_id}/dispositions",
            ImpactDispositionRead, intent,
            lambda item: (item.project_id, item.assessment_id, item.version)
            == (project_id, assessment_id, expected_version + 1)
            and item.actor == intent.actor
            and (item.finding_id, item.remediation_target) == (finding_id, remediation_target)
            and (item.review_scope_changed is None or lease_token is not None)
            and all(getattr(item, key) == value for key, value in intent.model_dump(
                exclude={"client_operation_id", "expected_version", "actor", "lease_token"},
            ).items()),
            expected_status=201,
        )


def _batch_matches(
    result: ImpactDispositionBatchRead, intent: ImpactDispositionBatchCreate,
    project_id: UUID, assessment_id: UUID,
) -> bool:
    return (
        (result.project_id, result.assessment_id, result.version)
        == (project_id, assessment_id, intent.expected_version + len(intent.items))
        and len(result.items) == len(intent.items)
        and (result.review_scope_changed is None or intent.lease_token is not None)
        and all(actual.actor == intent.actor and all(
            getattr(actual, key) == getattr(expected, key)
            for key in ("candidate_reference", "disposition", "reason", "remediation_work_item_id",
                        "finding_id", "remediation_target")
        ) for expected, actual in zip(intent.items, result.items, strict=True))
    )


def _register_assessment_batch(server: FastMCP, api: MnemonicAPI) -> None:
    @server.tool(annotations=_WRITE)
    async def decision_assessment_append_batch(
        project_id: UUID, assessment_id: UUID,
        expected_version: Annotated[StrictInt, Field(ge=0)],
        items: Annotated[list[ImpactDispositionItem], Field(min_length=1, max_length=100)],
        actor: ImpactActor, client_operation_id: UUID,
        lease_token: LeaseTokenInput | None = None,
    ) -> ImpactDispositionBatchRead:
        """Atomically append 1–100 evidenced dispositions to one assessment episode. Each item names a unique exact candidate_reference, disposition, concrete reason, and optional remediation_work_item_id; actor and expected_version apply to the whole batch. Items may include finding_id and remediation_target under the singleton rules; metadata stays exact per item. Versions advance consecutively in input order. An invalid item rolls back every item and any lease renewal. Done requirements remain per candidate and episode. Optionally supply an active implementation lease_token for a current review driver associated with this episode, including an adopted or moved driver; a fresh successful write renews its last granted duration clamped to current project bounds. No token and exact receipt replays never renew ownership; unrelated and code-review capabilities are refused. Retain the one operation UUID and all arguments, including item order, for an exact uncertain retry. A definitive version conflict requires reading decision_assessment_get before preparing a new intent. Never copy verdicts between episodes or infer authorization from this operation."""
        intent = _validated(ImpactDispositionBatchCreate, {
            "expected_version": expected_version, "items": items, "actor": actor,
            "client_operation_id": client_operation_id,
            "lease_token": lease_token.get_secret_value() if lease_token is not None else None,
        })
        return await _write(
            api, "POST",
            f"projects/{project_id}/knowledge/assessments/{assessment_id}/dispositions/batch",
            ImpactDispositionBatchRead, intent,
            lambda result: _batch_matches(result, intent, project_id, assessment_id),
            expected_status=201,
        )
