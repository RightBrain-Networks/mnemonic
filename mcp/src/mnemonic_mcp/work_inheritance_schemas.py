"""Immutable, edge-specific decision-change inheritance declarations."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_serializer,
)


def valid_reason(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("An inheritance reason must contain nonblank text without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("An inheritance reason must be valid UTF-8") from exc
    return value


InheritanceReason = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=4000),
    AfterValidator(valid_reason),
]


class DecisionChangeInheritanceExclusion(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    origin_work_item_id: UUID
    origin_context_checkpoint_id: UUID
    reason: InheritanceReason
    created_at: AwareDatetime

    @field_serializer("created_at")
    def utc_time(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
