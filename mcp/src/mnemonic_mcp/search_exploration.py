"""Independent date, diagnostic and tag-vocabulary contracts for search consumers."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .validation_rules import VALIDATION_RULES

DiagnosticsMode = Literal["on_empty", "always", "off"]
DATE_FIELDS = ("created_after", "created_before", "updated_after", "updated_before")
def _date_value(value: object) -> object:
    if not isinstance(value, (str, datetime)):
        raise PydanticCustomError("datetime_type", "Input should be a valid datetime")
    return value


SearchDate = Annotated[datetime, BeforeValidator(_date_value)]


class DateBounds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    created_after: SearchDate | None = Field(default=None, exclude_if=lambda value: value is None)
    created_before: SearchDate | None = Field(default=None, exclude_if=lambda value: value is None)
    updated_after: SearchDate | None = Field(default=None, exclude_if=lambda value: value is None)
    updated_before: SearchDate | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator(*DATE_FIELDS)
    @classmethod
    def utc_bound(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            code = "search_datetime_timezone_required"
            raise PydanticCustomError(code, VALIDATION_RULES[code][1])
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        for prefix in ("created", "updated"):
            lower, upper = getattr(self, f"{prefix}_after"), getattr(self, f"{prefix}_before")
            if lower is not None and upper is not None and lower >= upper:
                code = f"search_{prefix}_range_invalid"
                raise PydanticCustomError(code, VALIDATION_RULES[code][1])
        return self


    def contains(self, created: datetime, updated: datetime) -> bool:
        return all(
            bound is None or (value >= bound if name.endswith("after") else value < bound)
            for name, value in (("created_after", created), ("created_before", created),
                                ("updated_after", updated), ("updated_before", updated))
            for bound in (getattr(self, name),)
        )


class EchoedDateBounds(DateBounds):
    @field_validator(*DATE_FIELDS, mode="before")
    @classmethod
    def utc_echo(cls, value: object) -> object:
        if value is None:
            raise ValueError("Unused effective date bounds must be omitted")
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(parsed, datetime) or parsed.utcoffset() != UTC.utcoffset(parsed):
            raise ValueError("Effective date bounds must use UTC")
        return parsed


class TagCountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50
    offset: Annotated[StrictInt, Field(ge=0, le=1_000_000)] = 0


class TagCount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tag: Annotated[str, Field(strict=True, min_length=1, max_length=50)]
    count: Annotated[StrictInt, Field(ge=1)]


class TagCountPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: Annotated[list[TagCount], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: Annotated[StrictInt, Field(ge=1, le=100)]
    offset: Annotated[StrictInt, Field(ge=0)]
    next_offset: Annotated[StrictInt, Field(ge=0)] | None = None
    count_unit: Literal["canonical_work_items"] = "canonical_work_items"
    member_scope: Literal["returned_work_items"] = "returned_work_items"
    selected_tag_applied: Literal[True] = True

    @field_validator("selected_tag_applied", mode="before")
    @classmethod
    def exact_selected_tag(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Selected-tag application must be explicit true")
        return value

    def matches_request(self, request: TagCountRequest, work_total: int) -> bool:
        length = min(request.limit, max(0, self.total - request.offset))
        next_offset = request.offset + length if request.offset + length < self.total else None
        tags = [item.tag for item in self.items]
        return ((work_total > 0 or self.total == 0)
                and self.limit == request.limit and self.offset == request.offset
                and len(self.items) == length and self.next_offset == next_offset
                and tags == sorted(set(tags), key=lambda tag: tag.encode("utf-8"))
                and all(item.count <= work_total for item in self.items)
                and self.model_fields_set == set(type(self).model_fields))
