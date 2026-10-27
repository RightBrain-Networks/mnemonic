"""Project label definitions and current work assignments."""

import re
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from mnemonic_api.schemas import APIModel


def normalize_name(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not value or len(value) > 50:
        raise ValueError("Tag names must contain 1–50 lowercase letters, digits or dashes")
    return value


type TagColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class ProjectTagRead(APIModel):
    id: UUID
    project_id: UUID
    name: str
    description: str
    color: str
    menu_order: int
    system: bool


class TagCatalog(APIModel):
    project_id: UUID
    items: list[ProjectTagRead]
    total: int


class TagCreate(APIModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=240)
    color: TagColor | None = None
    client_operation_id: UUID | None = Field(default=None, repr=False)

    @field_validator("color")
    @classmethod
    def lower_color(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        return normalize_name(value)

    @field_validator("description")
    @classmethod
    def plain_description(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("Tag descriptions must be a short single line")
        return value.strip()


class TagPatch(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=240)
    color: TagColor | None = None
    client_operation_id: UUID | None = Field(default=None, repr=False)

    @field_validator("color")
    @classmethod
    def lower_color(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str | None) -> str | None:
        return normalize_name(value) if value is not None else None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        fields = self.model_fields_set - {"client_operation_id"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("Provide a non-null tag name, description or color")
        if self.description is not None:
            self.description = TagCreate.plain_description(self.description)
        return self


class TagDelete(APIModel):
    client_operation_id: UUID | None = Field(default=None, repr=False)


class TagOrder(APIModel):
    tag_ids: list[UUID] = Field(max_length=1000)
    client_operation_id: UUID | None = Field(default=None, repr=False)

    @field_validator("tag_ids")
    @classmethod
    def unique_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Each tag must occur exactly once")
        return value
