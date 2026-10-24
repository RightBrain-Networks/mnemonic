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
from pydantic.experimental.missing_sentinel import MISSING
from pydantic.json_schema import SkipJsonSchema

from .provenance_models import CommitHash, RepositoryIdentity, ScopeCompletionCitation, nonblank
from .response_validation import validate_page_items
from .review_scope_models import ReviewScopeChanged
from .revision_author_models import RevisionAuthorContext, RevisionAuthorWork


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
ImpactDetail = Literal["full", "compact"]
ImpactScopes = Literal["full", "summary", "none"]


class ImpactProjection(ImpactModel):
    detail: ImpactDetail = "full"
    include_diff: StrictBool = True
    include_scopes: ImpactScopes = "full"


def require_projection_fields(value: ImpactModel, names: tuple[str, ...], present: bool) -> None:
    if any((getattr(value, name) is not MISSING) != present for name in names):
        raise ValueError("Impact fields disagree with the declared projection")


def effective_projection(value: ImpactProjection | MISSING) -> ImpactProjection:
    return ImpactProjection() if value is MISSING else value



class ImpactQuery(ImpactProjection):
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


class ImpactScopeSummary(ImpactModel):
    captured_scope_count: Annotated[StrictInt, Field(ge=0, le=10)]
    commit_hashes: Annotated[list[CommitHash], Field(max_length=10)]
    evidence_truncated: StrictBool

    @model_validator(mode="after")
    def coherent_summary(self) -> Self:
        if len(self.commit_hashes) > self.captured_scope_count or (
            len(self.commit_hashes) != len(set(self.commit_hashes))
        ) or bool(self.commit_hashes) != bool(self.captured_scope_count):
            raise ValueError("Scope summary must describe distinct captured commits")
        return self


class ImpactCandidateRead(ImpactModel):
    decision_id: Identifier
    reference: Reference
    revision: Revision
    title: str | MISSING = MISSING
    state: Literal["proposed", "rejected", "accepted", "superseded", "partially-superseded"]
    is_current: StrictBool
    relationship: Literal["origin", "depends-on", "depends-on-prerequisite", "related-only"]
    related_direction: Literal["incoming", "outgoing"] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    path: list[Reference]
    rationale: str | None | MISSING = MISSING
    scopes: list[ImpactScopeEvidence] | MISSING = MISSING
    scope_summary: ImpactScopeSummary | MISSING = MISSING
    related_work_item_ids: list[UUID] | MISSING = MISSING
    related_artifact_ids: list[UUID] | MISSING = MISSING
    evidence_truncated: StrictBool

    @model_validator(mode="after")
    def coherent_identity(self) -> Self:
        if self.related_direction is not None and self.relationship != "related-only":
            raise ValueError("Only related-only candidates can carry a related direction")
        if self.reference != f"{self.decision_id}-{self.revision}":
            raise ValueError("Candidate revision identity disagrees")
        if not self.path or self.path[-1] != self.reference:
            raise ValueError("Candidate path must end at its exact revision")
        if len(self.path) != len(set(self.path)):
            raise ValueError("Candidate path cannot repeat a revision")
        if self.scopes is not MISSING and any(
            scope.revision != self.revision for scope in self.scopes
        ):
            raise ValueError("Candidate scope cites a different revision")
        return self

    def validate_projection(self, options: ImpactProjection) -> None:
        require_projection_fields(self, (
            "title", "rationale", "related_work_item_ids", "related_artifact_ids",
        ), options.detail == "full")
        require_projection_fields(self, ("scopes",), options.include_scopes == "full")
        require_projection_fields(self, ("scope_summary",), options.include_scopes == "summary")
        if self.scope_summary is not MISSING and (
            self.scope_summary.evidence_truncated != self.evidence_truncated
        ):
            raise ValueError("Scope summary must preserve candidate evidence coverage")

    @property
    def requires_disposition(self) -> bool:
        return self.relationship in ("origin", "depends-on") or (
            self.relationship == "related-only" and self.related_direction == "incoming"
        )


class ImpactAuthorRead(ImpactModel):
    reference: Reference
    author_kind: Literal["human", "agent"]
    agent_session_id: str | None
    model_id: str | None
    authored_at: datetime
    work_observed_at: datetime
    active_work_items: list[RevisionAuthorWork] | MISSING = MISSING
    active_work_item_ids: Annotated[list[UUID], Field(max_length=10)] | MISSING = MISSING
    active_work_items_truncated: StrictBool

    def validate_projection(self, options: ImpactProjection) -> None:
        require_projection_fields(self, ("active_work_items",), options.detail == "full")
        require_projection_fields(self, ("active_work_item_ids",), options.detail == "compact")
        if options.detail == "full":
            RevisionAuthorContext.model_validate(self.model_dump())
        elif self.active_work_item_ids is not MISSING:
            if len(self.active_work_item_ids) != len(set(self.active_work_item_ids)):
                raise ValueError("Author work identities must be unique")
            if self.author_kind == "human" and (
                self.agent_session_id or self.model_id or self.active_work_item_ids
            ):
                raise ValueError("Human author cannot have agent session holdings")


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
    origin_author: ImpactAuthorRead
    items: list[ImpactCandidateRead]
    projection: ImpactProjection | MISSING = MISSING
    kind: ImpactKind
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    context_count: Annotated[StrictInt, Field(ge=0)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1)]
    offset: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None
    coverage: ImpactCoverage

    @model_validator(mode="after")
    def coherent_projection(self) -> Self:
        options = effective_projection(self.projection)
        self.origin_author.validate_projection(options)
        for item in self.items:
            item.validate_projection(options)
        return self

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        if self.origin_author.reference != self.origin_reference:
            raise ValueError("Author context must identify the pinned origin")
        if any(item.reference != self.origin_reference for item in self.coverage.warnings):
            raise ValueError("Coverage warnings must identify the pinned origin")
        expected_total = self.candidate_count if self.kind == "code-impact" else self.context_count
        if self.total != expected_total or any(
            item.requires_disposition != (self.kind == "code-impact")
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


class ImpactDispositionItem(ImpactModel):
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
    actor: ImpactActor
    lease_token: str | None = Field(default=None, repr=False, exclude_if=lambda value: value is None)


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
    review_scope_changed: ReviewScopeChanged | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class ImpactDispositionBatchRequest(ImpactModel):
    expected_version: Annotated[StrictInt, Field(ge=0)]
    items: Annotated[list[ImpactDispositionItem], Field(min_length=1, max_length=100)]
    actor: ImpactActor
    lease_token: str | None = Field(default=None, repr=False, exclude_if=lambda value: value is None)

    @field_validator("items")
    @classmethod
    def unique_candidates(cls, items: list[ImpactDispositionItem]) -> list[ImpactDispositionItem]:
        if len({item.candidate_reference for item in items}) != len(items):
            raise ValueError("A batch must name each candidate at most once")
        return items


class ImpactDispositionBatchCreate(ImpactDispositionBatchRequest):
    client_operation_id: UUID


class ImpactDispositionBatchRead(ImpactModel):
    project_id: UUID
    assessment_id: UUID
    version: Annotated[StrictInt, Field(ge=1)]
    items: Annotated[list[ImpactDispositionRead], Field(min_length=1, max_length=100)]
    review_scope_changed: ReviewScopeChanged | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def coherent_items(self) -> Self:
        if any(item.review_scope_changed is not None for item in self.items):
            raise ValueError("Batch scope notices belong only to the top-level result")
        if len({item.id for item in self.items}) != len(self.items) or len({
            item.candidate_reference for item in self.items
        }) != len(self.items):
            raise ValueError("Batch dispositions must have unique identities and candidates")
        start = self.version - len(self.items) + 1
        if any((item.project_id, item.assessment_id, item.version) != (
            self.project_id, self.assessment_id, start + offset,
        ) for offset, item in enumerate(self.items)):
            raise ValueError("Batch dispositions must belong to one episode in consecutive order")
        return self


class ImpactDiff(ImpactModel):
    changed_fields: list[str]
    before_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    after_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    unified_diff: Annotated[str, Field(max_length=16384)]
    truncated: StrictBool


class ImpactDiffRead(ImpactModel):
    changed_fields: list[str]
    before_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    after_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    unified_diff: Annotated[str, Field(max_length=16384)] | MISSING = MISSING
    truncated: StrictBool


class ImpactDispositionProjection(ImpactModel):
    id: UUID | MISSING = MISSING
    project_id: UUID | MISSING = MISSING
    assessment_id: UUID | MISSING = MISSING
    version: Annotated[StrictInt, Field(ge=1)]
    candidate_reference: Reference
    disposition: Disposition
    reason: str | MISSING = MISSING
    remediation_work_item_id: UUID | None
    actor: ImpactActor | MISSING = MISSING
    created_at: datetime | MISSING = MISSING

    def validate_projection(self, options: ImpactProjection) -> None:
        require_projection_fields(self, (
            "id", "project_id", "assessment_id", "reason", "actor", "created_at",
        ), options.detail == "full")


class ImpactAssessmentRead(ImpactModel):
    id: UUID
    project_id: UUID
    trigger: Literal["revision", "supersession"]
    before_reference: Reference
    after_reference: Reference
    before_author: ImpactAuthorRead
    after_author: ImpactAuthorRead
    extent: Literal["full", "partial"] | None
    reason: str | None
    diff: ImpactDiffRead
    projection: ImpactProjection | MISSING = MISSING
    candidate_count: Annotated[StrictInt, Field(ge=0)]
    context_count: Annotated[StrictInt, Field(ge=0)]
    coverage: ImpactCoverage
    created_at: datetime
    version: Annotated[StrictInt, Field(ge=0)]
    unassessed_count: Annotated[StrictInt, Field(ge=0)]
    change_required_count: Annotated[StrictInt, Field(ge=0)]
    status: Literal["pending", "change-required", "resolved"]

    @model_validator(mode="after")
    def coherent_projection(self) -> Self:
        options = effective_projection(self.projection)
        self.before_author.validate_projection(options)
        self.after_author.validate_projection(options)
        require_projection_fields(self.diff, ("unified_diff",), options.include_diff)
        return self

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
    candidates: list[ImpactCandidateRead]
    dispositions: list[ImpactDispositionProjection]
    disposition_history_total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    offset: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None

    @model_validator(mode="after")
    def coherent_candidate_projection(self) -> Self:
        options = effective_projection(self.projection)
        for candidate in self.candidates:
            candidate.validate_projection(options)
        for disposition in self.dispositions:
            disposition.validate_projection(options)
        return self

    @model_validator(mode="after")
    def coherent_candidates(self) -> Self:
        expected_total = self.candidate_count if self.kind == "code-impact" else self.context_count
        if self.total != expected_total or any(
            item.requires_disposition != (self.kind == "code-impact")
            for item in self.candidates
        ):
            raise ValueError("Assessment page must match its selected kind")
        validate_page_items(self.candidates, total=self.total, limit=self.limit,
                            offset=self.offset, key=lambda item: item.reference)
        if effective_projection(self.projection).detail == "full" and any(
            item.project_id != self.project_id or item.assessment_id != self.id
            for item in self.dispositions
        ):
            raise ValueError("Assessment disposition identifies another episode")
        refs = {item.reference for item in self.candidates}
        if any(item.version > self.version or item.candidate_reference not in refs
               for item in self.dispositions):
            raise ValueError("Assessment disposition does not match this candidate page")
        if len({item.candidate_reference for item in self.dispositions}) != len(self.dispositions):
            raise ValueError("Candidate page must contain only the latest disposition")
        next_offset = self.offset + len(self.candidates)
        expected = next_offset if next_offset < self.total else None
        if self.next_offset != expected:
            raise ValueError("Assessment continuation does not match its candidate page")
        return self


class ImpactAssessmentQuery(ImpactProjection):
    decision_id: Identifier | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0
