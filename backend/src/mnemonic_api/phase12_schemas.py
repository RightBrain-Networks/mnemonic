"""Bounded activity and human-report contracts, independent of work model graphs."""

import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_serializer,
    model_validator,
)

MAX_SEQUENCE = 2**63 - 1
BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f"
)


def decimal_sequence(value: str) -> str:
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value:
        raise ValueError("Expected a canonical decimal string")
    if int(value) > MAX_SEQUENCE:
        raise ValueError("Sequence exceeds the supported range")
    return value


def positive_revision(value: str) -> str:
    if value == "0":
        raise ValueError("Revision must be positive")
    return value


def report_text(value: str, *, multiline: bool = False) -> str:
    if not value.strip():
        raise ValueError("Text must not be blank")
    value.encode("utf-8")
    for character in value:
        if character in BIDI_CONTROLS:
            raise ValueError("Directional formatting controls are not supported")
        if multiline and character in "\r\n\t":
            continue
        if unicodedata.category(character) in {"Cc", "Cs"}:
            raise ValueError("Control characters are not supported")
        if not multiline and character in "\u2028\u2029":
            raise ValueError("Report text must be one paragraph")
    return value


def report_summary(value: str) -> str:
    report_text(value)
    if len(value.encode("utf-8")) > 8000:
        raise ValueError("Report summary exceeds its byte bound")
    return value


def fyi_item(value: str) -> str:
    report_text(value)
    if len(value.encode("utf-8")) > 2400:
        raise ValueError("FYI exceeds its byte bound")
    return value


def authoring_prompt(value: str) -> str:
    report_text(value, multiline=True)
    if len(value.encode("utf-8")) > 16384:
        raise ValueError("Authoring prompt exceeds its byte bound")
    return value


Sequence = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=19),
    AfterValidator(decimal_sequence),
]
PositiveRevision = Annotated[Sequence, AfterValidator(positive_revision)]
ReportSummary = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=2000),
    AfterValidator(report_summary),
]
FYIItem = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=600),
    AfterValidator(fyi_item),
]
AuthoringPrompt = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=8000),
    AfterValidator(authoring_prompt),
]
Cursor = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$"),
]
ActorClient = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
ActorSession = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200)]
ActorModel = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=120)]
CloseoutStatus = Literal["done", "wont-do", "promoted"]
DismissalFilter = Literal["undismissed", "dismissed", "all"]
WorkStatus = Literal["pending", "deferred", "done", "wont-do", "promoted"]
WorkEventType = Literal[
    "work_follow_up_requested", "work_follow_up_answered", "work_follow_up_superseded",
    "code_review_requested", "code_review_completed", "code_review_superseded",
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
    "work_merged",
    "work_moved",
    "human_attention_requested",
    "human_attention_resolved",
]
ActivityKind = Literal[
    "work_event",
    "project_created",
    "project_updated",
    "project_settings_updated",
    "lease_renewed",
    "job_completion_report_created",
    "job_completion_report_dismissed",
    "job_completion_report_follow_up_created",
]


class Phase12Model(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class JobCompletionReportInput(Phase12Model):
    summary: ReportSummary
    fyi_items: list[FYIItem] = Field(max_length=10)
    prompt_revision: PositiveRevision

    @model_validator(mode="after")
    def charged_bytes(self) -> Self:
        if sum(len(value.encode("utf-8")) for value in [self.summary, *self.fyi_items]) > 16384:
            raise ValueError("Report exceeds its aggregate byte bound")
        return self


class JobCompletionReportRead(JobCompletionReportInput):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    closeout_event_id: PositiveRevision
    closeout_work_version: Annotated[StrictInt, Field(ge=1, le=2147483647)]
    closeout_status: CloseoutStatus
    completion_checkpoint_id: UUID | None
    work_title_at_closeout: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    actor_client: ActorClient
    actor_session_id: ActorSession
    actor_model: ActorModel | None
    prompt_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    created_at: datetime

    @model_validator(mode="after")
    def closeout_identity(self) -> Self:
        if (self.closeout_status == "done") != (self.completion_checkpoint_id is not None):
            raise ValueError("Only Done reports identify a completion checkpoint")
        if self.created_at.tzinfo is None:
            raise ValueError("Report timestamp must be timezone aware")
        return self

    @field_serializer("created_at")
    def utc_created_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class JobCompletionReportDetailRead(JobCompletionReportRead):
    authoring_prompt: AuthoringPrompt


class HumanDismissalRead(Phase12Model):
    id: UUID
    created_at: datetime
    actor_client: ActorClient
    actor_session_id: ActorSession
    actor_model: ActorModel | None

    @field_serializer("created_at")
    def utc_created_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def aware_time(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("Dismissal timestamp must be timezone aware")
        return self


class SourceWorkState(Phase12Model):
    work_item_id: UUID
    status: WorkStatus
    canonical_work_item_id: UUID
    deleted: StrictBool


class JobCompletionReportEnvelope(Phase12Model):
    created_sequence: PositiveRevision
    report: JobCompletionReportRead
    human_dismissed: StrictBool
    human_dismissal: HumanDismissalRead | None
    source_work_state: SourceWorkState
    follow_up_count: Sequence

    @model_validator(mode="after")
    def review_coherence(self) -> Self:
        if self.human_dismissed != (self.human_dismissal is not None):
            raise ValueError("Dismissal fields disagree")
        if self.source_work_state.work_item_id != self.report.work_item_id:
            raise ValueError("Source work does not own this report")
        return self


class JobCompletionReportDetailEnvelope(JobCompletionReportEnvelope):
    report: JobCompletionReportDetailRead


class JobCompletionReportPage(Phase12Model):
    project_id: UUID
    stream_id: UUID
    dismissal: DismissalFilter
    work_item_id: UUID | None
    as_of_sequence: Sequence
    items: list[JobCompletionReportEnvelope] = Field(max_length=50)
    has_more: StrictBool
    next_cursor: Cursor | None

    @model_validator(mode="after")
    def page_coherence(self) -> Self:
        if self.has_more != (self.next_cursor is not None) or (self.has_more and not self.items):
            raise ValueError("Report page continuation is incoherent")
        if len({item.report.id for item in self.items}) != len(self.items):
            raise ValueError("Report page contains duplicate reports")
        sequences = [int(item.created_sequence) for item in self.items]
        if sequences != sorted(set(sequences), reverse=True):
            raise ValueError("Reports must be unique and descending")
        if sequences and sequences[0] > int(self.as_of_sequence):
            raise ValueError("Report exceeds the captured high water")
        for item in self.items:
            if item.report.project_id != self.project_id:
                raise ValueError("Report belongs to another project")
            if self.work_item_id is not None and item.report.work_item_id != self.work_item_id:
                raise ValueError("Report belongs to another work item")
            if self.dismissal != "all" and item.human_dismissed != (self.dismissal == "dismissed"):
                raise ValueError("Report does not match the dismissal filter")
        return self


class JobCompletionReportCount(Phase12Model):
    project_id: UUID
    undismissed_count: Sequence
    as_of_sequence: Sequence


class JobCompletionReportFollowUpRead(Phase12Model):
    id: UUID
    project_id: UUID
    report_id: UUID
    source_work_item_id: UUID
    follow_up_work_item_id: UUID
    created_sequence: PositiveRevision
    actor_client: ActorClient
    actor_session_id: ActorSession
    actor_model: ActorModel | None
    created_at: datetime

    @model_validator(mode="after")
    def different_work(self) -> Self:
        if self.source_work_item_id == self.follow_up_work_item_id:
            raise ValueError("A follow-up must be different work")
        if self.created_at.tzinfo is None:
            raise ValueError("Follow-up timestamp must be timezone aware")
        return self

    @field_serializer("created_at")
    def utc_created_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ReportFollowUpPage(Phase12Model):
    project_id: UUID
    report_id: UUID
    items: list[JobCompletionReportFollowUpRead] = Field(max_length=50)
    as_of_sequence: Sequence
    has_more: StrictBool
    next_cursor: Cursor | None


class WorkReportFollowUpPage(Phase12Model):
    project_id: UUID
    work_item_id: UUID
    direction: Literal["origin", "created"]
    items: list[JobCompletionReportFollowUpRead] = Field(max_length=50)
    as_of_sequence: Sequence
    has_more: StrictBool
    next_cursor: Cursor | None


_ACTIVITY_REFERENCES = {
    "work_event": {"work_event_id", "event_type", "work_item_id"},
    "project_created": set(),
    "project_updated": set(),
    "project_settings_updated": {"settings_revision"},
    "lease_renewed": {"work_item_id", "lease_generation_id"},
    "job_completion_report_created": {"work_item_id", "job_completion_report_id"},
    "job_completion_report_dismissed": {
        "work_item_id",
        "job_completion_report_id",
        "human_dismissal_id",
    },
    "job_completion_report_follow_up_created": {
        "work_item_id",
        "job_completion_report_id",
        "follow_up_id",
    },
}
_ACTIVITY_REFERENCE_FIELDS = frozenset().union(*_ACTIVITY_REFERENCES.values())


class ProjectActivityRead(Phase12Model):
    sequence: PositiveRevision
    kind: ActivityKind
    work_event_id: PositiveRevision | None
    event_type: WorkEventType | None
    work_item_id: UUID | None
    job_completion_report_id: UUID | None
    human_dismissal_id: UUID | None
    follow_up_id: UUID | None
    settings_revision: PositiveRevision | None
    lease_generation_id: UUID | None
    recorded_at: datetime
    origin: Literal["live", "history_import"]

    @model_validator(mode="after")
    def reference_matrix(self) -> Self:
        present = {key for key in _ACTIVITY_REFERENCE_FIELDS if getattr(self, key) is not None}
        if present != _ACTIVITY_REFERENCES[self.kind]:
            raise ValueError("Activity reference matrix is invalid")
        if self.origin == "history_import" and self.kind != "work_event":
            raise ValueError("Only recorded work events may be imported")
        if self.recorded_at.tzinfo is None:
            raise ValueError("Activity timestamp must be timezone aware")
        return self

    @field_serializer("recorded_at")
    def utc_recorded_at(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ProjectActivityPage(Phase12Model):
    project_id: UUID
    stream_id: UUID
    items: list[ProjectActivityRead] = Field(max_length=100)
    next_cursor: Cursor
    has_more: StrictBool
    through_sequence: Sequence
    historical_through_sequence: Sequence
    historical_coverage: Literal["recorded_work_events_only"]

    @model_validator(mode="after")
    def ordered_prefix(self) -> Self:
        sequences = [int(item.sequence) for item in self.items]
        if sequences != sorted(set(sequences)):
            raise ValueError("Activity entries must be unique and ascending")
        if sequences and sequences[-1] > int(self.through_sequence):
            raise ValueError("Activity exceeds the captured head")
        if int(self.historical_through_sequence) > int(self.through_sequence):
            raise ValueError("Historical boundary exceeds the captured head")
        if self.has_more and not self.items:
            raise ValueError("An empty page cannot have more entries")
        return self
