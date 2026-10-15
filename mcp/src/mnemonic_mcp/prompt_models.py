"""Project-authored prompt content returned by a bounded safe render read."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictStr, field_validator, model_validator

from .phase12_models import Phase12Wire, PositiveDecimalString

PromptId = Literal[
    "recall-pointer", "job-completion-report", "cold-code-review", "warm-code-review",
    "review-recommendation", "review-remediation", "resume-work", "request-human-review",
    "priority-rubric",
]


class RenderedPrompt(Phase12Wire):
    project_id: UUID
    prompt_id: PromptId
    work_item_id: UUID | None
    code_review_id: UUID | None
    content: Annotated[StrictStr, Field(min_length=1, max_length=100000)]
    revision: Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
    prompt_revision: PositiveDecimalString | None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or len(value.encode("utf-8")) > 400_000:
            raise ValueError("Rendered prompts must be nonblank, NUL-free and bounded UTF-8.")
        return value

    @model_validator(mode="after")
    def enforce_report_revision(self) -> Self:
        if (self.prompt_id == "job-completion-report") != (self.prompt_revision is not None):
            raise ValueError("Only a report prompt carries the required closeout prompt_revision.")
        return self
