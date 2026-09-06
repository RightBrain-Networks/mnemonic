"""Typed REST wire models; the API remains the validation authority."""

import base64
import binascii
import ipaddress
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    RootModel,
    SecretStr,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
    TypeAdapter,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from .external_records import (
    ExternalCandidates,
    ExternalDuplicateSuggestion,
    ExternalReferences,
    OmissionOnlyExternalReferences,
    validate_external_page,
)
from .phase12_models import JobCompletionReportRead, reject_null_report
from .response_validation import validate_page_bounds, validate_page_items

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

VerificationType = Literal["command", "observation"]
VerificationOutcome = Literal["passed", "failed", "inconclusive", "skipped"]
ArtifactType = Literal[
    "commit",
    "pull_request",
    "branch",
    "test_run",
    "repository_path",
    "external_issue",
    "build_artifact",
]

_ARTIFACT_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
_ARTIFACT_HOST_LABEL = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
    re.ASCII,
)
_ARTIFACT_IPV6_HOST = re.compile(r"^[0-9a-f:]+$", re.ASCII)

MAX_COMPLETION_EVIDENCE_ENTRIES = 20
MAX_COMPLETION_EVIDENCE_BYTES = 32768
MAX_COMPLETION_EVENT_ID = 9223372036854775806
MAX_COMPLETION_WORK_VERSION = 2147483647
MAX_COMPLETION_EVIDENCE_CURSOR_BYTES = 2048
MAX_COMPLETION_EXPECTED_VERSION = 2147483646


def _omit_default_from_json_schema(schema: dict[str, Any]) -> None:
    """Describe a runtime-optional field as omission-only to MCP clients."""
    schema.pop("default", None)


def _validated_evidence_text(
    value: str,
    *,
    label: str,
    maximum_bytes: int,
) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must use valid Unicode.") from error
    if not value.strip() or b"\x00" in encoded:
        raise ValueError(f"{label} must be nonblank and contain no NUL bytes.")
    if len(encoded) > maximum_bytes:
        raise ValueError(f"{label} exceeds its UTF-8 byte limit.")
    return value


def _validated_evidence_name(value: str) -> str:
    return _validated_evidence_text(
        value,
        label="Evidence names",
        maximum_bytes=800,
    )


def _validated_evidence_summary(value: str) -> str:
    return _validated_evidence_text(
        value,
        label="Evidence summaries",
        maximum_bytes=16000,
    )


def _validated_evidence_command(value: str) -> str:
    return _validated_evidence_text(
        value,
        label="Evidence commands",
        maximum_bytes=16384,
    )


_PYTHON_EDGE_WHITESPACE = (
    r" \t\n\r\v\f\u001c-\u001f\u0085\u00a0\u1680"
    r"\u2000-\u200a\u2028\u2029\u202f\u205f\u3000"
)
_JSON_SCHEMA_EXACT_END = r"(?![\s\S])"
_EVIDENCE_TEXT_SCHEMA_PATTERN = (
    rf"^(?![\s\S]*\u0000)(?![\s\S]*[\ud800-\udfff])"
    rf"(?=[\s\S]*[^{_PYTHON_EDGE_WHITESPACE}])"
    rf"[\s\S]+{_JSON_SCHEMA_EXACT_END}"
)
_BRANCH_SCHEMA_PATTERN = (
    rf"^(?![\s\S]*\u0000)(?![\s\S]*[\ud800-\udfff])"
    rf"(?![{_PYTHON_EDGE_WHITESPACE}])[\s\S]*[^{_PYTHON_EDGE_WHITESPACE}]"
    rf"{_JSON_SCHEMA_EXACT_END}"
)
_PORT_SCHEMA_PATTERN = (
    r"(?:0|[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"
)
_IPV4_OCTET_SCHEMA_PATTERN = r"(?:0|[1-9][0-9]?|1[0-9]{2}|2[0-4][0-9]|25[0-5])"
_IPV4_SCHEMA_PATTERN = rf"{_IPV4_OCTET_SCHEMA_PATTERN}(?:\.{_IPV4_OCTET_SCHEMA_PATTERN}){{3}}"
_DNS_LABEL_SCHEMA_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_DNS_SCHEMA_PATTERN = rf"{_DNS_LABEL_SCHEMA_PATTERN}(?:\.{_DNS_LABEL_SCHEMA_PATTERN})*"
_IPV6_NONZERO_HEXTET_SCHEMA_PATTERN = r"[1-9a-f][0-9a-f]{0,3}"


def _canonical_ipv6_shape(zero_mask: int) -> str:
    zeroes = tuple(bool(zero_mask & (1 << position)) for position in range(8))
    zero_runs: list[tuple[int, int]] = []
    position = 0
    while position < 8:
        if not zeroes[position]:
            position += 1
            continue
        end = position + 1
        while end < 8 and zeroes[end]:
            end += 1
        zero_runs.append((position, end - position))
        position = end
    compression = max(zero_runs, key=lambda run: (run[1], -run[0]), default=None)
    groups = [
        "0" if zero else _IPV6_NONZERO_HEXTET_SCHEMA_PATTERN for zero in zeroes
    ]
    if compression is None or compression[1] < 2:
        return ":".join(groups)
    start, length = compression
    return ":".join(groups[:start]) + "::" + ":".join(groups[start + length :])


_IPV6_SCHEMA_PATTERN = (
    r"\[(?:" + "|".join(_canonical_ipv6_shape(mask) for mask in range(256)) + r")\]"
)
_URL_PATH_CHARACTER_SCHEMA_PATTERN = r"(?:[A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-F]{2})"
_URL_DOT_SEGMENT_SCHEMA_PATTERN = r"(?:\.{1,2}|%2E|\.%2E|%2E\.|%2E%2E)"
_HTTPS_ARTIFACT_SCHEMA_PATTERN = (
    rf"^https://(?:{_IPV4_SCHEMA_PATTERN}"
    rf"|(?![0-9.]+(?::(?:{_PORT_SCHEMA_PATTERN}))?/)"
    rf"(?=[^/:]{{1,253}}(?::(?:{_PORT_SCHEMA_PATTERN}))?/){_DNS_SCHEMA_PATTERN}"
    rf"|{_IPV6_SCHEMA_PATTERN})"
    rf"(?::(?!443/){_PORT_SCHEMA_PATTERN})?/"
    rf"(?!{_URL_DOT_SEGMENT_SCHEMA_PATTERN}(?:/|{_JSON_SCHEMA_EXACT_END}))"
    rf"{_URL_PATH_CHARACTER_SCHEMA_PATTERN}*"
    rf"(?:/(?!{_URL_DOT_SEGMENT_SCHEMA_PATTERN}(?:/|{_JSON_SCHEMA_EXACT_END}))"
    rf"{_URL_PATH_CHARACTER_SCHEMA_PATTERN}*)*{_JSON_SCHEMA_EXACT_END}"
)


def _evidence_text_json_schema(maximum_bytes: int) -> dict[str, object]:
    return {
        "pattern": _EVIDENCE_TEXT_SCHEMA_PATTERN,
        "x-utf8-max-bytes": maximum_bytes,
    }


EvidenceName = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=200),
    Field(json_schema_extra=_evidence_text_json_schema(800)),
    AfterValidator(_validated_evidence_name),
]
EvidenceSummary = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=4000),
    Field(json_schema_extra=_evidence_text_json_schema(16000)),
    AfterValidator(_validated_evidence_summary),
]
EvidenceCommand = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=4096),
    Field(json_schema_extra=_evidence_text_json_schema(16384)),
    AfterValidator(_validated_evidence_command),
]
EvidenceCommit = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^[0-9a-f]{7,64}$"),
]

_OBSERVED_AT_PATTERN = re.compile(
    r"^(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?"
    r"(?P<zone>Z|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):"
    r"(?P<offset_minute>[0-9]{2}))$",
    re.ASCII,
)

_CALENDAR_DATE_SCHEMA_PATTERN = (
    r"(?:(?!0000-)[0-9]{4}-(?:"
    r"(?:0[13578]|1[02])-(?:0[1-9]|[12][0-9]|3[01])"
    r"|(?:0[469]|11)-(?:0[1-9]|[12][0-9]|30)"
    r"|02-(?:0[1-9]|1[0-9]|2[0-8]))"
    r"|(?:(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26]))"
    r"|(?:0[48]|[2468][048]|[13579][26])00)-02-29)"
)
_OBSERVED_AT_VALIDATION_SCHEMA_PATTERN = (
    r"^(?![\s\S]*-00:00(?![\s\S]))"
    + _CALENDAR_DATE_SCHEMA_PATTERN
    + r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    + r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))"
    + _JSON_SCHEMA_EXACT_END
)
_CANONICAL_UTC_TIMESTAMP_SCHEMA = {
    "type": "string",
    "format": "date-time",
    "minLength": 20,
    "maxLength": 27,
    "pattern": (
        "^"
        + _CALENDAR_DATE_SCHEMA_PATTERN
        + r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
        + r"(?:\.[0-9]{6})?Z"
        + _JSON_SCHEMA_EXACT_END
    ),
}


def _parsed_observed_at(value: object) -> datetime:
    if not isinstance(value, str) or not 20 <= len(value) <= 32:
        raise ValueError("observed_at must use the bounded RFC 3339 spelling.")
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("observed_at must contain ASCII only.") from error
    matched = _OBSERVED_AT_PATTERN.fullmatch(value)
    if matched is None or value.endswith("-00:00"):
        raise ValueError("observed_at must use the bounded RFC 3339 spelling.")

    groups = matched.groupdict()
    offset_hour = int(groups["offset_hour"] or "0")
    offset_minute = int(groups["offset_minute"] or "0")
    if offset_hour > 14 or offset_minute > 59 or (
        offset_hour == 14 and offset_minute != 0
    ):
        raise ValueError("observed_at has an invalid UTC offset.")
    offset = timedelta(hours=offset_hour, minutes=offset_minute)
    if groups["sign"] == "-":
        offset = -offset
    fraction = (groups["fraction"] or "").ljust(6, "0")
    try:
        parsed = datetime(
            int(groups["year"]),
            int(groups["month"]),
            int(groups["day"]),
            int(groups["hour"]),
            int(groups["minute"]),
            int(groups["second"]),
            int(fraction or "0"),
            tzinfo=timezone(offset),
        )
        return parsed.astimezone(UTC)
    except (OverflowError, ValueError) as error:
        raise ValueError("observed_at is outside the supported UTC range.") from error


def _serialized_observed_at(value: datetime) -> str:
    value = value.astimezone(UTC)
    base = value.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{value.microsecond:06d}Z" if value.microsecond else f"{base}Z"


def _parsed_canonical_observed_at(value: object) -> datetime:
    parsed = _parsed_observed_at(value)
    if not isinstance(value, str) or value != _serialized_observed_at(parsed):
        raise ValueError("Response timestamps must use the canonical UTC spelling.")
    return parsed


ObservedAt = Annotated[
    datetime,
    BeforeValidator(_parsed_observed_at),
    PlainSerializer(_serialized_observed_at, return_type=str),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "minLength": 20,
            "maxLength": 32,
            "pattern": _OBSERVED_AT_VALIDATION_SCHEMA_PATTERN,
        },
        mode="validation",
    ),
    WithJsonSchema(_CANONICAL_UTC_TIMESTAMP_SCHEMA, mode="serialization"),
]
CanonicalObservedAt = Annotated[
    datetime,
    BeforeValidator(_parsed_canonical_observed_at),
    PlainSerializer(_serialized_observed_at, return_type=str),
    WithJsonSchema(_CANONICAL_UTC_TIMESTAMP_SCHEMA),
]

OmissionOnlyObservedAt = Annotated[
    ObservedAt | SkipJsonSchema[None],
    Field(json_schema_extra=_omit_default_from_json_schema),
]
OmissionOnlyCanonicalObservedAt = Annotated[
    CanonicalObservedAt | SkipJsonSchema[None],
    Field(json_schema_extra=_omit_default_from_json_schema),
]
OmissionOnlyEvidenceCommit = Annotated[
    EvidenceCommit | SkipJsonSchema[None],
    Field(json_schema_extra=_omit_default_from_json_schema),
]
BoundedExitCode = Annotated[
    StrictInt,
    Field(ge=-2147483648, le=2147483647),
]
OmissionOnlyExitCode = Annotated[
    BoundedExitCode | SkipJsonSchema[None],
    Field(json_schema_extra=_omit_default_from_json_schema),
]

_REPOSITORY_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._@+=,~\-]+$", re.ASCII)


def _validated_repository_path(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Repository paths must use the supported ASCII grammar.") from error
    if not encoded or len(encoded) > 512:
        raise ValueError("Repository paths must contain between 1 and 512 ASCII bytes.")
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("Repository paths must contain safe relative components.")
    if any(_REPOSITORY_PATH_COMPONENT.fullmatch(component) is None for component in components):
        raise ValueError("Repository paths must use the supported ASCII grammar.")
    return value


def _validated_artifact_branch(value: str) -> str:
    _validated_evidence_text(
        value,
        label="Artifact branches",
        maximum_bytes=MAX_COMPLETION_EVIDENCE_BYTES,
    )
    if value != value.strip():
        raise ValueError("Artifact branches cannot have leading or trailing whitespace.")
    return value


def _validated_artifact_hostname(hostname: str) -> None:
    if ":" in hostname:
        try:
            ipv6_address = ipaddress.IPv6Address(hostname)
        except ipaddress.AddressValueError as error:
            raise ValueError("Artifact URL has an invalid IPv6 hostname.") from error
        if (
            "." in hostname
            or _ARTIFACT_IPV6_HOST.fullmatch(hostname) is None
            or str(ipv6_address) != hostname
        ):
            raise ValueError("Artifact URL has an invalid IPv6 hostname.")
        return
    if len(hostname) > 253 or hostname.endswith("."):
        raise ValueError("Artifact URL hostname exceeds the DNS grammar.")
    labels = hostname.split(".")
    if any(
        not 1 <= len(label) <= 63 or _ARTIFACT_HOST_LABEL.fullmatch(label) is None
        for label in labels
    ):
        raise ValueError("Artifact URL hostname exceeds the DNS grammar.")


def _validated_artifact_url(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Artifact URLs must use ASCII.") from error
    if not 1 <= len(encoded) <= 2000 or value != value.strip():
        raise ValueError("Artifact URLs must contain between 1 and 2,000 ASCII bytes.")
    if any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ValueError("Artifact URLs cannot contain whitespace or controls.")
    if not value.startswith("https://") or "\\" in value:
        raise ValueError("Artifact URLs must be absolute lowercase HTTPS URLs.")
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        canonical = str(_ARTIFACT_URL_ADAPTER.validate_python(value))
    except ValueError as error:
        raise ValueError("Artifact URLs must be unambiguous HTTPS URLs.") from error
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.port == 443
        or not parsed.path
        or canonical != value
        or re.fullmatch(
            rf"/(?:{_URL_PATH_CHARACTER_SCHEMA_PATTERN})*"
            rf"(?:/(?:{_URL_PATH_CHARACTER_SCHEMA_PATTERN})*)*",
            parsed.path,
        )
        is None
        or any(
            component.upper() in {".", "..", "%2E", ".%2E", "%2E.", "%2E%2E"}
            for component in parsed.path.split("/")
        )
    ):
        raise ValueError("Artifact URLs must be exact, canonical, and free of secret-bearing parts.")
    _validated_artifact_hostname(parsed.hostname)
    return value


def _bounded_positive_decimal_schema_pattern(maximum: int) -> str:
    """Return an exact decimal-string grammar for a positive upper bound."""
    digits = str(maximum)
    branches = [rf"[1-9][0-9]{{0,{len(digits) - 2}}}"]
    prefix = ""
    for position, digit in enumerate(digits):
        lower = 1 if position == 0 else 0
        upper = int(digit) - 1
        if lower <= upper:
            choice = str(lower) if lower == upper else f"[{lower}-{upper}]"
            remaining = len(digits) - position - 1
            suffix = "" if remaining == 0 else rf"[0-9]{{{remaining}}}"
            branches.append(prefix + choice + suffix)
        prefix += digit
    branches.append(digits)
    return rf"^(?:{'|'.join(branches)}){_JSON_SCHEMA_EXACT_END}"


_COMPLETION_EVENT_ID_SCHEMA_PATTERN = _bounded_positive_decimal_schema_pattern(
    MAX_COMPLETION_EVENT_ID
)


def _validated_completion_event_id(value: str) -> str:
    if not value.isascii() or re.fullmatch(r"[1-9][0-9]{0,18}", value) is None:
        raise ValueError("Completion event IDs must be canonical decimal strings.")
    if int(value) > MAX_COMPLETION_EVENT_ID:
        raise ValueError("Completion event ID exceeds the supported range.")
    return value


CompletionEventID = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=19, pattern=r"^[1-9][0-9]{0,18}$"),
    Field(json_schema_extra={"pattern": _COMPLETION_EVENT_ID_SCHEMA_PATTERN}),
    AfterValidator(_validated_completion_event_id),
]


def _decoded_completion_evidence_cursor(value: str) -> dict[str, object]:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Completion evidence cursors must use ASCII.") from error
    if not 1 <= len(encoded) <= 4096 or "=" in value:
        raise ValueError("Completion evidence cursor has an invalid encoded length.")
    if re.fullmatch(r"[A-Za-z0-9_-]+", value, re.ASCII) is None:
        raise ValueError("Completion evidence cursor is not canonical base64url.")
    padding = b"=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("Completion evidence cursor is not canonical base64url.") from error
    if not 1 <= len(decoded) <= MAX_COMPLETION_EVIDENCE_CURSOR_BYTES:
        raise ValueError("Completion evidence cursor exceeds its decoded byte limit.")
    try:
        document = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Completion evidence cursor does not contain canonical JSON.") from error
    if not isinstance(document, dict):
        raise TypeError("Completion evidence cursor must contain one object.")
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if canonical != decoded or base64.urlsafe_b64encode(decoded).rstrip(b"=") != encoded:
        raise ValueError("Completion evidence cursor is not canonically encoded.")
    return document


_COMPLETION_EVIDENCE_CURSOR_FIELDS = {
    "as_of_completion_event_id",
    "direction",
    "endpoint",
    "last_completion_event_id",
    "project_id",
    "v",
    "work_item_id",
}


def _cursor_scope_uuid(document: dict[str, object], name: str) -> str:
    raw_uuid = document.get(name)
    if not isinstance(raw_uuid, str):
        raise TypeError("Completion evidence cursor has an invalid scope.")
    try:
        parsed_uuid = UUID(raw_uuid)
    except ValueError as error:
        raise ValueError("Completion evidence cursor has an invalid scope.") from error
    if str(parsed_uuid) != raw_uuid:
        raise ValueError("Completion evidence cursor scope must use canonical UUIDs.")
    return raw_uuid


def _cursor_event_id(document: dict[str, object], name: str) -> str:
    raw_id = document.get(name)
    if not isinstance(raw_id, str):
        raise TypeError("Completion evidence cursor has an invalid event identity.")
    return _validated_completion_event_id(raw_id)


def _validated_completion_evidence_cursor(value: str) -> str:
    document = _decoded_completion_evidence_cursor(value)
    if set(document) != _COMPLETION_EVIDENCE_CURSOR_FIELDS:
        raise ValueError("Completion evidence cursor has an invalid shape.")
    if (
        document.get("direction") != "desc"
        or document.get("endpoint") != "completion_evidence"
    ):
        raise ValueError("Completion evidence cursor has an invalid endpoint or direction.")
    version = document.get("v")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ValueError("Completion evidence cursor has an unsupported version.")
    _cursor_scope_uuid(document, "project_id")
    _cursor_scope_uuid(document, "work_item_id")
    high_water = _cursor_event_id(document, "as_of_completion_event_id")
    last_event = _cursor_event_id(document, "last_completion_event_id")
    if int(last_event) > int(high_water):
        raise ValueError("Completion evidence cursor lies beyond its high-water identity.")
    return value


CompletionEvidenceCursor = Annotated[
    StrictStr,
    StringConstraints(
        min_length=1,
        max_length=4096,
        pattern=r"^[A-Za-z0-9_-]+$",
    ),
    AfterValidator(_validated_completion_evidence_cursor),
]


def _rejected_null_completion_evidence_cursor(value: object) -> object:
    if value is None:
        raise ValueError("cursor cannot be null.")
    return value


CompletionEvidenceCursorArgument = Annotated[
    CompletionEvidenceCursor | SkipJsonSchema[None],
    BeforeValidator(_rejected_null_completion_evidence_cursor),
    Field(json_schema_extra=_omit_default_from_json_schema),
]


def completion_evidence_cursor_document(value: str) -> dict[str, object]:
    """Return an already-validated cursor document for response-coherence checks."""
    _validated_completion_evidence_cursor(value)
    return _decoded_completion_evidence_cursor(value)


_AFFECTED_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9._@+=,~*\-]+$", re.ASCII)


def _validated_affected_path(value: str) -> str:
    """Validate one preserved, shell-transport-safe repository scope pattern."""
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Affected paths must use the supported ASCII grammar.") from error
    if not encoded or len(encoded) > 512:
        raise ValueError("Each affected path must contain between 1 and 512 ASCII bytes.")

    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("Affected paths must contain nonempty repository-relative components.")
    for component in components:
        if _AFFECTED_PATH_COMPONENT.fullmatch(component) is None:
            raise ValueError("Affected paths must use the supported ASCII grammar.")
        if "**" in component and component != "**":
            raise ValueError("A double star is valid only as a complete path component.")
    return value


AffectedPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9._@+=,~*/-]+$",
    ),
    AfterValidator(_validated_affected_path),
]


def _validated_affected_paths(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError("Affected paths must not contain exact duplicates.")
    if sum(len(path.encode("ascii")) for path in value) > 16384:
        raise ValueError("Affected paths must contain at most 16,384 ASCII bytes in total.")
    return value


AffectedPaths = Annotated[
    list[AffectedPath],
    Field(max_length=64),
    AfterValidator(_validated_affected_paths),
]


def _normalized_suggestion_tag(value: str) -> str:
    normalized = _validated_event_text(value).lower()
    if len(normalized) > 50:
        raise ValueError("Normalized tags must contain at most 50 characters.")
    return normalized


DuplicateSuggestionTitle = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_validated_event_text),
]
DuplicateSuggestionSummary = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    AfterValidator(_validated_event_text),
]
DuplicateSuggestionPrompt = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100000),
    AfterValidator(_validated_event_text),
]
DuplicateSuggestionTag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    AfterValidator(_normalized_suggestion_tag),
]
DuplicateSuggestionTags = Annotated[list[DuplicateSuggestionTag], Field(max_length=20)]
DuplicateSuggestionSignal = Literal["exact_title", "lexical", "semantic"]
DuplicateSuggestionMode = Literal["hybrid_full", "hybrid_shortlist", "lexical"]
DuplicateSuggestionSemanticScope = Literal[
    "full_project", "lexical_shortlist", "unavailable"
]


class DuplicateSuggestionRequest(BaseModel):
    """Strict non-persistent creation draft used only for advisory comparison."""

    model_config = ConfigDict(extra="forbid")

    title: DuplicateSuggestionTitle
    summary: DuplicateSuggestionSummary
    initial_prompt: DuplicateSuggestionPrompt
    tags: DuplicateSuggestionTags = Field(default_factory=list)
    external_candidates: ExternalCandidates = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
    exclude_work_item_id: UUID | None = Field(default_factory=lambda: None)
    limit: StrictInt = Field(default=5, ge=1, le=10)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.lower() for tag in value))


class CanonicalResponse(BaseModel):
    """Canonical responses reject additions except on deliberate search pointers."""

    model_config = ConfigDict(extra="forbid")


class EmptyEventMetadata(CanonicalResponse):
    pass


class WorkSnapshot(CanonicalResponse):
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )

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


class ExternalReferencesChange(CanonicalResponse):
    before: ExternalReferences
    after: ExternalReferences


class WorkChangeSet(CanonicalResponse):
    title: TitleChange | None = None
    summary: SummaryChange | None = None
    priority: PriorityChange | None = None
    status: StatusChange | None = None
    external_references: ExternalReferencesChange | None = None

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
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.id,
        )
        return self


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
    affected_paths: AffectedPaths = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_metadata: StoredMetadataInput = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_baseline_for_scope(self) -> Self:
        if self.affected_paths and self.verified_against is None:
            raise ValueError("Affected paths require a verified_against commit declaration.")
        return self


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
    affected_paths: AffectedPaths = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
    tags: list[str]
    source_metadata: dict[str, JsonValue]
    migration_origin: MigrationOrigin | None
    legacy_record_id: UUID | None
    created_at: datetime

    @field_validator("affected_paths", mode="before")
    @classmethod
    def reject_explicit_empty_scope(cls, value: object) -> object:
        if value == []:
            raise ValueError("An empty affected_paths response is noncanonical.")
        return value

    @model_validator(mode="after")
    def require_baseline_for_scope(self) -> Self:
        if self.affected_paths and self.verified_against is None:
            raise ValueError("Affected paths require a verified_against commit declaration.")
        return self


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
    created_at: CanonicalObservedAt


def _reject_explicit_nulls(model: BaseModel, fields: tuple[str, ...]) -> None:
    if any(name in model.model_fields_set and getattr(model, name) is None for name in fields):
        raise ValueError("Optional completion evidence fields cannot be null.")


_COMMAND_VERIFICATION_JSON_SCHEMA = {
    "oneOf": [
        {
            "properties": {
                "outcome": {"const": "passed"},
                "exit_code": {"const": 0, "type": "integer"},
            },
            "required": ["exit_code"],
        },
        {
            "properties": {
                "outcome": {"const": "failed"},
                "exit_code": {
                    "anyOf": [
                        {
                            "minimum": -2147483648,
                            "maximum": -1,
                            "type": "integer",
                        },
                        {
                            "minimum": 1,
                            "maximum": 2147483647,
                            "type": "integer",
                        },
                    ]
                },
            },
            "required": ["exit_code"],
        },
        {
            "properties": {"outcome": {"const": "inconclusive"}},
            "not": {"required": ["exit_code"]},
        },
    ]
}


class CommandVerificationInput(CanonicalResponse):
    model_config = ConfigDict(json_schema_extra=_COMMAND_VERIFICATION_JSON_SCHEMA)

    verification_type: Literal["command"]
    name: EvidenceName
    outcome: Literal["passed", "failed", "inconclusive"]
    summary: EvidenceSummary
    command: EvidenceCommand
    exit_code: OmissionOnlyExitCode = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    observed_at: OmissionOnlyObservedAt = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    observed_at_commit: OmissionOnlyEvidenceCommit = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def enforce_command_matrix(self) -> Self:
        _reject_explicit_nulls(
            self,
            ("exit_code", "observed_at", "observed_at_commit"),
        )
        has_exit_code = "exit_code" in self.model_fields_set
        if self.outcome == "passed" and (not has_exit_code or self.exit_code != 0):
            raise ValueError("A passed command requires exit_code zero.")
        if self.outcome == "failed" and (
            not has_exit_code or self.exit_code is None or self.exit_code == 0
        ):
            raise ValueError("A failed command requires a nonzero exit_code.")
        if self.outcome == "inconclusive" and has_exit_code:
            raise ValueError("An inconclusive command cannot contain exit_code.")
        return self


class ObservationVerificationInput(CanonicalResponse):
    verification_type: Literal["observation"]
    name: EvidenceName
    outcome: VerificationOutcome
    summary: EvidenceSummary
    observed_at: OmissionOnlyObservedAt = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    observed_at_commit: OmissionOnlyEvidenceCommit = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def reject_null_observation_fields(self) -> Self:
        _reject_explicit_nulls(self, ("observed_at", "observed_at_commit"))
        return self


VerificationResultInput = Annotated[
    CommandVerificationInput | ObservationVerificationInput,
    Field(discriminator="verification_type"),
]


class CommandVerificationRead(CommandVerificationInput):
    observed_at: OmissionOnlyCanonicalObservedAt = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    position: StrictInt = Field(ge=0, le=19)
    created_at: CanonicalObservedAt


class ObservationVerificationRead(ObservationVerificationInput):
    observed_at: OmissionOnlyCanonicalObservedAt = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    position: StrictInt = Field(ge=0, le=19)
    created_at: CanonicalObservedAt


VerificationResultRead = Annotated[
    CommandVerificationRead | ObservationVerificationRead,
    Field(discriminator="verification_type"),
]


_ARTIFACT_REFERENCE_JSON_SCHEMA = {
    "oneOf": [
        {
            "properties": {
                "artifact_type": {"const": "commit"},
                "reference": {
                    "type": "string",
                    "minLength": 7,
                    "maxLength": 64,
                    "pattern": r"^[0-9a-f]{7,64}$",
                },
            }
        },
        {
            "properties": {
                "artifact_type": {"const": "branch"},
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "pattern": _BRANCH_SCHEMA_PATTERN,
                    "x-utf8-max-bytes": 800,
                },
            }
        },
        {
            "properties": {
                "artifact_type": {"const": "repository_path"},
                "reference": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 512,
                    "pattern": (
                        r"^(?!\.{1,2}(?:/|$))[A-Za-z0-9._@+=,~\-]+"
                        r"(?:/(?!\.{1,2}(?:/|$))[A-Za-z0-9._@+=,~\-]+)*$"
                    ),
                },
            }
        },
        {
            "properties": {
                "artifact_type": {
                    "enum": [
                        "pull_request",
                        "test_run",
                        "external_issue",
                        "build_artifact",
                    ]
                },
                "reference": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 1,
                    "maxLength": 2000,
                    "pattern": _HTTPS_ARTIFACT_SCHEMA_PATTERN,
                },
            }
        },
    ]
}


class ArtifactReferenceInput(CanonicalResponse):
    model_config = ConfigDict(json_schema_extra=_ARTIFACT_REFERENCE_JSON_SCHEMA)

    artifact_type: ArtifactType
    label: EvidenceName
    reference: StrictStr = Field(min_length=1)

    @field_validator("reference")
    @classmethod
    def validate_reference(cls, value: str, info: ValidationInfo) -> str:
        artifact_type = info.data.get("artifact_type")
        if artifact_type == "commit":
            if re.fullmatch(r"[0-9a-f]{7,64}", value, re.ASCII) is None:
                raise ValueError("Commit artifact references require lowercase hexadecimal.")
        elif artifact_type == "branch":
            if len(value) > 200:
                raise ValueError("Artifact branches must be at most 200 characters.")
            _validated_artifact_branch(value)
        elif artifact_type == "repository_path":
            _validated_repository_path(value)
        elif artifact_type in {
            "pull_request",
            "test_run",
            "external_issue",
            "build_artifact",
        }:
            _validated_artifact_url(value)
        else:  # pragma: no cover - the literal field rejects this first
            raise ValueError("Unknown artifact type.")
        return value


class ArtifactReferenceRead(ArtifactReferenceInput):
    id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    position: StrictInt = Field(ge=0, le=19)
    created_at: CanonicalObservedAt


def _completion_evidence_bytes(
    verification_results: Sequence[CommandVerificationInput | ObservationVerificationInput],
    artifact_references: Sequence[ArtifactReferenceInput],
) -> int:
    total = 0
    for result in verification_results:
        total += len(result.verification_type.encode("utf-8"))
        total += len(result.name.encode("utf-8"))
        total += len(result.outcome.encode("utf-8"))
        total += len(result.summary.encode("utf-8"))
        if isinstance(result, CommandVerificationInput):
            total += len(result.command.encode("utf-8"))
        if result.observed_at is not None:
            total += 32
        if result.observed_at_commit is not None:
            total += len(result.observed_at_commit.encode("utf-8"))
    for artifact in artifact_references:
        total += len(artifact.artifact_type.encode("utf-8"))
        total += len(artifact.label.encode("utf-8"))
        total += len(artifact.reference.encode("utf-8"))
    return total


def _validate_completion_evidence_aggregate(
    verification_results: Sequence[CommandVerificationInput | ObservationVerificationInput],
    artifact_references: Sequence[ArtifactReferenceInput],
    *,
    require_nonempty: bool,
) -> None:
    count = len(verification_results) + len(artifact_references)
    if count > MAX_COMPLETION_EVIDENCE_ENTRIES or (require_nonempty and count == 0):
        raise ValueError("Completion evidence must contain between 1 and 20 entries.")
    if _completion_evidence_bytes(verification_results, artifact_references) > (
        MAX_COMPLETION_EVIDENCE_BYTES
    ):
        raise ValueError("Completion evidence exceeds its aggregate UTF-8 byte limit.")
    identities = [
        (artifact.artifact_type, artifact.reference) for artifact in artifact_references
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("Completion evidence cannot repeat an artifact type/reference pair.")


def _completion_evidence_count_json_schema(*, require_nonempty: bool) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "description": (
            "At most 20 verification results and artifact references combined. "
            "The aggregate text content is additionally limited to 32768 UTF-8 bytes."
        ),
        "x-utf8-aggregate-max-bytes": MAX_COMPLETION_EVIDENCE_BYTES,
        "allOf": [
            {
                "if": {
                    "properties": {
                        "verification_results": {"minItems": verification_count}
                    },
                    "required": ["verification_results"],
                },
                "then": {
                    "properties": {
                        "artifact_references": {
                            "maxItems": MAX_COMPLETION_EVIDENCE_ENTRIES
                            - verification_count
                        }
                    }
                },
            }
            for verification_count in range(1, MAX_COMPLETION_EVIDENCE_ENTRIES + 1)
        ]
    }
    if require_nonempty:
        schema["anyOf"] = [
            {
                "properties": {"verification_results": {"minItems": 1}},
                "required": ["verification_results"],
            },
            {
                "properties": {"artifact_references": {"minItems": 1}},
                "required": ["artifact_references"],
            },
        ]
    return schema


class CompletionEvidenceInput(CanonicalResponse):
    model_config = ConfigDict(
        json_schema_extra=_completion_evidence_count_json_schema(
            require_nonempty=False
        )
    )

    verification_results: list[VerificationResultInput] = Field(
        default_factory=list,
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
    )
    artifact_references: list[ArtifactReferenceInput] = Field(
        default_factory=list,
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
        json_schema_extra={
            "x-unique-by": ["artifact_type", "reference"],
        },
    )

    @model_validator(mode="after")
    def enforce_aggregate(self) -> Self:
        _validate_completion_evidence_aggregate(
            self.verification_results,
            self.artifact_references,
            require_nonempty=False,
        )
        return self

    @property
    def is_empty(self) -> bool:
        return not self.verification_results and not self.artifact_references


def _rejected_null_completion_evidence(value: object) -> object:
    if value is None:
        raise ValueError("completion_evidence cannot be null.")
    return value


CompletionEvidenceArgument = Annotated[
    CompletionEvidenceInput | SkipJsonSchema[None],
    BeforeValidator(_rejected_null_completion_evidence),
    Field(json_schema_extra=_omit_default_from_json_schema),
]


class CompletionEvidencePayloadRead(CanonicalResponse):
    model_config = ConfigDict(
        json_schema_extra=_completion_evidence_count_json_schema(
            require_nonempty=True
        )
    )

    verification_results: list[VerificationResultRead] = Field(
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
    )
    artifact_references: list[ArtifactReferenceRead] = Field(
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
    )

    @model_validator(mode="after")
    def enforce_aggregate(self) -> Self:
        _validate_completion_evidence_aggregate(
            self.verification_results,
            self.artifact_references,
            require_nonempty=True,
        )
        for values in (self.verification_results, self.artifact_references):
            if [value.position for value in values] != list(range(len(values))):
                raise ValueError("Completion evidence positions must be contiguous and ordered.")
            if len({value.id for value in values}) != len(values):
                raise ValueError("Completion evidence IDs cannot repeat within one family.")
        return self


class CompletionEvidenceEpisodeRead(CanonicalResponse):
    model_config = ConfigDict(
        json_schema_extra=_completion_evidence_count_json_schema(
            require_nonempty=False
        )
    )

    completion_event_id: CompletionEventID
    completion_checkpoint: CheckpointPointer
    verification_results: list[VerificationResultRead] = Field(
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
    )
    artifact_references: list[ArtifactReferenceRead] = Field(
        max_length=MAX_COMPLETION_EVIDENCE_ENTRIES,
    )

    @model_validator(mode="after")
    def enforce_episode(self) -> Self:
        checkpoint = self.completion_checkpoint
        if checkpoint.kind != "completion":
            raise ValueError("Completion evidence requires a completion checkpoint.")
        for values in (self.verification_results, self.artifact_references):
            if [value.position for value in values] != list(range(len(values))):
                raise ValueError("Completion evidence positions must be contiguous and ordered.")
            if len({value.id for value in values}) != len(values):
                raise ValueError("Completion evidence IDs cannot repeat within one family.")
            for value in values:
                if (
                    value.work_item_id != checkpoint.work_item_id
                    or value.completion_checkpoint_id != checkpoint.id
                    or value.created_at != checkpoint.created_at
                ):
                    raise ValueError("Completion evidence parent or timestamp is incoherent.")
        _validate_completion_evidence_aggregate(
            self.verification_results,
            self.artifact_references,
            require_nonempty=False,
        )
        return self


def _validate_completion_evidence_page_identity(page: CompletionEvidencePage) -> None:
    if page.is_duplicate:
        if (
            page.canonical_work_item_id == page.work_item_id
            or page.current_completion_checkpoint_id is not None
        ):
            raise ValueError("Duplicate evidence pages require a distinct canonical identity.")
    elif page.canonical_work_item_id != page.work_item_id:
        raise ValueError("Canonical evidence pages must identify their requested work item.")
    if page.lifecycle_status != "done" and page.current_completion_checkpoint_id is not None:
        raise ValueError("Only done canonical work can identify a current completion.")


def _validate_completion_evidence_page_counts(page: CompletionEvidencePage) -> None:
    validate_page_bounds(count=len(page.items), total=page.total, limit=page.limit)
    structured_on_page = sum(
        bool(item.verification_results or item.artifact_references) for item in page.items
    )
    if not structured_on_page <= page.structured_completion_total <= page.total:
        raise ValueError("Completion evidence structured totals are incoherent.")


def _validate_completion_evidence_page_items(page: CompletionEvidencePage) -> None:
    event_ids = [int(item.completion_event_id) for item in page.items]
    if event_ids != sorted(set(event_ids), reverse=True):
        raise ValueError("Completion evidence episodes must be unique and newest first.")
    checkpoint_ids = [item.completion_checkpoint.id for item in page.items]
    if len(checkpoint_ids) != len(set(checkpoint_ids)):
        raise ValueError("Completion evidence checkpoints cannot repeat.")
    for field_name in ("verification_results", "artifact_references"):
        child_ids = [
            child.id
            for item in page.items
            for child in getattr(item, field_name)
        ]
        if len(child_ids) != len(set(child_ids)):
            raise ValueError("Completion evidence child IDs cannot repeat across episodes.")
    if any(
        item.completion_checkpoint.work_item_id != page.work_item_id for item in page.items
    ):
        raise ValueError("Completion evidence episode belongs to another work item.")
    if page.as_of_completion_event_id is not None and any(
        event_id > int(page.as_of_completion_event_id) for event_id in event_ids
    ):
        raise ValueError("Completion evidence page exceeds its high-water identity.")


def _validate_completion_evidence_page_state(page: CompletionEvidencePage) -> None:
    if page.total == 0:
        if page.as_of_completion_event_id is not None or page.items or page.next_cursor:
            raise ValueError("An empty evidence history cannot carry event state.")
    else:
        if page.as_of_completion_event_id is None:
            raise ValueError("A nonempty evidence history requires a high-water identity.")
        if not page.items:
            raise ValueError("A nonempty evidence history page must contain an episode.")
    if page.next_cursor is None:
        return
    if len(page.items) != page.limit or page.total <= len(page.items):
        raise ValueError("A continuation cursor requires a complete nonterminal page.")
    cursor = completion_evidence_cursor_document(page.next_cursor)
    if (
        cursor["work_item_id"] != str(page.work_item_id)
        or cursor["as_of_completion_event_id"] != page.as_of_completion_event_id
        or cursor["last_completion_event_id"] != page.items[-1].completion_event_id
    ):
        raise ValueError("Completion evidence continuation cursor is incoherent.")


class CompletionEvidencePage(CanonicalResponse):
    work_item_id: UUID
    work_version: StrictInt = Field(ge=1, le=MAX_COMPLETION_WORK_VERSION)
    lifecycle_status: Status
    is_duplicate: StrictBool
    canonical_work_item_id: UUID
    current_completion_checkpoint_id: UUID | None
    as_of_completion_event_id: CompletionEventID | None
    items: list[CompletionEvidenceEpisodeRead] = Field(max_length=10)
    total: StrictInt = Field(ge=0, le=MAX_COMPLETION_EVENT_ID)
    structured_completion_total: StrictInt = Field(ge=0, le=MAX_COMPLETION_EVENT_ID)
    limit: StrictInt = Field(ge=1, le=10)
    next_cursor: CompletionEvidenceCursor | None

    @model_validator(mode="after")
    def enforce_page(self) -> Self:
        _validate_completion_evidence_page_identity(self)
        _validate_completion_evidence_page_counts(self)
        _validate_completion_evidence_page_items(self)
        _validate_completion_evidence_page_state(self)
        return self


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
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )

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

class WorkUpdateRead(WorkItemRead):
    """Flattened update result; old receipts keep the report field absent."""

    job_completion_report: Annotated[
        JobCompletionReportRead | SkipJsonSchema[None],
        Field(json_schema_extra=_omit_default_from_json_schema),
    ] = Field(default=None, exclude_if=lambda value: value is None)

    _non_null_report = field_validator("job_completion_report", mode="before")(reject_null_report)

    @model_validator(mode="after")
    def enforce_report_ownership(self) -> Self:
        report = self.job_completion_report
        if report is not None and (
            report.project_id != self.project_id or report.work_item_id != self.id
            or report.closeout_status != self.status or self.status not in {"wont-do", "promoted"}
            or report.closeout_work_version != self.version
            or report.work_title_at_closeout != self.title
        ):
            raise ValueError("Update report disagrees with its terminal work identity.")
        return self


class WorkIdentityPointer(CanonicalResponse):
    id: UUID
    title: str
    status: Status


class DuplicateCandidateSummary(CanonicalResponse):
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )

    work_item_id: UUID
    title: TitleEventText
    summary: SummaryEventText
    status: Status
    updated_at: UTCDateTime
    duplicate_member_count: StrictInt = Field(ge=0)

class DuplicateSuggestion(CanonicalResponse):
    canonical_work: DuplicateCandidateSummary
    matched_member: WorkIdentityPointer
    rank: StrictInt = Field(ge=1, le=10)
    signals: list[DuplicateSuggestionSignal] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def enforce_candidate_contract(self) -> Self:
        signal_order = {"exact_title": 0, "lexical": 1, "semantic": 2}
        signal_positions = [signal_order[signal] for signal in self.signals]
        if signal_positions != sorted(set(signal_positions)):
            raise ValueError("Suggestion signals must be unique and canonically ordered.")
        canonical = self.canonical_work
        matched = self.matched_member
        if matched.id == canonical.work_item_id and (
            matched.title != canonical.title or matched.status != canonical.status
        ):
            raise ValueError("A root match must identify the canonical candidate exactly.")
        if (
            matched.id != canonical.work_item_id
            and canonical.duplicate_member_count == 0
        ):
            raise ValueError("An alias match requires a duplicate group member.")
        return self


class DuplicateSuggestionPage(CanonicalResponse):
    items: list[DuplicateSuggestion] = Field(max_length=10)
    limit: StrictInt = Field(ge=1, le=10)
    mode: DuplicateSuggestionMode
    semantic_available: StrictBool
    semantic_scope: DuplicateSuggestionSemanticScope
    composition_version: Literal["duplicate-suggestion-v1"]
    exact_title_group_total: StrictInt = Field(ge=0)
    omitted_exact_title_group_count: StrictInt = Field(ge=0)

    external_items: Annotated[
        list[ExternalDuplicateSuggestion] | SkipJsonSchema[None],
        Field(max_length=10, json_schema_extra=_omit_default_from_json_schema),
    ] = Field(default=None, exclude_if=lambda value: value is None)
    external_candidate_count: Annotated[
        StrictInt | SkipJsonSchema[None],
        Field(ge=1, le=64, json_schema_extra=_omit_default_from_json_schema),
    ] = Field(default=None, exclude_if=lambda value: value is None)
    external_scope: Annotated[
        Literal["hybrid", "lexical", "unavailable"] | SkipJsonSchema[None],
        Field(json_schema_extra=_omit_default_from_json_schema),
    ] = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("external_items", "external_candidate_count", "external_scope", mode="before")
    @classmethod
    def reject_null_external_extension(cls, value: object) -> object:
        if value is None:
            raise ValueError("External response fields cannot be null.")
        return value

    @model_validator(mode="after")
    def enforce_external_contract(self) -> Self:
        validate_external_page(self)
        return self

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        if len(self.items) > self.limit:
            raise ValueError("Suggestion items cannot exceed the requested limit.")
        if [item.rank for item in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("Suggestion ranks must be contiguous and ordered.")

        canonical_ids = [item.canonical_work.work_item_id for item in self.items]
        if len(canonical_ids) != len(set(canonical_ids)):
            raise ValueError("Suggestion candidates must have unique canonical roots.")
        matched_ids = [item.matched_member.id for item in self.items]
        if len(matched_ids) != len(set(matched_ids)):
            raise ValueError("Suggestion candidates must have unique matched members.")

        visible_exact = min(self.exact_title_group_total, self.limit)
        if len(self.items) < visible_exact:
            raise ValueError("Exact-title groups must fill available response slots first.")
        exact_items = [item for item in self.items if "exact_title" in item.signals]
        if self.items[:visible_exact] != exact_items:
            raise ValueError("Exact-title suggestion groups must be returned first.")
        if len(exact_items) != visible_exact or (
            self.omitted_exact_title_group_count
            != self.exact_title_group_total - visible_exact
        ):
            raise ValueError("Exact-title suggestion counts are incoherent.")

        expected_mode = {
            "hybrid_full": (True, "full_project"),
            "hybrid_shortlist": (True, "lexical_shortlist"),
            "lexical": (False, "unavailable"),
        }[self.mode]
        if (self.semantic_available, self.semantic_scope) != expected_mode:
            raise ValueError("Suggestion semantic mode and scope are incoherent.")
        if not self.semantic_available and any(
            "semantic" in item.signals for item in self.items
        ):
            raise ValueError("Lexical suggestions cannot claim semantic evidence.")
        return self


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

    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )

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

    @model_validator(mode="after")
    def enforce_projection(self) -> Self:
        direction, counterpart_id = _relationship_projection(
            self.relationship, self.relative_to_work_item_id
        )
        if self.direction != direction or self.counterpart.id != counterpart_id:
            raise ValueError("Relationship adjacency must match its endpoints.")
        return self


class RelationshipPage(CanonicalResponse):
    items: list[AdjacentRelationshipRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.relationship.id,
        )
        return self


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
        validate_page_items(
            self.items, total=self.total, limit=self.limit, key=lambda item: item.gate.id,
        )
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
        validate_page_items(
            self.items, total=self.total, limit=self.limit, key=lambda item: item.id,
        )
        return self


class WorkItemPointer(CanonicalResponse):
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )

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
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.summary.work_item.id,
        )
        if self.items and not (
            all(isinstance(item, WorkSearchHit) for item in self.items)
            or all(isinstance(item, HierarchySummary) for item in self.items)
        ):
            raise ValueError("A work page cannot mix full search hits and hierarchy roots.")
        return self


class ReadyWorkPage(CanonicalResponse):
    """Strict pointer-only ready envelope, deliberately separate from search."""

    items: list[WorkSummaryMinimal]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_ready_page_contract(self) -> Self:
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.work_item.id,
        )
        if any(
            item.work_item.status != "pending"
            or item.display_state not in {"pending", "dropped"}
            for item in self.items
        ):
            raise ValueError("Ready-work pages may contain only pending or dropped ready items.")
        return self


class CheckpointPage(CanonicalResponse):
    items: list[CheckpointRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.id,
        )
        return self


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
    maximum_bytes = 131072 if event.event_type in {
        "work_created", "work_updated", "work_status_changed", "work_reopened"
    } else 16384
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > maximum_bytes:
        raise ValueError("Event metadata exceeds its event-specific byte bound.")
    if event.event_type in {"work_updated", "work_status_changed", "work_reopened"}:
        return _validated_status_metadata(event, payload)
    if event.event_type in _RELATIONSHIP_EVENT_TYPES:
        return _validated_relationship_metadata(event, payload)
    if event.event_type in {"human_attention_requested", "human_attention_resolved"}:
        return HumanGateEventMetadata.model_validate(payload)
    return _validated_other_event_metadata(event, payload)


class WorkEventPage(CanonicalResponse):
    items: list[WorkEventRead]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    pre_phase5_history_may_be_incomplete: bool

    @model_validator(mode="after")
    def enforce_page_contract(self) -> Self:
        validate_page_items(
            self.items, total=self.total, limit=self.limit, offset=self.offset,
            key=lambda item: item.id,
        )
        return self


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
                if (
                    edge.id in seen_relationship_ids
                    or edge.project_id != self.work_item.project_id
                    or adjacent.relative_to_work_item_id != self.work_item.id
                    or adjacent.direction != direction
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


def _relationship_projection(
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
    raise ValueError("Relationship does not touch its relative work item.")


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
    external_references: OmissionOnlyExternalReferences = Field(
        default=None, exclude_if=lambda value: value is None,
    )

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
    job_completion_report: Annotated[
        JobCompletionReportRead | SkipJsonSchema[None],
        Field(json_schema_extra=_omit_default_from_json_schema),
    ] = Field(default=None, exclude_if=lambda value: value is None)

    _non_null_report = field_validator("job_completion_report", mode="before")(reject_null_report)

    @model_validator(mode="after")
    def enforce_report_ownership(self) -> Self:
        report = self.job_completion_report
        if report is not None and (
            report.project_id != self.work_item.project_id
            or report.work_item_id != self.work_item.id
            or report.closeout_status != "done" or self.work_item.status != "done"
            or report.closeout_work_version != self.work_item.version
            or report.work_title_at_closeout != self.work_item.title
            or report.completion_checkpoint_id != self.checkpoint.id
            or report.actor_client != self.checkpoint.source_client
            or report.actor_session_id != self.checkpoint.source_session_id
            or report.actor_model != self.checkpoint.source_model
        ):
            raise ValueError("Completion report disagrees with its exact completion episode.")
        return self

    completion_evidence: Annotated[
        CompletionEvidencePayloadRead | SkipJsonSchema[None],
        Field(json_schema_extra=_omit_default_from_json_schema),
    ] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("completion_evidence", mode="before")
    @classmethod
    def reject_null_or_empty_evidence(cls, value: object) -> object:
        if value is None:
            raise ValueError("A present completion_evidence response cannot be null.")
        if isinstance(value, dict) and not any(
            value.get(name) for name in ("verification_results", "artifact_references")
        ):
            raise ValueError("A present completion_evidence response cannot be empty.")
        return value

    @model_validator(mode="after")
    def enforce_completion_evidence_ownership(self) -> Self:
        if (
            self.checkpoint.work_item_id != self.work_item.id
            or self.checkpoint.kind != "completion"
        ):
            raise ValueError("Completion response identities are incoherent.")
        if self.completion_evidence is None:
            return self
        for values in (
            self.completion_evidence.verification_results,
            self.completion_evidence.artifact_references,
        ):
            for value in values:
                if (
                    value.work_item_id != self.work_item.id
                    or value.completion_checkpoint_id != self.checkpoint.id
                    or value.created_at != self.checkpoint.created_at
                ):
                    raise ValueError("Completion evidence parent or timestamp is incoherent.")
        return self


class WorkDeletionResult(CanonicalResponse):
    deleted: bool = True
    project_id: UUID
    work_item_id: UUID
    version: int
