"""Bounded, exact-spelling external reference values; no provider access or normalization."""

import ipaddress
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)
from pydantic.json_schema import SkipJsonSchema

_PCHAR = r"[A-Za-z0-9._~!$&'()*+,;=:@%-]"
_URL = re.compile(
    rf"https?://(?:\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?)"
    rf"(?::[0-9]+)?(?:/{_PCHAR}*(?:/{_PCHAR}*)*)?"
    rf"(?:\?(?:{_PCHAR}|[/?])*)?(?:#(?:{_PCHAR}|[/?])*)?",
    re.IGNORECASE,
)
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")


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


def display_label(value: str) -> str:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Labels require valid Unicode") from exc
    if (
        not value.strip()
        or len(value) > 120
        or len(encoded) > 480
        or any(
            ord(char) < 32
            or 127 <= ord(char) <= 159
            or ord(char)
            in {
                0x061C,
                0x200E,
                0x200F,
                0x2028,
                0x2029,
                0x202A,
                0x202B,
                0x202C,
                0x202D,
                0x202E,
                0x2066,
                0x2067,
                0x2068,
                0x2069,
            }
            for char in value
        )
    ):
        raise ValueError("Labels must be bounded nonblank single-line display text")
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
    base = (
        f"{result.year:04d}-{result.month:02d}-{result.day:02d}T"
        f"{result.hour:02d}:{result.minute:02d}:{result.second:02d}"
    )
    fraction = f".{result.microsecond:06d}".rstrip("0") if result.microsecond else ""
    return base + fraction + "Z"


ExternalURL = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=2000), AfterValidator(external_url)
]
ExternalState = Literal["open", "closed", "merged", "unknown"]
ExternalLabel = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=120), AfterValidator(display_label)
]
ObservationTime = Annotated[str, StringConstraints(strict=True), AfterValidator(observation_time)]


class ExternalReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: ExternalURL
    kind: Literal["tracked-by", "references"]
    state: ExternalState
    label: ExternalLabel | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    state_observed_at: ObservationTime | SkipJsonSchema[None] = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("label", "state_observed_at", mode="before")
    @classmethod
    def _reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Optional reference values must be omitted, not null")
        return value


def canonical_reference_bytes(value: list[ExternalReference]) -> bytes:
    return json.dumps(
        [item.model_dump(mode="json") for item in value],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def references_are_valid(value: list[ExternalReference]) -> list[ExternalReference]:
    if len({item.url for item in value}) != len(value):
        raise ValueError("External reference URLs must be unique")
    if len(canonical_reference_bytes(value)) > 32768:
        raise ValueError("External references exceed 32768 canonical UTF-8 JSON bytes")
    return value


ExternalReferences = Annotated[
    list[ExternalReference], Field(strict=True, max_length=10), AfterValidator(references_are_valid)
]
