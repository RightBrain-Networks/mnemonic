"""Bounded agent assertions about shared-filesystem transcript locations."""

from pathlib import PurePosixPath
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


class TranscriptLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
    path: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def safe_location(self) -> Self:
        for value in (self.client, self.path):
            if any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF
                   for character in value):
                raise ValueError("Transcript locations cannot contain control characters")
        if not PurePosixPath(self.path).is_absolute() or ".." in PurePosixPath(self.path).parts:
            raise ValueError("Transcript path must be an absolute filesystem path without '..'")
        return self


def distinct_transcript_sources(sources: list[TranscriptLocation]) -> list[TranscriptLocation]:
    if len({source.path for source in sources}) != len(sources):
        raise ValueError("Each subagent transcript path may be reported only once")
    return sources


TranscriptSources = Annotated[
    list[TranscriptLocation], Field(min_length=1, max_length=100),
    AfterValidator(distinct_transcript_sources),
]
