"""Explicit declarations of decision use; reading a document does not imply use."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)


def repository_identity(value: str) -> str:
    value.encode("utf-8")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("Repository identity cannot contain whitespace or control characters")
    if "?" in value or "#" in value or ("://" in value and "@" in value.split("://", 1)[1]):
        raise ValueError("Use a repository identity without credentials, query, or fragment")
    return value


def nonblank(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("An explanation must be nonblank text")
    value.encode("utf-8")
    return value


RepositoryIdentity = Annotated[
    str, StringConstraints(min_length=1, max_length=500), AfterValidator(repository_identity)
]
CommitHash = Annotated[
    str, StringConstraints(pattern=r"^(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})$"),
    AfterValidator(str.lower),
]


class ProvenanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class ScopeCompletionCitation(ProvenanceModel):
    report_id: UUID
    work_item_id: UUID
    actor_client: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    actor_session_id: Annotated[str | None, Field(min_length=1, max_length=200)]
    actor_model: Annotated[str | None, Field(min_length=1, max_length=120)]
    created_at: datetime


class DecisionCitation(ProvenanceModel):
    decision_id: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
    revision: Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]


class ImplementationCommit(ProvenanceModel):
    repository: RepositoryIdentity
    commit_hash: CommitHash


class DecisionProvenance(ProvenanceModel):
    disposition: Literal["cited", "no-applicable-decisions", "not-code"]
    reason: Annotated[str | None, Field(max_length=2000)] = None
    citations: Annotated[list[DecisionCitation], Field(max_length=100)] = []
    commits: Annotated[list[ImplementationCommit], Field(max_length=100)] = []

    @model_validator(mode="after")
    def coherent_declaration(self) -> Self:
        if self.reason is not None:
            nonblank(self.reason)
        if self.disposition == "cited":
            if not self.citations or not self.commits:
                raise ValueError("Cited decisions require exact revisions and implementation commits")
        elif not self.reason or self.citations:
            raise ValueError("An uncited disposition requires a reason and no citations")
        if self.disposition == "not-code" and self.commits:
            raise ValueError("Not-code cannot declare implementation commits")
        citations = {(item.decision_id, item.revision) for item in self.citations}
        commits = {(item.repository, item.commit_hash) for item in self.commits}
        if len(citations) != len(self.citations) or len(commits) != len(self.commits):
            raise ValueError("Citations and commits must be unique")
        if len(citations) * len(commits) > 500:
            raise ValueError("A declaration may create at most 500 commit citations")
        return self
