"""Pinned decision dependencies and explicit, auditable impact reassessment."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from .provenance_models import CommitHash, RepositoryIdentity, ScopeCompletionCitation, nonblank
from .response_validation import validate_page_items
from .revision_author_models import RevisionAuthorContext


class ImpactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
Reference = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$")]
Revision = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DecisionDependency(ImpactModel):
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


class SupersessionDetail(ImpactModel):
    decision_id: Identifier
    revision: Revision
    extent: Literal["full", "partial"]
    reason: Reason

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)


ImpactKind = Literal["code-impact", "context"]


class ImpactQuery(ImpactModel):
    kind: ImpactKind = "code-impact"
    revision: Annotated[int | None, Field(ge=0)] = None
    max_depth: Annotated[int, Field(ge=1, le=16)] = 8
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class ImpactScopeEvidence(ImpactModel):
    id: UUID
    commit_hash: CommitHash
    revision: Revision
    work_item_id: UUID | None
    agent_session_id: str | None
    repository: RepositoryIdentity | None = None
    completion_report_id: UUID | None = None
    merged_commit_hashes: list[CommitHash] = []
    completion_citations: list[ScopeCompletionCitation] = Field(
        default=[], max_length=10, exclude_if=lambda v: not v
    )
    completion_citations_truncated: StrictBool = Field(default=False, exclude_if=lambda v: not v)


class ImpactCandidate(ImpactModel):
    decision_id: Identifier
    reference: Reference
    revision: Revision
    title: str
    state: Literal["proposed", "rejected", "accepted", "superseded", "partially-superseded"]
    is_current: StrictBool
    relationship: Literal["origin", "depends-on", "depends-on-prerequisite", "related-only"]
    path: list[Reference]
    rationale: str | None
    scopes: list[ImpactScopeEvidence]
    related_work_item_ids: list[UUID]
    related_artifact_ids: list[UUID]
    evidence_truncated: StrictBool

    @model_validator(mode="after")
    def coherent_identity(self) -> Self:
        if self.reference != f"{self.decision_id}-{self.revision}":
            raise ValueError("Candidate revision identity disagrees")
        if not self.path or self.path[-1] != self.reference:
            raise ValueError("Candidate path must end at its exact revision")
        if len(self.path) != len(set(self.path)):
            raise ValueError("Candidate path cannot repeat a revision")
        if any(scope.revision != self.revision for scope in self.scopes):
            raise ValueError("Candidate scope cites a different revision")
        return self


class ImpactWarning(ImpactModel):
    code: Literal["origin_has_no_linked_commits"]
    reference: Reference
    message: str


class ImpactCoverage(ImpactModel):
    traversal_complete: StrictBool
    provenance_complete: Literal[False] = False
    max_depth: Annotated[StrictInt, Field(ge=1)]
    candidate_limit: Annotated[StrictInt, Field(ge=1)]
    visited_revisions: Annotated[StrictInt, Field(ge=0)]
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


class ImpactRead(ImpactModel):
    project_id: UUID
    origin_reference: Reference
    origin_author: RevisionAuthorContext
    items: list[ImpactCandidate]
    kind: ImpactKind
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    context_count: Annotated[StrictInt, Field(ge=0)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1)]
    offset: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None
    coverage: ImpactCoverage

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        if self.origin_author.reference != self.origin_reference:
            raise ValueError("Author context must identify the pinned origin")
        if any(item.reference != self.origin_reference for item in self.coverage.warnings):
            raise ValueError("Coverage warnings must identify the pinned origin")
        expected_total = self.candidate_count if self.kind == "code-impact" else self.context_count
        if self.total != expected_total or any(
            (item.relationship in ("origin", "depends-on")) != (self.kind == "code-impact")
            for item in self.items
        ):
            raise ValueError("Impact page must match its selected kind")
        validate_page_items(self.items, total=self.total, limit=self.limit,
                            offset=self.offset, key=lambda item: item.reference)
        expected = self.offset + len(self.items) if self.offset + len(self.items) < self.total else None
        if self.next_offset != expected:
            raise ValueError("Impact continuation does not match its page")
        return self


Disposition = Literal["still-valid", "change-required", "not-affected", "resolved"]


class ImpactActor(ImpactModel):
    actor_client: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    actor_session_id: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    actor_model: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] = None

    @field_validator("actor_client", "actor_session_id", "actor_model")
    @classmethod
    def valid_actor(cls, value: str | None) -> str | None:
        return nonblank(value) if value is not None else None


class ImpactDispositionRequest(ImpactModel):
    candidate_reference: Reference
    expected_version: Annotated[StrictInt, Field(ge=0)]
    disposition: Disposition
    reason: Reason
    remediation_work_item_id: UUID | None = None
    actor: ImpactActor

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)


class ImpactDispositionCreate(ImpactDispositionRequest):
    client_operation_id: UUID


class ImpactDispositionRead(ImpactModel):
    id: UUID
    project_id: UUID
    assessment_id: UUID
    version: Annotated[StrictInt, Field(ge=1)]
    candidate_reference: Reference
    disposition: Disposition
    reason: str
    remediation_work_item_id: UUID | None
    actor: ImpactActor
    created_at: datetime


class ImpactDiff(ImpactModel):
    changed_fields: list[str]
    before_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    after_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    unified_diff: Annotated[str, Field(max_length=16384)]
    truncated: StrictBool


class ImpactAssessmentRead(ImpactModel):
    id: UUID
    project_id: UUID
    trigger: Literal["revision", "supersession"]
    before_reference: Reference
    after_reference: Reference
    before_author: RevisionAuthorContext
    after_author: RevisionAuthorContext
    extent: Literal["full", "partial"] | None
    reason: str | None
    diff: ImpactDiff
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    context_count: Annotated[StrictInt, Field(ge=0)]
    coverage: ImpactCoverage
    created_at: datetime
    version: Annotated[StrictInt, Field(ge=0)]
    unassessed_count: Annotated[StrictInt, Field(ge=0)]
    change_required_count: Annotated[StrictInt, Field(ge=0)]
    status: Literal["pending", "change-required", "resolved"]

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if (self.before_author.reference, self.after_author.reference) != (
            self.before_reference, self.after_reference,
        ):
            raise ValueError("Revision authors must identify the assessment transition")
        if any(item.reference != self.before_reference for item in self.coverage.warnings):
            raise ValueError("Assessment warnings must identify the captured origin")
        if self.unassessed_count > self.candidate_count or (
            self.unassessed_count + self.change_required_count
            > self.candidate_count + self.context_count
        ):
            raise ValueError("Assessment summary counts exceed captured candidates")
        expected = (
            "change-required" if self.change_required_count
            else "pending" if self.unassessed_count else "resolved"
        )
        if self.status != expected:
            raise ValueError("Assessment status disagrees with candidate dispositions")
        return self


class ImpactAssessmentDetail(ImpactAssessmentRead):
    kind: ImpactKind
    total: Annotated[StrictInt, Field(ge=0)]
    candidates: list[ImpactCandidate]
    dispositions: list[ImpactDispositionRead]
    disposition_history_total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    offset: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None

    @model_validator(mode="after")
    def coherent_candidates(self) -> Self:
        expected_total = self.candidate_count if self.kind == "code-impact" else self.context_count
        if self.total != expected_total or any(
            (item.relationship in ("origin", "depends-on")) != (self.kind == "code-impact")
            for item in self.candidates
        ):
            raise ValueError("Assessment page must match its selected kind")
        validate_page_items(self.candidates, total=self.total, limit=self.limit,
                            offset=self.offset, key=lambda item: item.reference)
        refs = {item.reference for item in self.candidates}
        if any(item.project_id != self.project_id or item.assessment_id != self.id
               or item.version > self.version or item.candidate_reference not in refs
               for item in self.dispositions):
            raise ValueError("Assessment disposition does not match this candidate page")
        if len({item.candidate_reference for item in self.dispositions}) != len(self.dispositions):
            raise ValueError("Candidate page must contain only the latest disposition")
        next_offset = self.offset + len(self.candidates)
        expected = next_offset if next_offset < self.total else None
        if self.next_offset != expected:
            raise ValueError("Assessment continuation does not match its candidate page")
        return self


class ImpactAssessmentQuery(ImpactModel):
    decision_id: Identifier | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0
