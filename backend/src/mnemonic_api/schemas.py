"""Strict wire models. Agent-authored prompt text is never stripped or rewritten."""

import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationInfo,
    computed_field,
    field_serializer,
    field_validator,
    model_serializer,
    model_validator,
)

AFFECTED_PATH_MAX_COUNT = 64
AFFECTED_PATH_MAX_BYTES = 512
AFFECTED_PATHS_MAX_BYTES = 16384
_AFFECTED_PATH_COMPONENT_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._@+=,~-*"
)
AFFECTED_PATHS_DESCRIPTION = (
    "Ordered caller-declared repository dependency patterns. Entries use a narrow ASCII "
    "root-relative grammar: slash-separated components containing letters, digits, . _ @ + = , "
    "~ - and single-component * wildcards; ** is allowed only as a complete component. "
    "At most 64 entries, 512 bytes each, and 16384 bytes total. A non-empty list requires "
    "verified_against. Omitted or empty means no dependency scope was declared and is omitted "
    "from canonical responses; ** explicitly declares all eligible repository paths."
)


def no_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL characters cannot be stored")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Text must contain valid Unicode characters") from exc
    return value


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Must contain non-whitespace text")
    return no_nul(value)


def http_url(value: str) -> str:
    no_nul(value)
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Must be a valid HTTP or HTTPS URL") from exc
    if not valid or parsed.username or parsed.password or any(char.isspace() for char in value):
        raise ValueError("Must be an HTTP or HTTPS URL without credentials or whitespace")
    return value


def normalized_tag(value: str) -> str:
    value = nonblank(value).lower()
    if len(value) > 50:
        raise ValueError("Normalized tags must contain at most 50 characters")
    return value


def affected_path_is_valid(value: str) -> str:
    """Validate one stored scope pattern without normalizing caller provenance."""
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Affected paths must contain valid ASCII") from exc
    if len(encoded) > AFFECTED_PATH_MAX_BYTES:
        raise ValueError("Affected paths must be at most 512 bytes")
    if not value.isascii():
        raise ValueError("Affected paths must contain only supported ASCII characters")

    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("Affected paths must contain non-empty root-relative components")
    for component in components:
        if any(character not in _AFFECTED_PATH_COMPONENT_CHARACTERS for character in component):
            raise ValueError("Affected paths contain an unsupported character")
        if "**" in component and component != "**":
            raise ValueError("Double star must be a complete affected-path component")
    return value


AffectedPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=AFFECTED_PATH_MAX_BYTES,
        pattern=r"^[A-Za-z0-9._@+=,~*/-]+$",
    ),
    AfterValidator(affected_path_is_valid),
]
AffectedPaths = Annotated[list[AffectedPath], Field(max_length=AFFECTED_PATH_MAX_COUNT)]


def affected_paths_are_valid(value: list[str], info: ValidationInfo) -> list[str]:
    """Apply list-wide bounds and the baseline dependency after entry validation."""
    if len(value) != len(set(value)):
        raise ValueError("Affected paths must not contain duplicates")
    if sum(len(path.encode("utf-8")) for path in value) > AFFECTED_PATHS_MAX_BYTES:
        raise ValueError("Affected paths must be at most 16384 bytes in total")
    if value and info.data.get("verified_against") is None:
        raise ValueError("Affected paths require verified_against")
    return value


def metadata_is_bounded(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ValueError("Metadata must contain finite, valid JSON values") from exc
    if len(encoded) > 16384:
        raise ValueError("Metadata must be at most 16 KB of UTF-8 JSON")
    if b"\\u0000" in encoded:

        def contains_nul(item: JsonValue) -> bool:
            if isinstance(item, str):
                return "\x00" in item
            if isinstance(item, list):
                return any(contains_nul(child) for child in item)
            if isinstance(item, dict):
                return any("\x00" in key or contains_nul(child) for key, child in item.items())
            return False

        if contains_nul(value):
            raise ValueError("Metadata cannot contain NUL characters")
    return value


Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(nonblank),
]
Summary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    AfterValidator(nonblank),
]
StoredTitle = Annotated[str, StringConstraints(min_length=1, max_length=200)]
StoredSummary = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
Prompt = Annotated[
    str, StringConstraints(min_length=1, max_length=100000), AfterValidator(nonblank)
]
ClientName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
    AfterValidator(nonblank),
]
SessionID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(nonblank),
]
ReleasedLeaseHolderClient = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80),
    AfterValidator(nonblank),
]
ReleasedLeaseHolderSessionID = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    AfterValidator(nonblank),
]
ClaimRequestID = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(nonblank),
]
LeaseToken = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    AfterValidator(nonblank),
]
ModelName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    AfterValidator(nonblank),
]
BranchName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(nonblank),
]
CommitID = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, pattern=r"^[0-9a-fA-F]{7,64}$")
]
HTTPURL = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    AfterValidator(http_url),
]
Tag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    AfterValidator(normalized_tag),
]
ReadyTagFilter = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    AfterValidator(nonblank),
]
Tags = Annotated[list[Tag], Field(max_length=20)]
Metadata = Annotated[dict[str, JsonValue], AfterValidator(metadata_is_bounded)]
EVENT_SECRET_KEYS = frozenset(
    {"lease_token", "claim_request_id", "api_key", "authorization", "cookie", "secret"}
)


def event_metadata_is_safe(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    metadata_is_bounded(value)

    def validate(item: JsonValue) -> None:
        if isinstance(item, list):
            for child in item:
                validate(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                if key.casefold() in EVENT_SECRET_KEYS:
                    raise ValueError("Event metadata contains a reserved secret-like key")
                validate(child)

    validate(value)
    return value


EventMetadata = Annotated[dict[str, JsonValue], AfterValidator(event_metadata_is_safe)]
GateText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=4000),
    AfterValidator(nonblank),
]
GateCursor = Annotated[
    str,
    StringConstraints(min_length=1, max_length=4096),
    AfterValidator(nonblank),
]
CLIENT_OPERATION_ID_DESCRIPTION = (
    "Optional caller-generated UUID for durable replay of this mutation. Reuse it only with "
    "the exact same operation and semantic arguments after an unknown outcome."
)
REQUIRED_CLIENT_OPERATION_ID_DESCRIPTION = (
    "Required caller-generated UUID for durable replay of this irreversible mutation. Reuse it "
    "only with the exact same operation and semantic arguments after an unknown outcome."
)
MergeRationale = Annotated[
    str,
    StringConstraints(min_length=1, max_length=4000),
    AfterValidator(nonblank),
]


def progress_request_metadata_is_safe(
    value: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Apply request-only reserved-key rules without changing readable event history."""
    event_metadata_is_safe(value)
    reserved_keys = {"client_operation_id", "gate_id", "gate_type"}

    def validate(item: JsonValue) -> None:
        if isinstance(item, list):
            for child in item:
                validate(child)
        elif isinstance(item, dict):
            for key, child in item.items():
                if key.casefold() in reserved_keys:
                    raise ValueError("Progress metadata contains a reserved control key")
                validate(child)

    validate(value)
    return value


ProgressRequestMetadata = Annotated[
    dict[str, JsonValue], AfterValidator(progress_request_metadata_is_safe)
]
Status = Literal["pending", "deferred", "done", "wont-do", "promoted"]
EventStatus = Literal["open", "pending", "deferred", "done", "wont-do", "promoted"]
EventCreateStatus = Literal["open", "pending", "deferred", "wont-do", "promoted"]
MutableStatus = Literal["pending", "wont-do", "promoted"]
CreateStatus = Literal["pending", "wont-do", "promoted"]
CheckpointKind = Literal["context", "progress", "completion"]
AppendCheckpointKind = Literal["context", "progress"]
MigrationOrigin = Literal["legacy-handoff-snapshot", "legacy-comment"]
RelationshipType = Literal["blocks", "parent-child", "discovered-from", "duplicate-of", "related"]
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
ProjectName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    AfterValidator(nonblank),
]
ProjectDescription = Annotated[str, StringConstraints(max_length=4000), AfterValidator(no_nul)]
Slug = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
]
RecallPointerTemplate = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100000),
    AfterValidator(nonblank),
]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class MutationActor(APIModel):
    """Client-asserted mutation provenance; never an authenticated identity."""

    actor_client: ClientName
    actor_session_id: SessionID
    actor_model: ModelName | None = None


class Timestamps(APIModel):
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProjectCreate(APIModel):
    name: ProjectName
    slug: Slug | None = None
    description: ProjectDescription = ""
    repository_url: HTTPURL | None = None

    @model_validator(mode="after")
    def default_slug(self) -> Self:
        if self.slug is None:
            ascii_name = unicodedata.normalize("NFKD", self.name).encode("ascii", "ignore").decode()
            self.slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:100].strip("-")
            if not self.slug:
                raise ValueError("Provide a lowercase ASCII slug for this project name")
        return self


class ProjectPatch(APIModel):
    name: ProjectName | None = None
    description: ProjectDescription | None = None
    repository_url: HTTPURL | None = None

    @model_validator(mode="after")
    def editable_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one editable field")
        for field in ("name", "description"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ProjectRead(Timestamps):
    id: UUID
    name: str
    slug: str
    description: str
    repository_url: str | None


class ProjectSettingsPatch(APIModel):
    recall_pointer_template: RecallPointerTemplate | None


class ProjectSettingsRead(APIModel):
    project_id: UUID
    recall_pointer_template: str | None


class CheckpointPayload(APIModel):
    prompt: Prompt
    source_client: ClientName
    source_session_id: SessionID
    source_model: ModelName | None = None
    source_session_url: HTTPURL | None = None
    repository_branch: BranchName | None = None
    verified_against: CommitID | None = None
    affected_paths: AffectedPaths = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description=AFFECTED_PATHS_DESCRIPTION,
        examples=[["src/**", "tests/test_*.py"]],
    )
    tags: Tags = Field(default_factory=list)
    source_metadata: Metadata = Field(default_factory=dict)

    @field_validator("affected_paths")
    @classmethod
    def validate_affected_paths(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        return affected_paths_are_valid(value, info)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.lower() for tag in value))


class InitialCheckpointCreate(CheckpointPayload):
    pass


class CheckpointCreate(CheckpointPayload):
    kind: AppendCheckpointKind = "context"
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class CompletionCheckpointCreate(CheckpointPayload):
    pass


class InitialRelationshipCreate(APIModel):
    type: RelationshipType
    direction: Literal["incoming", "outgoing"]
    other_work_item_id: UUID
    context_checkpoint_id: UUID | None = None

    @model_validator(mode="after")
    def discovery_context(self) -> Self:
        if self.type == "discovered-from":
            if self.direction != "outgoing":
                raise ValueError(
                    "Initial discovered-from relationships must be outgoing from the new work item"
                )
            if self.context_checkpoint_id is None:
                raise ValueError("discovered-from requires context_checkpoint_id")
        return self


class WorkItemCreate(APIModel):
    title: Title
    summary: Summary
    priority: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    status: CreateStatus = "pending"
    initial_checkpoint: InitialCheckpointCreate
    initial_relationships: Annotated[list[InitialRelationshipCreate], Field(max_length=10)] = Field(
        default_factory=list
    )
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class RelationshipCreate(APIModel):
    relationship_type: RelationshipType
    source_work_item_id: UUID
    target_work_item_id: UUID
    created_by_client: ClientName
    created_by_session_id: SessionID
    created_by_model: ModelName | None = None
    context_checkpoint_id: UUID | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def relationship_rules(self) -> Self:
        if self.source_work_item_id == self.target_work_item_id:
            raise ValueError("A relationship cannot connect a work item to itself")
        if self.relationship_type == "discovered-from" and self.context_checkpoint_id is None:
            raise ValueError("discovered-from requires context_checkpoint_id")
        return self


class WorkItemPatch(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    title: Title | None = None
    summary: Summary | None = None
    priority: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    status: MutableStatus | None = None
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    actor: MutationActor | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def editable_fields(self) -> Self:
        fields = self.model_fields_set - {
            "expected_version",
            "lease_token",
            "actor",
            "client_operation_id",
        }
        if not fields:
            raise ValueError("Provide at least one editable field besides expected_version")
        for field in fields:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if self.client_operation_id is not None and self.actor is None:
            raise ValueError("actor is required when client_operation_id is present")
        return self


class WorkDeferralCreate(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    actor: MutationActor | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def keyed_operation_requires_actor(self) -> Self:
        if self.client_operation_id is not None and self.actor is None:
            raise ValueError("actor is required when client_operation_id is present")
        return self


class WorkCompletionCreate(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    checkpoint: CompletionCheckpointCreate
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class WorkDeletionCreate(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    actor: MutationActor | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def keyed_operation_requires_actor(self) -> Self:
        if self.client_operation_id is not None and self.actor is None:
            raise ValueError("actor is required when client_operation_id is present")
        return self


class WorkClaimCreate(APIModel):
    holder_client: ClientName
    holder_session_id: SessionID
    claim_request_id: ClaimRequestID


class LeaseTokenCreate(APIModel):
    lease_token: LeaseToken = Field(repr=False)


class LeaseReleaseCreate(APIModel):
    lease_token: LeaseToken = Field(repr=False)
    actor: MutationActor | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def keyed_operation_requires_actor(self) -> Self:
        if self.client_operation_id is not None and self.actor is None:
            raise ValueError("actor is required when client_operation_id is present")
        return self


class RelationshipRemovalCreate(APIModel):
    actor: MutationActor | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    @model_validator(mode="after")
    def keyed_operation_requires_actor(self) -> Self:
        if self.client_operation_id is not None and self.actor is None:
            raise ValueError("actor is required when client_operation_id is present")
        return self


class HumanGateRequestCreate(APIModel):
    gate_type: Literal["human"] = "human"
    question: GateText
    requested_by_client: ClientName
    requested_by_session_id: SessionID
    requested_by_model: ModelName | None = None
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class HumanGateContextRevision(APIModel):
    work_version: Annotated[StrictInt, Field(ge=1)]
    context_checkpoint_id: UUID
    relationship_event_count: Annotated[StrictInt, Field(ge=0)]


class MergeReviewRevision(APIModel):
    """Exact, commit-visible context reviewed before an irreversible merge."""

    work_version: Annotated[StrictInt, Field(ge=1)]
    context_checkpoint_id: UUID
    work_event_count: Annotated[StrictInt, Field(ge=1)]


class WorkMergeRequest(APIModel):
    """Domain fields shared by the public request and receipt-isolated service payload."""

    destination_work_item_id: UUID
    reviewed_source_revision: MergeReviewRevision
    reviewed_destination_revision: MergeReviewRevision
    rationale: MergeRationale
    merged_by_client: ClientName
    merged_by_session_id: SessionID
    merged_by_model: ModelName | None = None
    lease_token: LeaseToken | None = Field(default=None, repr=False)


class WorkMergeCreate(WorkMergeRequest):
    client_operation_id: UUID = Field(
        repr=False,
        description=REQUIRED_CLIENT_OPERATION_ID_DESCRIPTION,
    )


class HumanGateResolutionCreate(APIModel):
    resolution: GateText
    resolved_by_client: ClientName
    resolved_by_session_id: SessionID
    resolved_by_model: ModelName | None = None
    reviewed_context_revision: HumanGateContextRevision
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class HumanGateRead(APIModel):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    gate_type: Literal["human"]
    question: str
    requested_by_client: str
    requested_by_session_id: str
    requested_by_model: str | None
    requested_context_revision: HumanGateContextRevision
    created_at: datetime
    status: Literal["unresolved", "resolved"]
    current_context_revision: HumanGateContextRevision
    resolved_at: datetime | None
    resolution: str | None
    resolved_by_client: str | None
    resolved_by_session_id: str | None
    resolved_by_model: str | None
    resolved_context_revision: HumanGateContextRevision | None

    @computed_field(return_type=bool)
    @property
    def work_changed_since_request(self) -> bool:
        return (
            self.current_context_revision.work_version
            != self.requested_context_revision.work_version
        )

    @computed_field(return_type=bool)
    @property
    def context_checkpoint_changed_since_request(self) -> bool:
        return (
            self.current_context_revision.context_checkpoint_id
            != self.requested_context_revision.context_checkpoint_id
        )

    @computed_field(return_type=bool)
    @property
    def relationships_changed_since_request(self) -> bool:
        return (
            self.current_context_revision.relationship_event_count
            != self.requested_context_revision.relationship_event_count
        )

    @computed_field(return_type=bool)
    @property
    def context_changed_since_request(self) -> bool:
        return (
            self.work_changed_since_request
            or self.context_checkpoint_changed_since_request
            or self.relationships_changed_since_request
        )

    @computed_field(return_type=bool | None)
    @property
    def context_changed_at_resolution(self) -> bool | None:
        if self.resolved_context_revision is None:
            return None
        return self.resolved_context_revision != self.requested_context_revision

    @model_validator(mode="after")
    def enforce_gate_contract(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("Gate timestamps must include a UTC offset")

        required_resolution_values = (
            self.resolved_at,
            self.resolution,
            self.resolved_by_client,
            self.resolved_by_session_id,
            self.resolved_context_revision,
        )
        if self.status == "unresolved":
            if any(
                value is not None
                for value in (*required_resolution_values, self.resolved_by_model)
            ):
                raise ValueError("Unresolved gates cannot contain resolution fields")
            return self
        if any(value is None for value in required_resolution_values):
            raise ValueError("Resolved gates require complete resolution fields")
        assert self.resolved_at is not None
        assert self.resolution is not None
        assert self.resolved_by_client is not None
        assert self.resolved_by_session_id is not None
        assert self.resolved_context_revision is not None
        if self.resolved_at.tzinfo is None or self.resolved_at < self.created_at:
            raise ValueError("Gate resolution timestamps must be ordered UTC values")
        if (
            not self.resolution.strip()
            or not self.resolved_by_client.strip()
            or not self.resolved_by_session_id.strip()
        ):
            raise ValueError("Gate resolution provenance must be nonblank")
        return self

    @field_serializer("created_at", "resolved_at")
    def utc_gate_time(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkItemRead(Timestamps):
    id: UUID
    project_id: UUID
    title: str
    summary: str
    status: Status
    priority: int
    initial_checkpoint_id: UUID
    version: int


class CheckpointRead(APIModel):
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
    affected_paths: AffectedPaths = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        description=AFFECTED_PATHS_DESCRIPTION,
        examples=[["src/**", "tests/test_*.py"]],
    )
    tags: list[str]
    source_metadata: dict[str, JsonValue]
    migration_origin: MigrationOrigin | None
    legacy_record_id: UUID | None
    created_at: datetime

    @field_validator("affected_paths")
    @classmethod
    def validate_affected_paths(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        return affected_paths_are_valid(value, info)

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CheckpointPointer(APIModel):
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

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class LeasePublic(APIModel):
    holder_client: str
    holder_session_id: str
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime

    @field_serializer("acquired_at", "renewed_at", "expires_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ClaimReceipt(LeasePublic):
    work_item_id: UUID
    claim_request_id: str
    lease_token: str = Field(repr=False)


class Readiness(APIModel):
    lifecycle_status: Status
    is_duplicate: bool
    canonical_work_item_id: UUID
    is_terminal: bool
    has_active_lease: bool
    has_dropped_lease: bool
    active_lease: LeasePublic | None
    unresolved_blocker_count: int
    is_blocked: bool
    unresolved_gate_count: int
    is_gated: bool
    is_ready: bool
    display_state: Literal[
        "pending",
        "active",
        "dropped",
        "blocked",
        "waiting",
        "duplicate",
        "deferred",
        "done",
        "wont-do",
        "promoted",
    ]


class WorkIdentityPointer(APIModel):
    id: UUID
    title: str
    status: Status


class CanonicalWorkProjection(APIModel):
    is_duplicate: StrictBool
    direct_destination: WorkIdentityPointer | None
    canonical_work_item: WorkIdentityPointer
    path: list[WorkIdentityPointer] = Field(max_length=50)
    duplicate_member_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def canonical_path_is_coherent(self) -> Self:
        ids = [item.id for item in self.path]
        if len(ids) != len(set(ids)):
            raise ValueError("A canonical path cannot repeat a work item")
        if not self.is_duplicate:
            if self.direct_destination is not None or self.path:
                raise ValueError("A canonical root cannot have a destination path")
            return self
        if (
            self.direct_destination is None
            or not self.path
            or self.duplicate_member_count < len(self.path)
        ):
            raise ValueError("A duplicate requires a direct destination and path")
        if self.path[0] != self.direct_destination:
            raise ValueError("The canonical path must begin with the direct destination")
        if self.path[-1] != self.canonical_work_item:
            raise ValueError("The canonical path must end with the canonical work item")
        return self


class WorkItemDetailRead(APIModel):
    work_item: WorkItemRead
    canonical: CanonicalWorkProjection

    @model_validator(mode="after")
    def requested_identity_is_coherent(self) -> Self:
        requested = WorkIdentityPointer(
            id=self.work_item.id,
            title=self.work_item.title,
            status=self.work_item.status,
        )
        if not self.canonical.is_duplicate and self.canonical.canonical_work_item != requested:
            raise ValueError("A canonical root must point to itself")
        if (
            self.canonical.is_duplicate
            and self.work_item.id in {item.id for item in self.canonical.path}
        ):
            raise ValueError("A duplicate path cannot contain its requested source")
        return self


class RelationshipEdgeRead(APIModel):
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

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkPointer(APIModel):
    id: UUID
    title: str
    status: Status
    readiness: Readiness


class AdjacentRelationshipRead(APIModel):
    relationship: RelationshipEdgeRead
    relative_to_work_item_id: UUID
    direction: Literal["incoming", "outgoing", "undirected"]
    counterpart: WorkPointer


class RelationshipCreationResult(APIModel):
    relationship: RelationshipEdgeRead
    created: bool


class RelationshipRemovalResult(APIModel):
    project_id: UUID
    relationship_id: UUID
    removed: bool


class WorkItemPointer(APIModel):
    id: UUID
    title: str
    status: Status
    priority: int
    version: int
    updated_at: datetime

    @field_serializer("updated_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkSummaryMinimal(APIModel):
    """The cheapest shape that still supports choosing between work items."""

    work_item: WorkItemPointer
    checkpoint_count: int
    display_state: Literal[
        "pending",
        "active",
        "dropped",
        "blocked",
        "waiting",
        "deferred",
        "done",
        "wont-do",
        "promoted",
    ] = Field(
        description="readiness.display_state; request view=full for the whole readiness object."
    )


class ReadyWorkPage(APIModel):
    items: list[WorkSummaryMinimal]
    total: int
    limit: int
    offset: int


class WorkSummary(APIModel):
    work_item: WorkItemRead
    checkpoint_count: int
    ancestor_path: list[WorkIdentityPointer] = Field(default_factory=list)
    ancestor_path_truncated: bool = False
    current_context: CheckpointPointer
    readiness: Readiness


class WorkSearchHit(APIModel):
    summary: WorkSummary
    matched_member: WorkIdentityPointer


DuplicateSuggestionSignal = Literal["exact_title", "lexical", "semantic"]
DuplicateSuggestionMode = Literal["hybrid_full", "hybrid_shortlist", "lexical"]
DuplicateSuggestionSemanticScope = Literal[
    "full_project", "lexical_shortlist", "unavailable"
]
_DUPLICATE_SUGGESTION_SIGNAL_ORDER = ("exact_title", "lexical", "semantic")


class DuplicateSuggestionRequest(APIModel):
    """An ephemeral create draft; no authority or mutation control is accepted."""

    title: Title
    summary: Summary
    initial_prompt: Prompt
    tags: Tags = Field(default_factory=list)
    exclude_work_item_id: UUID | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 5

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.lower() for tag in value))


class DuplicateCandidateSummary(APIModel):
    work_item_id: UUID
    title: StoredTitle
    summary: StoredSummary
    status: Status
    updated_at: datetime
    duplicate_member_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def aware_timestamp(self) -> Self:
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() != timedelta(0):
            raise ValueError("Candidate timestamps must use UTC")
        return self

    @field_serializer("updated_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class DuplicateSuggestion(APIModel):
    canonical_work: DuplicateCandidateSummary
    matched_member: WorkIdentityPointer
    rank: Annotated[StrictInt, Field(ge=1, le=10)]
    signals: list[DuplicateSuggestionSignal] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def ordered_unique_signals(self) -> Self:
        expected = sorted(
            set(self.signals),
            key=_DUPLICATE_SUGGESTION_SIGNAL_ORDER.index,
        )
        if self.signals != expected:
            raise ValueError("Suggestion signals must be unique and in canonical order")
        if self.matched_member.id == self.canonical_work.work_item_id and (
            self.matched_member.title != self.canonical_work.title
            or self.matched_member.status != self.canonical_work.status
        ):
            raise ValueError("A root match must identify the canonical candidate exactly")
        if (
            self.matched_member.id != self.canonical_work.work_item_id
            and self.canonical_work.duplicate_member_count == 0
        ):
            raise ValueError("An alias match requires a duplicate group member")
        return self


class DuplicateSuggestionPage(APIModel):
    items: list[DuplicateSuggestion] = Field(max_length=10)
    limit: Annotated[StrictInt, Field(ge=1, le=10)]
    mode: DuplicateSuggestionMode
    semantic_available: StrictBool
    semantic_scope: DuplicateSuggestionSemanticScope
    composition_version: Literal["duplicate-suggestion-v1"]
    exact_title_group_total: Annotated[StrictInt, Field(ge=0)]
    omitted_exact_title_group_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def page_is_coherent(self) -> Self:
        self._require_rank_and_group_identity()
        self._require_semantic_mode()
        self._require_exact_lane()
        return self

    def _require_rank_and_group_identity(self) -> None:
        if len(self.items) > self.limit:
            raise ValueError("Suggestion items cannot exceed the requested limit")
        if [item.rank for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Suggestion ranks must be contiguous and ordered")
        root_ids = [item.canonical_work.work_item_id for item in self.items]
        if len(root_ids) != len(set(root_ids)):
            raise ValueError("Suggestion canonical groups must be unique")
        member_ids = [item.matched_member.id for item in self.items]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("Suggestion matched members must be unique")

    def _require_semantic_mode(self) -> None:
        expected = {
            "hybrid_full": (True, "full_project"),
            "hybrid_shortlist": (True, "lexical_shortlist"),
            "lexical": (False, "unavailable"),
        }[self.mode]
        if (self.semantic_available, self.semantic_scope) != expected:
            raise ValueError("Suggestion mode and semantic scope are inconsistent")
        if not self.semantic_available and any(
            "semantic" in item.signals for item in self.items
        ):
            raise ValueError("Lexical fallback cannot report semantic evidence")

    def _require_exact_lane(self) -> None:
        visible_exact = min(self.exact_title_group_total, self.limit)
        if len(self.items) < visible_exact:
            raise ValueError("Exact-title groups must fill available response slots first")
        actual_exact = sum("exact_title" in item.signals for item in self.items)
        if actual_exact != visible_exact:
            raise ValueError("Exact-title group totals are inconsistent")
        if any(
            "exact_title" not in item.signals for item in self.items[:visible_exact]
        ) or any("exact_title" in item.signals for item in self.items[visible_exact:]):
            raise ValueError("Exact-title suggestions must form the response prefix")
        if self.omitted_exact_title_group_count != self.exact_title_group_total - visible_exact:
            raise ValueError("Omitted exact-title group count is inconsistent")


class HumanAttentionItem(APIModel):
    gate: HumanGateRead
    summary: WorkSummary


class HumanAttentionPage(APIModel):
    items: list[HumanAttentionItem]
    total: int
    limit: int
    next_cursor: str | None


class HumanGatePage(APIModel):
    items: list[HumanGateRead]
    total: int
    limit: int
    next_cursor: str | None


class HierarchyPresentation(APIModel):
    direct_child_count: int
    descendant_count: int
    blocked_descendant_count: int
    active_descendant_count: int
    completed_descendant_count: int
    discovered_descendant_count: int
    branch_unresolved_human_gate_count: int
    branch_merged_duplicate_count: int
    is_discovered_work: bool
    discovered_from_parent: bool
    next_active_descendant_lease_expires_at: datetime | None

    @field_serializer("next_active_descendant_lease_expires_at")
    def utc_expiry(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class HierarchySummary(APIModel):
    summary: WorkSummary
    self_matches_filter: bool
    has_matching_descendants: bool
    presentation: HierarchyPresentation


class WorkCreation(APIModel):
    work_item: WorkItemRead
    initial_checkpoint: CheckpointRead
    initial_relationships: list[RelationshipEdgeRead] = Field(default_factory=list)


class WorkMergeRead(APIModel):
    id: UUID
    merge_sequence: Annotated[StrictInt, Field(ge=1)]
    project_id: UUID
    source_work_item_id: UUID
    destination_work_item_id: UUID
    duplicate_relationship_id: UUID
    reviewed_source_revision: MergeReviewRevision
    reviewed_destination_revision: MergeReviewRevision
    resulting_source_work_version: Annotated[StrictInt, Field(ge=2)]
    resulting_destination_work_version: Annotated[StrictInt, Field(ge=2)]
    rationale: str
    merged_by_client: str
    merged_by_session_id: str
    merged_by_model: str | None
    created_at: datetime

    @model_validator(mode="after")
    def merge_fact_is_coherent(self) -> Self:
        if self.source_work_item_id == self.destination_work_item_id:
            raise ValueError("A merge requires distinct endpoints")
        if self.resulting_source_work_version != self.reviewed_source_revision.work_version + 1:
            raise ValueError("Source merge version is inconsistent")
        if (
            self.resulting_destination_work_version
            != self.reviewed_destination_revision.work_version + 1
        ):
            raise ValueError("Destination merge version is inconsistent")
        if self.created_at.tzinfo is None:
            raise ValueError("Merge timestamps must include a UTC offset")
        return self

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class RelationshipCounts(APIModel):
    incoming: int = 0
    outgoing: int = 0
    undirected: int = 0
    total: int = 0


class DuplicateMergeEligibility(APIModel):
    incident_blocks_count: Annotated[StrictInt, Field(ge=0)]
    incident_parent_child_count: Annotated[StrictInt, Field(ge=0)]
    has_unresolved_gate: StrictBool
    source_lease_state: Literal["none", "expired", "active"]


EventBody = Annotated[
    str, StringConstraints(min_length=1, max_length=4000), AfterValidator(nonblank)
]


class ProgressEventCreate(APIModel):
    event_type: Literal["progress"] = "progress"
    body: EventBody
    metadata: ProgressRequestMetadata = Field(default_factory=dict)
    actor: MutationActor
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    client_operation_id: UUID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )


class EmptyEventMetadata(APIModel):
    pass


class WorkSnapshot(APIModel):
    title: Title
    summary: Summary
    status: EventCreateStatus
    priority: Annotated[StrictInt, Field(ge=0, le=100)]
    version: Literal[1]


class WorkCreatedLiveMetadata(APIModel):
    initial: WorkSnapshot


class TitleChange(APIModel):
    before: Title
    after: Title


class SummaryChange(APIModel):
    before: Summary
    after: Summary


class PriorityChange(APIModel):
    before: Annotated[StrictInt, Field(ge=0, le=100)]
    after: Annotated[StrictInt, Field(ge=0, le=100)]


class StatusChange(APIModel):
    before: EventStatus
    after: EventStatus


class WorkChangeSet(APIModel):
    title: TitleChange | None = None
    summary: SummaryChange | None = None
    priority: PriorityChange | None = None
    status: StatusChange | None = None

    @model_validator(mode="after")
    def require_nonempty(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Event changes must not be empty")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Event changes cannot contain null values")
        return self

    @model_serializer(mode="wrap")
    def serialize_set_fields(self, handler):
        serialized = handler(self)
        return {field: serialized[field] for field in self.model_fields_set}


class WorkUpdatedMetadata(APIModel):
    changes: WorkChangeSet
    work_version: Annotated[StrictInt, Field(ge=1)]


class WorkStatusMetadata(APIModel):
    from_status: EventStatus
    to_status: EventStatus
    changes: WorkChangeSet
    work_version: Annotated[StrictInt, Field(ge=1)]


def event_datetime_is_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Event metadata timestamps must be UTC")
    return value


UTCEventDateTime = Annotated[datetime, AfterValidator(event_datetime_is_utc)]


class WorkClaimedLiveMetadata(APIModel):
    expires_at: UTCEventDateTime


class WorkClaimedBackfillMetadata(APIModel):
    observed_expires_at: UTCEventDateTime
    expiry_basis: Literal["retained_lease_at_cutover"]


class WorkReleasedClientMetadata(APIModel):
    lease_holder_kind: Literal["client"]
    lease_holder_client: ReleasedLeaseHolderClient
    lease_holder_session_id: ReleasedLeaseHolderSessionID


class WorkReleasedUnattributedMetadata(APIModel):
    lease_holder_kind: Literal["unattributed"]


class CheckpointAddedMetadata(APIModel):
    checkpoint_kind: AppendCheckpointKind


class RelationshipEventMetadata(APIModel):
    relationship_type: RelationshipType


class HumanGateEventMetadata(APIModel):
    gate_id: UUID
    gate_type: Literal["human"]


class WorkMergedMetadata(APIModel):
    merge_id: UUID
    source_work_item_id: UUID
    destination_work_item_id: UUID
    role: Literal["source", "destination"]
    source_work_version: Annotated[StrictInt, Field(ge=2)]
    destination_work_version: Annotated[StrictInt, Field(ge=2)]

    @model_validator(mode="after")
    def endpoints_are_distinct(self) -> Self:
        if self.source_work_item_id == self.destination_work_item_id:
            raise ValueError("Merge event endpoints must be distinct")
        return self


class WorkCompletedLiveMetadata(APIModel):
    from_status: Literal["open", "pending"]
    to_status: Literal["done"]
    work_version: Annotated[StrictInt, Field(ge=1)]


class WorkDeletedMetadata(APIModel):
    final_status: EventStatus
    final_version: Annotated[StrictInt, Field(ge=1)]


class ProgressEventMetadata(RootModel[EventMetadata]):
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
    | HumanGateEventMetadata
    | WorkMergedMetadata
    | WorkCompletedLiveMetadata
    | WorkDeletedMetadata
    | ProgressEventMetadata
)


def _event_metadata_payload(metadata: WorkEventMetadata) -> dict[str, JsonValue]:
    if isinstance(metadata, ProgressEventMetadata):
        return metadata.root
    return metadata.model_dump(mode="json")


# Event families. Each set names the event types one contract rule applies to.
_LIVE_CLIENT_EVENTS = frozenset(
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
_BACKFILL_EVENTS = frozenset(
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
_GATE_EVENTS = frozenset({"human_attention_requested", "human_attention_resolved"})
_TEXT_EVENTS = frozenset({"progress", "work_merged", *_GATE_EVENTS})
_CHECKPOINT_EVENTS = frozenset({"work_created", "checkpoint_added", "work_completed"})
_LEASE_EVENTS = frozenset({"work_claimed", "work_released"})
_LIFECYCLE_EVENTS = frozenset({"work_status_changed", "work_reopened"})
_RELATIONSHIP_EVENTS = frozenset(
    {
        "dependency_added",
        "dependency_removed",
        "relationship_added",
        "relationship_removed",
    }
)

# Metadata shapes keyed by (event type, origin): live events carry the typed
# snapshot, while backfilled history keeps only what the legacy tables recorded.
_ORIGIN_METADATA_TYPES: dict[tuple[str, str], type[WorkEventMetadata]] = {
    ("work_created", "live"): WorkCreatedLiveMetadata,
    ("work_created", "backfill"): EmptyEventMetadata,
    ("work_claimed", "live"): WorkClaimedLiveMetadata,
    ("work_claimed", "backfill"): WorkClaimedBackfillMetadata,
    ("work_completed", "live"): WorkCompletedLiveMetadata,
    ("work_completed", "backfill"): EmptyEventMetadata,
}
# Metadata shapes that depend on the event type alone.
_PLAIN_METADATA_TYPES: dict[str, type[WorkEventMetadata]] = {
    "checkpoint_added": CheckpointAddedMetadata,
    "progress": ProgressEventMetadata,
    "human_attention_requested": HumanGateEventMetadata,
    "human_attention_resolved": HumanGateEventMetadata,
    "work_merged": WorkMergedMetadata,
    "work_deleted": WorkDeletedMetadata,
}


def _work_updated_metadata(payload: dict[str, JsonValue]) -> WorkUpdatedMetadata:
    parsed = WorkUpdatedMetadata.model_validate(payload)
    status_change = parsed.changes.status
    if status_change is not None and status_change.before != status_change.after:
        raise ValueError("Ordinary work updates cannot change status")
    return parsed


def _lifecycle_metadata(event_type: str, payload: dict[str, JsonValue]) -> WorkStatusMetadata:
    parsed = WorkStatusMetadata.model_validate(payload)
    status_change = parsed.changes.status
    if status_change is None:
        raise ValueError("Lifecycle events require a typed status change")
    if (status_change.before, status_change.after) != (parsed.from_status, parsed.to_status):
        raise ValueError("Lifecycle event status metadata is inconsistent")
    if event_type == "work_status_changed" and (
        parsed.from_status not in {"open", "pending"}
        or parsed.to_status not in {"deferred", "wont-do", "promoted"}
    ):
        raise ValueError("Status-change events must leave pending work")
    if event_type == "work_reopened" and (
        parsed.to_status not in {"open", "pending"} or parsed.from_status in {"open", "pending"}
    ):
        raise ValueError("Reopen events must return held or terminal work to pending")
    return parsed


def _work_released_metadata(
    payload: dict[str, JsonValue],
) -> WorkReleasedClientMetadata | WorkReleasedUnattributedMetadata:
    if payload.get("lease_holder_kind") == "client":
        return WorkReleasedClientMetadata.model_validate(payload)
    return WorkReleasedUnattributedMetadata.model_validate(payload)


class WorkEventRead(APIModel):
    id: int
    project_id: UUID
    work_item_id: UUID
    event_type: EventType
    actor_kind: Literal["client", "unattributed"]
    actor_client: str | None
    actor_session_id: str | None
    actor_model: str | None
    body: str | None
    checkpoint_id: UUID | None
    lease_generation_id: UUID | None
    lease_release_id: UUID | None
    relationship_id: UUID | None
    relationship_source_work_item_id: UUID | None
    relationship_target_work_item_id: UUID | None
    relationship_context_checkpoint_work_item_id: UUID | None
    relationship_context_checkpoint_id: UUID | None
    relationship_direction: Literal["incoming", "outgoing", "undirected"] | None
    counterpart_work_item_id: UUID | None
    metadata_version: Literal[1]
    metadata: WorkEventMetadata
    origin: Literal["live", "backfill"]
    created_at: datetime

    @model_validator(mode="after")
    def enforce_event_contract(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("Event timestamps must include a UTC offset")
        self._require_actor_provenance()
        self._require_origin_rules()
        self._require_body_rules()
        self._require_reference_columns()
        self._require_relationship_projection()
        self.metadata = self._typed_metadata()
        self._require_merge_projection()
        return self

    def _require_merge_projection(self) -> None:
        if self.event_type != "work_merged":
            return
        if not isinstance(self.metadata, WorkMergedMetadata):
            raise ValueError("Merge events require typed merge metadata")
        expected_work_item_id = (
            self.metadata.source_work_item_id
            if self.metadata.role == "source"
            else self.metadata.destination_work_item_id
        )
        if self.work_item_id != expected_work_item_id:
            raise ValueError("Merge event role does not match its work item")

    def _require_actor_provenance(self) -> None:
        actor_values = (self.actor_client, self.actor_session_id, self.actor_model)
        if self.actor_kind != "client":
            if any(value is not None for value in actor_values):
                raise ValueError("Unattributed events cannot contain actor provenance")
            return
        if self.actor_client is None or self.actor_session_id is None:
            raise ValueError("Client events require client and session provenance")
        if not self.actor_client.strip() or not self.actor_session_id.strip():
            raise ValueError("Client event provenance must be nonblank")
        if self.actor_model is not None and not self.actor_model.strip():
            raise ValueError("Client event model provenance must be nonblank")

    def _require_origin_rules(self) -> None:
        if self.origin == "live":
            if self.event_type in _LIVE_CLIENT_EVENTS and self.actor_kind != "client":
                raise ValueError("This live event requires client provenance")
            return
        if self.event_type not in _BACKFILL_EVENTS:
            raise ValueError("This event type cannot be backfilled")
        if self.event_type == "work_deleted" and self.actor_kind != "unattributed":
            raise ValueError("Backfilled deletion events must be unattributed")

    def _require_body_rules(self) -> None:
        if self.event_type not in _TEXT_EVENTS:
            if self.body is not None:
                raise ValueError("Server-reserved event types cannot contain a body")
            return
        if self.origin != "live" or self.body is None:
            raise ValueError("Text events require a live body")
        if not 1 <= len(self.body) <= 4000 or not self.body.strip() or "\x00" in self.body:
            raise ValueError("Text event bodies must be bounded nonblank text")

    def _require_reference_columns(self) -> None:
        if (self.checkpoint_id is not None) != (self.event_type in _CHECKPOINT_EVENTS):
            raise ValueError("Checkpoint event references do not match the event type")
        if (self.lease_generation_id is not None) != (self.event_type in _LEASE_EVENTS):
            raise ValueError("Lease generation references do not match the event type")
        if (self.lease_release_id is not None) != (self.event_type == "work_released"):
            raise ValueError("Lease release references do not match the event type")

    def _require_relationship_projection(self) -> None:
        relationship_values = (
            self.relationship_id,
            self.relationship_source_work_item_id,
            self.relationship_target_work_item_id,
            self.relationship_direction,
            self.counterpart_work_item_id,
        )
        context_values = (
            self.relationship_context_checkpoint_work_item_id,
            self.relationship_context_checkpoint_id,
        )
        if self.event_type not in _RELATIONSHIP_EVENTS:
            if any(value is not None for value in (*relationship_values, *context_values)):
                raise ValueError("Non-relationship events cannot contain relationship references")
            return
        if any(value is None for value in relationship_values):
            raise ValueError("Relationship events require the complete endpoint projection")
        source_work_item_id, target_work_item_id = self._relationship_endpoints()
        if source_work_item_id == target_work_item_id:
            raise ValueError("Relationship endpoints must be distinct")
        if self.work_item_id not in {source_work_item_id, target_work_item_id}:
            raise ValueError("Relationship event work item must be an endpoint")
        expected_counterpart = (
            target_work_item_id if self.work_item_id == source_work_item_id else source_work_item_id
        )
        if self.counterpart_work_item_id != expected_counterpart:
            raise ValueError("Relationship event counterpart is inconsistent")
        self._require_relationship_context(source_work_item_id, target_work_item_id)

    def _require_relationship_context(
        self, source_work_item_id: UUID, target_work_item_id: UUID
    ) -> None:
        context_owner = self.relationship_context_checkpoint_work_item_id
        if (context_owner is None) != (self.relationship_context_checkpoint_id is None):
            raise ValueError("Relationship context references must be paired")
        if context_owner is not None and context_owner not in {
            source_work_item_id,
            target_work_item_id,
        }:
            raise ValueError("Relationship context owner must be an endpoint")

    def _relationship_endpoints(self) -> tuple[UUID, UUID]:
        source_work_item_id = self.relationship_source_work_item_id
        target_work_item_id = self.relationship_target_work_item_id
        assert source_work_item_id is not None
        assert target_work_item_id is not None
        return source_work_item_id, target_work_item_id

    def _typed_metadata(self) -> WorkEventMetadata:
        payload = _event_metadata_payload(self.metadata)
        origin_shape = _ORIGIN_METADATA_TYPES.get((self.event_type, self.origin))
        if origin_shape is not None:
            return origin_shape.model_validate(payload)
        if self.event_type == "work_updated":
            return _work_updated_metadata(payload)
        if self.event_type in _LIFECYCLE_EVENTS:
            return _lifecycle_metadata(self.event_type, payload)
        if self.event_type == "work_released":
            return _work_released_metadata(payload)
        if self.event_type in _RELATIONSHIP_EVENTS:
            return self._relationship_metadata(payload)
        plain_shape = _PLAIN_METADATA_TYPES.get(self.event_type)
        if plain_shape is None:  # pragma: no cover - EventType keeps this exhaustive
            raise ValueError("Unknown event type")
        return plain_shape.model_validate(payload)

    def _relationship_metadata(self, payload: dict[str, JsonValue]) -> RelationshipEventMetadata:
        parsed = RelationshipEventMetadata.model_validate(payload)
        is_dependency = self.event_type.startswith("dependency_")
        if is_dependency != (parsed.relationship_type == "blocks"):
            raise ValueError("Relationship event family does not match its type")
        source_work_item_id, target_work_item_id = self._relationship_endpoints()
        if parsed.relationship_type == "related":
            if self.relationship_direction != "undirected":
                raise ValueError("Related events must be projected as undirected")
            if source_work_item_id >= target_work_item_id:
                raise ValueError("Related event endpoints must be normalized")
        else:
            expected_direction = (
                "incoming" if self.work_item_id == target_work_item_id else "outgoing"
            )
            if self.relationship_direction != expected_direction:
                raise ValueError("Directed relationship event direction is inconsistent")
        if parsed.relationship_type == "discovered-from" and (
            self.relationship_context_checkpoint_id is None
            or self.relationship_context_checkpoint_work_item_id != target_work_item_id
        ):
            raise ValueError("Discovered-from events require target-owned context")
        return parsed

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class WorkEventPage(APIModel):
    items: list[WorkEventRead]
    total: int
    limit: int
    offset: int
    pre_phase5_history_may_be_incomplete: bool


class WorkMergeResult(APIModel):
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
    def merge_result_is_coherent(self) -> Self:
        self._require_endpoints()
        self._require_supporting_relationship()
        self._require_relationship_events()
        self._require_merge_events()
        event_ids = [
            *(event.id for event in self.relationship_events),
            *(event.id for event in self.merge_events),
        ]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Merge result events must have distinct identities")
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
            raise ValueError("Merge result endpoint snapshots are incoherent")
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
            raise ValueError("A fresh merge must point directly to its canonical destination")

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
            raise ValueError("Merge supporting relationship is incoherent")
        if self.supporting_relationship_created and (
            edge.created_by_client != merge.merged_by_client
            or edge.created_by_session_id != merge.merged_by_session_id
            or edge.created_by_model != merge.merged_by_model
            or edge.created_at != merge.created_at
            or edge.context_checkpoint_work_item_id is not None
            or edge.context_checkpoint_id is not None
        ):
            raise ValueError("A merge-created relationship must share merge provenance")

    def _require_relationship_events(self) -> None:
        expected_count = 2 if self.supporting_relationship_created else 0
        if len(self.relationship_events) != expected_count:
            raise ValueError("Merge relationship event count is inconsistent")
        if not self.relationship_events:
            return
        expected_work_ids = [
            self.merge.source_work_item_id,
            self.merge.destination_work_item_id,
        ]
        expected_directions = ["outgoing", "incoming"]
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
                or event.relationship_id != edge.id
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
                raise ValueError("Merge relationship event ordering is incoherent")

    def _require_merge_events(self) -> None:
        expected_work_ids = [self.merge.source_work_item_id, self.merge.destination_work_item_id]
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
                raise ValueError("Merge event ordering or provenance is incoherent")


class WorkContext(APIModel):
    work_item: WorkItemRead
    merge_review_revision: MergeReviewRevision
    canonical: CanonicalWorkProjection
    duplicate_members: list[WorkIdentityPointer] = Field(max_length=20)
    duplicate_member_total: Annotated[StrictInt, Field(ge=0)]
    omitted_duplicate_member_count: Annotated[StrictInt, Field(ge=0)]
    initial_checkpoint: CheckpointRead
    current_context: CheckpointRead | None = Field(
        description=(
            "The newest context checkpoint, or null when it is the initial checkpoint. "
            "When current_context_is_initial is true, read initial_checkpoint instead; "
            "the body is never serialized twice."
        )
    )
    current_context_is_initial: bool = Field(
        description="True when the newest context checkpoint is the initial checkpoint."
    )
    recent_checkpoints: list[CheckpointRead] = Field(
        description=(
            "Older checkpoints for context, newest first. Never repeats the checkpoint "
            "returned as initial_checkpoint or current_context."
        )
    )
    checkpoint_total: int = Field(
        description="Every checkpoint on this work item, including those not returned here."
    )
    omitted_checkpoint_count: int = Field(
        description=(
            "checkpoint_total minus the distinct checkpoints in this payload. Page the "
            "remainder with list_checkpoints."
        )
    )
    readiness: Readiness
    unresolved_gates: list[HumanGateRead]
    unresolved_gate_total: int
    omitted_unresolved_gate_count: int
    recent_resolved_gates: list[HumanGateRead]
    resolved_gate_total: int
    omitted_resolved_gate_count: int
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
    omitted_relationship_counts: RelationshipCounts = Field(default_factory=RelationshipCounts)
    duplicate_merge_eligibility: DuplicateMergeEligibility
    recent_events: list[WorkEventRead]
    event_total: int
    omitted_event_count: int
    pre_phase5_history_may_be_incomplete: bool

    @model_validator(mode="after")
    def merge_revision_is_coherent(self) -> Self:
        if self.merge_review_revision.work_version != self.work_item.version:
            raise ValueError("Merge review work version does not match the requested item")
        current_checkpoint_id = (
            self.initial_checkpoint.id
            if self.current_context_is_initial
            else self.current_context.id if self.current_context is not None else None
        )
        if current_checkpoint_id != self.merge_review_revision.context_checkpoint_id:
            raise ValueError("Merge review checkpoint does not match current context")
        if self.merge_review_revision.work_event_count != self.event_total:
            raise ValueError("Merge review event count does not match event total")
        return self

    @model_validator(mode="after")
    def duplicate_member_totals_are_coherent(self) -> Self:
        member_ids = [member.id for member in self.duplicate_members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("Duplicate members must be unique")
        if self.canonical.canonical_work_item.id in member_ids:
            raise ValueError("Duplicate members cannot contain the canonical root")
        if self.canonical.is_duplicate and (
            not member_ids or member_ids[0] != self.work_item.id
        ):
            raise ValueError("Requested duplicate must be the first duplicate member")
        if self.duplicate_member_total != len(member_ids) + self.omitted_duplicate_member_count:
            raise ValueError("Duplicate member totals are inconsistent")
        if self.duplicate_member_total != self.canonical.duplicate_member_count:
            raise ValueError("Canonical and context duplicate member totals differ")
        return self

    @model_validator(mode="after")
    def relationship_totals_are_coherent(self) -> Self:
        relationship_groups = {
            "incoming": self.incoming_relationships,
            "outgoing": self.outgoing_relationships,
            "undirected": self.undirected_relationships,
        }
        for direction, relationships in relationship_groups.items():
            visible = len(relationships)
            total = getattr(self.relationship_counts, direction)
            omitted = getattr(self.omitted_relationship_counts, direction)
            if total != visible + omitted:
                raise ValueError(f"{direction.title()} relationship totals are inconsistent")
        visible_total = sum(len(items) for items in relationship_groups.values())
        if self.relationship_counts.total != visible_total + self.omitted_relationship_counts.total:
            raise ValueError("Overall relationship totals are inconsistent")
        if self.relationship_counts.total != (
            self.relationship_counts.incoming
            + self.relationship_counts.outgoing
            + self.relationship_counts.undirected
        ):
            raise ValueError("Relationship category totals do not sum to the overall total")
        if self.omitted_relationship_counts.total != (
            self.omitted_relationship_counts.incoming
            + self.omitted_relationship_counts.outgoing
            + self.omitted_relationship_counts.undirected
        ):
            raise ValueError("Omitted relationship totals do not sum to the overall omission")
        return self

    @model_validator(mode="after")
    def readiness_projection_is_coherent(self) -> Self:
        if self.readiness.canonical_work_item_id != self.canonical.canonical_work_item.id:
            raise ValueError("Readiness and canonical projections disagree")
        return self


class ClaimAndRecall(APIModel):
    lease: ClaimReceipt
    context: WorkContext


class ReleaseResult(APIModel):
    work_item_id: UUID
    released: bool


class WorkCompletionRead(APIModel):
    work_item: WorkItemRead
    checkpoint: CheckpointRead


class WorkDeletionRead(APIModel):
    deleted: bool = True
    project_id: UUID
    work_item_id: UUID
    version: int


class Page[T](APIModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProjectListQuery(APIModel):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkItemListQuery(APIModel):
    q: Annotated[str, StringConstraints(max_length=500), AfterValidator(no_nul)] | None = None
    semantic: bool = False
    status: Literal[
        "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
    ] = (
        "pending"
    )
    sort: Literal["updated", "created", "priority"] = "updated"
    tag: Tag | None = None
    source_client: ClientName | None = None
    source_session_id: SessionID | None = None
    view: Literal["full", "roots"] = "full"
    duplicate_scope: Literal["canonical", "aliases", "all"] = "canonical"
    canonical_work_item_id: UUID | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @model_validator(mode="after")
    def query_view_rules(self) -> Self:
        query = (self.q or "").strip()
        if self.semantic and not query:
            raise ValueError("semantic=true requires a nonblank q")
        if self.view == "roots" and query:
            raise ValueError("A nonblank q requires view=full")
        if self.view == "roots" and self.duplicate_scope != "canonical":
            raise ValueError("Hierarchy roots require duplicate_scope=canonical")
        if self.canonical_work_item_id is not None and self.duplicate_scope == "canonical":
            raise ValueError("canonical_work_item_id requires duplicate_scope=aliases or all")
        return self


class ReadyWorkListQuery(APIModel):
    min_priority: int = Field(default=0, ge=0, le=100)
    tag: ReadyTagFilter | None = None
    parent_work_item_id: UUID | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RelationshipListQuery(APIModel):
    direction: Literal["incoming", "outgoing", "undirected", "both"] = "both"
    type: RelationshipType | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ChildrenListQuery(APIModel):
    status: Literal[
        "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
    ] = "pending"
    sort: Literal["updated", "created", "priority"] = "updated"
    tag: Tag | None = None
    source_client: ClientName | None = None
    source_session_id: SessionID | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("tag")
    @classmethod
    def normalize_tag_filter(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class CheckpointListQuery(APIModel):
    order: Literal["oldest", "newest"] = "oldest"
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkEventListQuery(APIModel):
    order: Literal["oldest", "newest"] = "oldest"
    event_type: EventType | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkContextQuery(APIModel):
    recent_limit: int = Field(default=5, ge=0, le=20)
    recent_event_limit: int = Field(default=10, ge=0, le=20)


class HumanAttentionListQuery(APIModel):
    work_item_id: UUID | None = None
    limit: int = Field(default=30, ge=0, le=100)
    cursor: GateCursor | None = None

    @model_validator(mode="after")
    def count_mode_has_no_cursor(self) -> Self:
        if self.limit == 0 and self.cursor is not None:
            raise ValueError("limit=0 does not accept a cursor")
        return self


class HumanGateListQuery(APIModel):
    status: Literal["all", "unresolved", "resolved"] = "all"
    limit: int = Field(default=30, ge=1, le=100)
    cursor: GateCursor | None = None
