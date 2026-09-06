"""Strict wire models. Agent-authored prompt text is never stripped or rewritten."""

import ipaddress
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
    BeforeValidator,
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
from pydantic.json_schema import SkipJsonSchema, WithJsonSchema

from mnemonic_api.code_review_schemas import (
    CodeReviewContext,
    CodeReviewHandoffInput,
    CodeReviewRead,
    CodeReviewRecommendationAnswer,
    CodeReviewRemediationRead,
    CodeReviewResultInput,
    CodeReviewResultRead,
    ReviewMode,
    ReviewPolicyRead,
    ReviewThreshold,
    ReviewVersion,
    ScopeHash,
    WorkFollowUpAnswerRead,
    WorkFollowUpRead,
    scope_hash,
)
from mnemonic_api.external_duplicate_schemas import ExternalCandidates, ExternalSuggestionFields
from mnemonic_api.external_references import ExternalReferences, ExternalURL
from mnemonic_api.phase12_schemas import (
    AuthoringPrompt,
    HumanDismissalRead,
    JobCompletionReportFollowUpRead,
    JobCompletionReportInput,
    JobCompletionReportRead,
    PositiveRevision,
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

COMPLETION_EVIDENCE_MAX_ENTRIES = 20
COMPLETION_EVIDENCE_MAX_BYTES = 32768
COMPLETION_HISTORY_MAX_LIMIT = 10
COMPLETION_HISTORY_MAX_BYTES = 3 * 1024 * 1024
COMPLETION_EVENT_ID_MAX = 9223372036854775806
COMPLETION_EXPECTED_VERSION_MAX = 2147483646
_OBSERVED_AT_PATTERN = re.compile(
    r"^(?P<year>(?!0000)[0-9]{4})-"
    r"(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12][0-9]|3[01])"
    r"T(?P<hour>[01][0-9]|2[0-3]):(?P<minute>[0-5][0-9]):"
    r"(?P<second>[0-5][0-9])"
    r"(?:\.(?P<fraction>[0-9]{1,6}))?"
    r"(?P<zone>Z|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):"
    r"(?P<offset_minute>[0-9]{2}))$"
)
_COMPLETION_EVENT_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,18}$")
_COMPLETION_OPERATION_ID_SCHEMA_PATTERN = (
    r"^(?:[0-9A-Fa-f]{32}|"
    r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}|"
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}|"
    r"urn:uuid:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?![\s\S])"
)
_ARTIFACT_URL_TYPES = frozenset({"pull_request", "test_run", "external_issue", "build_artifact"})
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
    rf"^https://(?:"
    rf"{_IPV4_SCHEMA_PATTERN}"
    rf"|(?![0-9.]+(?::(?:{_PORT_SCHEMA_PATTERN}))?/)"
    rf"(?=[^/:]{{1,253}}(?::(?:{_PORT_SCHEMA_PATTERN}))?/)"
    rf"{_DNS_SCHEMA_PATTERN}"
    rf"|{_IPV6_SCHEMA_PATTERN})"
    rf"(?::(?!443/){_PORT_SCHEMA_PATTERN})?/"
    rf"(?!{_URL_DOT_SEGMENT_SCHEMA_PATTERN}(?:/|{_JSON_SCHEMA_EXACT_END}))"
    rf"{_URL_PATH_CHARACTER_SCHEMA_PATTERN}*"
    rf"(?:/(?!{_URL_DOT_SEGMENT_SCHEMA_PATTERN}(?:/|{_JSON_SCHEMA_EXACT_END}))"
    rf"{_URL_PATH_CHARACTER_SCHEMA_PATTERN}*)*{_JSON_SCHEMA_EXACT_END}"
)
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


def _bounded_evidence_text(
    value: str,
    *,
    field: str,
    max_characters: int,
    max_bytes: int,
) -> str:
    nonblank(value)
    if len(value) > max_characters:
        raise ValueError(f"{field} must contain at most {max_characters} characters")
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} must contain at most {max_bytes} UTF-8 bytes")
    return value


def evidence_name(value: str) -> str:
    return _bounded_evidence_text(value, field="name", max_characters=200, max_bytes=800)


def evidence_summary(value: str) -> str:
    return _bounded_evidence_text(value, field="summary", max_characters=4000, max_bytes=16000)


def evidence_command(value: str) -> str:
    return _bounded_evidence_text(value, field="command", max_characters=4096, max_bytes=16384)


def artifact_label(value: str) -> str:
    return _bounded_evidence_text(value, field="label", max_characters=200, max_bytes=800)


def artifact_branch(value: str) -> str:
    _bounded_evidence_text(value, field="branch", max_characters=200, max_bytes=800)
    if value != value.strip():
        raise ValueError("Artifact branches cannot contain edge whitespace")
    return value


def observed_commit(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{7,64}", value) is None:
        raise ValueError("Observed commits require lowercase hexadecimal IDs")
    return value


def repository_artifact_path(value: str) -> str:
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("Repository paths must contain valid ASCII") from exc
    if not encoded or len(encoded) > 512:
        raise ValueError("Repository paths must contain between 1 and 512 bytes")
    allowed = _AFFECTED_PATH_COMPONENT_CHARACTERS - {"*"}
    components = value.split("/")
    if any(component in {"", ".", ".."} for component in components):
        raise ValueError("Repository paths must contain root-relative components")
    if any(character not in allowed for component in components for character in component):
        raise ValueError("Repository paths contain an unsupported character")
    return value


def artifact_https_url(value: str) -> str:
    no_nul(value)
    try:
        encoded = value.encode("ascii")
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeEncodeError, ValueError) as exc:
        raise ValueError("Artifact URLs must be unambiguous ASCII HTTPS URLs") from exc
    authority = value[len("https://") :].split("/", 1)[0]
    if authority.startswith("["):
        closing_bracket = authority.find("]")
        raw_hostname = authority[1:closing_bracket] if closing_bracket >= 0 else ""
    else:
        raw_hostname = authority.split(":", 1)[0]
    hostname = parsed.hostname
    host_is_valid = False
    if hostname is not None:
        if ":" in hostname:
            try:
                ipv6_address = ipaddress.IPv6Address(hostname)
                host_is_valid = (
                    "." not in hostname
                    and re.fullmatch(r"[0-9a-f:]+", hostname) is not None
                    and str(ipv6_address) == hostname
                )
            except ValueError:
                host_is_valid = False
        elif re.fullmatch(r"[0-9.]+", hostname) is not None:
            try:
                host_is_valid = str(ipaddress.IPv4Address(hostname)) == hostname
            except ipaddress.AddressValueError:
                host_is_valid = False
        elif len(hostname) <= 253:
            host_is_valid = all(
                1 <= len(label) <= 63
                and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) is not None
                for label in hostname.split(".")
            )
    if (
        not 1 <= len(encoded) <= 2000
        or not value.startswith("https://")
        or any(delimiter in value for delimiter in ("\\", "?", "#"))
        or parsed.scheme != "https"
        or not parsed.netloc
        or authority.endswith(":")
        or raw_hostname != hostname
        or not host_is_valid
        or parsed.username is not None
        or parsed.password is not None
        or port == 443
        or not parsed.path
        or any(ord(character) <= 32 or ord(character) >= 127 for character in value)
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
        or (
            parsed.port is not None
            and re.fullmatch(r"0|[1-9][0-9]{0,4}", parsed.netloc.rsplit(":", 1)[-1]) is None
        )
    ):
        raise ValueError(
            "Artifact URLs must be exact ASCII HTTPS URLs without credentials, query, or fragment"
        )
    return value


def parse_observed_at(value: object) -> datetime:
    if not isinstance(value, str) or not value.isascii() or not 20 <= len(value) <= 32:
        raise ValueError("observed_at must be a 20-32 byte ASCII RFC 3339 timestamp")
    match = _OBSERVED_AT_PATTERN.fullmatch(value)
    if match is None or value.endswith("-00:00"):
        raise ValueError("observed_at must use the supported RFC 3339 grammar")
    offset_hour = int(match.group("offset_hour") or 0)
    offset_minute = int(match.group("offset_minute") or 0)
    if offset_hour > 14 or offset_minute > 59 or (offset_hour == 14 and offset_minute != 0):
        raise ValueError("observed_at UTC offset is outside the supported range")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
        utc_value = parsed.astimezone(UTC)
    except (OverflowError, ValueError) as exc:
        raise ValueError("observed_at is not a finite representable instant") from exc
    if not 1 <= utc_value.year <= 9999:
        raise ValueError("observed_at is outside the supported UTC range")
    return utc_value


def canonical_observed_at(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("observed_at must include a UTC offset")
    utc_value = value.astimezone(UTC)
    timespec = "seconds" if utc_value.microsecond == 0 else "microseconds"
    return utc_value.isoformat(timespec=timespec).replace("+00:00", "Z")


def completion_event_id(value: str) -> int:
    if not isinstance(value, str) or _COMPLETION_EVENT_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("Completion event IDs must be canonical positive decimal strings")
    parsed = int(value)
    if parsed > COMPLETION_EVENT_ID_MAX:
        raise ValueError("Completion event ID is outside the supported range")
    return parsed


def _bounded_positive_decimal_schema_pattern(maximum: int) -> str:
    """Return an anchored decimal-string grammar whose numeric value is bounded."""
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
    COMPLETION_EVENT_ID_MAX
)


def _two_digit_range_schema_pattern(minimum: int, maximum: int) -> str:
    values = [f"{value:02d}" for value in range(minimum, maximum + 1)]
    return values[0] if len(values) == 1 else "(?:" + "|".join(values) + ")"


_TIMESTAMP_MINUTE_SCHEMA_PATTERN = r"[0-5][0-9]"
_TIMESTAMP_SECOND_SCHEMA_PATTERN = r"[0-5][0-9](?:\.[0-9]{1,6})?"
_BOUNDARY_OFFSET_HOUR_SCHEMA_PATTERN = r"(?:0[0-9]|1[0-4])"


def _timestamp_boundary_exclusion_schema_pattern() -> str:
    """Exclude exactly the local timestamps whose offset leaves years 1..9999."""

    lower_hour_overflow = (
        "(?:"
        + "|".join(
            f"{local_hour:02d}:{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}:"
            f"{_TIMESTAMP_SECOND_SCHEMA_PATTERN}\\+"
            f"{_two_digit_range_schema_pattern(local_hour + 1, 14)}:"
            f"{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}"
            for local_hour in range(14)
        )
        + ")"
    )
    equal_lower_hours = (
        "(?:"
        + "|".join(
            f"{hour:02d}:{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}:"
            f"{_TIMESTAMP_SECOND_SCHEMA_PATTERN}\\+{hour:02d}:"
            f"{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}"
            for hour in range(15)
        )
        + ")"
    )
    lower_minute_overflow = (
        "(?:"
        + "|".join(
            f"{minute:02d}:{_TIMESTAMP_SECOND_SCHEMA_PATTERN}\\+"
            f"{_BOUNDARY_OFFSET_HOUR_SCHEMA_PATTERN}:"
            f"{_two_digit_range_schema_pattern(minute + 1, 59)}"
            for minute in range(59)
        )
        + ")"
    )

    upper_hour_overflow = (
        "(?:"
        + "|".join(
            f"{local_hour:02d}:{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}:"
            f"{_TIMESTAMP_SECOND_SCHEMA_PATTERN}-"
            f"{_two_digit_range_schema_pattern(24 - local_hour, 14)}:"
            f"{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}"
            for local_hour in range(10, 24)
        )
        + ")"
    )
    equal_upper_hours = (
        "(?:"
        + "|".join(
            f"{local_hour:02d}:{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}:"
            f"{_TIMESTAMP_SECOND_SCHEMA_PATTERN}-{23 - local_hour:02d}:"
            f"{_TIMESTAMP_MINUTE_SCHEMA_PATTERN}"
            for local_hour in range(9, 24)
        )
        + ")"
    )
    upper_minute_overflow = (
        "(?:"
        + "|".join(
            f"{minute:02d}:{_TIMESTAMP_SECOND_SCHEMA_PATTERN}-"
            f"{_BOUNDARY_OFFSET_HOUR_SCHEMA_PATTERN}:"
            f"{_two_digit_range_schema_pattern(60 - minute, 59)}"
            for minute in range(1, 60)
        )
        + ")"
    )

    return (
        rf"(?!0001-01-01T{lower_hour_overflow})"
        rf"(?!(?=0001-01-01T{equal_lower_hours})"
        rf"0001-01-01T{_BOUNDARY_OFFSET_HOUR_SCHEMA_PATTERN}:"
        rf"{lower_minute_overflow})"
        rf"(?!9999-12-31T{upper_hour_overflow})"
        rf"(?!(?=9999-12-31T{equal_upper_hours})"
        rf"9999-12-31T(?:0[9]|1[0-9]|2[0-3]):{upper_minute_overflow})"
    )


_OBSERVED_AT_VALIDATION_SCHEMA_PATTERN = (
    r"^(?![\s\S]*-00:00(?![\s\S]))"
    + _timestamp_boundary_exclusion_schema_pattern()
    + r"[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))"
    rf"{_JSON_SCHEMA_EXACT_END}"
)
_CANONICAL_UTC_TIMESTAMP_SCHEMA = {
    "type": "string",
    "format": "date-time",
    "minLength": 20,
    "maxLength": 27,
    "pattern": (
        r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
        r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
        rf"(?:\.[0-9]{{6}})?Z{_JSON_SCHEMA_EXACT_END}"
    ),
}


def canonical_or_database_time(value: object) -> datetime:
    """Accept aware database datetimes or exact canonical UTC wire strings."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Record time must include a UTC offset")
        utc_value = value.astimezone(UTC)
        if not 1 <= utc_value.year <= 9999:
            raise ValueError("Record time is outside the supported range")
        return utc_value
    parsed = parse_observed_at(value)
    if canonical_observed_at(parsed) != value:
        raise ValueError("Record time must use canonical UTC spelling")
    return parsed


EvidenceName = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=200, pattern=r"\S"),
    Field(
        description="Nonblank evidence name; at most 200 characters and 800 UTF-8 bytes.",
        json_schema_extra={
            "pattern": _EVIDENCE_TEXT_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 800,
        },
    ),
    AfterValidator(evidence_name),
]
EvidenceSummary = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4000, pattern=r"\S"),
    Field(
        description="Nonblank evidence summary; at most 4000 characters and 16000 UTF-8 bytes.",
        json_schema_extra={
            "pattern": _EVIDENCE_TEXT_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 16000,
        },
    ),
    AfterValidator(evidence_summary),
]
EvidenceCommand = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4096, pattern=r"\S"),
    Field(
        description="Nonblank executed command; at most 4096 characters and 16384 UTF-8 bytes.",
        json_schema_extra={
            "pattern": _EVIDENCE_TEXT_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 16384,
        },
    ),
    AfterValidator(evidence_command),
]
ArtifactLabel = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=200, pattern=r"\S"),
    Field(
        description="Nonblank artifact label; at most 200 characters and 800 UTF-8 bytes.",
        json_schema_extra={
            "pattern": _EVIDENCE_TEXT_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 800,
        },
    ),
    AfterValidator(artifact_label),
]
ArtifactBranch = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=200),
    Field(
        json_schema_extra={
            "pattern": _BRANCH_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 800,
        }
    ),
    AfterValidator(artifact_branch),
]
ArtifactCommit = Annotated[
    str,
    StringConstraints(strict=True, min_length=7, max_length=64),
    Field(json_schema_extra={"pattern": rf"^[0-9a-f]{{7,64}}{_JSON_SCHEMA_EXACT_END}"}),
]
RepositoryArtifactPath = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9._@+=,~/-]+$",
    ),
    Field(
        json_schema_extra={
            "pattern": (
                r"^(?!\.{1,2}(?:/|(?![\s\S])))[A-Za-z0-9._@+=,~-]+"
                r"(?:/(?!\.{1,2}(?:/|(?![\s\S])))[A-Za-z0-9._@+=,~-]+)*"
                rf"{_JSON_SCHEMA_EXACT_END}"
            ),
            "x-utf8-max-bytes": 512,
        }
    ),
    AfterValidator(repository_artifact_path),
]
ArtifactHTTPSURL = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=2000, pattern=r"^https://"),
    Field(
        json_schema_extra={
            "format": "uri",
            "pattern": _HTTPS_ARTIFACT_SCHEMA_PATTERN,
            "x-utf8-max-bytes": 2000,
        }
    ),
    AfterValidator(artifact_https_url),
]
ObservedCommit = Annotated[
    str,
    StringConstraints(strict=True, min_length=7, max_length=64),
    Field(json_schema_extra={"pattern": rf"^[0-9a-f]{{7,64}}{_JSON_SCHEMA_EXACT_END}"}),
    AfterValidator(observed_commit),
]
ObservedAt = Annotated[
    datetime,
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
OmissionOnlyObservedAt = ObservedAt | SkipJsonSchema[None]
OmissionOnlyObservedCommit = ObservedCommit | SkipJsonSchema[None]
OmissionOnlyExitCode = (
    Annotated[StrictInt, Field(ge=-2147483648, le=2147483647)] | SkipJsonSchema[None]
)


def valid_completion_event_id_string(value: str) -> str:
    completion_event_id(value)
    return value


def evidence_cursor_is_valid(value: str) -> str:
    if not value.isascii():
        raise ValueError("Completion evidence cursors must contain ASCII")
    if len(value) % 4 == 1:
        raise ValueError("Completion evidence cursors must be unpadded base64url")
    return value


CompletionEventID = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=19),
    Field(json_schema_extra={"pattern": _COMPLETION_EVENT_ID_SCHEMA_PATTERN}),
    AfterValidator(valid_completion_event_id_string),
]
CompletionEvidenceCursor = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4096),
    Field(
        json_schema_extra={
            "pattern": rf"^(?:[A-Za-z0-9_-]{{4}})*(?:[A-Za-z0-9_-]{{2}}|"
            rf"[A-Za-z0-9_-]{{3}})?{_JSON_SCHEMA_EXACT_END}"
        }
    ),
    AfterValidator(evidence_cursor_is_valid),
]
CompletionOperationID = Annotated[
    UUID,
    WithJsonSchema(
        {"type": "string", "pattern": _COMPLETION_OPERATION_ID_SCHEMA_PATTERN},
        mode="validation",
    ),
    WithJsonSchema({"type": "string", "format": "uuid"}, mode="serialization"),
]
CanonicalRecordTime = Annotated[
    datetime,
    BeforeValidator(canonical_or_database_time),
    WithJsonSchema(_CANONICAL_UTC_TIMESTAMP_SCHEMA),
]
CanonicalTimestampString = Annotated[str, WithJsonSchema(_CANONICAL_UTC_TIMESTAMP_SCHEMA)]
OmissionOnlyCanonicalTime = CanonicalRecordTime | SkipJsonSchema[None]
CompletionCount = Annotated[
    StrictInt,
    Field(ge=0, le=COMPLETION_EVENT_ID_MAX),
    WithJsonSchema(
        {
            "type": "integer",
            "minimum": 0,
            "maximum": COMPLETION_EVENT_ID_MAX,
        }
    ),
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
    "human_attention_requested",
    "human_attention_resolved",
    "work_merged",
    "work_moved",
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


def _reject_explicit_null(data: object, *field_names: str) -> object:
    if isinstance(data, dict):
        for field_name in field_names:
            if field_name in data and data[field_name] is None:
                raise ValueError(f"{field_name} cannot be null")
    return data


def _combined_evidence_schema(*, require_nonempty: bool = False) -> dict[str, object]:
    """Describe the cross-array entry cap in executable JSON Schema."""
    schema: dict[str, object] = {
        "anyOf": [
            {
                "properties": {
                    "verification_results": {"maxItems": result_count},
                    "artifact_references": {
                        "maxItems": COMPLETION_EVIDENCE_MAX_ENTRIES - result_count
                    },
                }
            }
            for result_count in range(COMPLETION_EVIDENCE_MAX_ENTRIES + 1)
        ],
        "description": (
            "At most 20 verification results and artifact references combined. "
            "The aggregate text content is additionally limited to 32768 UTF-8 bytes."
        ),
        "x-utf8-aggregate-max-bytes": COMPLETION_EVIDENCE_MAX_BYTES,
    }
    if require_nonempty:
        schema["allOf"] = [
            {
                "anyOf": [
                    {"properties": {"verification_results": {"minItems": 1}}},
                    {"properties": {"artifact_references": {"minItems": 1}}},
                ]
            }
        ]
    return schema


_COMMAND_RESULT_SCHEMA = {
    "oneOf": [
        {
            "required": ["exit_code"],
            "properties": {
                "outcome": {"const": "passed"},
                "exit_code": {"const": 0},
            },
        },
        {
            "required": ["exit_code"],
            "properties": {
                "outcome": {"const": "failed"},
                "exit_code": {"not": {"const": 0}},
            },
        },
        {
            "properties": {"outcome": {"const": "inconclusive"}},
            "not": {"required": ["exit_code"]},
        },
    ]
}


_ARTIFACT_REFERENCE_SCHEMA = {
    "allOf": [
        {
            "if": {"properties": {"artifact_type": {"const": "commit"}}},
            "then": {
                "properties": {
                    "reference": {
                        "type": "string",
                        "minLength": 7,
                        "maxLength": 64,
                        "pattern": (rf"^[0-9a-f]{{7,64}}{_JSON_SCHEMA_EXACT_END}"),
                    }
                }
            },
        },
        {
            "if": {"properties": {"artifact_type": {"const": "branch"}}},
            "then": {
                "properties": {
                    "reference": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "pattern": _BRANCH_SCHEMA_PATTERN,
                        "x-utf8-max-bytes": 800,
                    }
                }
            },
        },
        {
            "if": {"properties": {"artifact_type": {"const": "repository_path"}}},
            "then": {
                "properties": {
                    "reference": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 512,
                        "pattern": (
                            r"^(?!\.{1,2}(?:/|(?![\s\S])))"
                            r"[A-Za-z0-9._@+=,~-]+"
                            r"(?:/(?!\.{1,2}(?:/|(?![\s\S])))"
                            r"[A-Za-z0-9._@+=,~-]+)*"
                            rf"{_JSON_SCHEMA_EXACT_END}"
                        ),
                        "x-utf8-max-bytes": 512,
                    }
                }
            },
        },
        {
            "if": {
                "properties": {
                    "artifact_type": {
                        "enum": [
                            "pull_request",
                            "test_run",
                            "external_issue",
                            "build_artifact",
                        ]
                    }
                }
            },
            "then": {
                "properties": {
                    "reference": {
                        "type": "string",
                        "format": "uri",
                        "minLength": 1,
                        "maxLength": 2000,
                        "pattern": _HTTPS_ARTIFACT_SCHEMA_PATTERN,
                        "x-utf8-max-bytes": 2000,
                    }
                }
            },
        },
    ]
}


class _VerificationInputBase(APIModel):
    name: EvidenceName
    summary: EvidenceSummary
    observed_at: OmissionOnlyObservedAt = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observed_at_commit: OmissionOnlyObservedCommit = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="before")
    @classmethod
    def optional_fields_are_sparse(cls, data: object) -> object:
        return _reject_explicit_null(data, "observed_at", "observed_at_commit")

    @field_validator("observed_at", mode="before")
    @classmethod
    def strict_observed_time(cls, value: object) -> datetime:
        return parse_observed_at(value)

    @field_serializer("observed_at")
    def serialize_observed_time(self, value: datetime | None) -> str:
        assert value is not None
        return canonical_observed_at(value)


class CommandVerificationInput(_VerificationInputBase):
    verification_type: Literal["command"]
    outcome: Literal["passed", "failed", "inconclusive"]
    command: EvidenceCommand
    exit_code: OmissionOnlyExitCode = Field(default=None, exclude_if=lambda value: value is None)

    model_config = ConfigDict(json_schema_extra=_COMMAND_RESULT_SCHEMA)

    @model_validator(mode="before")
    @classmethod
    def exit_code_presence_is_exact(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        outcome = data.get("outcome")
        if outcome in {"passed", "failed"}:
            if "exit_code" not in data or data["exit_code"] is None:
                raise ValueError("Determinate command results require exit_code")
        elif "exit_code" in data:
            raise ValueError("Inconclusive command results omit exit_code")
        return data

    @model_validator(mode="after")
    def command_result_is_coherent(self) -> Self:
        if self.outcome == "passed" and self.exit_code != 0:
            raise ValueError("Passed commands require exit_code 0")
        if self.outcome == "failed" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("Failed commands require a nonzero exit_code")
        if self.outcome == "inconclusive" and self.exit_code is not None:
            raise ValueError("Inconclusive commands omit exit_code")
        return self


class ObservationVerificationInput(_VerificationInputBase):
    verification_type: Literal["observation"]
    outcome: VerificationOutcome


VerificationResultInput = Annotated[
    CommandVerificationInput | ObservationVerificationInput,
    Field(discriminator="verification_type"),
]


class ArtifactReferenceInput(APIModel):
    artifact_type: ArtifactType
    label: ArtifactLabel
    reference: Annotated[str, StringConstraints(strict=True)]

    model_config = ConfigDict(json_schema_extra=_ARTIFACT_REFERENCE_SCHEMA)

    @model_validator(mode="after")
    def reference_matches_type(self) -> Self:
        if self.artifact_type == "commit":
            if re.fullmatch(r"[0-9a-f]{7,64}", self.reference) is None:
                raise ValueError("Commit artifacts require lowercase hexadecimal IDs")
        elif self.artifact_type == "branch":
            artifact_branch(self.reference)
        elif self.artifact_type == "repository_path":
            repository_artifact_path(self.reference)
        elif self.artifact_type in _ARTIFACT_URL_TYPES:
            artifact_https_url(self.reference)
        return self


def completion_evidence_text_bytes(
    results: list[CommandVerificationInput | ObservationVerificationInput],
    artifacts: list[ArtifactReferenceInput],
) -> int:
    total = 0
    for result in results:
        values = (
            result.verification_type,
            result.name,
            result.outcome,
            result.summary,
        )
        total += sum(len(value.encode("utf-8")) for value in values)
        if isinstance(result, CommandVerificationInput):
            total += len(result.command.encode("utf-8"))
        if result.observed_at is not None:
            total += 32
        if result.observed_at_commit is not None:
            total += len(result.observed_at_commit.encode("utf-8"))
    for artifact in artifacts:
        total += sum(
            len(value.encode("utf-8"))
            for value in (artifact.artifact_type, artifact.label, artifact.reference)
        )
    return total


class CompletionEvidenceInput(APIModel):
    verification_results: list[VerificationResultInput] = Field(
        default_factory=list, max_length=COMPLETION_EVIDENCE_MAX_ENTRIES
    )
    artifact_references: list[ArtifactReferenceInput] = Field(
        default_factory=list,
        max_length=COMPLETION_EVIDENCE_MAX_ENTRIES,
        json_schema_extra={"x-unique-by": ["artifact_type", "reference"]},
    )

    model_config = ConfigDict(json_schema_extra=_combined_evidence_schema())

    @model_validator(mode="after")
    def aggregate_is_bounded(self) -> Self:
        total = len(self.verification_results) + len(self.artifact_references)
        if total > COMPLETION_EVIDENCE_MAX_ENTRIES:
            raise ValueError("Completion evidence contains more than 20 entries")
        identities = [
            (artifact.artifact_type, artifact.reference) for artifact in self.artifact_references
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Artifact references must be unique within a completion")
        if (
            completion_evidence_text_bytes(self.verification_results, self.artifact_references)
            > COMPLETION_EVIDENCE_MAX_BYTES
        ):
            raise ValueError("Completion evidence exceeds 32768 UTF-8 bytes")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.verification_results and not self.artifact_references


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
    slug: Slug | None = None
    description: ProjectDescription | None = None
    repository_url: HTTPURL | None = None

    @model_validator(mode="after")
    def editable_fields(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one editable field")
        for field in ("name", "slug", "description"):
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
    expected_revision: PositiveRevision
    recall_pointer_template: RecallPointerTemplate | None = None
    job_completion_report_prompt: AuthoringPrompt | None = None
    code_review_required_min_priority: ReviewThreshold | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_optional_min_priority: ReviewThreshold | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    allow_remediation_code_reviews: StrictBool | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="before")
    @classmethod
    def review_settings_are_nonnull(cls, data: object) -> object:
        return _reject_explicit_null(data, "code_review_required_min_priority",
                                     "code_review_optional_min_priority",
                                     "allow_remediation_code_reviews")

    @model_validator(mode="after")
    def editable_setting(self) -> Self:
        if not self.model_fields_set - {"expected_revision"}:
            raise ValueError("Provide at least one editable setting")
        return self


class ProjectSettingsRead(APIModel):
    project_id: UUID
    recall_pointer_template: str | None
    job_completion_report_prompt: AuthoringPrompt
    revision: PositiveRevision
    code_review_required_min_priority: ReviewThreshold
    code_review_optional_min_priority: ReviewThreshold
    allow_remediation_code_reviews: StrictBool


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
    def validate_affected_paths(cls, value: list[str], info: ValidationInfo) -> list[str]:
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
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
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
    supersede_code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    expected_code_review_version: ReviewVersion | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    supersede_follow_up_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    expected_follow_up_version: ReviewVersion | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="before")
    @classmethod
    def supersession_is_nonnull(cls, data: object) -> object:
        return _reject_explicit_null(data, "supersede_code_review_id",
                                     "expected_code_review_version", "supersede_follow_up_id",
                                     "expected_follow_up_version")

    @model_validator(mode="after")
    def supersession_controls_are_paired(self) -> Self:
        review = self.supersede_code_review_id is not None
        question = self.supersede_follow_up_id is not None
        if review != (self.expected_code_review_version is not None):
            raise ValueError("Review supersession requires its exact ID and expected version")
        if question != (self.expected_follow_up_version is not None):
            raise ValueError("Question supersession requires its exact ID and expected version")
        if (review and question) or ((review or question) and self.status != "pending"):
            raise ValueError("Supersession applies only to explicitly reopening one obligation")
        return self
    external_references: ExternalReferences | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
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

    job_completion_report: JobCompletionReportInput | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
        description=("Required for fresh Done/Won’t do/Promoted closeouts. Sparse omission is "
                     "accepted only to recover an already-completed historical receipt."),
    )

    @field_validator("job_completion_report", mode="before")
    @classmethod
    def report_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("job_completion_report cannot be null")
        return value

    @model_validator(mode="after")
    def editable_fields(self) -> Self:
        fields = self.model_fields_set - {
            "expected_version",
            "lease_token",
            "actor",
            "client_operation_id",
            "job_completion_report",
            "supersede_code_review_id", "expected_code_review_version",
            "supersede_follow_up_id", "expected_follow_up_version",
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


class WorkMoveCreate(APIModel):
    target_project_id: UUID
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


class WorkCompletionRequest(APIModel):
    """Control-free completion intent used after receipt preparation."""

    expected_version: Annotated[StrictInt, Field(ge=1, le=COMPLETION_EXPECTED_VERSION_MAX)]
    code_review_handoff: CodeReviewHandoffInput | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @field_validator("code_review_handoff", mode="before")
    @classmethod
    def review_handoff_is_nonnull(cls, value: object) -> object:
        if value is None:
            raise ValueError("code_review_handoff cannot be null")
        return value
    checkpoint: CompletionCheckpointCreate
    lease_token: LeaseToken | None = Field(default=None, repr=False)
    completion_evidence: CompletionEvidenceInput | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    job_completion_report: JobCompletionReportInput | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
        description=("Required for fresh Done/Won’t do/Promoted closeouts. Sparse omission is "
                     "accepted only to recover an already-completed historical receipt."),
    )

    @field_validator("job_completion_report", mode="before")
    @classmethod
    def report_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("job_completion_report cannot be null")
        return value

    @field_validator("completion_evidence", mode="before")
    @classmethod
    def evidence_is_not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("completion_evidence cannot be null")
        return value

    @field_validator("completion_evidence")
    @classmethod
    def empty_evidence_is_omitted(
        cls, value: CompletionEvidenceInput | None
    ) -> CompletionEvidenceInput | None:
        return None if value is not None and value.is_empty else value


class WorkCompletionCreate(WorkCompletionRequest):
    client_operation_id: CompletionOperationID | None = Field(
        default=None, repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION
    )

    model_config = ConfigDict(
        json_schema_extra={
            "if": {
                "required": ["completion_evidence"],
                "properties": {
                    "completion_evidence": {
                        "type": "object",
                        "anyOf": [
                            {
                                "required": ["verification_results"],
                                "properties": {
                                    "verification_results": {
                                        "type": "array",
                                        "minItems": 1,
                                    }
                                },
                            },
                            {
                                "required": ["artifact_references"],
                                "properties": {
                                    "artifact_references": {
                                        "type": "array",
                                        "minItems": 1,
                                    }
                                },
                            },
                        ],
                    }
                },
            },
            "then": {
                "required": ["client_operation_id"],
                "properties": {
                    "client_operation_id": {
                        "type": "string",
                        "pattern": _COMPLETION_OPERATION_ID_SCHEMA_PATTERN,
                    }
                },
            },
        }
    )

    @model_validator(mode="after")
    def evidence_requires_operation_id(self) -> Self:
        if self.completion_evidence is not None and self.client_operation_id is None:
            raise ValueError("Non-empty completion evidence requires client_operation_id")
        return self


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
    purpose: Literal["implementation", "code_review"] = Field(
        default="implementation", exclude_if=lambda value: value == "implementation",
    )
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    mode: ReviewMode | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="before")
    @classmethod
    def review_identity_is_nonnull(cls, data: object) -> object:
        return _reject_explicit_null(data, "purpose", "code_review_id", "mode")

    @model_validator(mode="after")
    def review_claim_identity(self) -> Self:
        supplied = self.code_review_id is not None and self.mode is not None
        if (self.purpose == "code_review") != supplied:
            raise ValueError("Review claims require exact review ID and mode")
        if self.purpose == "implementation" and (self.code_review_id or self.mode):
            raise ValueError("Implementation claims cannot carry review identity")
        return self


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
                value is not None for value in (*required_resolution_values, self.resolved_by_model)
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


class WorkUpdateRead(WorkItemRead):
    job_completion_report: JobCompletionReportRead | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def report_coherence(self) -> Self:
        if "job_completion_report" in self.model_fields_set and self.job_completion_report is None:
            raise ValueError("job_completion_report cannot be null")
        report = self.job_completion_report
        if report is not None and (
            report.project_id != self.project_id or report.work_item_id != self.id
            or report.closeout_work_version != self.version or report.closeout_status != self.status
            or report.work_title_at_closeout != self.title
        ):
            raise ValueError("Closeout report does not match this work update")
        return self


class WorkMoveRead(APIModel):
    source_project_id: UUID
    target_project_id: UUID
    preserved_status: Status
    work_item: WorkItemRead

    @model_validator(mode="after")
    def moved_work_is_coherent(self) -> Self:
        if self.source_project_id == self.target_project_id:
            raise ValueError("A move requires distinct source and target projects")
        if (
            self.work_item.project_id != self.target_project_id
            or self.work_item.status != self.preserved_status
        ):
            raise ValueError("Moved work does not match the target project and preserved status")
        return self


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
    def validate_affected_paths(cls, value: list[str], info: ValidationInfo) -> list[str]:
        return affected_paths_are_valid(value, info)

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CompletionCheckpointRead(CheckpointRead):
    kind: Literal["completion"]


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


class CompletionCheckpointPointer(CheckpointPointer):
    kind: Literal["completion"]
    created_at: CanonicalRecordTime


class _VerificationReadBase(APIModel):
    id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    position: Annotated[StrictInt, Field(ge=0, le=19)]
    name: EvidenceName
    summary: EvidenceSummary
    observed_at: OmissionOnlyCanonicalTime = Field(
        default=None, exclude_if=lambda value: value is None
    )
    observed_at_commit: OmissionOnlyObservedCommit = Field(
        default=None, exclude_if=lambda value: value is None
    )
    created_at: CanonicalRecordTime

    @model_validator(mode="before")
    @classmethod
    def optional_fields_are_sparse(cls, data: object) -> object:
        return _reject_explicit_null(data, "observed_at", "observed_at_commit")

    @field_validator("observed_at", mode="before")
    @classmethod
    def canonical_or_database_observed_time(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("Stored observed_at must include a UTC offset")
            value = value.astimezone(UTC)
            if not 1 <= value.year <= 9999:
                raise ValueError("Stored observed_at is outside the supported range")
            return value
        parsed = parse_observed_at(value)
        if canonical_observed_at(parsed) != value:
            raise ValueError("Stored observed_at must use canonical UTC spelling")
        return parsed

    @field_validator("created_at")
    @classmethod
    def record_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Evidence record time must include a UTC offset")
        return value

    @field_serializer("observed_at", return_type=CanonicalTimestampString)
    def serialize_observed_time(self, value: datetime | None) -> str:
        assert value is not None
        return canonical_observed_at(value)

    @field_serializer("created_at")
    def serialize_record_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class CommandVerificationRead(_VerificationReadBase):
    verification_type: Literal["command"]
    outcome: Literal["passed", "failed", "inconclusive"]
    command: EvidenceCommand
    exit_code: OmissionOnlyExitCode = Field(default=None, exclude_if=lambda value: value is None)

    model_config = ConfigDict(json_schema_extra=_COMMAND_RESULT_SCHEMA)

    @model_validator(mode="before")
    @classmethod
    def exit_code_presence_is_exact(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        outcome = data.get("outcome")
        if outcome in {"passed", "failed"}:
            if "exit_code" not in data or data["exit_code"] is None:
                raise ValueError("Determinate command results require exit_code")
        elif "exit_code" in data:
            raise ValueError("Inconclusive command results omit exit_code")
        return data

    @model_validator(mode="after")
    def command_result_is_coherent(self) -> Self:
        if self.outcome == "passed" and self.exit_code != 0:
            raise ValueError("Passed commands require exit_code 0")
        if self.outcome == "failed" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("Failed commands require a nonzero exit_code")
        if self.outcome == "inconclusive" and self.exit_code is not None:
            raise ValueError("Inconclusive commands omit exit_code")
        return self


class ObservationVerificationRead(_VerificationReadBase):
    verification_type: Literal["observation"]
    outcome: VerificationOutcome


VerificationResultRead = Annotated[
    CommandVerificationRead | ObservationVerificationRead,
    Field(discriminator="verification_type"),
]


class ArtifactReferenceRead(ArtifactReferenceInput):
    id: UUID
    work_item_id: UUID
    completion_checkpoint_id: UUID
    position: Annotated[StrictInt, Field(ge=0, le=19)]
    created_at: CanonicalRecordTime

    @field_validator("created_at")
    @classmethod
    def record_time_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Artifact record time must include a UTC offset")
        return value

    @field_serializer("created_at")
    def serialize_record_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _completion_evidence_read_text_bytes(
    verification_results: list[CommandVerificationRead | ObservationVerificationRead],
    artifact_references: list[ArtifactReferenceRead],
) -> int:
    total = 0
    for result in verification_results:
        total += sum(
            len(value.encode("utf-8"))
            for value in (
                result.verification_type,
                result.name,
                result.outcome,
                result.summary,
            )
        )
        if isinstance(result, CommandVerificationRead):
            total += len(result.command.encode("utf-8"))
        if result.observed_at is not None:
            total += 32
        if result.observed_at_commit is not None:
            total += len(result.observed_at_commit.encode("utf-8"))
    return total + sum(
        len(value.encode("utf-8"))
        for artifact in artifact_references
        for value in (artifact.artifact_type, artifact.label, artifact.reference)
    )


def _require_completion_evidence_read_size(
    verification_results: list[CommandVerificationRead | ObservationVerificationRead],
    artifact_references: list[ArtifactReferenceRead],
) -> None:
    if (
        _completion_evidence_read_text_bytes(verification_results, artifact_references)
        > COMPLETION_EVIDENCE_MAX_BYTES
    ):
        raise ValueError("Completion evidence exceeds 32768 UTF-8 bytes")


def _evidence_children_are_coherent(
    verification_results: list[CommandVerificationRead | ObservationVerificationRead],
    artifact_references: list[ArtifactReferenceRead],
    *,
    work_item_id: UUID | None = None,
    checkpoint_id: UUID | None = None,
    created_at: datetime | None = None,
) -> None:
    total = len(verification_results) + len(artifact_references)
    if total > COMPLETION_EVIDENCE_MAX_ENTRIES:
        raise ValueError("Completion evidence contains more than 20 entries")
    for family in (verification_results, artifact_references):
        if [item.position for item in family] != list(range(len(family))):
            raise ValueError("Evidence positions must be contiguous and ordered")
        if len({item.id for item in family}) != len(family):
            raise ValueError("Evidence row IDs must be unique")
        for item in family:
            if work_item_id is not None and item.work_item_id != work_item_id:
                raise ValueError("Evidence belongs to another work item")
            if checkpoint_id is not None and item.completion_checkpoint_id != checkpoint_id:
                raise ValueError("Evidence belongs to another completion checkpoint")
            if created_at is not None and item.created_at != created_at:
                raise ValueError("Evidence record time must equal its completion checkpoint")
    identities = [(artifact.artifact_type, artifact.reference) for artifact in artifact_references]
    if len(identities) != len(set(identities)):
        raise ValueError("Artifact references must be unique within a completion")
    _require_completion_evidence_read_size(verification_results, artifact_references)


def _completion_page_identity_is_coherent(page: CompletionEvidencePage) -> None:
    if page.is_duplicate:
        if page.canonical_work_item_id == page.work_item_id:
            raise ValueError("A duplicate evidence page requires another canonical work")
    elif page.canonical_work_item_id != page.work_item_id:
        raise ValueError("A canonical evidence page must point to itself")
    if page.current_completion_checkpoint_id is not None and (
        page.lifecycle_status != "done" or page.is_duplicate
    ):
        raise ValueError("Current completion pointer disagrees with lifecycle state")


def _completion_page_cursor_is_coherent(page: CompletionEvidencePage) -> None:
    if page.total == 0:
        if (
            page.items
            or page.structured_completion_total != 0
            or page.as_of_completion_event_id is not None
            or page.next_cursor is not None
        ):
            raise ValueError("An empty completion history cannot have page state")
        return
    if page.as_of_completion_event_id is None:
        raise ValueError("A retained completion history requires a high-water event")
    if not page.items:
        raise ValueError("A nonempty completion history page requires an episode")
    if page.next_cursor is not None and len(page.items) != page.limit:
        raise ValueError("A continuation cursor requires one full page of episodes")


def _completion_page_items_are_coherent(page: CompletionEvidencePage) -> None:
    event_ids = [completion_event_id(item.completion_event_id) for item in page.items]
    if event_ids != sorted(event_ids, reverse=True) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Completion episodes must be unique and newest first")
    if page.as_of_completion_event_id is not None:
        high_water = completion_event_id(page.as_of_completion_event_id)
        if any(value > high_water for value in event_ids):
            raise ValueError("Completion episode exceeds the page high-water event")
    if any(item.completion_checkpoint.work_item_id != page.work_item_id for item in page.items):
        raise ValueError("Completion episode belongs to another work item")
    structured_items = sum(
        bool(item.verification_results or item.artifact_references) for item in page.items
    )
    if structured_items > page.structured_completion_total:
        raise ValueError("Structured completion total omits a returned episode")


class CompletionEvidencePayloadRead(APIModel):
    verification_results: list[VerificationResultRead] = Field(max_length=20)
    artifact_references: list[ArtifactReferenceRead] = Field(max_length=20)

    model_config = ConfigDict(json_schema_extra=_combined_evidence_schema(require_nonempty=True))

    @model_validator(mode="after")
    def payload_is_nonempty_and_coherent(self) -> Self:
        _evidence_children_are_coherent(self.verification_results, self.artifact_references)
        if not self.verification_results and not self.artifact_references:
            raise ValueError("A completion evidence payload cannot be empty")
        return self


class CompletionEvidenceEpisodeRead(APIModel):
    completion_event_id: CompletionEventID
    completion_checkpoint: CompletionCheckpointPointer
    verification_results: list[VerificationResultRead] = Field(max_length=20)
    artifact_references: list[ArtifactReferenceRead] = Field(max_length=20)

    model_config = ConfigDict(json_schema_extra=_combined_evidence_schema())

    @model_validator(mode="after")
    def episode_is_coherent(self) -> Self:
        checkpoint = self.completion_checkpoint
        if checkpoint.kind != "completion":
            raise ValueError("Evidence episodes require completion checkpoints")
        _evidence_children_are_coherent(
            self.verification_results,
            self.artifact_references,
            work_item_id=checkpoint.work_item_id,
            checkpoint_id=checkpoint.id,
            created_at=checkpoint.created_at,
        )
        return self


class CompletionEvidencePage(APIModel):
    work_item_id: UUID
    work_version: Annotated[StrictInt, Field(ge=1, le=2147483647)]
    lifecycle_status: Status
    is_duplicate: StrictBool
    canonical_work_item_id: UUID
    current_completion_checkpoint_id: UUID | None
    as_of_completion_event_id: CompletionEventID | None
    items: list[CompletionEvidenceEpisodeRead] = Field(max_length=10)
    total: CompletionCount
    structured_completion_total: CompletionCount
    limit: Annotated[StrictInt, Field(ge=1, le=10)]
    next_cursor: CompletionEvidenceCursor | None

    @model_validator(mode="after")
    def page_is_coherent(self) -> Self:
        if (
            self.structured_completion_total > self.total
            or len(self.items) > self.total
            or len(self.items) > self.limit
        ):
            raise ValueError("Completion evidence page totals are inconsistent")
        _completion_page_identity_is_coherent(self)
        _completion_page_cursor_is_coherent(self)
        _completion_page_items_are_coherent(self)
        return self


class CompletionEvidenceListQuery(APIModel):
    limit: Annotated[int, Field(ge=1, le=10)] = 10
    cursor: CompletionEvidenceCursor | SkipJsonSchema[None] = None


class LeasePublic(APIModel):
    purpose: Literal["implementation", "code_review"] = Field(
        default="implementation", exclude_if=lambda value: value == "implementation",
    )
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    mode: ReviewMode | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    holder_client: ClientName
    holder_session_id: SessionID
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def purpose_matches_identity(self) -> Self:
        fields = (self.code_review_id, self.mode)
        if self.purpose == "code_review" and any(value is None for value in fields):
            raise ValueError("Review lease requires exact review identity and mode")
        if self.purpose == "implementation" and any(value is not None for value in fields):
            raise ValueError("Implementation lease cannot carry review identity")
        return self

    @field_serializer("acquired_at", "renewed_at", "expires_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class DashboardLeaseObservation(APIModel):
    """Exact safe active-lease projection reviewed in the dashboard."""

    holder_client: ClientName
    holder_session_id: SessionID
    acquired_at: datetime
    renewed_at: datetime
    expires_at: datetime


class ClaimReceipt(LeasePublic):
    work_item_id: UUID
    claim_request_id: str
    lease_token: str = Field(repr=False)
    lease_generation_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_version: ReviewVersion | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    scope_sha256: ScopeHash | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def review_capability_is_complete(self) -> Self:
        fields = (self.lease_generation_id, self.code_review_version, self.scope_sha256)
        if self.purpose == "code_review" and any(value is None for value in fields):
            raise ValueError("Review claim requires generation, revision and scope hash")
        if self.purpose == "implementation" and any(value is not None for value in fields):
            raise ValueError("Implementation claim cannot carry review capability fields")
        return self


class DashboardWorkActivationCreate(APIModel):
    """Browser-only human decision to represent work as Active with a safe lease."""

    expected_version: Annotated[StrictInt, Field(ge=1)]
    actor: MutationActor
    claim_request_id: ClaimRequestID

    @model_validator(mode="after")
    def require_dashboard_human(self) -> Self:
        if self.actor.actor_client != "dashboard" or self.actor.actor_model is not None:
            raise ValueError("Manual activation requires dashboard human provenance")
        return self


class DashboardWorkPendingCreate(APIModel):
    """Browser-only human decision to clear the exact observed lease state."""

    expected_version: Annotated[StrictInt, Field(ge=1)]
    expected_lease_state: Literal["active", "dropped"]
    expected_active_lease: DashboardLeaseObservation | None = None
    actor: MutationActor

    @model_validator(mode="after")
    def require_observed_dashboard_state(self) -> Self:
        if self.actor.actor_client != "dashboard" or self.actor.actor_model is not None:
            raise ValueError("Manual Pending requires dashboard human provenance")
        if (self.expected_lease_state == "active") != (self.expected_active_lease is not None):
            raise ValueError("Active release requires the exact observed public lease")
        return self


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
    code_review_context: CodeReviewContext | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def requested_identity_is_coherent(self) -> Self:
        _validate_code_review_context_scope(self.code_review_context, self.work_item)
        requested = WorkIdentityPointer(
            id=self.work_item.id,
            title=self.work_item.title,
            status=self.work_item.status,
        )
        if not self.canonical.is_duplicate and self.canonical.canonical_work_item != requested:
            raise ValueError("A canonical root must point to itself")
        if self.canonical.is_duplicate and self.work_item.id in {
            item.id for item in self.canonical.path
        }:
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
    project_id: UUID
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
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
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
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
DuplicateSuggestionSemanticScope = Literal["full_project", "lexical_shortlist", "unavailable"]
_DUPLICATE_SUGGESTION_SIGNAL_ORDER = ("exact_title", "lexical", "semantic")


class DuplicateSuggestionRequest(APIModel):
    """An ephemeral create draft; no authority or mutation control is accepted."""

    title: Title
    summary: Summary
    initial_prompt: Prompt
    tags: Tags = Field(default_factory=list)
    exclude_work_item_id: UUID | None = None
    external_candidates: ExternalCandidates = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    limit: Annotated[StrictInt, Field(ge=1, le=10)] = 5

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.lower() for tag in value))


class DuplicateCandidateSummary(APIModel):
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
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


class DuplicateSuggestionPage(APIModel, ExternalSuggestionFields):
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
        if self.external_items is not None and len(self.external_items) > self.limit:
            raise ValueError("External suggestions cannot exceed the requested limit")
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
        if not self.semantic_available and any("semantic" in item.signals for item in self.items):
            raise ValueError("Lexical fallback cannot report semantic evidence")

    def _require_exact_lane(self) -> None:
        visible_exact = min(self.exact_title_group_total, self.limit)
        if len(self.items) < visible_exact:
            raise ValueError("Exact-title groups must fill available response slots first")
        actual_exact = sum("exact_title" in item.signals for item in self.items)
        if actual_exact != visible_exact:
            raise ValueError("Exact-title group totals are inconsistent")
        if any("exact_title" not in item.signals for item in self.items[:visible_exact]) or any(
            "exact_title" in item.signals for item in self.items[visible_exact:]
        ):
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
    external_references: ExternalReferences = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )
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


class ExternalReferencesChange(APIModel):
    before: ExternalReferences
    after: ExternalReferences


class WorkChangeSet(APIModel):
    external_references: ExternalReferencesChange | None = None
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


class ReviewLeaseMetadata(APIModel):
    purpose: Literal["code_review"] | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    mode: ReviewMode | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def lease_review_fields(self) -> Self:
        present = [self.purpose is not None, self.code_review_id is not None, self.mode is not None]
        if any(present) and not all(present):
            raise ValueError("Review lease metadata must contain exact purpose, review and mode")
        return self


class ReviewEventMetadata(APIModel):
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    work_follow_up_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    work_follow_up_answer_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_result_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class WorkClaimedLiveMetadata(ReviewLeaseMetadata):
    expires_at: UTCEventDateTime


class WorkClaimedBackfillMetadata(APIModel):
    observed_expires_at: UTCEventDateTime
    expiry_basis: Literal["retained_lease_at_cutover"]


class WorkReleasedClientMetadata(ReviewLeaseMetadata):
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


class WorkMovedMetadata(APIModel):
    move_id: UUID
    source_project_id: UUID
    target_project_id: UUID
    role: Literal["source", "target"]
    work_version: Annotated[StrictInt, Field(ge=2)]

    @model_validator(mode="after")
    def projects_are_distinct(self) -> Self:
        if self.source_project_id == self.target_project_id:
            raise ValueError("Move event projects must be distinct")
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
    | ReviewEventMetadata
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
    | WorkMovedMetadata
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
        "work_follow_up_requested", "work_follow_up_answered", "work_follow_up_superseded",
        "code_review_requested", "code_review_completed", "code_review_superseded",
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
    "work_moved": WorkMovedMetadata,
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
    code_review_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    work_follow_up_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    work_follow_up_answer_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_result_id: UUID | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
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
        self._review_references()
        self._require_merge_projection()
        self._require_move_projection()
        return self

    def _review_references(self) -> None:
        matrix = {
            "work_follow_up_requested": {"work_follow_up_id"},
            "work_follow_up_answered": {"work_follow_up_id", "work_follow_up_answer_id"},
            "work_follow_up_superseded": {"work_follow_up_id"},
            "code_review_requested": {"code_review_id"},
            "code_review_completed": {"code_review_id", "code_review_result_id"},
            "code_review_superseded": {"code_review_id"},
        }
        fields = {
            "code_review_id", "work_follow_up_id", "work_follow_up_answer_id",
            "code_review_result_id",
        }
        expected = matrix.get(self.event_type, set())
        if (self.event_type in {"work_claimed", "work_released"}
                and getattr(self.metadata, "purpose", None)):
            expected = {"code_review_id"}
        present = {field for field in fields if getattr(self, field) is not None}
        if present != expected:
            raise ValueError("Review event reference columns are inconsistent")
        if any(getattr(self.metadata, field, None) != getattr(self, field) for field in expected):
            raise ValueError("Review metadata does not match its source reference")

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

    def _require_move_projection(self) -> None:
        if self.event_type != "work_moved":
            return
        if not isinstance(self.metadata, WorkMovedMetadata):
            raise ValueError("Move events require typed move metadata")
        expected_project_id = (
            self.metadata.source_project_id
            if self.metadata.role == "source"
            else self.metadata.target_project_id
        )
        if self.project_id != expected_project_id:
            raise ValueError("Move event role does not match its project")

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
        if self.event_type.startswith(("code_review_", "work_follow_up_")):
            return ReviewEventMetadata.model_validate(payload)
        limit = 131072 if self.event_type in {
            "work_created", "work_updated", "work_status_changed", "work_reopened",
        } else 16384
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")) > limit:
            raise ValueError("Event metadata exceeds its event-specific UTF-8 JSON bound")
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
            self.direct_destination.id,
            self.direct_destination.title,
            self.direct_destination.status,
        ) != destination_identity or (
            self.canonical_work_item.id,
            self.canonical_work_item.title,
            self.canonical_work_item.status,
        ) != destination_identity:
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
                or metadata.destination_work_item_id != self.merge.destination_work_item_id
                or metadata.role != role
                or metadata.source_work_version != self.merge.resulting_source_work_version
                or metadata.destination_work_version
                != self.merge.resulting_destination_work_version
            ):
                raise ValueError("Merge event ordering or provenance is incoherent")


class WorkContext(APIModel):
    code_review_context: CodeReviewContext | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
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
        _validate_code_review_context_scope(self.code_review_context, self.work_item)
        if self.merge_review_revision.work_version != self.work_item.version:
            raise ValueError("Merge review work version does not match the requested item")
        current_checkpoint_id = (
            self.initial_checkpoint.id
            if self.current_context_is_initial
            else self.current_context.id
            if self.current_context is not None
            else None
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
        if self.canonical.is_duplicate and (not member_ids or member_ids[0] != self.work_item.id):
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
    code_review_handoff: CodeReviewHandoffInput | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    review_policy_decision: ReviewPolicyRead | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    code_review_request: CodeReviewRead | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    agent_follow_ups: list[WorkFollowUpRead] | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None, min_length=1, max_length=1,
    )
    job_completion_report: JobCompletionReportRead | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    work_item: WorkItemRead
    checkpoint: CompletionCheckpointRead
    completion_evidence: CompletionEvidencePayloadRead | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def completion_is_coherent(self) -> Self:
        self._check_report_coherence()
        self._check_review_coherence()
        if "completion_evidence" in self.model_fields_set and self.completion_evidence is None:
            raise ValueError("completion_evidence cannot be null")
        if (
            self.checkpoint.kind != "completion"
            or self.checkpoint.work_item_id != self.work_item.id
        ):
            raise ValueError("Completion checkpoint does not belong to the work item")
        if self.completion_evidence is not None:
            _evidence_children_are_coherent(
                self.completion_evidence.verification_results,
                self.completion_evidence.artifact_references,
                work_item_id=self.work_item.id,
                checkpoint_id=self.checkpoint.id,
                created_at=self.checkpoint.created_at,
            )
        return self

    def _check_review_coherence(self) -> None:
        policy = self.review_policy_decision
        if policy is None:
            if any(value is not None for value in (
                self.code_review_request, self.agent_follow_ups, self.code_review_handoff,
            )):
                raise ValueError("Review resources require a completion policy")
            return
        if (policy.project_id != self.work_item.project_id
                or policy.work_item_id != self.work_item.id
                or policy.completion_checkpoint_id != self.checkpoint.id
                or policy.priority_at_closeout != self.work_item.priority
                or self.work_item.status != "done"
                or self.job_completion_report is None
                or policy.settings_revision != self.job_completion_report.prompt_revision
                or policy.completion_event_id != self.job_completion_report.closeout_event_id):
            raise ValueError("Review policy does not match completion")
        if (policy.decision == "mandatory") != (self.code_review_request is not None):
            raise ValueError("Mandatory completion requires its review request")
        if (policy.decision == "mandatory") != (self.code_review_handoff is not None):
            raise ValueError("Mandatory completion requires its accepted handoff snapshot")
        if (policy.decision == "ask_recommendation") != (self.agent_follow_ups is not None):
            raise ValueError("Optional completion requires its question")
        self._check_review_resources(policy)

    def _check_review_resources(self, policy: ReviewPolicyRead) -> None:
        if self.code_review_request is not None:
            request = self.code_review_request
            if (request.policy_decision_id != policy.id or request.state != "requested"
                    or request.request_reason != "mandatory"
                    or request.project_id != policy.project_id
                    or request.work_item_id != policy.work_item_id
                    or request.completion_checkpoint_id != policy.completion_checkpoint_id
                    or request.completion_event_id != policy.completion_event_id
                    or self.code_review_handoff is None
                    or request.scope_sha256 != scope_hash(self.code_review_handoff.scope)):
                raise ValueError("Completion review snapshot is inconsistent")
        if self.agent_follow_ups is not None:
            question = self.agent_follow_ups[0]
            if (question.kind_data.get("policy_decision_id") != policy.id
                    or question.state != "pending"
                    or question.project_id != policy.project_id
                    or question.work_item_id != policy.work_item_id
                    or question.completion_checkpoint_id != policy.completion_checkpoint_id
                    or question.trigger_event_id != policy.completion_event_id):
                raise ValueError("Completion question snapshot is inconsistent")

    def _check_report_coherence(self) -> None:
        report = self.job_completion_report
        if "job_completion_report" in self.model_fields_set and report is None:
            raise ValueError("job_completion_report cannot be null")
        if report is not None and (
            report.work_item_id != self.work_item.id
            or report.project_id != self.work_item.project_id
            or report.closeout_status != "done"
            or report.completion_checkpoint_id != self.checkpoint.id
            or report.closeout_work_version != self.work_item.version
            or report.work_title_at_closeout != self.work_item.title
        ):
            raise ValueError("Report does not match this completion")


class JobCompletionReportDismissalRequest(APIModel):
    actor: MutationActor


class JobCompletionReportDismissalCreate(JobCompletionReportDismissalRequest):
    client_operation_id: UUID = Field(repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION)


class JobCompletionReportDismissalResult(APIModel):
    project_id: UUID
    report_id: UUID
    dismissed: StrictBool
    human_dismissal: HumanDismissalRead


class JobCompletionReportFollowUpRequest(APIModel):
    actor: MutationActor
    title: Title
    summary: Summary
    priority: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    initial_checkpoint: InitialCheckpointCreate

    @model_validator(mode="after")
    def actor_matches_checkpoint(self) -> Self:
        checkpoint = self.initial_checkpoint
        if (self.actor.actor_client, self.actor.actor_session_id, self.actor.actor_model) != (
            checkpoint.source_client, checkpoint.source_session_id, checkpoint.source_model
        ):
            raise ValueError("Follow-up actor must match initial checkpoint attribution")
        return self


class JobCompletionReportFollowUpCreate(JobCompletionReportFollowUpRequest):
    client_operation_id: UUID = Field(repr=False, description=CLIENT_OPERATION_ID_DESCRIPTION)


class JobCompletionReportFollowUpResult(APIModel):
    work_item: WorkItemRead
    initial_checkpoint: CheckpointRead
    follow_up: JobCompletionReportFollowUpRead

    @model_validator(mode="after")
    def created_work_matches(self) -> Self:
        if (
            self.work_item.status != "pending" or self.work_item.version != 1
            or self.work_item.id != self.initial_checkpoint.work_item_id
            or self.work_item.initial_checkpoint_id != self.initial_checkpoint.id
            or self.follow_up.follow_up_work_item_id != self.work_item.id
            or self.follow_up.project_id != self.work_item.project_id
        ):
            raise ValueError("Follow-up creation is incoherent")
        return self


class WorkDeletionRead(APIModel):
    deleted: bool = True
    project_id: UUID
    work_item_id: UUID
    version: int


class WorkFollowUpResponseRequest(APIModel):
    expected_follow_up_version: ReviewVersion
    actor: MutationActor
    answer: CodeReviewRecommendationAnswer


class WorkFollowUpResponseCreate(WorkFollowUpResponseRequest):
    client_operation_id: UUID = Field(repr=False)


class WorkFollowUpResponseResult(APIModel):
    code_review_handoff: CodeReviewHandoffInput | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    follow_up: WorkFollowUpRead
    answer: WorkFollowUpAnswerRead
    code_review_request: CodeReviewRead | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def correspondence(self) -> Self:
        if (self.follow_up.answer_id != self.answer.id or self.follow_up.state != "answered"
                or self.answer.follow_up_id != self.follow_up.id
                or self.answer.work_item_id != self.follow_up.work_item_id
                or self.answer.project_id != self.follow_up.project_id):
            raise ValueError("Follow-up answer correspondence is invalid")
        if self.answer.recommend_review != (self.code_review_request is not None):
            raise ValueError("Affirmative answers require the review snapshot")
        if self.answer.recommend_review != (self.code_review_handoff is not None):
            raise ValueError("Affirmative answers require the accepted handoff snapshot")
        if self.code_review_request is not None and (
            self.code_review_request.answer_id != self.answer.id
            or self.code_review_request.id != self.answer.code_review_id
            or self.code_review_request.work_item_id != self.answer.work_item_id
            or self.code_review_request.project_id != self.answer.project_id
            or self.code_review_request.state != "requested"
            or self.code_review_request.request_reason != "recommended"
            or self.code_review_handoff is None
            or self.code_review_request.scope_sha256 != scope_hash(self.code_review_handoff.scope)
        ):
            raise ValueError("Answer review does not match")
        return self


class CodeReviewCompletionRequest(APIModel):
    expected_review_version: ReviewVersion
    scope_sha256: ScopeHash
    lease_token: LeaseToken = Field(repr=False)
    actor: MutationActor
    result: CodeReviewResultInput


class CodeReviewCompletionCreate(CodeReviewCompletionRequest):
    client_operation_id: UUID = Field(repr=False)


class CodeReviewCompletionRead(APIModel):
    review: CodeReviewRead
    result: CodeReviewResultRead
    remediation: CodeReviewRemediationRead | None
    remediation_work: WorkCreation | None

    @model_validator(mode="after")
    def correspondence(self) -> Self:
        if (self.review.state != "completed" or self.review.result_id != self.result.id
                or self.result.review_id != self.review.id
                or self.result.work_item_id != self.review.work_item_id
                or self.result.project_id != self.review.project_id
                or self.result.scope_sha256 != self.review.scope_sha256):
            raise ValueError("Review result correspondence is invalid")
        if bool(self.result.findings) != (self.remediation is not None):
            raise ValueError("Findings require exactly one remediation")
        if (self.remediation is None) != (self.remediation_work is None):
            raise ValueError("Remediation needs its creation snapshot")
        if self.remediation is not None and self.remediation_work is not None:
            if (self.remediation.result_id != self.result.id
                    or self.remediation.review_id != self.review.id
                    or self.remediation.source_work_item_id != self.review.work_item_id
                    or self.remediation.remediation_work_item_id
                    != self.remediation_work.work_item.id
                    or self.remediation_work.work_item.status != "pending"):
                raise ValueError("Remediation identity is invalid")
        return self


def _validate_code_review_context_scope(
    context: CodeReviewContext | None, work: WorkItemRead,
) -> None:
    if context is None:
        return
    for resource in (context.current_review, context.pending_follow_up):
        if resource is not None and (
            resource.project_id != work.project_id or resource.work_item_id != work.id
            or work.status != "done"
        ):
            raise ValueError("Review context refers to another work item or lifecycle")
    origin = context.remediation_origin
    if origin is not None and (
        origin.project_id != work.project_id or origin.remediation_work_item_id != work.id
    ):
        raise ValueError("Remediation origin refers to another work item")


class Page[T](APIModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProjectListQuery(APIModel):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class WorkItemListQuery(APIModel):
    external_url: ExternalURL | None = None
    q: Annotated[str, StringConstraints(max_length=500), AfterValidator(no_nul)] | None = None
    semantic: bool = False
    status: Literal[
        "pending", "active", "dropped", "deferred", "done", "wont-do", "promoted", "all"
    ] = "pending"
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
        if self.view == "roots" and self.external_url is not None:
            raise ValueError("external_url requires view=full")
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
