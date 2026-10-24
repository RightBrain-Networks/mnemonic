"""Pinned decision dependencies and explicit, auditable impact reassessment."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, field_validator
from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.decision_link_schemas import DecisionDependency as DecisionDependency
from mnemonic_api.decision_link_schemas import SupersessionDetail as SupersessionDetail
from mnemonic_api.decision_scope_schemas import ScopeCompletionCitation
from mnemonic_api.revision_author_schemas import RevisionAuthorWork
from mnemonic_api.schemas import APIModel, LeaseToken, MutationActor

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$")]
Revision = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


ImpactKind = Literal["code-impact", "context"]
RelatedDirection = Literal["incoming", "outgoing"]
ImpactDetail = Literal["full", "compact"]
ImpactScopes = Literal["full", "summary", "none"]


class ImpactProjection(APIModel):
    detail: ImpactDetail = "full"
    include_diff: bool = True
    include_scopes: ImpactScopes = "full"


def is_code_impact(relationship: str, related_direction: str | None = None) -> bool:
    """Historical related-only snapshots without direction remain optional context."""
    return relationship in {"origin", "depends-on"} or (
        relationship == "related-only" and related_direction == "incoming"
    )


class ImpactPageQuery(ImpactProjection):
    kind: ImpactKind = "code-impact"
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class ImpactQuery(ImpactPageQuery):
    revision: Annotated[int | None, Field(ge=0)] = None
    max_depth: Annotated[int, Field(ge=1, le=16)] = 8


class ImpactScopeEvidence(APIModel):
    id: UUID
    commit_hash: str
    revision: int
    work_item_id: UUID | None
    agent_session_id: str | None
    repository: str | None = None
    completion_report_id: UUID | None = None
    merged_commit_hashes: list[str] = []
    completion_citations: list[ScopeCompletionCitation] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    completion_citations_truncated: bool = Field(default=False, exclude_if=lambda value: not value)


class ImpactCandidate(APIModel):
    decision_id: str
    reference: str
    revision: int
    title: str
    state: str
    is_current: bool
    relationship: Literal["origin", "depends-on", "depends-on-prerequisite", "related-only"]
    related_direction: RelatedDirection | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    path: list[str]
    rationale: str | None
    scopes: list[ImpactScopeEvidence]
    related_work_item_ids: list[UUID]
    related_artifact_ids: list[UUID]
    evidence_truncated: bool


class ImpactScopeSummary(APIModel):
    captured_scope_count: int
    commit_hashes: list[str]
    evidence_truncated: bool


class ImpactCandidateRead(APIModel):
    """Read projection only: persisted candidates retain their complete original schema."""

    decision_id: str
    reference: str
    revision: int
    title: str | MISSING = MISSING
    state: str
    is_current: bool
    relationship: Literal["origin", "depends-on", "depends-on-prerequisite", "related-only"]
    related_direction: RelatedDirection | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    path: list[str]
    rationale: str | None | MISSING = MISSING
    scopes: list[ImpactScopeEvidence] | MISSING = MISSING
    scope_summary: ImpactScopeSummary | MISSING = MISSING
    related_work_item_ids: list[UUID] | MISSING = MISSING
    related_artifact_ids: list[UUID] | MISSING = MISSING
    evidence_truncated: bool


class ImpactAuthorRead(APIModel):
    reference: str
    author_kind: Literal["human", "agent"]
    agent_session_id: str | None
    model_id: str | None
    authored_at: datetime
    work_observed_at: datetime
    active_work_items: list[RevisionAuthorWork] | MISSING = MISSING
    active_work_item_ids: list[UUID] | MISSING = MISSING
    active_work_items_truncated: bool


class ImpactWarning(APIModel):
    code: Literal["origin_has_no_linked_commits"]
    reference: str
    message: str


class ImpactCoverage(APIModel):
    traversal_complete: bool
    provenance_complete: Literal[False] = False
    max_depth: int
    candidate_limit: int
    visited_revisions: int
    reasons: list[str]
    warnings: list[ImpactWarning] = []
    related_semantics: Literal["unknown"] = "unknown"
    snapshot_at: datetime | None = None
    notes: list[str] = [
        "Only recorded dependencies and implementation citations are discoverable.",
        "Related-only records have unknown causal relevance.",
    ]
    path_policy: Literal["one deterministic shortest path per revision"] = (
        "one deterministic shortest path per revision"
    )


class ImpactRead(APIModel):
    project_id: UUID
    origin_reference: str
    origin_author: ImpactAuthorRead
    items: list[ImpactCandidateRead]
    projection: ImpactProjection | MISSING = MISSING
    kind: ImpactKind
    candidate_count: int
    context_count: int
    total: int
    limit: int
    offset: int
    next_offset: int | None
    coverage: ImpactCoverage


Disposition = Literal["still-valid", "change-required", "not-affected", "resolved"]


class ImpactDispositionItem(APIModel):
    candidate_reference: Reference
    disposition: Disposition
    reason: Reason
    remediation_work_item_id: UUID | None = None

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)


class ImpactDispositionRequest(ImpactDispositionItem):
    expected_version: Annotated[StrictInt, Field(ge=0)]
    actor: MutationActor
    lease_token: LeaseToken | None = Field(
        default=None, repr=False, exclude_if=lambda value: value is None,
    )


class ImpactDispositionCreate(ImpactDispositionRequest):
    client_operation_id: UUID


class ImpactDispositionRead(APIModel):
    id: UUID
    project_id: UUID
    assessment_id: UUID
    version: int
    candidate_reference: str
    disposition: Disposition
    reason: str
    remediation_work_item_id: UUID | None
    actor: MutationActor
    created_at: datetime


class ImpactDispositionBatchRequest(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=0)]
    items: Annotated[list[ImpactDispositionItem], Field(min_length=1, max_length=100)]
    actor: MutationActor
    lease_token: LeaseToken | None = Field(
        default=None, repr=False, exclude_if=lambda value: value is None,
    )

    @field_validator("items")
    @classmethod
    def unique_candidates(cls, items: list[ImpactDispositionItem]) -> list[ImpactDispositionItem]:
        if len({item.candidate_reference for item in items}) != len(items):
            raise ValueError("A batch must name each candidate at most once")
        return items


class ImpactDispositionBatchCreate(ImpactDispositionBatchRequest):
    client_operation_id: UUID


class ImpactDispositionBatchRead(APIModel):
    project_id: UUID
    assessment_id: UUID
    version: Annotated[StrictInt, Field(ge=1)]
    items: Annotated[list[ImpactDispositionRead], Field(min_length=1, max_length=100)]


class ImpactDiff(APIModel):
    changed_fields: list[str]
    before_sha256: str
    after_sha256: str
    unified_diff: str
    truncated: bool


class ImpactDiffRead(APIModel):
    changed_fields: list[str]
    before_sha256: str
    after_sha256: str
    unified_diff: str | MISSING = MISSING
    truncated: bool


class ImpactDispositionProjection(APIModel):
    id: UUID | MISSING = MISSING
    project_id: UUID | MISSING = MISSING
    assessment_id: UUID | MISSING = MISSING
    version: int
    candidate_reference: str
    disposition: Disposition
    reason: str | MISSING = MISSING
    remediation_work_item_id: UUID | None
    actor: MutationActor | MISSING = MISSING
    created_at: datetime | MISSING = MISSING


class ImpactAssessmentRead(APIModel):
    id: UUID
    project_id: UUID
    trigger: Literal["revision", "supersession"]
    before_reference: str
    after_reference: str
    before_author: ImpactAuthorRead
    after_author: ImpactAuthorRead
    extent: Literal["full", "partial"] | None
    reason: str | None
    diff: ImpactDiffRead
    projection: ImpactProjection | MISSING = MISSING
    candidate_count: int
    context_count: int
    coverage: ImpactCoverage
    created_at: datetime
    version: int
    unassessed_count: int
    change_required_count: int
    status: Literal["pending", "change-required", "resolved"]


class ImpactAssessmentDetail(ImpactAssessmentRead):
    kind: ImpactKind
    total: int
    candidates: list[ImpactCandidateRead]
    dispositions: list[ImpactDispositionProjection]
    disposition_history_total: int
    limit: int
    offset: int
    next_offset: int | None


class ImpactAssessmentQuery(ImpactProjection):
    change_id: UUID | None = None
    decision_id: Identifier | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0
