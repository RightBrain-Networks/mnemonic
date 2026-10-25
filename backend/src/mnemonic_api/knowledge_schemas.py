"""Knowledge documents are unstructured UTF-8 text with revision-pinned provenance."""

import re
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, StringConstraints, field_validator, model_validator
from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.decision_change_schemas import (
    DecisionChangeDeclaration,
    DecisionRevisionChangeRead,
)
from mnemonic_api.decision_scope_schemas import ScopeCompletionCitation
from mnemonic_api.impact_schemas import DecisionDependency, SupersessionDetail
from mnemonic_api.product_decision_schemas import DecisionHumanGateRead
from mnemonic_api.provenance_schemas import RepositoryIdentity
from mnemonic_api.review_scope_schemas import ExtendedDecisionReview
from mnemonic_api.schemas import APIModel

DecisionState = Literal["proposed", "rejected", "accepted", "superseded", "partially-superseded"]
DecisionMediaType = Literal["text/plain", "text/markdown"]
DomainReviewPolicy = Literal["inherit", "on", "off"]
DecisionIdentifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
DecisionReference = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}(?:-[0-9]{1,10})?$")
]
RevisionNumber = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
MAX_DECISION_BYTES = 1_048_576


def _database_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Text cannot contain NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Text must be valid Unicode") from None
    return value


class KnowledgePage[T](APIModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class KnowledgePagination(APIModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class DomainCreateRequest(APIModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    prefix: Annotated[str, StringConstraints(min_length=1, max_length=5)]
    automatic_decision_reviews: DomainReviewPolicy | MISSING = Field(
        default=MISSING, exclude_if=lambda value: value == "inherit"
    )

    decision_review_priority: Annotated[StrictInt, Field(ge=0, le=100)] | MISSING = MISSING

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _database_text(value)

    @field_validator("prefix")
    @classmethod
    def uppercase_prefix(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{1,5}", value):
            raise ValueError("Domain prefix must contain one to five ASCII letters")
        return value


class DomainCreate(DomainCreateRequest):
    client_operation_id: UUID


class DomainRenameRequest(APIModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] | MISSING = MISSING
    automatic_decision_reviews: DomainReviewPolicy | MISSING = MISSING
    decision_review_priority: Annotated[StrictInt, Field(ge=0, le=100)] | MISSING = MISSING
    expected_version: Annotated[StrictInt, Field(ge=1)]

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return _database_text(value)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if (self.name is MISSING and self.automatic_decision_reviews is MISSING
                and self.decision_review_priority is MISSING):
            raise ValueError("Supply a domain name, automatic review setting, or priority")
        return self


class DomainRename(DomainRenameRequest):
    client_operation_id: UUID


class KnowledgeDomainRead(APIModel):
    is_product_decision_domain: bool = Field(default=False, exclude_if=lambda value: not value)
    id: UUID
    project_id: UUID
    name: str
    prefix: str
    # Permanent receipts predating domain policy retain their original sparse shape.
    automatic_decision_reviews: DomainReviewPolicy | MISSING = MISSING
    decision_review_priority: Annotated[StrictInt, Field(ge=0, le=100)] | MISSING = MISSING
    version: int
    created_at: datetime
    updated_at: datetime
    decision_count: int


class DecisionPutRequest(APIModel):
    source_work_item_id: UUID | None | MISSING = MISSING
    change: DecisionChangeDeclaration | None = Field(default=None, exclude_if=lambda v: v is None)
    domain_id: UUID
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    content: Annotated[str, Field(max_length=MAX_DECISION_BYTES)]
    media_type: DecisionMediaType
    state: DecisionState
    decision_id: DecisionIdentifier | None = None
    expected_revision: RevisionNumber | None = None
    related_decision_ids: Annotated[list[DecisionIdentifier], Field(max_length=100)] = []
    related_work_item_ids: Annotated[list[UUID], Field(max_length=100)] = []
    related_artifact_ids: Annotated[list[UUID], Field(max_length=100)] = []
    supersedes: Annotated[list[DecisionIdentifier], Field(max_length=100)] = []
    depends_on: Annotated[list[DecisionDependency], Field(max_length=100)] = Field(
        default=[], exclude_if=lambda value: not value
    )
    supersession_details: Annotated[list[SupersessionDetail], Field(max_length=100)] = Field(
        default=[], exclude_if=lambda value: not value
    )
    author_kind: Literal["agent", "human"]
    agent_session_id: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    model_id: Annotated[str | None, Field(min_length=1, max_length=120)] = None

    @field_validator("title", "agent_session_id", "model_id")
    @classmethod
    def valid_metadata(cls, value: str | None) -> str | None:
        return None if value is None else _database_text(value)

    @field_validator("content")
    @classmethod
    def utf8_text(cls, value: str) -> str:
        encoded = _database_text(value).encode("utf-8")
        if len(encoded) > MAX_DECISION_BYTES:
            raise ValueError("Decision text exceeds the 1 MiB UTF-8 byte limit")
        return value

    @model_validator(mode="after")
    def revision_and_author(self) -> Self:
        if (self.decision_id is None) != (self.expected_revision is None):
            raise ValueError("Updates require decision_id and expected_revision together")
        if self.author_kind == "agent":
            if not (self.agent_session_id or "").strip() or not (self.model_id or "").strip():
                raise ValueError("Agent authors must supply agent_session_id and model_id")
        elif self.agent_session_id is not None or self.model_id is not None:
            raise ValueError("Human authors cannot assert agent identity")
        return self


class DecisionPut(DecisionPutRequest):
    client_operation_id: UUID


class DecisionRead(APIModel):
    change: DecisionRevisionChangeRead | None = Field(default=None, exclude_if=lambda v: v is None)
    human_gate: DecisionHumanGateRead | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    id: UUID
    project_id: UUID
    domain_id: UUID
    domain_name: str
    prefix: str
    ordinal: int
    decision_id: str
    reference: str
    revision: int
    title: str
    state: DecisionState
    media_type: DecisionMediaType
    size_bytes: int
    sha256: str
    created_at: datetime
    updated_at: datetime
    author_kind: Literal["agent", "human"]
    agent_session_id: str | None
    model_id: str | None
    related_decision_ids: list[str]
    referenced_by: list[str]
    related_work_item_ids: list[UUID]
    related_artifact_ids: list[UUID]
    supersedes: list[str]
    superseded_by: list[str]
    scope_count: int
    depends_on: list[DecisionDependency] = Field(default=[], exclude_if=lambda value: not value)
    supersession_details: list[SupersessionDetail] = Field(
        default=[], exclude_if=lambda value: not value
    )


class DecisionTextRead(DecisionRead):
    content: str
    total_lines: int
    line_start: int
    line_end: int


class DecisionPutResult(APIModel):
    extended_review: ExtendedDecisionReview | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    decision: DecisionRead
    created: bool


    @model_validator(mode="after")
    def extension_matches_change(self) -> Self:
        if self.extended_review is not None and (
            not self.created or self.decision.change is None
            or self.extended_review.review_scope_changed.project_id != self.decision.project_id
            or self.extended_review.review_scope_changed.change_id != self.decision.change.change_id
        ):
            raise ValueError("An extended review must belong to this fresh decision change")
        return self


class DecisionListQuery(KnowledgePagination):
    domain_id: UUID | None = None
    state: DecisionState | None = None
    q: Annotated[str, Field(max_length=1000)] = ""


class DecisionTextQuery(APIModel):
    revision: Annotated[int | None, Field(ge=0)] = None
    line_start: Annotated[int | None, Field(ge=1)] = None
    line_end: Annotated[int | None, Field(ge=1)] = None

    @model_validator(mode="after")
    def ordered_lines(self) -> Self:
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must be greater than or equal to line_start")
        return self


class DecisionScopeRequest(APIModel):
    repository: RepositoryIdentity | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    revision: RevisionNumber
    commit_hash: Annotated[str, StringConstraints(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")]
    work_item_id: UUID | None = None
    agent_session_id: Annotated[str | None, Field(min_length=1, max_length=200)] = None

    @field_validator("agent_session_id")
    @classmethod
    def valid_session(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("An optional agent session must be nonblank when supplied")
        return None if value is None else _database_text(value)

    @field_validator("commit_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return value.lower()


class DecisionScopeAppend(DecisionScopeRequest):
    client_operation_id: UUID


class DecisionScopeRead(APIModel):
    repository: RepositoryIdentity | None = Field(default=None, exclude_if=lambda v: v is None)
    completion_report_id: UUID | None = Field(default=None, exclude_if=lambda v: v is None)
    merged_commit_hashes: list[str] = Field(default=[], exclude_if=lambda v: not v)
    completion_citations: list[ScopeCompletionCitation] = Field(
        default=[], max_length=50, exclude_if=lambda v: not v
    )
    completion_citations_truncated: bool = Field(default=False, exclude_if=lambda v: not v)
    id: UUID
    project_id: UUID
    document_id: UUID
    decision_id: str
    revision: int
    commit_hash: str
    work_item_id: UUID | None
    agent_session_id: str | None
    created_at: datetime


class DecisionScopeResult(APIModel):
    scope: DecisionScopeRead
    created: bool
