"""Project-authored prompt content returned by a bounded safe render read."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class RenderedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    content: Annotated[StrictStr, Field(min_length=1, max_length=100000)]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or len(value.encode("utf-8")) > 400_000:
            raise ValueError("Rendered prompts must be nonblank, NUL-free and bounded UTF-8.")
        return value
