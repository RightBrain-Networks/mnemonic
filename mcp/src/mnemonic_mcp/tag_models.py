"""Project tag vocabulary and explicit work assignments."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

TagName = Annotated[str, Field(min_length=1, max_length=50, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
TagMode = Literal["or", "and"]


def distinct_tags(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("Tag names must be unique.")
    return value


TagNames = Annotated[
    list[TagName], Field(min_length=1, max_length=20), AfterValidator(distinct_tags),
]


class ProjectTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    name: TagName
    description: str = Field(max_length=240)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    menu_order: StrictInt = Field(ge=0)
    system: StrictBool


class ProjectTagList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    items: list[ProjectTag]
    total: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def complete_ordered_catalog(self) -> Self:
        if any(item.project_id != self.project_id for item in self.items):
            raise ValueError("Tag entries must belong to the catalog project.")
        if self.total != len(self.items):
            raise ValueError("Tag catalog must contain every available tag.")
        for values in (
            [item.id for item in self.items],
            [item.name for item in self.items],
            [item.menu_order for item in self.items],
        ):
            if len(set(values)) != len(values):
                raise ValueError("Tag catalog identities and menu positions must be unique.")
        if self.items != sorted(self.items, key=lambda item: item.menu_order):
            raise ValueError("Tag catalog must follow menu order.")
        return self


def tags_match(assigned: list[str] | None, selected: list[str] | None, mode: TagMode) -> bool:
    """Verify requested tag scope without assuming assignment order."""
    if selected is None:
        return True
    present = set(assigned or [])
    return set(selected) <= present if mode == "and" else bool(present.intersection(selected))
