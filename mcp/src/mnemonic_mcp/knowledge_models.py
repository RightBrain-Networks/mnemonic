"""Knowledge documents are unstructured UTF-8 text with revision-pinned provenance."""

import re
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

from .decision_change_models import DecisionChangeInput, DecisionRevisionChange
from .impact_models import DecisionDependency, SupersessionDetail
from .provenance_models import CommitHash, RepositoryIdentity, ScopeCompletionCitation
from .response_validation import validate_page_items


class KnowledgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

DecisionState = Literal[
    "proposed", "rejected", "accepted", "superseded", "partially-superseded"
]
DecisionMediaType = Literal["text/plain", "text/markdown"]
DecisionIdentifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
DecisionReference = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}(?:-[0-9]{1,10})?$")
]
RevisionNumber = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
MAX_DECISION_BYTES = 1_048_576


class KnowledgePage[T](KnowledgeModel):
    items: list[T]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    offset: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        validate_page_items(self.items, total=self.total, limit=self.limit,
                            offset=self.offset, key=lambda item: (
                                getattr(item, "id", None), getattr(item, "revision", None),
                            ))
        return self


class KnowledgePagination(KnowledgeModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0)] = 0


class DomainCreateRequest(KnowledgeModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    prefix: Annotated[str, StringConstraints(min_length=1, max_length=5)]

    @field_validator("prefix")
    @classmethod
    def uppercase_prefix(cls, value: str) -> str:
        value = value.upper()
        if not re.fullmatch(r"[A-Z]{1,5}", value):
            raise ValueError("Domain prefix must contain one to five ASCII letters")
        return value


class DomainCreate(DomainCreateRequest):
    client_operation_id: UUID


class DomainRenameRequest(KnowledgeModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    expected_version: Annotated[StrictInt, Field(ge=1)]


class DomainRename(DomainRenameRequest):
    client_operation_id: UUID


class KnowledgeDomainRead(KnowledgeModel):
    id: UUID
    project_id: UUID
    name: str
    prefix: Annotated[str, Field(pattern=r"^[A-Z]{1,5}$")]
    version: Annotated[StrictInt, Field(ge=1)]
    created_at: datetime
    updated_at: datetime
    decision_count: Annotated[StrictInt, Field(ge=0)]
    is_product_decision_domain: StrictBool = Field(default=False, exclude_if=lambda v: not v)


class DecisionPutRequest(KnowledgeModel):
    source_work_item_id: UUID | None | MISSING = MISSING
    change: DecisionChangeInput | None = Field(default=None, exclude_if=lambda v: v is None)
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
        default=[], exclude_if=lambda value: not value,
    )
    supersession_details: Annotated[list[SupersessionDetail], Field(max_length=100)] = Field(
        default=[], exclude_if=lambda value: not value,
    )
    author_kind: Literal["agent", "human"]
    agent_session_id: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    model_id: Annotated[str | None, Field(min_length=1, max_length=120)] = None

    @field_validator("content")
    @classmethod
    def utf8_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Decision text cannot contain NUL characters")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Decision text must be valid Unicode") from None
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


class DecisionHumanGateRead(KnowledgeModel):
    gate_id: UUID
    work_item_id: UUID
    work_project_id: UUID
    status: Literal["unresolved", "resolved"]
    question_version: Annotated[StrictInt, Field(ge=1)]


class DecisionRead(KnowledgeModel):
    change: DecisionRevisionChange | None = Field(default=None, exclude_if=lambda v: v is None)
    id: UUID
    project_id: UUID
    domain_id: UUID
    domain_name: str
    prefix: Annotated[str, Field(pattern=r"^[A-Z]{1,5}$")]
    ordinal: Annotated[StrictInt, Field(ge=1)]
    decision_id: DecisionIdentifier
    reference: DecisionReference
    revision: RevisionNumber
    title: str
    state: DecisionState
    media_type: DecisionMediaType
    size_bytes: Annotated[StrictInt, Field(ge=0, le=MAX_DECISION_BYTES)]
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
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
    scope_count: Annotated[StrictInt, Field(ge=0)]
    depends_on: list[DecisionDependency] = Field(default=[], exclude_if=lambda value: not value)
    supersession_details: list[SupersessionDetail] = Field(default=[], exclude_if=lambda v: not v)
    human_gate: DecisionHumanGateRead | None = Field(default=None, exclude_if=lambda v: v is None)


class DecisionTextRead(DecisionRead):
    content: Annotated[str, Field(max_length=MAX_DECISION_BYTES)]
    total_lines: Annotated[StrictInt, Field(ge=0)]
    line_start: Annotated[StrictInt, Field(ge=1)]
    line_end: Annotated[StrictInt, Field(ge=0)]

    @field_validator("content")
    @classmethod
    def valid_utf8_text(cls, value: str) -> str:
        return DecisionPutRequest.utf8_text(value)


class DecisionPutResult(KnowledgeModel):
    decision: DecisionRead
    created: StrictBool


class DecisionListQuery(KnowledgePagination):
    domain_id: UUID | None = None
    state: DecisionState | None = None
    q: Annotated[str, Field(max_length=1000)] = ""


class DecisionTextQuery(KnowledgeModel):
    revision: Annotated[int | None, Field(ge=0)] = None
    line_start: Annotated[int | None, Field(ge=1)] = None
    line_end: Annotated[int | None, Field(ge=1)] = None

    @model_validator(mode="after")
    def ordered_lines(self) -> Self:
        if (self.line_start is not None and self.line_end is not None
                and self.line_end < self.line_start):
            raise ValueError("line_end must be greater than or equal to line_start")
        return self


class DecisionScopeRequest(KnowledgeModel):
    revision: RevisionNumber
    commit_hash: Annotated[str, StringConstraints(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$")]
    work_item_id: UUID | None = None
    agent_session_id: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    repository: RepositoryIdentity | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("commit_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return value.lower()


class DecisionScopeAppend(DecisionScopeRequest):
    client_operation_id: UUID


class DecisionScopeRead(KnowledgeModel):
    id: UUID
    project_id: UUID
    document_id: UUID
    decision_id: DecisionIdentifier
    revision: RevisionNumber
    commit_hash: Annotated[str, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]
    work_item_id: UUID | None
    agent_session_id: str | None
    created_at: datetime
    repository: RepositoryIdentity | None = Field(default=None, exclude_if=lambda v: v is None)
    completion_report_id: UUID | None = Field(default=None, exclude_if=lambda v: v is None)
    merged_commit_hashes: list[CommitHash] = Field(default=[], exclude_if=lambda v: not v)
    completion_citations: list[ScopeCompletionCitation] = Field(
        default=[], max_length=50, exclude_if=lambda v: not v
    )
    completion_citations_truncated: StrictBool = Field(default=False, exclude_if=lambda v: not v)


class DecisionScopeResult(KnowledgeModel):
    scope: DecisionScopeRead
    created: StrictBool
