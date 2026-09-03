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
    SecretStr,
    StrictBool,
    StrictInt,
    model_serializer,
    model_validator,
)

Status = Literal["pending", "deferred", "done", "wont-do", "promoted"]
EventStatus = Literal["open", "pending", "deferred", "done", "wont-do", "promoted"]
EventCreateStatus = Literal["open", "pending", "deferred", "wont-do", "promoted"]
UpdateStatus = Literal["pending", "wont-do", "promoted"]
SearchStatus = Literal[
    "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
]
SearchView = Literal["full", "roots"]
DuplicateScope = Literal["canonical", "aliases", "all"]
CheckpointKind = Literal["context", "progress", "completion"]
AppendCheckpointKind = Literal["context", "progress"]
CheckpointOrder = Literal["oldest", "newest"]
MigrationOrigin = Literal["legacy-handoff-snapshot", "legacy-comment"]
DisplayState = Literal[
    "duplicate",
    "pending",
    "active",
    "dropped",
    "blocked",
    "waiting",
    "deferred",
    "done",
    "wont-do",
    "promoted",
]
ReadyDisplayState = Literal[
    "pending",
    "active",
    "dropped",
    "blocked",
    "waiting",
    "deferred",
    "done",
    "wont-do",
    "promoted",
]
HumanGateStatus = Literal["unresolved", "resolved"]
HumanGateHistoryStatus = Literal["all", "unresolved", "resolved"]
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
    "human_attention_requested",
    "human_attention_resolved",
    "work_merged",
]

_HISTORICAL_RESERVED_METADATA_KEYS = frozenset(
    {
        "lease_token",
        "claim_request_id",
        "api_key",
        "authorization",
        "cookie",
        "secret",
    }
)
_REQUEST_RESERVED_METADATA_KEYS = _HISTORICAL_RESERVED_METADATA_KEYS | {
    "client_operation_id",
    "gate_id",
    "gate_type",
}


def _validate_progress_metadata_item(
    item: JsonValue,
    *,
    reserved_keys: frozenset[str],
) -> None:
    if isinstance(item, dict):
        _validate_progress_metadata_object(item, reserved_keys=reserved_keys)
    elif isinstance(item, list):
        for child in item:
            _validate_progress_metadata_item(child, reserved_keys=reserved_keys)
    elif isinstance(item, str) and "\x00" in item:
        raise ValueError("Progress metadata cannot contain NUL characters.")


def _validate_progress_metadata_object(
    value: dict[str, JsonValue],
    *,
    reserved_keys: frozenset[str],
) -> None:
    for key, child in value.items():
        if "\x00" in key:
            raise ValueError("Progress metadata cannot contain NUL characters.")
        if key.lower() in reserved_keys:
            raise ValueError("Progress metadata contains a reserved key.")
        _validate_progress_metadata_item(child, reserved_keys=reserved_keys)


def _bounded_progress_metadata(
    value: dict[str, JsonValue],
    *,
    reserved_keys: frozenset[str],
) -> dict[str, JsonValue]:
    """Validate the intentionally open progress object without rewriting its content."""
    _validate_progress_metadata_object(value, reserved_keys=reserved_keys)
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (RecursionError, UnicodeEncodeError, ValueError) as error:
        raise ValueError("Progress metadata must contain finite valid JSON.") from error
    if len(encoded) > 16384:
        raise ValueError("Progress metadata must be at most 16 KiB.")
    return value


def _bounded_stored_metadata(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Reject every request-time secret/control key, including the Phase 6 UUID."""
    return _bounded_progress_metadata(value, reserved_keys=_REQUEST_RESERVED_METADATA_KEYS)


def _bounded_historical_progress_metadata(
    value: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Preserve the Phase 5 read contract for metadata accepted before Phase 6."""
    return _bounded_progress_metadata(
        value,
        reserved_keys=_HISTORICAL_RESERVED_METADATA_KEYS,
    )


StoredMetadataInput = Annotated[
    dict[str, JsonValue],
    AfterValidator(_bounded_stored_metadata),
]
ProgressMetadataInput = StoredMetadataInput
HistoricalProgressMetadata = Annotated[
    dict[str, JsonValue],
    AfterValidator(_bounded_historical_progress_metadata),
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
RetainedModelName = Annotated[
    str,
    Field(min_length=1, max_length=120),
    AfterValidator(_validated_event_text),
]
HumanGateText = Annotated[
    str,
    Field(min_length=1, max_length=4000),
    AfterValidator(_validated_event_text),
]
OpaqueCursor = Annotated[
    str,
    Field(min_length=1, max_length=4096),
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
    status: EventCreateStatus
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
    before: EventStatus
    after: EventStatus


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
    from_status: EventStatus
    to_status: EventStatus
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


class WorkMergedMetadata(CanonicalResponse):
    merge_id: UUID
    source_work_item_id: UUID
    destination_work_item_id: UUID
    role: Literal["source", "destination"]
    source_work_version: StrictInt = Field(ge=2)
    destination_work_version: StrictInt = Field(ge=2)

    @model_validator(mode="after")
    def enforce_distinct_endpoints(self) -> Self:
        if self.source_work_item_id == self.destination_work_item_id:
            raise ValueError("Merge event endpoints must be distinct.")
        return self


class HumanGateEventMetadata(CanonicalResponse):
    gate_id: UUID
    gate_type: Literal["human"]


class WorkCompletedLiveMetadata(CanonicalResponse):
    from_status: Literal["open", "pending"]
    to_status: Literal["done"]
    work_version: int = Field(ge=1)


class WorkDeletedMetadata(CanonicalResponse):
    final_status: EventStatus
    final_version: int = Field(ge=1)


class ProgressEventMetadata(RootModel[HistoricalProgressMetadata]):
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
    | WorkMergedMetadata
    | HumanGateEventMetadata
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
    source_metadata: StoredMetadataInput = Field(default_factory=dict)


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
    is_duplicate: StrictBool
    canonical_work_item_id: UUID
    is_terminal: StrictBool
    has_active_lease: StrictBool
    has_dropped_lease: StrictBool
    active_lease: LeasePublic | None
    unresolved_blocker_count: StrictInt = Field(ge=0)
    is_blocked: StrictBool
    unresolved_gate_count: StrictInt = Field(ge=0)
    is_gated: StrictBool
    is_ready: StrictBool
    display_state: DisplayState

    @model_validator(mode="after")
    def enforce_readiness_contract(self) -> Self:
        _validate_readiness_facts(self)
        _validate_ready_state(self)
        if self.display_state != _expected_display_state(self):
            raise ValueError("Display state must follow the canonical precedence.")
        return self


def _validate_readiness_facts(readiness: Readiness) -> None:
    if readiness.is_terminal != (
        readiness.lifecycle_status in {"done", "wont-do", "promoted"}
    ):
        raise ValueError("Terminal readiness must match lifecycle status.")
    if readiness.has_active_lease != (readiness.active_lease is not None):
        raise ValueError("Active lease readiness must match its public projection.")
    if readiness.has_active_lease and readiness.has_dropped_lease:
        raise ValueError("A retained lease cannot be active and dropped simultaneously.")
    if readiness.is_blocked != (readiness.unresolved_blocker_count > 0):
        raise ValueError("Blocked readiness must match its unresolved count.")
    if readiness.is_gated != (readiness.unresolved_gate_count > 0):
        raise ValueError("Gated readiness must match its unresolved count.")


def _validate_ready_state(readiness: Readiness) -> None:
    expected_ready = (
        readiness.lifecycle_status == "pending"
        and not readiness.is_duplicate
        and not readiness.has_active_lease
        and readiness.unresolved_blocker_count == 0
        and readiness.unresolved_gate_count == 0
    )
    if readiness.is_ready != expected_ready:
        raise ValueError("Ready state must match lifecycle, lease, blocker, and gate facts.")


def _expected_display_state(readiness: Readiness) -> DisplayState:
    if readiness.is_duplicate:
        return "duplicate"
    if readiness.lifecycle_status != "pending":
        return readiness.lifecycle_status
    if readiness.is_gated:
        return "waiting"
    if readiness.is_blocked:
        return "blocked"
    if readiness.has_active_lease:
        return "active"
    if readiness.has_dropped_lease:
        return "dropped"
    return "pending"


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


class CanonicalWorkProjection(CanonicalResponse):
    is_duplicate: StrictBool
    direct_destination: WorkIdentityPointer | None
    canonical_work_item: WorkIdentityPointer
    path: list[WorkIdentityPointer] = Field(max_length=50)
    duplicate_member_count: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def enforce_projection_contract(self) -> Self:
        path_ids = [item.id for item in self.path]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError("A canonical path cannot repeat a work item.")
        if self.is_duplicate:
            if (
                self.direct_destination is None
                or not self.path
                or self.path[0] != self.direct_destination
                or self.path[-1] != self.canonical_work_item
                or self.duplicate_member_count < len(self.path)
            ):
                raise ValueError("Duplicate projections require a complete path to a root.")
        elif self.direct_destination is not None or self.path:
            raise ValueError("Canonical roots cannot contain a destination path.")
        return self


class WorkItemDetailRead(CanonicalResponse):
    work_item: WorkItemRead
    canonical: CanonicalWorkProjection

    @model_validator(mode="after")
    def enforce_detail_contract(self) -> Self:
        requested = self.work_item
        projection = self.canonical
        if projection.is_duplicate:
            if requested.id in {item.id for item in projection.path}:
                raise ValueError("A duplicate path cannot contain its requested source.")
        elif (
            projection.canonical_work_item.id != requested.id
            or projection.canonical_work_item.title != requested.title
            or projection.canonical_work_item.status != requested.status
        ):
            raise ValueError("A canonical root projection must identify the requested work item.")
        return self


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
    checkpoint_count: StrictInt = Field(ge=1)
    ancestor_path: list[WorkIdentityPointer] = Field(default_factory=list)
    ancestor_path_truncated: StrictBool = False
    current_context: CheckpointPointer
    readiness: Readiness

    @model_validator(mode="after")
    def enforce_summary_contract(self) -> Self:
        work_item = self.work_item
        ancestor_ids = [ancestor.id for ancestor in self.ancestor_path]
        if (
            self.current_context.work_item_id != work_item.id
            or self.readiness.lifecycle_status != work_item.status
            or (self.readiness.canonical_work_item_id == work_item.id)
            == self.readiness.is_duplicate
            or work_item.id in ancestor_ids
            or len(ancestor_ids) != len(set(ancestor_ids))
        ):
            raise ValueError("Work summary identity and readiness are incoherent.")
        return self


class HumanGateContextRevision(CanonicalResponse):
    work_version: StrictInt = Field(ge=1)
    context_checkpoint_id: UUID
    relationship_event_count: StrictInt = Field(ge=0)


class MergeReviewRevision(CanonicalResponse):
    work_version: StrictInt = Field(ge=1)
    context_checkpoint_id: UUID
    work_event_count: StrictInt = Field(ge=1)


class HumanGateRead(CanonicalResponse):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    gate_type: Literal["human"]
    question: HumanGateText
    requested_by_client: RetainedClientName
    requested_by_session_id: RetainedSessionID
    requested_by_model: RetainedModelName | None
    requested_context_revision: HumanGateContextRevision
    created_at: UTCDateTime
    status: HumanGateStatus
    current_context_revision: HumanGateContextRevision
    work_changed_since_request: StrictBool
    context_checkpoint_changed_since_request: StrictBool
    relationships_changed_since_request: StrictBool
    context_changed_since_request: StrictBool
    resolved_at: UTCDateTime | None
    resolution: HumanGateText | None
    resolved_by_client: RetainedClientName | None
    resolved_by_session_id: RetainedSessionID | None
    resolved_by_model: RetainedModelName | None
    resolved_context_revision: HumanGateContextRevision | None
    context_changed_at_resolution: StrictBool | None

    @model_validator(mode="after")
    def enforce_gate_contract(self) -> Self:
        resolution_required = (
            self.resolved_at,
            self.resolution,
            self.resolved_by_client,
            self.resolved_by_session_id,
            self.resolved_context_revision,
            self.context_changed_at_resolution,
        )
        if self.status == "unresolved":
            if any(value is not None for value in (*resolution_required, self.resolved_by_model)):
                raise ValueError("Unresolved gates cannot contain resolution fields.")
            return self

        if any(value is None for value in resolution_required):
            raise ValueError("Resolved gates require complete resolution fields.")
        if self.resolved_at is not None and self.resolved_at < self.created_at:
            raise ValueError("Gate resolution cannot predate its request.")
        return self


class HumanAttentionItem(CanonicalResponse):
    gate: HumanGateRead
    summary: WorkSummary

    @model_validator(mode="after")
    def enforce_attention_item_contract(self) -> Self:
        work_item = self.summary.work_item
        if (
            self.gate.status != "unresolved"
            or self.gate.project_id != work_item.project_id
            or self.gate.work_item_id != work_item.id
            or not self.summary.readiness.is_gated
            or self.summary.readiness.unresolved_gate_count < 1
            or self.gate.current_context_revision.work_version != work_item.version
            or self.gate.current_context_revision.context_checkpoint_id
            != self.summary.current_context.id
        ):
            raise ValueError("Human-attention gate and work summary are incoherent.")
        return self


class HumanAttentionPage(CanonicalResponse):
    items: list[HumanAttentionItem] = Field(max_length=100)
    total: StrictInt = Field(ge=0)
    limit: StrictInt = Field(ge=0, le=100)
    next_cursor: OpaqueCursor | None

    @model_validator(mode="after")
    def enforce_attention_page_contract(self) -> Self:
        if len(self.items) > self.limit or len(self.items) > self.total:
            raise ValueError("Human-attention page bounds are inconsistent.")
        if self.limit == 0 and (self.items or self.next_cursor is not None):
            raise ValueError("Count-only attention pages cannot contain items or a cursor.")
        return self


class HumanGatePage(CanonicalResponse):
    items: list[HumanGateRead] = Field(max_length=100)
    total: StrictInt = Field(ge=0)
    limit: StrictInt = Field(ge=1, le=100)
    next_cursor: OpaqueCursor | None

    @model_validator(mode="after")
    def enforce_gate_page_contract(self) -> Self:
        if len(self.items) > self.limit or len(self.items) > self.total:
            raise ValueError("Human-gate page bounds are inconsistent.")
        return self


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
    checkpoint_count: StrictInt = Field(ge=1)
    display_state: ReadyDisplayState


class WorkSearchHit(CanonicalResponse):
    summary: WorkSummary
    matched_member: WorkIdentityPointer


class HierarchyPresentation(CanonicalResponse):
    direct_child_count: StrictInt = Field(ge=0)
    descendant_count: StrictInt = Field(ge=0)
    blocked_descendant_count: StrictInt = Field(ge=0)
    active_descendant_count: StrictInt = Field(ge=0)
    completed_descendant_count: StrictInt = Field(ge=0)
    discovered_descendant_count: StrictInt = Field(ge=0)
    branch_unresolved_human_gate_count: StrictInt = Field(ge=0)
    branch_merged_duplicate_count: StrictInt = Field(ge=0)
    is_discovered_work: StrictBool
    discovered_from_parent: StrictBool
    next_active_descendant_lease_expires_at: UTCDateTime | None

    @model_validator(mode="after")
    def enforce_hierarchy_counts(self) -> Self:
        bounded_counts = (
            self.direct_child_count,
            self.blocked_descendant_count,
            self.active_descendant_count,
            self.completed_descendant_count,
            self.discovered_descendant_count,
        )
        if any(count > self.descendant_count for count in bounded_counts):
            raise ValueError("Hierarchy counts cannot exceed the descendant total.")
        if self.discovered_from_parent and not self.is_discovered_work:
            raise ValueError("Parent discovery requires discovered work.")
        return self


class HierarchySummary(CanonicalResponse):
    summary: WorkSummary
    self_matches_filter: StrictBool
    has_matching_descendants: StrictBool
    presentation: HierarchyPresentation

    @model_validator(mode="after")
    def enforce_root_contract(self) -> Self:
        work_item = self.summary.work_item
        readiness = self.summary.readiness
        if readiness.is_duplicate or readiness.canonical_work_item_id != work_item.id:
            raise ValueError("Hierarchy summaries may contain only canonical roots.")
        return self


class WorkPage(CanonicalResponse):
    items: list[WorkSearchHit | HierarchySummary]
    total: StrictInt = Field(ge=0)
    limit: StrictInt = Field(ge=1, le=100)
    offset: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        if len(self.items) > self.limit:
            raise ValueError("Work pages cannot exceed their declared limit.")
        if self.items and (
            self.offset >= self.total or self.offset + len(self.items) > self.total
        ):
            raise ValueError("Work page items must fit within the declared total.")
        if self.items and not (
            all(isinstance(item, WorkSearchHit) for item in self.items)
            or all(isinstance(item, HierarchySummary) for item in self.items)
        ):
            raise ValueError("A work page cannot mix full search hits and hierarchy roots.")
        work_item_ids = [item.summary.work_item.id for item in self.items]
        if len(work_item_ids) != len(set(work_item_ids)):
            raise ValueError("Work pages cannot repeat work items.")
        return self


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
            item.work_item.status != "pending"
            or item.display_state not in {"pending", "dropped"}
            for item in self.items
        ):
            raise ValueError("Ready-work pages may contain only pending or dropped ready items.")
        return self


class CheckpointPage(CanonicalResponse):
    items: list[CheckpointRead]
    total: int
    limit: int
    offset: int


class RelationshipCounts(CanonicalResponse):
    incoming: StrictInt = Field(default=0, ge=0)
    outgoing: StrictInt = Field(default=0, ge=0)
    undirected: StrictInt = Field(default=0, ge=0)
    total: StrictInt = Field(default=0, ge=0)

    @model_validator(mode="after")
    def enforce_total(self) -> Self:
        if self.total != self.incoming + self.outgoing + self.undirected:
            raise ValueError("Relationship totals must equal their directional counts.")
        return self


class DuplicateMergeEligibility(CanonicalResponse):
    incident_blocks_count: StrictInt = Field(ge=0)
    incident_parent_child_count: StrictInt = Field(ge=0)
    has_unresolved_gate: StrictBool
    source_lease_state: Literal["none", "expired", "active"]


class WorkEventRead(CanonicalResponse):
    """Strict event wire model with event-type/origin-specific metadata validation."""

    id: int = Field(ge=1)
    project_id: UUID
    work_item_id: UUID
    event_type: EventType
    actor_kind: ActorKind
    actor_client: str | None = Field(max_length=80)
    actor_session_id: str | None = Field(max_length=200)
    actor_model: str | None = Field(max_length=120)
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
        _validate_event_actor(self)
        _validate_event_origin(self)
        _validate_event_body(self)
        _validate_event_references(self)
        self.metadata = _validated_event_metadata(self)
        return self


_LIVE_CLIENT_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        "work_created",
        "work_claimed",
        "checkpoint_added",
        "progress",
        "dependency_added",
        "relationship_added",
        "work_completed",
        "human_attention_requested",
        "human_attention_resolved",
        "work_merged",
    }
)
_BACKFILL_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        "work_created",
        "work_claimed",
        "checkpoint_added",
        "dependency_added",
        "relationship_added",
        "work_completed",
        "work_deleted",
    }
)
_BODY_EVENT_TYPES: frozenset[EventType] = frozenset(
    {"progress", "human_attention_requested", "human_attention_resolved", "work_merged"}
)
_RELATIONSHIP_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        "dependency_added",
        "dependency_removed",
        "relationship_added",
        "relationship_removed",
    }
)


def _validate_client_event_actor(event: WorkEventRead) -> None:
    if event.actor_client is None or event.actor_session_id is None:
        raise ValueError("Client events require client and session provenance.")
    if not event.actor_client.strip() or not event.actor_session_id.strip():
        raise ValueError("Client event provenance must be nonblank.")
    if event.actor_model is not None and not event.actor_model.strip():
        raise ValueError("Client event model provenance must be nonblank.")


def _validate_event_actor(event: WorkEventRead) -> None:
    if event.actor_kind == "client":
        _validate_client_event_actor(event)
        return
    actor_values = (event.actor_client, event.actor_session_id, event.actor_model)
    if any(value is not None for value in actor_values):
        raise ValueError("Unattributed events cannot contain actor provenance.")


def _validate_event_origin(event: WorkEventRead) -> None:
    if (
        event.origin == "live"
        and event.event_type in _LIVE_CLIENT_EVENT_TYPES
        and event.actor_kind != "client"
    ):
        raise ValueError("This live event requires client provenance.")
    if event.origin == "backfill" and event.event_type not in _BACKFILL_EVENT_TYPES:
        raise ValueError("This event type cannot be backfilled.")
    if (
        event.origin == "backfill"
        and event.event_type == "work_deleted"
        and event.actor_kind != "unattributed"
    ):
        raise ValueError("Backfilled deletion events must be unattributed.")


def _validate_event_body(event: WorkEventRead) -> None:
    if event.event_type not in _BODY_EVENT_TYPES:
        if event.body is not None:
            raise ValueError("Other server-reserved event types cannot contain a body.")
        return
    if event.origin != "live" or event.body is None:
        raise ValueError("Body-bearing events require a live body.")
    if not 1 <= len(event.body) <= 4000 or not event.body.strip() or "\x00" in event.body:
        raise ValueError("Event bodies must be bounded nonblank text.")


def _validate_checkpoint_and_lease_references(event: WorkEventRead) -> None:
    checkpoint_events = {"work_created", "checkpoint_added", "work_completed"}
    if (event.checkpoint_id is not None) != (event.event_type in checkpoint_events):
        raise ValueError("Checkpoint event references do not match the event type.")
    lease_events = {"work_claimed", "work_released"}
    if (event.lease_generation_id is not None) != (event.event_type in lease_events):
        raise ValueError("Lease generation references do not match the event type.")
    if (event.lease_release_id is not None) != (event.event_type == "work_released"):
        raise ValueError("Lease release references do not match the event type.")


def _relationship_reference_values(event: WorkEventRead) -> tuple[UUID | str | None, ...]:
    return (
        event.relationship_id,
        event.relationship_source_work_item_id,
        event.relationship_target_work_item_id,
        event.relationship_direction,
        event.counterpart_work_item_id,
        event.relationship_context_checkpoint_work_item_id,
        event.relationship_context_checkpoint_id,
    )


def _validate_relationship_references(event: WorkEventRead) -> None:
    values = _relationship_reference_values(event)
    if event.event_type not in _RELATIONSHIP_EVENT_TYPES:
        if any(value is not None for value in values):
            raise ValueError("Non-relationship events cannot contain relationship references.")
        return
    if any(value is None for value in values[:5]):
        raise ValueError("Relationship events require the complete endpoint projection.")
    context_work_item_id = event.relationship_context_checkpoint_work_item_id
    context_checkpoint_id = event.relationship_context_checkpoint_id
    if (context_work_item_id is None) != (context_checkpoint_id is None):
        raise ValueError("Relationship context references must be paired.")
    if context_work_item_id is not None and context_work_item_id not in {
        event.relationship_source_work_item_id,
        event.relationship_target_work_item_id,
    }:
        raise ValueError("Relationship context must belong to an endpoint.")


def _validate_event_references(event: WorkEventRead) -> None:
    _validate_checkpoint_and_lease_references(event)
    _validate_relationship_references(event)


def _validated_status_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> WorkUpdatedMetadata | WorkStatusMetadata:
    if event.event_type == "work_updated":
        parsed_update = WorkUpdatedMetadata.model_validate(payload)
        status_change = parsed_update.changes.status
        if status_change is not None and status_change.before != status_change.after:
            raise ValueError("Ordinary work updates cannot change lifecycle status.")
        return parsed_update

    parsed_status = WorkStatusMetadata.model_validate(payload)
    status_change = parsed_status.changes.status
    if status_change is None:
        raise ValueError("Lifecycle events require a typed status change.")
    if (status_change.before, status_change.after) != (
        parsed_status.from_status,
        parsed_status.to_status,
    ):
        raise ValueError("Lifecycle event status metadata is inconsistent.")
    if event.event_type == "work_status_changed" and (
        parsed_status.from_status not in {"open", "pending"}
        or parsed_status.to_status not in {"deferred", "wont-do", "promoted"}
    ):
        raise ValueError("Status-change events must leave pending work.")
    if event.event_type == "work_reopened" and (
        parsed_status.to_status not in {"open", "pending"}
        or parsed_status.from_status in {"open", "pending"}
    ):
        raise ValueError("Reopen events must return held or terminal work to pending.")
    return parsed_status


def _validate_relationship_endpoints(
    event: WorkEventRead,
    metadata: RelationshipEventMetadata,
    source_id: UUID,
    target_id: UUID,
) -> None:
    if source_id == target_id:
        raise ValueError("Relationship event endpoints must differ.")
    if metadata.relationship_type == "related" and source_id > target_id:
        raise ValueError("Related relationship endpoints must be normalized.")
    if metadata.relationship_type == "discovered-from" and (
        event.relationship_context_checkpoint_work_item_id != target_id
    ):
        raise ValueError("Discovered-from context must belong to the target.")


def _expected_relationship_projection(
    event: WorkEventRead,
    relationship_type: RelationshipType,
    source_id: UUID,
    target_id: UUID,
) -> tuple[RelationshipDirection, UUID]:
    if event.work_item_id == source_id:
        direction: RelationshipDirection = (
            "undirected" if relationship_type == "related" else "outgoing"
        )
        return direction, target_id
    if event.work_item_id == target_id:
        direction = "undirected" if relationship_type == "related" else "incoming"
        return direction, source_id
    raise ValueError("Relationship event work item must be an endpoint.")


def _validated_relationship_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> RelationshipEventMetadata:
    parsed = RelationshipEventMetadata.model_validate(payload)
    is_dependency = event.event_type.startswith("dependency_")
    if is_dependency != (parsed.relationship_type == "blocks"):
        raise ValueError("Relationship event family does not match its type.")
    source_id = event.relationship_source_work_item_id
    target_id = event.relationship_target_work_item_id
    assert source_id is not None and target_id is not None
    _validate_relationship_endpoints(event, parsed, source_id, target_id)
    expected_direction, expected_counterpart = _expected_relationship_projection(
        event,
        parsed.relationship_type,
        source_id,
        target_id,
    )
    if event.relationship_direction != expected_direction:
        raise ValueError("Relationship direction projection is inconsistent.")
    if event.counterpart_work_item_id != expected_counterpart:
        raise ValueError("Relationship counterpart projection is inconsistent.")
    return parsed


def _validated_created_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> WorkCreatedLiveMetadata | EmptyEventMetadata:
    if event.origin == "live":
        return WorkCreatedLiveMetadata.model_validate(payload)
    return EmptyEventMetadata.model_validate(payload)


def _validated_claimed_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> WorkClaimedLiveMetadata | WorkClaimedBackfillMetadata:
    if event.origin == "live":
        return WorkClaimedLiveMetadata.model_validate(payload)
    return WorkClaimedBackfillMetadata.model_validate(payload)


def _validated_released_metadata(
    payload: dict[str, JsonValue],
) -> WorkReleasedClientMetadata | WorkReleasedUnattributedMetadata:
    if payload.get("lease_holder_kind") == "client":
        return WorkReleasedClientMetadata.model_validate(payload)
    return WorkReleasedUnattributedMetadata.model_validate(payload)


def _validated_completed_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> WorkCompletedLiveMetadata | EmptyEventMetadata:
    if event.origin == "live":
        return WorkCompletedLiveMetadata.model_validate(payload)
    return EmptyEventMetadata.model_validate(payload)


def _validated_other_event_metadata(
    event: WorkEventRead,
    payload: dict[str, JsonValue],
) -> WorkEventMetadata:
    if event.event_type == "work_created":
        return _validated_created_metadata(event, payload)
    if event.event_type == "work_claimed":
        return _validated_claimed_metadata(event, payload)
    if event.event_type == "work_released":
        return _validated_released_metadata(payload)
    if event.event_type == "checkpoint_added":
        return CheckpointAddedMetadata.model_validate(payload)
    if event.event_type == "progress":
        return ProgressEventMetadata.model_validate(payload)
    if event.event_type == "work_completed":
        return _validated_completed_metadata(event, payload)
    if event.event_type == "work_deleted":
        return WorkDeletedMetadata.model_validate(payload)
    if event.event_type == "work_merged":
        parsed = WorkMergedMetadata.model_validate(payload)
        expected_work_item_id = (
            parsed.source_work_item_id
            if parsed.role == "source"
            else parsed.destination_work_item_id
        )
        if event.work_item_id != expected_work_item_id:
            raise ValueError("Merge event role does not match its work item.")
        return parsed
    raise ValueError("Unknown event type.")


def _validated_event_metadata(event: WorkEventRead) -> WorkEventMetadata:
    payload = _metadata_payload(event.metadata)
    if event.event_type in {"work_updated", "work_status_changed", "work_reopened"}:
        return _validated_status_metadata(event, payload)
    if event.event_type in _RELATIONSHIP_EVENT_TYPES:
        return _validated_relationship_metadata(event, payload)
    if event.event_type in {"human_attention_requested", "human_attention_resolved"}:
        return HumanGateEventMetadata.model_validate(payload)
    return _validated_other_event_metadata(event, payload)


class WorkEventPage(CanonicalResponse):
    items: list[WorkEventRead]
    total: int
    limit: int
    offset: int
    pre_phase5_history_may_be_incomplete: bool


class WorkContext(CanonicalResponse):
    work_item: WorkItemRead
    merge_review_revision: MergeReviewRevision
    canonical: CanonicalWorkProjection
    duplicate_members: list[WorkIdentityPointer] = Field(max_length=20)
    duplicate_member_total: StrictInt = Field(ge=0)
    omitted_duplicate_member_count: StrictInt = Field(ge=0)
    initial_checkpoint: CheckpointRead
    # Null when the newest context checkpoint is the initial one; read
    # initial_checkpoint instead. The body is never serialized twice.
    current_context: CheckpointRead | None
    current_context_is_initial: StrictBool
    # Never repeats initial_checkpoint or current_context.
    recent_checkpoints: list[CheckpointRead] = Field(max_length=20)
    # Every checkpoint on the work item; omitted counts what is not in this payload.
    checkpoint_total: StrictInt = Field(ge=1)
    omitted_checkpoint_count: StrictInt = Field(ge=0)
    readiness: Readiness
    unresolved_gates: list[HumanGateRead] = Field(max_length=20)
    unresolved_gate_total: StrictInt = Field(ge=0)
    omitted_unresolved_gate_count: StrictInt = Field(ge=0)
    recent_resolved_gates: list[HumanGateRead] = Field(max_length=20)
    resolved_gate_total: StrictInt = Field(ge=0)
    omitted_resolved_gate_count: StrictInt = Field(ge=0)
    recent_events: list[WorkEventRead] = Field(max_length=20)
    event_total: StrictInt = Field(ge=1)
    omitted_event_count: StrictInt = Field(ge=0)
    pre_phase5_history_may_be_incomplete: StrictBool
    incoming_relationships: list[AdjacentRelationshipRead] = Field(
        default_factory=list, max_length=100
    )
    outgoing_relationships: list[AdjacentRelationshipRead] = Field(
        default_factory=list, max_length=100
    )
    undirected_relationships: list[AdjacentRelationshipRead] = Field(
        default_factory=list, max_length=100
    )
    relationship_counts: RelationshipCounts = Field(default_factory=RelationshipCounts)
    omitted_relationship_counts: RelationshipCounts = Field(
        default_factory=RelationshipCounts
    )
    duplicate_merge_eligibility: DuplicateMergeEligibility

    @model_validator(mode="after")
    def enforce_gate_recall_contract(self) -> Self:
        self._enforce_duplicate_contract()
        self._enforce_relationship_contract()
        context_checkpoint_id = self._enforce_history_contract()
        if self.unresolved_gate_total != self.readiness.unresolved_gate_count:
            raise ValueError("Recall gate totals must match readiness.")
        if self.omitted_unresolved_gate_count != (
            self.unresolved_gate_total - len(self.unresolved_gates)
        ):
            raise ValueError("Unresolved gate omission count is inconsistent.")
        if self.omitted_resolved_gate_count != (
            self.resolved_gate_total - len(self.recent_resolved_gates)
        ):
            raise ValueError("Resolved gate omission count is inconsistent.")

        seen_gate_ids: set[UUID] = set()
        for gate in [*self.unresolved_gates, *self.recent_resolved_gates]:
            if gate.id in seen_gate_ids:
                raise ValueError("Recall gate slices cannot duplicate a gate.")
            seen_gate_ids.add(gate.id)
            if (
                gate.project_id != self.work_item.project_id
                or gate.work_item_id != self.work_item.id
                or gate.current_context_revision.work_version != self.work_item.version
                or gate.current_context_revision.context_checkpoint_id
                != context_checkpoint_id
            ):
                raise ValueError("Recall gate projection is outside current work context.")
        if any(gate.status != "unresolved" for gate in self.unresolved_gates):
            raise ValueError("Unresolved recall slice may contain only unresolved gates.")
        if any(gate.status != "resolved" for gate in self.recent_resolved_gates):
            raise ValueError("Resolved recall slice may contain only resolved gates.")
        return self

    def _enforce_duplicate_contract(self) -> None:
        work_item = self.work_item
        projection = self.canonical
        revision = self.merge_review_revision
        requested_pointer = WorkIdentityPointer(
            id=work_item.id,
            title=work_item.title,
            status=work_item.status,
        )
        if (
            self.readiness.lifecycle_status != work_item.status
            or self.readiness.is_duplicate != projection.is_duplicate
            or self.readiness.canonical_work_item_id
            != projection.canonical_work_item.id
            or revision.work_version != work_item.version
            or revision.work_event_count != self.event_total
            or projection.duplicate_member_count != self.duplicate_member_total
            or self.omitted_duplicate_member_count
            != self.duplicate_member_total - len(self.duplicate_members)
        ):
            raise ValueError("Recall duplicate projection is incoherent.")
        if projection.is_duplicate and (
            requested_pointer in projection.path
            or not self.duplicate_members
            or self.duplicate_members[0] != requested_pointer
        ):
            raise ValueError("An alias recall must list the requested alias first.")
        if not projection.is_duplicate and projection.canonical_work_item != requested_pointer:
            raise ValueError("A root recall must identify the requested work item as canonical.")
        canonical_id = projection.canonical_work_item.id
        if any(member.id == canonical_id for member in self.duplicate_members):
            raise ValueError("Duplicate members must contain strict aliases only.")
        member_ids = [member.id for member in self.duplicate_members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("Duplicate member slices cannot repeat a work item.")
        expected_lease_state = (
            "active"
            if self.readiness.has_active_lease
            else "expired"
            if self.readiness.has_dropped_lease
            else "none"
        )
        if self.duplicate_merge_eligibility.source_lease_state != expected_lease_state:
            raise ValueError("Merge eligibility lease state is incoherent.")
        if (
            self.duplicate_merge_eligibility.has_unresolved_gate
            != (self.readiness.unresolved_gate_count > 0)
        ):
            raise ValueError("Merge eligibility gate state is incoherent.")

    def _enforce_history_contract(self) -> UUID:
        work_item = self.work_item
        initial = self.initial_checkpoint
        current = self.current_context
        if (
            initial.id != work_item.initial_checkpoint_id
            or initial.work_item_id != work_item.id
            or initial.kind != "context"
        ):
            raise ValueError("Recall initial checkpoint is outside the requested work item.")
        if self.current_context_is_initial:
            if current is not None:
                raise ValueError("Initial current context cannot be serialized twice.")
            context_checkpoint_id = initial.id
        else:
            if current is None or current.kind != "context":
                raise ValueError("Recall must identify one current context checkpoint.")
            context_checkpoint_id = current.id

        checkpoints = [initial, *([current] if current is not None else []), *self.recent_checkpoints]
        checkpoint_ids = [checkpoint.id for checkpoint in checkpoints]
        if (
            any(checkpoint.work_item_id != work_item.id for checkpoint in checkpoints)
            or len(checkpoint_ids) != len(set(checkpoint_ids))
            or self.omitted_checkpoint_count != self.checkpoint_total - len(checkpoints)
        ):
            raise ValueError("Recall checkpoint history is incoherent.")
        if self.merge_review_revision.context_checkpoint_id != context_checkpoint_id:
            raise ValueError("Merge review checkpoint must match current context.")

        event_ids = [event.id for event in self.recent_events]
        event_order = [(event.created_at, event.id) for event in self.recent_events]
        if (
            any(
                event.project_id != work_item.project_id
                or event.work_item_id != work_item.id
                for event in self.recent_events
            )
            or len(event_ids) != len(set(event_ids))
            or event_order != sorted(event_order)
            or self.omitted_event_count != self.event_total - len(self.recent_events)
        ):
            raise ValueError("Recall event history is incoherent.")
        return context_checkpoint_id

    def _enforce_relationship_contract(self) -> None:
        slices = {
            "incoming": self.incoming_relationships,
            "outgoing": self.outgoing_relationships,
            "undirected": self.undirected_relationships,
        }
        seen_relationship_ids: set[UUID] = set()
        for direction, relationships in slices.items():
            total = getattr(self.relationship_counts, direction)
            omitted = getattr(self.omitted_relationship_counts, direction)
            if omitted != total - len(relationships):
                raise ValueError("Relationship omission counts are incoherent.")
            ordering = [
                (adjacent.relationship.created_at, adjacent.relationship.id)
                for adjacent in relationships
            ]
            if ordering != sorted(ordering):
                raise ValueError("Recall relationship slices are not in canonical order.")
            for adjacent in relationships:
                edge = adjacent.relationship
                expected_direction, expected_counterpart = _context_relationship_projection(
                    edge, self.work_item.id
                )
                if (
                    edge.id in seen_relationship_ids
                    or edge.project_id != self.work_item.project_id
                    or adjacent.relative_to_work_item_id != self.work_item.id
                    or adjacent.direction != direction
                    or adjacent.direction != expected_direction
                    or adjacent.counterpart.id != expected_counterpart
                ):
                    raise ValueError("Recall relationships are outside the requested work item.")
                seen_relationship_ids.add(edge.id)
        if self.omitted_relationship_counts.total == 0:
            relationships = [
                *self.incoming_relationships,
                *self.outgoing_relationships,
                *self.undirected_relationships,
            ]
            if (
                self.duplicate_merge_eligibility.incident_blocks_count
                != sum(
                    item.relationship.relationship_type == "blocks"
                    for item in relationships
                )
                or self.duplicate_merge_eligibility.incident_parent_child_count
                != sum(
                    item.relationship.relationship_type == "parent-child"
                    for item in relationships
                )
            ):
                raise ValueError("Merge eligibility structural counts are incoherent.")


def _context_relationship_projection(
    edge: RelationshipEdgeRead,
    work_item_id: UUID,
) -> tuple[RelationshipDirection, UUID]:
    if edge.source_work_item_id == work_item_id:
        direction: RelationshipDirection = (
            "undirected" if edge.relationship_type == "related" else "outgoing"
        )
        return direction, edge.target_work_item_id
    if edge.target_work_item_id == work_item_id:
        direction = "undirected" if edge.relationship_type == "related" else "incoming"
        return direction, edge.source_work_item_id
    raise ValueError("Recall relationship does not touch the requested work item.")


class ClaimReceipt(CanonicalResponse):
    """Capability-bearing lease receipt returned only by claim and renew operations."""

    work_item_id: UUID
    holder_client: RetainedClientName
    holder_session_id: RetainedSessionID
    claim_request_id: RetainedSessionID
    acquired_at: UTCDateTime
    renewed_at: UTCDateTime
    expires_at: UTCDateTime
    lease_token: Annotated[
        str,
        Field(min_length=1, max_length=200, repr=False),
        AfterValidator(_validated_event_text),
    ]

    @model_validator(mode="after")
    def enforce_lease_times(self) -> Self:
        if self.renewed_at < self.acquired_at or self.expires_at <= self.renewed_at:
            raise ValueError("Claim receipt timestamps are incoherent.")
        return self


class ClaimAndRecall(CanonicalResponse):
    lease: ClaimReceipt
    context: WorkContext

    @model_validator(mode="after")
    def enforce_claim_context(self) -> Self:
        public = self.context.readiness.active_lease
        if (
            self.context.work_item.id != self.lease.work_item_id
            or self.context.canonical.is_duplicate
            or public is None
            or public.holder_client != self.lease.holder_client
            or public.holder_session_id != self.lease.holder_session_id
            or public.acquired_at != self.lease.acquired_at
            or public.renewed_at != self.lease.renewed_at
            or public.expires_at != self.lease.expires_at
        ):
            raise ValueError("Claim receipt and recalled context are incoherent.")
        return self


class ReleaseResult(CanonicalResponse):
    work_item_id: UUID
    released: bool


class WorkCreation(CanonicalResponse):
    work_item: WorkItemRead
    initial_checkpoint: CheckpointRead
    initial_relationships: list[RelationshipEdgeRead] = Field(default_factory=list)


class WorkMergeCreate(BaseModel):
    """Strict public request for one irreversible authoritative merge."""

    model_config = ConfigDict(extra="forbid")

    destination_work_item_id: UUID
    reviewed_source_revision: MergeReviewRevision
    reviewed_destination_revision: MergeReviewRevision
    rationale: HumanGateText
    merged_by_client: RetainedClientName
    merged_by_session_id: RetainedSessionID
    merged_by_model: RetainedModelName | None = None
    lease_token: SecretStr | None = Field(default=None, repr=False)
    client_operation_id: UUID = Field(repr=False)


class WorkMergeRead(CanonicalResponse):
    id: UUID
    merge_sequence: StrictInt = Field(ge=1)
    project_id: UUID
    source_work_item_id: UUID
    destination_work_item_id: UUID
    duplicate_relationship_id: UUID
    reviewed_source_revision: MergeReviewRevision
    reviewed_destination_revision: MergeReviewRevision
    resulting_source_work_version: StrictInt = Field(ge=2)
    resulting_destination_work_version: StrictInt = Field(ge=2)
    rationale: HumanGateText
    merged_by_client: RetainedClientName
    merged_by_session_id: RetainedSessionID
    merged_by_model: RetainedModelName | None
    created_at: UTCDateTime

    @model_validator(mode="after")
    def enforce_merge_fact_contract(self) -> Self:
        if self.source_work_item_id == self.destination_work_item_id:
            raise ValueError("A merge requires distinct endpoints.")
        if (
            self.resulting_source_work_version
            != self.reviewed_source_revision.work_version + 1
            or self.resulting_destination_work_version
            != self.reviewed_destination_revision.work_version + 1
        ):
            raise ValueError("Merge result versions must advance each reviewed endpoint once.")
        return self


class WorkMergeResult(CanonicalResponse):
    merge: WorkMergeRead
    source_work_item: WorkItemRead
    destination_work_item: WorkItemRead
    direct_destination: WorkIdentityPointer
    canonical_work_item: WorkIdentityPointer
    supporting_relationship_created: StrictBool
    supporting_relationship: RelationshipEdgeRead
    relationship_events: list[WorkEventRead] = Field(max_length=2)
    merge_events: list[WorkEventRead] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def enforce_merge_result_contract(self) -> Self:
        self._require_endpoints()
        self._require_supporting_relationship()
        self._require_relationship_events()
        self._require_merge_events()
        event_ids = [
            *(event.id for event in self.relationship_events),
            *(event.id for event in self.merge_events),
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Merge result events must have distinct identities.")
        return self

    def _require_endpoints(self) -> None:
        merge = self.merge
        source = self.source_work_item
        destination = self.destination_work_item
        if (
            source.id != merge.source_work_item_id
            or destination.id != merge.destination_work_item_id
            or source.project_id != merge.project_id
            or destination.project_id != merge.project_id
            or source.version != merge.resulting_source_work_version
            or destination.version != merge.resulting_destination_work_version
            or source.updated_at != merge.created_at
            or destination.updated_at != merge.created_at
        ):
            raise ValueError("Merge result endpoint snapshots are incoherent.")
        destination_identity = (destination.id, destination.title, destination.status)
        if (
            (
                self.direct_destination.id,
                self.direct_destination.title,
                self.direct_destination.status,
            )
            != destination_identity
            or (
                self.canonical_work_item.id,
                self.canonical_work_item.title,
                self.canonical_work_item.status,
            )
            != destination_identity
        ):
            raise ValueError("A fresh merge must point directly to its canonical destination.")

    def _require_supporting_relationship(self) -> None:
        merge = self.merge
        edge = self.supporting_relationship
        if (
            edge.id != merge.duplicate_relationship_id
            or edge.project_id != merge.project_id
            or edge.relationship_type != "duplicate-of"
            or edge.source_work_item_id != merge.source_work_item_id
            or edge.target_work_item_id != merge.destination_work_item_id
            or edge.created_at > merge.created_at
        ):
            raise ValueError("Merge supporting relationship is incoherent.")
        if self.supporting_relationship_created and (
            edge.created_by_client != merge.merged_by_client
            or edge.created_by_session_id != merge.merged_by_session_id
            or edge.created_by_model != merge.merged_by_model
            or edge.created_at != merge.created_at
            or edge.context_checkpoint_work_item_id is not None
            or edge.context_checkpoint_id is not None
        ):
            raise ValueError("A merge-created relationship must share merge provenance.")

    def _require_relationship_events(self) -> None:
        expected_count = 2 if self.supporting_relationship_created else 0
        if len(self.relationship_events) != expected_count:
            raise ValueError("Merge relationship event count is incoherent.")
        if not self.relationship_events:
            return
        expected_work_ids = [
            self.merge.source_work_item_id,
            self.merge.destination_work_item_id,
        ]
        expected_directions: list[RelationshipDirection] = ["outgoing", "incoming"]
        expected_counterparts = list(reversed(expected_work_ids))
        edge = self.supporting_relationship
        for event, work_item_id, direction, counterpart in zip(
            self.relationship_events,
            expected_work_ids,
            expected_directions,
            expected_counterparts,
            strict=True,
        ):
            if (
                event.event_type != "relationship_added"
                or event.work_item_id != work_item_id
                or event.project_id != self.merge.project_id
                or event.relationship_id != self.supporting_relationship.id
                or event.relationship_source_work_item_id != edge.source_work_item_id
                or event.relationship_target_work_item_id != edge.target_work_item_id
                or event.relationship_context_checkpoint_work_item_id
                != edge.context_checkpoint_work_item_id
                or event.relationship_context_checkpoint_id != edge.context_checkpoint_id
                or event.relationship_direction != direction
                or event.counterpart_work_item_id != counterpart
                or not isinstance(event.metadata, RelationshipEventMetadata)
                or event.metadata.relationship_type != "duplicate-of"
                or event.origin != "live"
                or event.actor_kind != "client"
                or event.actor_client != self.merge.merged_by_client
                or event.actor_session_id != self.merge.merged_by_session_id
                or event.actor_model != self.merge.merged_by_model
                or event.created_at != self.merge.created_at
            ):
                raise ValueError("Merge relationship event ordering is incoherent.")

    def _require_merge_events(self) -> None:
        expected_work_ids = [
            self.merge.source_work_item_id,
            self.merge.destination_work_item_id,
        ]
        expected_roles = ["source", "destination"]
        for event, work_item_id, role in zip(
            self.merge_events, expected_work_ids, expected_roles, strict=True
        ):
            metadata = event.metadata
            if (
                event.event_type != "work_merged"
                or event.work_item_id != work_item_id
                or event.project_id != self.merge.project_id
                or event.actor_kind != "client"
                or event.actor_client != self.merge.merged_by_client
                or event.actor_session_id != self.merge.merged_by_session_id
                or event.actor_model != self.merge.merged_by_model
                or event.body != self.merge.rationale
                or event.created_at != self.merge.created_at
                or not isinstance(metadata, WorkMergedMetadata)
                or metadata.merge_id != self.merge.id
                or metadata.source_work_item_id != self.merge.source_work_item_id
                or metadata.destination_work_item_id
                != self.merge.destination_work_item_id
                or metadata.role != role
                or metadata.source_work_version
                != self.merge.resulting_source_work_version
                or metadata.destination_work_version
                != self.merge.resulting_destination_work_version
            ):
                raise ValueError("Merge event ordering or provenance is incoherent.")


class WorkChanges(BaseModel):
    """Only supplied mutable work-item fields change."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    summary: str | None = Field(default=None, min_length=1, max_length=1000)
    priority: int | None = Field(default=None, ge=0, le=100)
    status: UpdateStatus | None = None

    @model_validator(mode="after")
    def require_changes(self) -> WorkChanges:
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
