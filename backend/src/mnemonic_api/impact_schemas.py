"""Pinned decision dependencies and explicit, auditable impact reassessment."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, field_validator

from mnemonic_api.schemas import APIModel, MutationActor

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$")]
Revision = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DecisionDependency(APIModel):
    decision_id: Identifier
    revision: Revision
    rationale: Reason

    @field_validator("rationale")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Reasons cannot contain NUL characters")
        value.encode("utf-8")
        return value


class SupersessionDetail(APIModel):
    decision_id: Identifier
    revision: Revision
    extent: Literal["full", "partial"]
    reason: Reason

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)


class ImpactQuery(APIModel):
    revision: Annotated[int | None, Field(ge=0)] = None
    max_depth: Annotated[int, Field(ge=1, le=16)] = 8
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class ImpactScopeEvidence(APIModel):
    id: UUID
    commit_hash: str
    revision: int
    work_item_id: UUID | None
    agent_session_id: str | None
    repository: str | None = None
    completion_report_id: UUID | None = None
    merged_commit_hashes: list[str] = []


class ImpactCandidate(APIModel):
    decision_id: str
    reference: str
    revision: int
    title: str
    state: str
    is_current: bool
    relationship: Literal["origin", "depends-on", "related-only"]
    path: list[str]
    rationale: str | None
    scopes: list[ImpactScopeEvidence]
    related_work_item_ids: list[UUID]
    related_artifact_ids: list[UUID]
    evidence_truncated: bool


class ImpactCoverage(APIModel):
    traversal_complete: bool
    provenance_complete: Literal[False] = False
    max_depth: int
    candidate_limit: int
    visited_revisions: int
    reasons: list[str]
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
    items: list[ImpactCandidate]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    coverage: ImpactCoverage


Disposition = Literal["still-valid", "change-required", "not-affected", "resolved"]


class ImpactDispositionRequest(APIModel):
    candidate_reference: Reference
    expected_version: Annotated[StrictInt, Field(ge=0)]
    disposition: Disposition
    reason: Reason
    remediation_work_item_id: UUID | None = None
    actor: MutationActor

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)


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


class ImpactDiff(APIModel):
    changed_fields: list[str]
    before_sha256: str
    after_sha256: str
    unified_diff: str
    truncated: bool


class ImpactAssessmentRead(APIModel):
    id: UUID
    project_id: UUID
    trigger: Literal["revision", "supersession"]
    before_reference: str
    after_reference: str
    extent: Literal["full", "partial"] | None
    reason: str | None
    diff: ImpactDiff
    candidate_count: int
    coverage: ImpactCoverage
    created_at: datetime
    version: int
    unassessed_count: int
    change_required_count: int
    status: Literal["pending", "change-required", "resolved"]


class ImpactAssessmentDetail(ImpactAssessmentRead):
    candidates: list[ImpactCandidate]
    dispositions: list[ImpactDispositionRead]
    disposition_history_total: int
    limit: int
    offset: int
    next_offset: int | None


class ImpactAssessmentQuery(APIModel):
    decision_id: Identifier | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0
