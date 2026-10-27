"""Shared names for current work labels."""
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints


def unique_names(value: list[str]) -> list[str]:
    if len(set(value)) != len(value):
        raise ValueError("Tag names must be unique")
    return value


type TagName = Annotated[str, StringConstraints(
    pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=50)]
type AssignedTags = Annotated[list[TagName], Field(min_length=1, max_length=20),
                              AfterValidator(unique_names)]
