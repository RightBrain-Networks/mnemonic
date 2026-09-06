"""Strict, bounded Phase 12 wire values, independent of execution context models."""

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

MAX_SIGNED_64 = 9223372036854775807
MAX_WORK_VERSION = 2147483647
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f"
)


class Phase12Wire(BaseModel):
    """Reject unknown fields and never display raw invalid strings in errors."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def decimal_string(value: str) -> str:
    if not re.fullmatch(r"0|[1-9][0-9]{0,18}", value, re.ASCII):
        raise ValueError("Expected a canonical nonnegative decimal string.")
    if int(value) > MAX_SIGNED_64:
        raise ValueError("The decimal value exceeds the signed 64-bit range.")
    return value


def positive_decimal_string(value: str) -> str:
    decimal_string(value)
    if value == "0":
        raise ValueError("The decimal value must be positive.")
    return value


DecimalString = Annotated[StrictStr, AfterValidator(decimal_string)]
PositiveDecimalString = Annotated[StrictStr, AfterValidator(positive_decimal_string)]


def _validated_text(value: str, *, max_bytes: int, multiline: bool = False) -> str:
    if not value.strip():
        raise ValueError("Text must be nonblank.")
    for character in value:
        if multiline and character in "\t\r\n":
            continue
        if unicodedata.category(character) in {"Cc", "Cs"} or character in _BIDI_CONTROLS:
            raise ValueError("Text contains a forbidden control character.")
        if not multiline and character in "\u2028\u2029":
            raise ValueError("Report text must occupy one paragraph or bullet.")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError("Text exceeds the UTF-8 byte limit.")
    return value


def report_summary(value: str) -> str:
    return _validated_text(value, max_bytes=8000)


def report_fyi(value: str) -> str:
    return _validated_text(value, max_bytes=2400)


def report_prompt(value: str) -> str:
    return _validated_text(value, max_bytes=16384, multiline=True)


ReportSummary = Annotated[
    StrictStr, Field(min_length=1, max_length=2000), AfterValidator(report_summary)
]
ReportFyi = Annotated[
    StrictStr, Field(min_length=1, max_length=600), AfterValidator(report_fyi)
]
ReportPrompt = Annotated[
    StrictStr, Field(min_length=1, max_length=8000), AfterValidator(report_prompt)
]


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Timestamp must use UTC.")
    return value


def parse_utc_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return utc_datetime(value)
    if not isinstance(value, str) or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z", value
    ) is None:
        raise ValueError("Timestamp must use canonical UTC spelling.")
    parsed = datetime.fromisoformat(value)
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError("Timestamp must use canonical UTC spelling.")
    return parsed


UTCDateTime = Annotated[
    datetime, BeforeValidator(parse_utc_timestamp), AfterValidator(utc_datetime),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


class JobCompletionReportInput(Phase12Wire):
    summary: ReportSummary
    fyi_items: Annotated[list[ReportFyi], Field(max_length=10)]
    prompt_revision: PositiveDecimalString

    @model_validator(mode="after")
    def enforce_aggregate_bytes(self) -> Self:
        if sum(len(value.encode("utf-8")) for value in [self.summary, *self.fyi_items]) > 16384:
            raise ValueError("Report text exceeds the aggregate UTF-8 byte limit.")
        return self


def reject_null_report(value: object) -> object:
    if value is None:
        raise ValueError("job_completion_report cannot be null.")
    return value


def omit_default(schema: dict[str, Any]) -> None:
    schema.pop("default", None)


JobCompletionReportArgument = Annotated[
    JobCompletionReportInput | SkipJsonSchema[None],
    BeforeValidator(reject_null_report),
    Field(json_schema_extra=omit_default),
]


class JobCompletionReportRead(JobCompletionReportInput):
    id: UUID
    project_id: UUID
    work_item_id: UUID
    closeout_event_id: PositiveDecimalString
    closeout_work_version: StrictInt = Field(ge=1, le=MAX_WORK_VERSION)
    closeout_status: Literal["done", "wont-do", "promoted"]
    completion_checkpoint_id: UUID | None
    work_title_at_closeout: StrictStr = Field(min_length=1, max_length=200)
    actor_client: StrictStr = Field(min_length=1, max_length=80)
    actor_session_id: StrictStr = Field(min_length=1, max_length=200)
    actor_model: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None
    prompt_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: UTCDateTime

    @field_validator("work_title_at_closeout", "actor_client", "actor_session_id", "actor_model")
    @classmethod
    def validate_retained_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("Retained identity text must be nonblank and NUL-free.")
        return value

    @model_validator(mode="after")
    def enforce_outcome_checkpoint(self) -> Self:
        if (self.closeout_status == "done") != (self.completion_checkpoint_id is not None):
            raise ValueError("Only Done reports identify a completion checkpoint.")
        return self


class JobCompletionReportDetailRead(JobCompletionReportRead):
    authoring_prompt: ReportPrompt

    @model_validator(mode="after")
    def enforce_prompt_hash(self) -> Self:
        if hashlib.sha256(self.authoring_prompt.encode("utf-8")).hexdigest() != self.prompt_sha256:
            raise ValueError("Report authoring prompt does not match its immutable hash.")
        return self


class ProjectSettingsRead(Phase12Wire):
    code_review_required_min_priority: Annotated[StrictInt, Field(ge=0, le=100, multiple_of=5)]
    code_review_optional_min_priority: Annotated[StrictInt, Field(ge=0, le=100, multiple_of=5)]
    allow_remediation_code_reviews: StrictBool
    project_id: UUID
    recall_pointer_template: Annotated[StrictStr, Field(min_length=1, max_length=100000)] | None
    job_completion_report_prompt: ReportPrompt
    revision: PositiveDecimalString

    @field_validator("recall_pointer_template")
    @classmethod
    def validate_recall_template(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("Recall template must be nonblank and NUL-free.")
        return value


DismissalFilter = Literal["undismissed", "dismissed", "all"]
WorkStatus = Literal["pending", "deferred", "done", "wont-do", "promoted"]
WorkEventType = Literal[
    "work_created", "work_updated", "work_status_changed", "work_reopened", "work_claimed",
    "work_released", "checkpoint_added", "progress", "dependency_added", "dependency_removed",
    "relationship_added", "relationship_removed", "work_completed", "work_deleted", "work_merged",
    "work_moved", "human_attention_requested", "human_attention_resolved",
    "work_follow_up_requested", "work_follow_up_answered", "work_follow_up_superseded",
    "code_review_requested", "code_review_completed", "code_review_superseded",
]
ActivityKind = Literal[
    "work_event", "project_created", "project_updated", "project_settings_updated", "lease_renewed",
    "job_completion_report_created", "job_completion_report_dismissed",
    "job_completion_report_follow_up_created",
]


class HumanDismissalRead(Phase12Wire):
    id: UUID
    created_at: UTCDateTime
    actor_client: StrictStr = Field(min_length=1, max_length=80)
    actor_session_id: StrictStr = Field(min_length=1, max_length=200)
    actor_model: Annotated[StrictStr, Field(min_length=1, max_length=120)] | None

    _retained_actor = field_validator("actor_client", "actor_session_id", "actor_model")(
        JobCompletionReportRead.validate_retained_text.__func__
    )


class SourceWorkState(Phase12Wire):
    work_item_id: UUID
    status: WorkStatus
    canonical_work_item_id: UUID
    deleted: StrictBool


class JobCompletionReportEnvelope(Phase12Wire):
    report: JobCompletionReportRead
    created_sequence: PositiveDecimalString
    human_dismissed: StrictBool
    human_dismissal: HumanDismissalRead | None
    source_work_state: SourceWorkState
    follow_up_count: DecimalString

    @model_validator(mode="after")
    def enforce_review_coherence(self) -> Self:
        if self.human_dismissed != (self.human_dismissal is not None):
            raise ValueError("Report dismissal fields disagree.")
        if self.source_work_state.work_item_id != self.report.work_item_id:
            raise ValueError("Report source work identity disagrees.")
        if self.human_dismissal is not None and self.human_dismissal.created_at < self.report.created_at:
            raise ValueError("Dismissal predates its report.")
        return self


class JobCompletionReportDetailEnvelope(JobCompletionReportEnvelope):
    report: JobCompletionReportDetailRead


class ActivityCursorDocument(Phase12Wire):
    v: Literal[1]
    kind: Literal["activity"]
    project_id: UUID
    stream_id: UUID
    after: DecimalString


class ReportCursorDocument(Phase12Wire):
    v: Literal[1]
    kind: Literal["reports"]
    project_id: UUID
    stream_id: UUID
    dismissal: DismissalFilter
    work_item_id: UUID | None
    upper: DecimalString
    last: PositiveDecimalString

    @model_validator(mode="after")
    def enforce_bounds(self) -> Self:
        if int(self.last) > int(self.upper):
            raise ValueError("Report cursor exceeds its high water.")
        return self


def _cursor_json(value: str) -> str:
    if not 1 <= len(value) <= 512 or not re.fullmatch(r"[A-Za-z0-9_-]+", value, re.ASCII):
        raise ValueError("Invalid bounded cursor encoding.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
            raise ValueError("Cursor encoding is noncanonical.")
        document = json.loads(decoded.decode("utf-8"))
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        if canonical.encode("utf-8") != decoded:
            raise ValueError("Cursor JSON is noncanonical.")
        return canonical
    except (UnicodeError, ValueError, binascii.Error) as error:
        raise ValueError("Invalid cursor document.") from error


def cursor_document[CursorT: Phase12Wire](value: str, model: type[CursorT]) -> CursorT:
    raw = _cursor_json(value)
    parsed = model.model_validate_json(raw, strict=True)
    if json.dumps(parsed.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("Cursor fields are noncanonical.")
    return parsed


def activity_cursor(value: str) -> str:
    cursor_document(value, ActivityCursorDocument)
    return value


def report_cursor(value: str) -> str:
    cursor_document(value, ReportCursorDocument)
    return value


ActivityCursor = Annotated[
    StrictStr, Field(min_length=1, max_length=512), AfterValidator(activity_cursor)
]
ReportCursor = Annotated[
    StrictStr, Field(min_length=1, max_length=512), AfterValidator(report_cursor)
]


def reject_null_cursor(value: object) -> object:
    if value is None:
        raise ValueError("Omit an unused cursor instead of passing null.")
    return value


ActivityCursorArgument = Annotated[
    ActivityCursor | SkipJsonSchema[None], BeforeValidator(reject_null_cursor),
    Field(json_schema_extra=omit_default),
]
ReportCursorArgument = Annotated[
    ReportCursor | SkipJsonSchema[None], BeforeValidator(reject_null_cursor),
    Field(json_schema_extra=omit_default),
]


class JobCompletionReportPage(Phase12Wire):
    project_id: UUID
    stream_id: UUID
    dismissal: DismissalFilter
    work_item_id: UUID | None
    as_of_sequence: DecimalString
    items: list[JobCompletionReportEnvelope] = Field(max_length=50)
    has_more: StrictBool
    next_cursor: ReportCursor | None

    @model_validator(mode="after")
    def enforce_page(self) -> Self:
        if self.has_more != (self.next_cursor is not None) or self.has_more and not self.items:
            raise ValueError("Report continuation is incoherent.")
        ids = [item.report.id for item in self.items]
        sequences = [int(item.created_sequence) for item in self.items]
        if len(set(ids)) != len(ids) or sequences != sorted(set(sequences), reverse=True):
            raise ValueError("Report entries must be unique and newest first.")
        if any(sequence > int(self.as_of_sequence) for sequence in sequences):
            raise ValueError("Report entries exceed the captured high water.")
        self._check_items()
        self._check_cursor()
        return self

    def _check_items(self) -> None:
        for item in self.items:
            if item.report.project_id != self.project_id:
                raise ValueError("Report project scope disagrees.")
            if self.work_item_id is not None and item.report.work_item_id != self.work_item_id:
                raise ValueError("Report work filter disagrees.")
            if self.dismissal != "all" and item.human_dismissed != (self.dismissal == "dismissed"):
                raise ValueError("Report dismissal filter disagrees.")

    def _check_cursor(self) -> None:
        if self.next_cursor is None:
            return
        cursor = cursor_document(self.next_cursor, ReportCursorDocument)
        if (
            cursor.project_id != self.project_id or cursor.stream_id != self.stream_id
            or cursor.dismissal != self.dismissal or cursor.work_item_id != self.work_item_id
            or cursor.upper != self.as_of_sequence or cursor.last != self.items[-1].created_sequence
        ):
            raise ValueError("Report continuation does not match this page.")


_ACTIVITY_REFERENCES = {
    "work_event": {"work_event_id", "event_type", "work_item_id"},
    "project_created": set(),
    "project_updated": set(),
    "project_settings_updated": {"settings_revision"},
    "lease_renewed": {"work_item_id", "lease_generation_id"},
    "job_completion_report_created": {"work_item_id", "job_completion_report_id"},
    "job_completion_report_dismissed": {
        "work_item_id", "job_completion_report_id", "human_dismissal_id",
    },
    "job_completion_report_follow_up_created": {
        "work_item_id", "job_completion_report_id", "follow_up_id",
    },
}
_ACTIVITY_REFERENCE_FIELDS = frozenset().union(*_ACTIVITY_REFERENCES.values())


class ProjectActivityRead(Phase12Wire):
    sequence: PositiveDecimalString
    kind: ActivityKind
    work_event_id: PositiveDecimalString | None
    event_type: WorkEventType | None
    work_item_id: UUID | None
    job_completion_report_id: UUID | None
    human_dismissal_id: UUID | None
    follow_up_id: UUID | None
    settings_revision: PositiveDecimalString | None
    lease_generation_id: UUID | None
    recorded_at: UTCDateTime
    origin: Literal["live", "history_import"]

    @model_validator(mode="after")
    def enforce_reference_matrix(self) -> Self:
        present = {key for key in _ACTIVITY_REFERENCE_FIELDS if getattr(self, key) is not None}
        if present != _ACTIVITY_REFERENCES[self.kind]:
            raise ValueError("Activity references disagree with its kind.")
        if self.origin == "history_import" and self.kind != "work_event":
            raise ValueError("Only recorded work events may be imported.")
        if len(self.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("Activity item exceeds its byte limit.")
        return self


class ProjectActivityPage(Phase12Wire):
    project_id: UUID
    stream_id: UUID
    items: list[ProjectActivityRead] = Field(max_length=100)
    next_cursor: ActivityCursor
    has_more: StrictBool
    through_sequence: DecimalString
    historical_through_sequence: DecimalString
    historical_coverage: Literal["recorded_work_events_only"]

    @model_validator(mode="after")
    def enforce_page(self) -> Self:
        sequences = [int(item.sequence) for item in self.items]
        if sequences and sequences != list(range(sequences[0], sequences[0] + len(sequences))):
            raise ValueError("Activity entries must form a contiguous ascending prefix.")
        head = int(self.through_sequence)
        history = int(self.historical_through_sequence)
        if history > head or sequences and sequences[-1] > head:
            raise ValueError("Activity exceeds the captured head.")
        self._check_origins(history)
        cursor = cursor_document(self.next_cursor, ActivityCursorDocument)
        if cursor.project_id != self.project_id or cursor.stream_id != self.stream_id:
            raise ValueError("Activity cursor scope disagrees.")
        if int(cursor.after) > head or sequences and int(cursor.after) != sequences[-1]:
            raise ValueError("Activity cursor does not identify the last accepted sequence.")
        if self.has_more != (int(cursor.after) < head) or self.has_more and not sequences:
            raise ValueError("Activity continuation disagrees with the captured head.")
        return self

    def _check_origins(self, history: int) -> None:
        for item in self.items:
            if (item.origin == "history_import") != (int(item.sequence) <= history):
                raise ValueError("Activity origin disagrees with the imported history boundary.")
