"""Typed REST wire models; the API remains the validation authority."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Status = Literal["open", "done", "wont-do", "promoted"]
UpdateStatus = Literal["open", "wont-do", "promoted"]
SearchStatus = Literal["open", "done", "wont-do", "promoted", "all"]
SearchView = Literal["minimal", "full"]
CheckpointKind = Literal["context", "progress", "completion"]
AppendCheckpointKind = Literal["context", "progress"]
CheckpointOrder = Literal["oldest", "newest"]
MigrationOrigin = Literal["legacy-handoff-snapshot", "legacy-comment"]
DisplayState = Literal["ready", "active", "blocked", "done", "wont-do", "promoted"]
RelationshipType = Literal[
    "blocks",
    "parent-child",
    "discovered-from",
    "duplicate-of",
    "related",
]
RelationshipDirection = Literal["incoming", "outgoing", "undirected"]
RelationshipListDirection = Literal["incoming", "outgoing", "undirected", "both"]
InitialRelationshipDirection = Literal["incoming", "outgoing"]


class CanonicalResponse(BaseModel):
    """Canonical responses reject additions except on deliberate search pointers."""

    model_config = ConfigDict(extra="forbid")


class Project(CanonicalResponse):
    id: UUID
    name: str
    slug: str
    description: str
    repository_url: str | None
    created_at: datetime
    updated_at: datetime


class ProjectPage(CanonicalResponse):
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


class WorkPointer(CanonicalResponse):
    """Compact relationship counterpart; ignore accidental upstream content additions."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    title: str
    status: Status
    readiness: Readiness


class RelationshipEdgeRead(CanonicalResponse):
    id: UUID
    project_id: UUID
    relationship_type: RelationshipType
    source_work_item_id: UUID
    target_work_item_id: UUID
    context_checkpoint_work_item_id: UUID | None
    context_checkpoint_id: UUID | None
    created_by_client: str
    created_by_session_id: str
    created_by_model: str | None
    created_at: datetime


class AdjacentRelationshipRead(CanonicalResponse):
    relationship: RelationshipEdgeRead
    relative_to_work_item_id: UUID
    direction: RelationshipDirection
    counterpart: WorkPointer


class RelationshipPage(CanonicalResponse):
    items: list[AdjacentRelationshipRead]
    total: int
    limit: int
    offset: int


class RelationshipCreationResult(CanonicalResponse):
    relationship: RelationshipEdgeRead
    created: bool


class RelationshipRemovalResult(CanonicalResponse):
    project_id: UUID
    relationship_id: UUID
    removed: bool


class InitialRelationshipInput(BaseModel):
    """A relationship expressed relative to a work item being created."""

    model_config = ConfigDict(extra="forbid")

    type: RelationshipType
    direction: InitialRelationshipDirection
    other_work_item_id: UUID
    context_checkpoint_id: UUID | None = None


class WorkSummary(CanonicalResponse):
    # Search must stay pointer-only even if an API regression adds checkpoint bodies.
    model_config = ConfigDict(extra="ignore")

    work_item: WorkItemRead
    checkpoint_count: int
    ancestor_path: list[WorkIdentityPointer] = Field(default_factory=list)
    ancestor_path_truncated: bool = False
    current_context: CheckpointPointer
    readiness: Readiness


class WorkItemPointer(CanonicalResponse):
    id: UUID
    title: str
    status: Status
    priority: int
    version: int
    updated_at: datetime


class WorkSummaryMinimal(CanonicalResponse):
    # Strict: a full WorkSummary must never validate as the minimal shape.
    model_config = ConfigDict(extra="forbid")

    work_item: WorkItemPointer
    checkpoint_count: int
    display_state: DisplayState


class WorkPage(CanonicalResponse):
    # view="minimal" yields WorkSummaryMinimal items; view="full" yields WorkSummary.
    items: list[WorkSummary | WorkSummaryMinimal]
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
    # Null when the newest context checkpoint is the initial one; read
    # initial_checkpoint instead. The body is never serialized twice.
    current_context: CheckpointRead | None
    current_context_is_initial: bool
    # Never repeats initial_checkpoint or current_context.
    recent_checkpoints: list[CheckpointRead]
    # Every checkpoint on the work item; omitted counts what is not in this payload.
    checkpoint_total: int
    omitted_checkpoint_count: int
    readiness: Readiness
    incoming_relationships: list[AdjacentRelationshipRead] = Field(default_factory=list)
    outgoing_relationships: list[AdjacentRelationshipRead] = Field(default_factory=list)
    undirected_relationships: list[AdjacentRelationshipRead] = Field(default_factory=list)
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
    initial_relationships: list[RelationshipEdgeRead] = Field(default_factory=list)


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
