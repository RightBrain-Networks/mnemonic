"""Typed REST wire models; the API remains the validation authority."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Status = Literal["open", "done", "wont-do", "promoted"]
UpdateStatus = Literal["open", "wont-do", "promoted"]
SearchStatus = Literal["open", "done", "wont-do", "promoted", "all"]
CheckpointKind = Literal["context", "progress", "completion"]
AppendCheckpointKind = Literal["context", "progress"]
CheckpointOrder = Literal["oldest", "newest"]
MigrationOrigin = Literal["legacy-handoff-snapshot", "legacy-comment"]
DisplayState = Literal["ready", "active", "blocked", "done", "wont-do", "promoted"]
CommentKind = Literal["comment", "work-summary"]


class CanonicalResponse(BaseModel):
    """Canonical responses reject additions except on deliberate search pointers."""

    model_config = ConfigDict(extra="forbid")


class Project(BaseModel):
    id: UUID
    name: str
    slug: str
    description: str
    repository_url: str | None
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[Project]
    total: int
    limit: int
    offset: int


class CheckpointInput(BaseModel):
    """A new immutable context packet supplied to a canonical work operation."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=100000)
    source_client: str = Field(min_length=1, max_length=80)
    source_session_id: str = Field(min_length=1, max_length=200)
    source_model: str | None = Field(default=None, max_length=120)
    source_session_url: str | None = Field(default=None, max_length=2000)
    repository_branch: str | None = Field(default=None, max_length=200)
    verified_against: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{7,64}$")
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CheckpointRead(CanonicalResponse):
    id: UUID
    work_item_id: UUID
    kind: CheckpointKind
    prompt: str
    source_client: str
    source_session_id: str
    source_model: str | None
    source_session_url: str | None
    repository_branch: str | None
    verified_against: str | None
    tags: list[str]
    source_metadata: dict[str, JsonValue]
    migration_origin: MigrationOrigin | None
    legacy_record_id: UUID | None
    created_at: datetime


class CheckpointPointer(CanonicalResponse):
    # Pointer models intentionally ignore accidental upstream body/metadata additions.
    model_config = ConfigDict(extra="ignore")

    id: UUID
    work_item_id: UUID
    kind: CheckpointKind
    source_client: str
    source_session_id: str
    source_model: str | None
    repository_branch: str | None
    verified_against: str | None
    tags: list[str]
    migration_origin: MigrationOrigin | None
    legacy_record_id: UUID | None
    created_at: datetime


class LeasePublic(CanonicalResponse):
    holder_client: str
    holder_session_id: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


class Readiness(CanonicalResponse):
    lifecycle_status: Status
    is_terminal: bool
    has_active_lease: bool
    active_lease: LeasePublic | None
    unresolved_blocker_count: int
    is_blocked: bool
    is_ready: bool
    display_state: DisplayState


class WorkItemRead(CanonicalResponse):
    id: UUID
    project_id: UUID
    title: str
    summary: str
    status: Status
    priority: int
    initial_checkpoint_id: UUID
    version: int
    created_at: datetime
    updated_at: datetime


class WorkIdentityPointer(CanonicalResponse):
    id: UUID
    title: str
    status: Status


class WorkSummary(CanonicalResponse):
    # Search must stay pointer-only even if an API regression adds checkpoint bodies.
    model_config = ConfigDict(extra="ignore")

    work_item: WorkItemRead
    checkpoint_count: int
    ancestor_path: list[WorkIdentityPointer] = Field(default_factory=list)
    ancestor_path_truncated: bool = False
    current_context: CheckpointPointer
    readiness: Readiness


class WorkPage(CanonicalResponse):
    items: list[WorkSummary]
    total: int
    limit: int
    offset: int


class CheckpointPage(CanonicalResponse):
    items: list[CheckpointRead]
    total: int
    limit: int
    offset: int


class RelationshipCounts(CanonicalResponse):
    incoming: int = 0
    outgoing: int = 0
    undirected: int = 0
    total: int = 0


class WorkContext(CanonicalResponse):
    work_item: WorkItemRead
    initial_checkpoint: CheckpointRead
    current_context: CheckpointRead
    recent_checkpoints: list[CheckpointRead]
    checkpoint_total: int
    omitted_checkpoint_count: int
    readiness: Readiness
    # Phase 1 returns an empty immediate graph. Keep the response bounded and
    # forward-compatible without exposing Phase 3 mutation tools yet.
    incoming_relationships: list[dict[str, JsonValue]] = Field(default_factory=list)
    outgoing_relationships: list[dict[str, JsonValue]] = Field(default_factory=list)
    undirected_relationships: list[dict[str, JsonValue]] = Field(default_factory=list)
    relationship_counts: RelationshipCounts = Field(default_factory=RelationshipCounts)


class ClaimReceipt(CanonicalResponse):
    """Capability-bearing lease receipt returned only by claim and renew operations."""

    work_item_id: UUID
    holder_client: str
    holder_session_id: str
    claim_request_id: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime
    lease_token: str = Field(repr=False)


class ClaimAndRecall(CanonicalResponse):
    lease: ClaimReceipt
    context: WorkContext


class ReleaseResult(CanonicalResponse):
    work_item_id: UUID
    released: bool


class WorkCreation(CanonicalResponse):
    work_item: WorkItemRead
    initial_checkpoint: CheckpointRead
    initial_relationships: list[dict[str, JsonValue]] = Field(default_factory=list)


class WorkChanges(BaseModel):
    """Only supplied mutable work-item fields change."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    priority: int | None = Field(default=None, ge=0, le=100)
    status: UpdateStatus | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "WorkChanges":
        if not self.model_fields_set:
            raise ValueError("Supply at least one editable field in changes.")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class WorkCompletion(CanonicalResponse):
    work_item: WorkItemRead
    checkpoint: CheckpointRead


class WorkDeletionResult(CanonicalResponse):
    deleted: bool = True
    project_id: UUID
    work_item_id: UUID
    version: int


# Deprecated hand-off projections remain during the compatibility window.
class HandoffSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    project_id: UUID
    title: str
    summary: str
    source_client: str
    source_session_id: str
    source_model: str | None
    source_session_url: str | None
    repository_branch: str | None
    verified_against: str | None
    tags: list[str]
    status: Status
    created_at: datetime
    updated_at: datetime
    version: int


class Handoff(HandoffSummary):
    prompt: str
    source_metadata: dict[str, JsonValue]


class HandoffPage(BaseModel):
    items: list[HandoffSummary]
    total: int
    limit: int
    offset: int


class HandoffComment(BaseModel):
    id: UUID
    handoff_id: UUID
    body: str
    kind: CommentKind
    source_client: str
    source_session_id: str
    source_model: str | None
    created_at: datetime


class HandoffCommentPage(BaseModel):
    items: list[HandoffComment]
    total: int
    limit: int
    offset: int


class HandoffCompletion(BaseModel):
    handoff: Handoff
    comment: HandoffComment


class HandoffChanges(BaseModel):
    """Deprecated flat updates may change work fields, never checkpoint fields."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    status: UpdateStatus | None = None

    @model_validator(mode="after")
    def require_changes(self) -> "HandoffChanges":
        if not self.model_fields_set:
            raise ValueError("Supply at least one editable field in changes.")
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self


class HandoffDeletionResult(BaseModel):
    deleted: bool = True
    project_id: UUID
    handoff_id: UUID
