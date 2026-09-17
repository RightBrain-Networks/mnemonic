"""Reusable, value-free exploration controls for every discovery front door."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mnemonic_api.validation_rules import validation_rule

DiagnosticsMode = Literal["on_empty", "always", "off"]
DATE_FIELDS = ("created_after", "created_before", "updated_after", "updated_before")


class DateBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    created_after: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description="Inclusive lower creation-time bound; include a timezone; normalized to UTC.",
    )
    created_before: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description="Exclusive upper creation-time bound; include a timezone; normalized to UTC.",
    )
    updated_after: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description="Inclusive lower update-time bound; include a timezone; normalized to UTC.",
    )
    updated_before: datetime | None = Field(
        default=None, exclude_if=lambda value: value is None,
        description="Exclusive upper update-time bound; include a timezone; normalized to UTC.",
    )

    @field_validator(*DATE_FIELDS)
    @classmethod
    def utc_bound(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise validation_rule("search_datetime_timezone_required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.created_after is not None and self.created_before is not None:
            if self.created_after >= self.created_before:
                raise validation_rule("search_created_range_invalid")
        if self.updated_after is not None and self.updated_before is not None:
            if self.updated_after >= self.updated_before:
                raise validation_rule("search_updated_range_invalid")
        return self


class SearchOptions(DateBounds):
    diagnostics: DiagnosticsMode = "on_empty"


class TagCountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class TagCount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: str = Field(min_length=1, max_length=50)
    count: int = Field(ge=1)


class TagCountPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[TagCount] = Field(max_length=100)
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    count_unit: Literal["canonical_work_items"] = "canonical_work_items"
    member_scope: Literal["returned_work_items"] = "returned_work_items"
    selected_tag_applied: Literal[True] = True
