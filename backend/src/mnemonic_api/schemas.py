"""Wire models. In particular, prompt text is never stripped or rewritten."""

import json
import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictInt,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
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
        # Accessing .port also rejects malformed/non-numeric/out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Must be a valid HTTP or HTTPS URL") from exc
    if not valid or parsed.username or parsed.password or any(char.isspace() for char in value):
        raise ValueError("Must be an HTTP or HTTPS URL without credentials or whitespace")
    return value


def normalized_tag(value: str) -> str:
    value = nonblank(value).lower()
    # Some Unicode letters expand to multiple characters when lowercased.
    if len(value) > 50:
        raise ValueError("Normalized tags must contain at most 50 characters")
    return value


def metadata_is_bounded(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ValueError("Metadata must contain finite, valid JSON values") from exc
    if len(encoded) > 16384:
        raise ValueError("Metadata must be at most 16 KB of UTF-8 JSON")
    if b"\\u0000" in encoded:
        # PostgreSQL JSONB cannot represent the NUL codepoint in keys or values.
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
Tags = Annotated[list[Tag], Field(max_length=20)]
Metadata = Annotated[dict[str, JsonValue], AfterValidator(metadata_is_bounded)]
Status = Literal["open", "done", "wont-do", "promoted"]
ProjectName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
    AfterValidator(nonblank),
]
ProjectDescription = Annotated[str, StringConstraints(max_length=4000), AfterValidator(no_nul)]
Slug = Annotated[
    str, StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
]


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


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


class HandoffCreate(APIModel):
    title: Title
    summary: Summary
    prompt: Prompt
    source_client: ClientName
    source_session_id: SessionID
    source_model: ModelName | None = None
    source_session_url: HTTPURL | None = None
    repository_branch: BranchName | None = None
    verified_against: CommitID | None = None
    tags: Tags = Field(default_factory=list)
    source_metadata: Metadata = Field(default_factory=dict)
    status: Status = "open"

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(tag.lower() for tag in value))


class HandoffPatch(APIModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    title: Title | None = None
    summary: Summary | None = None
    prompt: Prompt | None = None
    repository_branch: BranchName | None = None
    verified_against: CommitID | None = None
    tags: Tags | None = None
    source_metadata: Metadata | None = None
    status: Status | None = None

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(tag.lower() for tag in value))

    @model_validator(mode="after")
    def editable_fields(self) -> Self:
        fields = self.model_fields_set - {"expected_version"}
        if not fields:
            raise ValueError("Provide at least one editable field besides expected_version")
        nullable_fields = {"repository_branch", "verified_against"}
        for field in fields - nullable_fields:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class HandoffSummary(Timestamps):
    id: UUID
    project_id: UUID
    title: str
    summary: str
    source_client: str
    source_session_id: str
    source_model: str | None
    source_session_url: str | None
    repository_branch: str | None
    verified_against: str | None
    tags: list[str]
    status: Status
    version: int


class HandoffRead(HandoffSummary):
    prompt: str
    source_metadata: dict[str, JsonValue]


class Page[T](APIModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ProjectListQuery(APIModel):
    limit: int = Field(default=100, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class HandoffListQuery(APIModel):
    q: Annotated[str, StringConstraints(max_length=500), AfterValidator(no_nul)] | None = None
    semantic: bool = False
    status: Literal["open", "done", "wont-do", "promoted", "all"] = "open"
    tag: Tag | None = None
    source_client: ClientName | None = None
    source_session_id: SessionID | None = None
    limit: int = Field(default=30, ge=1, le=100)
    offset: int = Field(default=0, ge=0)

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str | None) -> str | None:
        return value.lower() if value else value
