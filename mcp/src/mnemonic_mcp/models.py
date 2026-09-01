"""Typed REST wire models; the API remains the validation authority."""

import json
from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    model_serializer,
    model_validator,
)

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
EventOrder = Literal["oldest", "newest"]
EventOrigin = Literal["live", "backfill"]
ActorKind = Literal["client", "unattributed"]
EventType = Literal[
    "work_created",
    "work_updated",
    "work_status_changed",
    "work_reopened",
    "work_claimed",
    "work_released",
    "checkpoint_added",
    "progress",
    "dependency_added",
    "dependency_removed",
    "relationship_added",
    "relationship_removed",
    "work_completed",
    "work_deleted",
]

_SERVER_RESERVED_METADATA_KEYS = frozenset(
    {
        "lease_token",
        "claim_request_id",
        "api_key",
        "authorization",
        "cookie",
        "secret",
    }
)


def _bounded_progress_metadata(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Validate the intentionally open progress object without rewriting its content."""

    def check(item: JsonValue) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if "\x00" in key:
                    raise ValueError("Progress metadata cannot contain NUL characters.")
                if key.lower() in _SERVER_RESERVED_METADATA_KEYS:
                    raise ValueError("Progress metadata contains a reserved key.")
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif isinstance(item, str) and "\x00" in item:
            raise ValueError("Progress metadata cannot contain NUL characters.")

    check(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError) as error:
        raise ValueError("Progress metadata must contain finite valid JSON.") from error
    if len(encoded) > 16384:
        raise ValueError("Progress metadata must be at most 16 KiB.")
    return value


ProgressMetadataInput = Annotated[
    dict[str, JsonValue],
    AfterValidator(_bounded_progress_metadata),
]


def _validated_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Event timestamps must use UTC.")
    return value


UTCDateTime = Annotated[datetime, AfterValidator(_validated_utc_datetime)]


def _validated_event_text(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("Event text must use valid Unicode.") from error
    if not value.strip() or b"\x00" in encoded:
        raise ValueError("Event text must be nonblank and contain no NUL bytes.")
    return value


TitleEventText = Annotated[
    str,
    Field(min_length=1, max_length=200),
    AfterValidator(_validated_event_text),
]
SummaryEventText = Annotated[
    str,
    Field(min_length=1, max_length=1000),
    AfterValidator(_validated_event_text),
]
RetainedClientName = Annotated[
    str,
    Field(min_length=1, max_length=80),
    AfterValidator(_validated_event_text),
]
RetainedSessionID = Annotated[
    str,
    Field(min_length=1, max_length=200),
    AfterValidator(_validated_event_text),
]


class CanonicalResponse(BaseModel):
    """Canonical responses reject additions except on deliberate search pointers."""

    model_config = ConfigDict(extra="forbid")


class EmptyEventMetadata(CanonicalResponse):
    pass


class WorkSnapshot(CanonicalResponse):
    title: TitleEventText
    summary: SummaryEventText
    status: UpdateStatus
    priority: int = Field(ge=0, le=100)
    version: Literal[1]


class WorkCreatedLiveMetadata(CanonicalResponse):
    initial: WorkSnapshot


class TitleChange(CanonicalResponse):
    before: TitleEventText
    after: TitleEventText


class SummaryChange(CanonicalResponse):
    before: SummaryEventText
    after: SummaryEventText


class PriorityChange(CanonicalResponse):
    before: int = Field(ge=0, le=100)
    after: int = Field(ge=0, le=100)


class StatusChange(CanonicalResponse):
    before: Status
    after: Status


class WorkChangeSet(CanonicalResponse):
    title: TitleChange | None = None
    summary: SummaryChange | None = None
    priority: PriorityChange | None = None
    status: StatusChange | None = None

    @model_validator(mode="after")
    def require_nonempty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Event changes must not be empty.")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Event changes cannot contain null values.")
        return self

    @model_serializer(mode="wrap")
    def serialize_set_fields(self, handler):
        serialized = handler(self)
        return {field: serialized[field] for field in self.model_fields_set}


class WorkUpdatedMetadata(CanonicalResponse):
    changes: WorkChangeSet
    work_version: int = Field(ge=1)


class WorkStatusMetadata(CanonicalResponse):
    from_status: Status
    to_status: Status
    changes: WorkChangeSet
    work_version: int = Field(ge=1)


class WorkClaimedLiveMetadata(CanonicalResponse):
    expires_at: UTCDateTime


class WorkClaimedBackfillMetadata(CanonicalResponse):
    observed_expires_at: UTCDateTime
    expiry_basis: Literal["retained_lease_at_cutover"]


class WorkReleasedClientMetadata(CanonicalResponse):
    lease_holder_kind: Literal["client"]
    lease_holder_client: RetainedClientName
    lease_holder_session_id: RetainedSessionID


class WorkReleasedUnattributedMetadata(CanonicalResponse):
    lease_holder_kind: Literal["unattributed"]


class CheckpointAddedMetadata(CanonicalResponse):
    checkpoint_kind: Literal["context", "progress"]


class RelationshipEventMetadata(CanonicalResponse):
    relationship_type: RelationshipType


class WorkCompletedLiveMetadata(CanonicalResponse):
    from_status: Literal["open"]
    to_status: Literal["done"]
    work_version: int = Field(ge=1)


class WorkDeletedMetadata(CanonicalResponse):
    final_status: Status
    final_version: int = Field(ge=1)


class ProgressEventMetadata(RootModel[ProgressMetadataInput]):
    pass


WorkEventMetadata = (
    EmptyEventMetadata
    | WorkCreatedLiveMetadata
    | WorkUpdatedMetadata
    | WorkStatusMetadata
    | WorkClaimedLiveMetadata
    | WorkClaimedBackfillMetadata
    | WorkReleasedClientMetadata
    | WorkReleasedUnattributedMetadata
    | CheckpointAddedMetadata
    | RelationshipEventMetadata
    | WorkCompletedLiveMetadata
    | WorkDeletedMetadata
    | ProgressEventMetadata
)


def _metadata_payload(metadata: WorkEventMetadata) -> dict[str, JsonValue]:
    if isinstance(metadata, ProgressEventMetadata):
        return metadata.root
    return metadata.model_dump(mode="json")


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


class ReadyWorkPage(CanonicalResponse):
    """Strict pointer-only ready envelope, deliberately separate from search."""

    items: list[WorkSummaryMinimal]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_ready_page_contract(self) -> Self:
        if len(self.items) > self.limit:
            raise ValueError("Ready-work pages cannot exceed their declared limit.")
        if self.items and (
            self.offset >= self.total or self.offset + len(self.items) > self.total
        ):
            raise ValueError("Ready-work page items must fit within the declared total.")
        if any(
            item.work_item.status != "open" or item.display_state != "ready"
            for item in self.items
        ):
            raise ValueError("Ready-work pages may contain only open ready items.")
        return self


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


class WorkEventRead(CanonicalResponse):
    """Strict event wire model with event-type/origin-specific metadata validation."""

    id: int = Field(ge=1)
    project_id: UUID
    work_item_id: UUID
    event_type: EventType
    actor_kind: ActorKind
    actor_client: str | None = Field(default=None, max_length=80)
    actor_session_id: str | None = Field(default=None, max_length=200)
    actor_model: str | None = Field(default=None, max_length=120)
    body: str | None
    checkpoint_id: UUID | None
    lease_generation_id: UUID | None
    lease_release_id: UUID | None
    relationship_id: UUID | None
    relationship_source_work_item_id: UUID | None
    relationship_target_work_item_id: UUID | None
    relationship_context_checkpoint_work_item_id: UUID | None
    relationship_context_checkpoint_id: UUID | None
    relationship_direction: RelationshipDirection | None
    counterpart_work_item_id: UUID | None
    metadata_version: Literal[1]
    metadata: WorkEventMetadata
    origin: EventOrigin
    created_at: UTCDateTime

    @model_validator(mode="after")
    def enforce_event_contract(self) -> Self:
        actor_values = (self.actor_client, self.actor_session_id, self.actor_model)
        if self.actor_kind == "client":
            if self.actor_client is None or self.actor_session_id is None:
                raise ValueError("Client events require client and session provenance.")
            if not self.actor_client.strip() or not self.actor_session_id.strip():
                raise ValueError("Client event provenance must be nonblank.")
            if self.actor_model is not None and not self.actor_model.strip():
                raise ValueError("Client event model provenance must be nonblank.")
        elif any(value is not None for value in actor_values):
            raise ValueError("Unattributed events cannot contain actor provenance.")

        live_client_events = {
            "work_created",
            "work_claimed",
            "checkpoint_added",
            "progress",
            "dependency_added",
            "relationship_added",
            "work_completed",
        }
        if (
            self.origin == "live"
            and self.event_type in live_client_events
            and self.actor_kind != "client"
        ):
            raise ValueError("This live event requires client provenance.")

        backfill_allowed = {
            "work_created",
            "work_claimed",
            "checkpoint_added",
            "dependency_added",
            "relationship_added",
            "work_completed",
            "work_deleted",
        }
        if self.origin == "backfill" and self.event_type not in backfill_allowed:
            raise ValueError("This event type cannot be backfilled.")
        if (
            self.origin == "backfill"
            and self.event_type == "work_deleted"
            and self.actor_kind != "unattributed"
        ):
            raise ValueError("Backfilled deletion events must be unattributed.")

        if self.event_type == "progress":
            if self.origin != "live" or self.body is None:
                raise ValueError("Progress events require a live body.")
            if not 1 <= len(self.body) <= 4000 or not self.body.strip() or "\x00" in self.body:
                raise ValueError("Progress event bodies must be bounded nonblank text.")
        elif self.body is not None:
            raise ValueError("Server-reserved event types cannot contain a body.")

        checkpoint_events = {"work_created", "checkpoint_added", "work_completed"}
        if (self.checkpoint_id is not None) != (self.event_type in checkpoint_events):
            raise ValueError("Checkpoint event references do not match the event type.")

        lease_events = {"work_claimed", "work_released"}
        if (self.lease_generation_id is not None) != (self.event_type in lease_events):
            raise ValueError("Lease generation references do not match the event type.")
        if (self.lease_release_id is not None) != (self.event_type == "work_released"):
            raise ValueError("Lease release references do not match the event type.")

        relationship_events = {
            "dependency_added",
            "dependency_removed",
            "relationship_added",
            "relationship_removed",
        }
        relationship_values = (
            self.relationship_id,
            self.relationship_source_work_item_id,
            self.relationship_target_work_item_id,
            self.relationship_direction,
            self.counterpart_work_item_id,
        )
        if self.event_type in relationship_events:
            if any(value is None for value in relationship_values):
                raise ValueError("Relationship events require the complete endpoint projection.")
            context_pair = (
                self.relationship_context_checkpoint_work_item_id,
                self.relationship_context_checkpoint_id,
            )
            if (context_pair[0] is None) != (context_pair[1] is None):
                raise ValueError("Relationship context references must be paired.")
            if (
                context_pair[0] is not None
                and context_pair[0]
                not in {
                    self.relationship_source_work_item_id,
                    self.relationship_target_work_item_id,
                }
            ):
                raise ValueError("Relationship context must belong to an endpoint.")
        elif any(
            value is not None
            for value in (
                *relationship_values,
                self.relationship_context_checkpoint_work_item_id,
                self.relationship_context_checkpoint_id,
            )
        ):
            raise ValueError("Non-relationship events cannot contain relationship references.")

        payload = _metadata_payload(self.metadata)
        parsed: WorkEventMetadata
        if self.event_type == "work_created":
            metadata_type = (
                WorkCreatedLiveMetadata if self.origin == "live" else EmptyEventMetadata
            )
            parsed = metadata_type.model_validate(payload)
        elif self.event_type == "work_updated":
            parsed = WorkUpdatedMetadata.model_validate(payload)
            status_change = parsed.changes.status
            if status_change is not None and status_change.before != status_change.after:
                raise ValueError("Ordinary work updates cannot change lifecycle status.")
        elif self.event_type in {"work_status_changed", "work_reopened"}:
            parsed = WorkStatusMetadata.model_validate(payload)
            status_change = parsed.changes.status
            if status_change is None:
                raise ValueError("Lifecycle events require a typed status change.")
            if (status_change.before, status_change.after) != (
                parsed.from_status,
                parsed.to_status,
            ):
                raise ValueError("Lifecycle event status metadata is inconsistent.")
            if self.event_type == "work_status_changed" and (
                parsed.from_status != "open"
                or parsed.to_status not in {"wont-do", "promoted"}
            ):
                raise ValueError("Status-change events must leave open work.")
            if self.event_type == "work_reopened" and (
                parsed.to_status != "open" or parsed.from_status == "open"
            ):
                raise ValueError("Reopen events must return terminal work to open.")
        elif self.event_type == "work_claimed":
            metadata_type = (
                WorkClaimedLiveMetadata
                if self.origin == "live"
                else WorkClaimedBackfillMetadata
            )
            parsed = metadata_type.model_validate(payload)
        elif self.event_type == "work_released":
            holder_kind = payload.get("lease_holder_kind")
            metadata_type = (
                WorkReleasedClientMetadata
                if holder_kind == "client"
                else WorkReleasedUnattributedMetadata
            )
            parsed = metadata_type.model_validate(payload)
        elif self.event_type == "checkpoint_added":
            parsed = CheckpointAddedMetadata.model_validate(payload)
        elif self.event_type == "progress":
            parsed = ProgressEventMetadata.model_validate(payload)
        elif self.event_type in relationship_events:
            parsed = RelationshipEventMetadata.model_validate(payload)
            is_dependency = self.event_type.startswith("dependency_")
            if is_dependency != (parsed.relationship_type == "blocks"):
                raise ValueError("Relationship event family does not match its type.")
            source_id = self.relationship_source_work_item_id
            target_id = self.relationship_target_work_item_id
            if source_id == target_id:
                raise ValueError("Relationship event endpoints must differ.")
            if parsed.relationship_type == "related" and source_id > target_id:
                raise ValueError("Related relationship endpoints must be normalized.")
            if parsed.relationship_type == "discovered-from" and (
                self.relationship_context_checkpoint_work_item_id != target_id
            ):
                raise ValueError("Discovered-from context must belong to the target.")
            if self.work_item_id == source_id:
                expected_direction: RelationshipDirection = (
                    "undirected"
                    if parsed.relationship_type == "related"
                    else "outgoing"
                )
                expected_counterpart = target_id
            elif self.work_item_id == target_id:
                expected_direction = (
                    "undirected"
                    if parsed.relationship_type == "related"
                    else "incoming"
                )
                expected_counterpart = source_id
            else:
                raise ValueError("Relationship event work item must be an endpoint.")
            if self.relationship_direction != expected_direction:
                raise ValueError("Relationship direction projection is inconsistent.")
            if self.counterpart_work_item_id != expected_counterpart:
                raise ValueError("Relationship counterpart projection is inconsistent.")
        elif self.event_type == "work_completed":
            metadata_type = (
                WorkCompletedLiveMetadata if self.origin == "live" else EmptyEventMetadata
            )
            parsed = metadata_type.model_validate(payload)
        elif self.event_type == "work_deleted":
            parsed = WorkDeletedMetadata.model_validate(payload)
        else:  # pragma: no cover - EventType keeps this exhaustive
            raise ValueError("Unknown event type.")

        self.metadata = parsed
        return self


class WorkEventPage(CanonicalResponse):
    items: list[WorkEventRead]
    total: int
    limit: int
    offset: int
    pre_phase5_history_may_be_incomplete: bool


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
    recent_events: list[WorkEventRead]
    event_total: int
    omitted_event_count: int
    pre_phase5_history_may_be_incomplete: bool
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
