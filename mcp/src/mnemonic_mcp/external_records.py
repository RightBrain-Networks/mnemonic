"""Strict external data contracts; these values never authorize provider or work writes."""

import ipaddress
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

ExternalState = Literal["open", "closed", "merged", "unknown"]
_PCHAR = r"[A-Za-z0-9._~!$&'()*+,;=:@%-]"
_URL = re.compile(
    rf"https?://(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?)"
    rf"(?::[0-9]+)?(?:/{_PCHAR}*(?:/{_PCHAR}*)*)?"
    rf"(?:\?(?:{_PCHAR}|[/?])*)?(?:#(?:{_PCHAR}|[/?])*)?", re.IGNORECASE,
)
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")

_BIDI = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069"
)


def external_url(value: str) -> str:
    if not 1 <= len(value) <= 2000 or not value.isascii() or not _URL.fullmatch(value):
        raise ValueError("External URLs must use the bounded ASCII HTTP(S) URI grammar")
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ValueError("External URLs must use valid percent escapes")
    try:
        parts = urlsplit(value)
        hostname = parts.hostname or ""
        _ = parts.port
        if ":" in hostname:
            ipaddress.IPv6Address(hostname)
        elif re.fullmatch(r"[0-9.]+", hostname):
            ipaddress.IPv4Address(hostname)
        elif len(hostname.rstrip(".")) > 253 or any(
            not 1 <= len(label) <= 63
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) is None
            for label in hostname.rstrip(".").split(".")
        ):
            raise ValueError("Invalid host")
    except ValueError as exc:
        raise ValueError("External URLs require a valid host and port") from exc
    return value


def plain_text(value: str, *, label: bool = False, title: bool = False) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("External text must contain valid Unicode.") from error
    if "\x00" in value or ((label or title) and not value.strip()):
        raise ValueError("External display text must be nonblank and contain no NUL.")
    if label or title:
        if any(unicodedata.category(c) == "Cc" or c in _BIDI for c in value):
            raise ValueError("External display text contains forbidden controls.")
        if "\u2028" in value or "\u2029" in value:
            raise ValueError("External display text must be single-line.")
    if label and len(encoded) > 480:
        raise ValueError("External labels must fit 480 UTF-8 bytes.")
    return value


def observation_time(value: str) -> str:
    if not _TIME.fullmatch(value):
        raise ValueError("Observation times require RFC 3339 with up to six fractional digits")
    if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError("Observation time offset is invalid")
    try:
        result = datetime.fromisoformat(value).astimezone(UTC)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Observation time is invalid") from exc
    base = result.strftime("%Y-%m-%dT%H:%M:%S")
    # strftime does not zero-pad years below 1000 on every supported platform.
    base = f"{result.year:04d}" + base[4:] if result.year >= 1000 else (
        f"{result.year:04d}-{result.month:02d}-{result.day:02d}T"
        f"{result.hour:02d}:{result.minute:02d}:{result.second:02d}"
    )
    fraction = f".{result.microsecond:06d}".rstrip("0") if result.microsecond else ""
    return base + fraction + "Z"


def _not_null(value: Any) -> Any:
    if value is None:
        raise ValueError("Omit absent external fields; explicit null is invalid.")
    return value


ExternalURL = Annotated[StrictStr, Field(min_length=1, max_length=2000), AfterValidator(external_url)]
ExternalLabel = Annotated[
    StrictStr, Field(min_length=1, max_length=120),
    AfterValidator(lambda value: plain_text(value, label=True)),
]
ExternalTitle = Annotated[
    StrictStr, Field(min_length=1, max_length=500),
    AfterValidator(lambda value: plain_text(value, title=True)),
]
ExternalBody = Annotated[StrictStr, Field(max_length=20000), AfterValidator(plain_text)]
ObservationTime = Annotated[StrictStr, AfterValidator(observation_time)]


class ExternalWire(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ExternalReference(ExternalWire):
    url: ExternalURL
    kind: Literal["tracked-by", "references"]
    label: Annotated[ExternalLabel | SkipJsonSchema[None], BeforeValidator(_not_null)] = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    state: ExternalState
    state_observed_at: Annotated[
        ObservationTime | SkipJsonSchema[None], BeforeValidator(_not_null),
    ] = Field(default=None, exclude_if=lambda value: value is None)


def references_bound(value: list[ExternalReference]) -> list[ExternalReference]:
    if len({item.url for item in value}) != len(value):
        raise ValueError("External reference URLs must be unique.")
    encoded = json.dumps(
        [item.model_dump(mode="json") for item in value],
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > 32768:
        raise ValueError("Canonical external references exceed 32,768 UTF-8 bytes.")
    return value


ExternalReferences = Annotated[
    list[ExternalReference], Field(strict=True, max_length=10), AfterValidator(references_bound),
]
OmissionOnlyExternalReferences = Annotated[
    ExternalReferences | SkipJsonSchema[None], BeforeValidator(_not_null),
]


class ExternalCandidateReference(ExternalWire):
    url: ExternalURL
    title: ExternalTitle
    state: ExternalState


class ExternalDuplicateCandidate(ExternalCandidateReference):
    body: ExternalBody


def candidates_bound(value: list[ExternalDuplicateCandidate]) -> list[ExternalDuplicateCandidate]:
    if len({item.url for item in value}) != len(value):
        raise ValueError("External candidate URLs must be unique.")
    return value


ExternalCandidates = Annotated[
    list[ExternalDuplicateCandidate], Field(strict=True, max_length=64), AfterValidator(candidates_bound),
]


class ExternalDuplicateSuggestion(ExternalWire):
    rank: StrictInt = Field(ge=1, le=10)
    signals: list[Literal["exact_title", "lexical", "semantic"]] = Field(min_length=1, max_length=3)
    reference: ExternalCandidateReference

    @model_validator(mode="after")
    def valid_signals(self) -> Self:
        order = {"exact_title": 0, "lexical": 1, "semantic": 2}
        positions = [order[signal] for signal in self.signals]
        if positions != sorted(set(positions)):
            raise ValueError("External signals must be unique and canonically ordered.")
        return self


def validate_external_page(page: Any) -> None:
    fields = {"external_items", "external_candidate_count", "external_scope"}
    present = page.model_fields_set & fields
    if not present:
        return
    if present != fields or any(getattr(page, field) is None for field in fields):
        raise ValueError("External response fields must appear together and cannot be null.")
    items = page.external_items
    if len(items) > min(page.limit, page.external_candidate_count):
        raise ValueError("External result exceeds its requested limit or supplied count.")
    if [item.rank for item in items] != list(range(1, len(items) + 1)):
        raise ValueError("External ranks must be contiguous.")
    urls = [item.reference.url for item in items]
    if len(set(urls)) != len(urls):
        raise ValueError("External results must have unique URLs.")
    exact = [item for item in items if "exact_title" in item.signals]
    if items[:len(exact)] != exact or [item.reference.url for item in exact] != sorted(
        item.reference.url for item in exact
    ):
        raise ValueError("External exact matches must be a URL-ordered prefix.")
    if page.external_scope == "unavailable" and items:
        raise ValueError("Unavailable external comparison must have no items.")
    if page.external_scope != "hybrid" and any("semantic" in item.signals for item in items):
        raise ValueError("External semantic evidence requires hybrid scope.")


def external_suggestions_match(page: Any, request: Any, title_key: Callable[[str], str]) -> bool:
    candidates = request.external_candidates
    if not candidates:
        return page.external_items is None
    if page.external_items is None or page.external_candidate_count != len(candidates):
        return False
    identities = {
        item.url: (item.title, item.state) for item in candidates
    }
    for item in page.external_items:
        if identities.get(item.reference.url) != (item.reference.title, item.reference.state):
            return False
        is_exact = title_key(item.reference.title) == title_key(request.title)
        if ("exact_title" in item.signals) != is_exact:
            return False
    if page.external_scope == "unavailable":
        return True
    expected_exact = sorted(
        item.url for item in candidates if title_key(item.title) == title_key(request.title)
    )[:request.limit]
    actual_exact = [item.reference.url for item in page.external_items if "exact_title" in item.signals]
    return actual_exact == expected_exact
