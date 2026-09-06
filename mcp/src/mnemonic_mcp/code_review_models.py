"""Independent, bounded MCP code-review wire contracts."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from .phase12_models import PositiveDecimalString, _validated_text, omit_default

PositiveRevision = PositiveDecimalString


def report_text(value: str, *, multiline: bool = False) -> str:
    return _validated_text(value, max_bytes=65536, multiline=multiline)


def multiline(value: str) -> str:
    return report_text(value, multiline=True)


def threshold(value: int) -> int:
    if value % 5:
        raise ValueError("Review thresholds must be multiples of five")
    return value


ReviewThreshold = Annotated[StrictInt, Field(ge=0, le=100), AfterValidator(threshold)]
ReviewVersion = Annotated[StrictInt, Field(ge=1, le=2147483647)]
ReviewMode = Literal["cold", "warm"]
ReviewDecision = Literal[
    "ineligible_depth_limit", "ineligible_remediation_disabled", "mandatory",
    "ask_recommendation", "not_requested",
]
ReviewText = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=2000),
    AfterValidator(multiline),
]
ReviewSummary = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=4000),
    AfterValidator(multiline),
]
RepositoryKey = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=80,
                           pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
FullCommit = Annotated[str, StringConstraints(strict=True, pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
ScopeHash = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
ClientName = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=80), AfterValidator(report_text),
]
SessionID = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=200), AfterValidator(report_text),
]
ModelName = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=120), AfterValidator(report_text),
]
ReviewTitle = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=200), AfterValidator(report_text),
]


def charged_bytes(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, list):
        return sum(charged_bytes(item) for item in value)
    if isinstance(value, dict):
        return sum(charged_bytes(key) + charged_bytes(item) for key, item in value.items())
    return 0


def require_bound(value: BaseModel, maximum: int, *, include: set[str] | None = None) -> None:
    payload = value.model_dump(mode="json", include=include)
    if len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > maximum:
        raise ValueError("Review content exceeds its aggregate byte bound")


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)


class ReviewActor(ReviewModel):
    actor_client: ClientName
    actor_session_id: SessionID
    actor_model: ModelName | None


class RepositoryRange(ReviewModel):
    repository_key: RepositoryKey
    repository_url: Annotated[str, StringConstraints(strict=True, max_length=2000)] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    checkout_path: Annotated[str, StringConstraints(strict=True, max_length=4096)] | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    object_format: Literal["sha1", "sha256"]
    base_commit: FullCommit
    head_commit: FullCommit

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        width = 40 if self.object_format == "sha1" else 64
        if len(self.base_commit) != width or len(self.head_commit) != width:
            raise ValueError("Commit length must match the repository object format")
        if self.repository_url is None and self.checkout_path is None:
            raise ValueError("At least one repository locator is required")
        self._locators()
        return self

    def _locators(self) -> None:
        for field in ("repository_url", "checkout_path"):
            value = getattr(self, field)
            if value is None:
                if field in self.model_fields_set:
                    raise ValueError("Locator null is not supported; omit it")
                continue
            report_text(value)
            if any(char in value for char in "`$;|<>\\"):
                raise ValueError("Repository locators cannot contain shell fragments")
        if self.repository_url is not None:
            parts = urlsplit(self.repository_url)
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                    or parts.query or parts.fragment or any(c.isspace() for c in self.repository_url)):
                raise ValueError("Repository URL must be credential-free HTTPS without query data")
        if self.checkout_path is not None and not self.checkout_path.startswith("/"):
            raise ValueError("Checkout hints must be absolute paths")


class CodeReviewScopeInput(ReviewModel):
    repositories: list[RepositoryRange] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def scope_is_unique_and_bounded(self) -> Self:
        if len({row.repository_key for row in self.repositories}) != len(self.repositories):
            raise ValueError("Repository keys must be unique")
        identities = {(r.repository_url, r.checkout_path, r.base_commit, r.head_commit)
                      for r in self.repositories}
        if len(identities) != len(self.repositories):
            raise ValueError("Duplicate repository ranges are not supported")
        require_bound(self, 65536)
        return self


def scope_hash(scope: CodeReviewScopeInput) -> str:
    encoded = json.dumps(scope.model_dump(mode="json"), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class CodeReviewHandoffNotes(ReviewModel):
    change_summary: ReviewSummary
    decisions: list[ReviewText] = Field(max_length=20)
    focus_areas: list[ReviewText] = Field(max_length=20)
    traps: list[ReviewText] = Field(max_length=20)
    validation_summary: ReviewSummary

    @model_validator(mode="after")
    def handoff_is_bounded(self) -> Self:
        require_bound(self, 65536)
        return self


class CodeReviewHandoffInput(ReviewModel):
    scope: CodeReviewScopeInput
    handoff: CodeReviewHandoffNotes


def reject_null_review_argument(value: object) -> object:
    if value is None:
        raise ValueError("Present review arguments cannot be null; omit absent values")
    return value


CodeReviewHandoffArgument = Annotated[
    CodeReviewHandoffInput | SkipJsonSchema[None], BeforeValidator(reject_null_review_argument),
    Field(json_schema_extra=omit_default),
]
ReviewIDArgument = Annotated[
    UUID | SkipJsonSchema[None], BeforeValidator(reject_null_review_argument),
    Field(json_schema_extra=omit_default),
]
ReviewModeArgument = Annotated[
    ReviewMode | SkipJsonSchema[None], BeforeValidator(reject_null_review_argument),
    Field(json_schema_extra=omit_default),
]
ReviewVersionArgument = Annotated[
    ReviewVersion | SkipJsonSchema[None], BeforeValidator(reject_null_review_argument),
    Field(json_schema_extra=omit_default),
]


class CodeReviewRecommendationAnswer(ReviewModel):
    kind: Literal["code_review_recommendation"]
    recommend_review: StrictBool
    rationale: ReviewText
    code_review_handoff: CodeReviewHandoffInput | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def answer_matches_handoff(self) -> Self:
        if self.recommend_review != (self.code_review_handoff is not None):
            raise ValueError("Only an affirmative answer requires a review handoff")
        if "code_review_handoff" in self.model_fields_set and self.code_review_handoff is None:
            raise ValueError("Handoff null is not supported")
        return self


class ReviewTimestamp(ReviewModel):
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Review timestamps must be UTC")
        return value

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReviewPolicyRead(ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    completion_event_id: PositiveRevision
    settings_revision: PositiveRevision
    required_min_priority: ReviewThreshold
    optional_min_priority: ReviewThreshold
    allow_remediation_code_reviews: StrictBool
    priority_at_closeout: Annotated[StrictInt, Field(ge=0, le=100)]
    remediation_depth: Annotated[StrictInt, Field(ge=0, le=2)]
    decision: ReviewDecision

    @model_validator(mode="after")
    def decision_corresponds(self) -> Self:
        if self.decision != review_policy(
            self.priority_at_closeout, self.required_min_priority, self.optional_min_priority,
            self.allow_remediation_code_reviews, self.remediation_depth,
        ):
            raise ValueError("Review decision disagrees with its policy snapshot")
        return self


class CodeReviewRead(ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    completion_event_id: PositiveRevision
    policy_decision_id: UUID
    answer_id: UUID | None
    request_reason: Literal["mandatory", "recommended"]
    schema_version: Literal[1]
    version: ReviewVersion
    state: Literal["requested", "completed", "superseded"]
    requesting_client: ClientName
    requesting_session_id: SessionID
    requesting_model: ModelName | None
    scope_sha256: ScopeHash
    created_event_id: PositiveRevision
    created_sequence: PositiveRevision
    result_id: UUID | None
    superseded_by_event_id: PositiveRevision | None

    @model_validator(mode="after")
    def review_state_corresponds(self) -> Self:
        if (self.state == "requested") != (self.version == 1):
            raise ValueError("Review state and revision disagree")
        if self.state != "requested" and self.version != 2:
            raise ValueError("A review can transition only once")
        if (self.state == "completed") != (self.result_id is not None):
            raise ValueError("Review completion and result disagree")
        if (self.state == "superseded") != (self.superseded_by_event_id is not None):
            raise ValueError("Review supersession witness disagrees")
        if (self.request_reason == "recommended") != (self.answer_id is not None):
            raise ValueError("Review recommendation witness disagrees")
        return self


class WorkFollowUpRead(ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    trigger_event_id: PositiveRevision
    completion_checkpoint_id: UUID
    kind: Literal["code_review_recommendation"]
    schema_version: Literal[1]
    version: ReviewVersion
    audience: Literal["origin_agent", "origin_human"]
    question: ReviewSummary
    allowed_answers: list[Literal["yes", "no"]] = Field(min_length=2, max_length=2)
    required_answer_fields: list[Annotated[str, StringConstraints(strict=True, max_length=80)]] = (
        Field(min_length=1, max_length=10)
    )
    origin_client: ClientName
    origin_session_id: SessionID
    origin_model: ModelName | None
    kind_data: dict[str, UUID]
    state: Literal["pending", "answered", "superseded"]
    answer_id: UUID | None
    superseded_by_event_id: PositiveRevision | None
    created_event_id: PositiveRevision
    created_sequence: PositiveRevision

    @model_validator(mode="after")
    def question_state_corresponds(self) -> Self:
        if self.allowed_answers != ["yes", "no"]:
            raise ValueError("Unsupported recommendation answer contract")
        if set(self.kind_data) != {"policy_decision_id"}:
            raise ValueError("Recommendation kind data must identify its exact policy")
        if (self.state == "pending") != (self.version == 1):
            raise ValueError("Follow-up state and revision disagree")
        if self.state != "pending" and self.version != 2:
            raise ValueError("A follow-up can transition only once")
        if (self.state == "answered") != (self.answer_id is not None):
            raise ValueError("Follow-up answer witness disagrees")
        if (self.state == "superseded") != (self.superseded_by_event_id is not None):
            raise ValueError("Follow-up supersession witness disagrees")
        return self


class WorkFollowUpAnswerRead(ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    follow_up_id: UUID
    recommend_review: StrictBool
    rationale: ReviewText
    actor_client: ClientName
    actor_session_id: SessionID
    actor_model: ModelName | None
    code_review_id: UUID | None
    created_event_id: PositiveRevision

    @model_validator(mode="after")
    def answer_review_corresponds(self) -> Self:
        if self.recommend_review != (self.code_review_id is not None):
            raise ValueError("Only affirmative answers have a review")
        return self


class ReviewCoverage(ReviewModel):
    repository_key: RepositoryKey
    base_commit: FullCommit
    head_commit: FullCommit


class CodeReviewFindingInput(ReviewModel):
    finding_key: Annotated[str, StringConstraints(strict=True, pattern=r"^F[0-9]{3}$")]
    severity: Literal["critical", "high", "medium", "low"]
    title: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200),
                     AfterValidator(report_text)]
    repository_key: RepositoryKey
    path: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=4096),
                    AfterValidator(report_text)]
    location_side: Literal["base", "head"]
    start_line: Annotated[StrictInt, Field(ge=1, le=2147483647)] | None = None
    end_line: Annotated[StrictInt, Field(ge=1, le=2147483647)] | None = None
    problem: ReviewText
    triggering_conditions: ReviewText
    impact: ReviewText
    evidence: ReviewText
    recommended_verification: ReviewText

    @model_validator(mode="after")
    def finding_is_valid(self) -> Self:
        if self.path.startswith(("/", "\\")) or ".." in self.path.split("/") or "\\" in self.path:
            raise ValueError("Finding paths must be repository relative without traversal")
        if self.end_line is not None and (self.start_line is None or self.end_line < self.start_line):
            raise ValueError("Finding line range is invalid")
        require_bound(self, 8192)
        return self


class CodeReviewResultInput(ReviewModel):
    mode: ReviewMode
    summary: ReviewSummary
    coverage: list[ReviewCoverage] = Field(min_length=1, max_length=10)
    limitations: list[Annotated[str, StringConstraints(strict=True, min_length=1, max_length=1000),
                               AfterValidator(multiline)]] = Field(max_length=20)
    findings: list[CodeReviewFindingInput] = Field(max_length=100)

    @model_validator(mode="after")
    def result_is_bounded(self) -> Self:
        if len({r.repository_key for r in self.coverage}) != len(self.coverage):
            raise ValueError("Coverage repository keys must be unique")
        if len({r.finding_key for r in self.findings}) != len(self.findings):
            raise ValueError("Finding keys must be unique")
        keys = {r.repository_key for r in self.coverage}
        if any(row.repository_key not in keys for row in self.findings):
            raise ValueError("Finding repository is missing from declared coverage")
        require_bound(self, 65536, include=set(CodeReviewResultInput.model_fields))
        return self


class CodeReviewResultRead(CodeReviewResultInput, ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    review_id: UUID
    scope_sha256: ScopeHash
    actor_client: ClientName
    actor_session_id: SessionID
    actor_model: ModelName | None
    lease_generation_id: UUID
    claim_event_id: PositiveRevision
    created_event_id: PositiveRevision


class CodeReviewRemediationRead(ReviewTimestamp):
    id: UUID
    project_id: UUID
    review_id: UUID
    result_id: UUID
    source_work_item_id: UUID
    completion_checkpoint_id: UUID
    remediation_work_item_id: UUID
    relationship_id: UUID
    parent_remediation_id: UUID | None
    root_work_item_id: UUID
    depth: Annotated[StrictInt, Field(ge=1, le=2)]


class CodeReviewContext(ReviewModel):
    remediation_depth: Annotated[StrictInt, Field(ge=0, le=2)]
    current_review: CodeReviewRead | None
    pending_follow_up: WorkFollowUpRead | None
    remediation_origin: CodeReviewRemediationRead | None

    @model_validator(mode="after")
    def provenance_corresponds(self) -> Self:
        if (self.remediation_depth > 0) != (self.remediation_origin is not None):
            raise ValueError("Remediation depth requires exact retained provenance")
        if self.remediation_origin is not None and self.remediation_origin.depth != self.remediation_depth:
            raise ValueError("Remediation depth disagrees with its origin")
        if self.current_review is not None and self.pending_follow_up is not None:
            raise ValueError("A current review and unanswered recommendation cannot coexist")
        if self.current_review is not None and self.current_review.state != "requested":
            raise ValueError("Current review pointer must identify an outstanding request")
        if self.pending_follow_up is not None and self.pending_follow_up.state != "pending":
            raise ValueError("Pending follow-up pointer cannot identify an answered question")
        if self.remediation_depth == 2 and (self.current_review or self.pending_follow_up):
            raise ValueError("Second-generation remediation cannot have review obligations")
        return self


class ReviewSourceState(ReviewModel):
    work_item_id: UUID
    title: ReviewTitle
    status: Literal["pending", "deferred", "done", "wont-do", "promoted"]
    deleted: StrictBool


class CodeReviewDetail(ReviewModel):
    review: CodeReviewRead
    policy_decision: ReviewPolicyRead
    scope: CodeReviewScopeInput
    handoff: CodeReviewHandoffNotes
    result: CodeReviewResultRead | None
    remediation: CodeReviewRemediationRead | None
    source_work_state: ReviewSourceState


class WorkFollowUpDetail(ReviewModel):
    follow_up: WorkFollowUpRead
    answer: WorkFollowUpAnswerRead | None
    code_review: CodeReviewRead | None
    source_work_state: ReviewSourceState


class ReviewQueueRow(ReviewTimestamp):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    title: ReviewTitle
    work_status: Literal["pending", "deferred", "done", "wont-do", "promoted"]
    state: Literal["pending", "answered", "requested", "completed", "superseded"]
    version: ReviewVersion
    created_sequence: PositiveRevision
    request_reason: Literal["mandatory", "recommended"] | None
    kind: Literal["code_review_recommendation"] | None
    remediation_depth: Annotated[StrictInt, Field(ge=0, le=2)]
    review_available: StrictBool
    result_id: UUID | None
    remediation_work_item_id: UUID | None
    lease: dict[str, str] | None

    @model_validator(mode="after")
    def queue_row_corresponds(self) -> Self:
        review = self.request_reason is not None
        if review == (self.kind is not None):
            raise ValueError("Queue row must identify exactly one resource kind")
        open_state = "requested" if review else "pending"
        allowed = {open_state, "completed" if review else "answered", "superseded"}
        if self.state not in allowed or self.version != (1 if self.state == open_state else 2):
            raise ValueError("Queue resource state/version disagree")
        if self.remediation_depth == 2:
            raise ValueError("Second-generation remediation cannot enter review queues")
        if (self.result_id is not None) != (review and self.state == "completed"):
            raise ValueError("Queue result witness disagrees with resource state")
        if self.remediation_work_item_id is not None and self.result_id is None:
            raise ValueError("Queue remediation requires a completed result")
        if self.lease is not None:
            from .models import LeasePublic

            lease = LeasePublic.model_validate(self.lease)
            if not review or self.state != "requested" or (
                lease.purpose != "code_review" or lease.code_review_id != self.id
            ):
                raise ValueError("Queue lease must belong to this requested review")
        if self.review_available and (
            not review or self.state != "requested" or self.work_status != "done"
            or self.lease is not None
        ):
            raise ValueError("Only unleased requested Done reviews can be available")
        return self


class ReviewQueuePage(ReviewModel):
    project_id: UUID
    items: list[ReviewQueueRow] = Field(max_length=50)
    has_more: StrictBool
    next_cursor: Annotated[str, StringConstraints(strict=True, max_length=4096)]


def review_policy(priority: int, required: int, optional: int, allow: bool, depth: int) -> ReviewDecision:
    if depth == 2:
        return "ineligible_depth_limit"
    if depth == 1 and not allow:
        return "ineligible_remediation_disabled"
    if required == 0 or (required < 100 and priority >= required):
        return "mandatory"
    if optional == 0 or (optional < 100 and priority >= optional):
        return "ask_recommendation"
    return "not_requested"
