"""Field scopes shared by dedicated and unified work discovery."""

from typing import Annotated, Literal

from pydantic import AfterValidator, Field

WorkField = Literal["title", "summary", "tags", "checkpoint", "identifiers", "provenance"]
WORK_FIELDS: tuple[WorkField, ...] = (
    "title", "summary", "tags", "checkpoint", "identifiers", "provenance",
)


def ordered_work_fields(value: list[WorkField]) -> list[WorkField]:
    return [field for field in WORK_FIELDS if field in value]


WorkFields = Annotated[
    list[WorkField], Field(min_length=1, max_length=6), AfterValidator(ordered_work_fields),
]
