"""Bounded, metadata-only evidence for dependency and planning cycles."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

MAX_CYCLE_STEPS = 32


class PrecedenceStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: UUID | None
    relationship_type: Literal["blocks", "parent-child"]
    source_work_item_id: UUID
    target_work_item_id: UUID

    @property
    def before(self) -> UUID:
        return (
            self.target_work_item_id
            if self.relationship_type == "parent-child"
            else self.source_work_item_id
        )

    @property
    def after(self) -> UUID:
        return (
            self.source_work_item_id
            if self.relationship_type == "parent-child"
            else self.target_work_item_id
        )


class DependencyIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["deadlock", "planning-cycle"]
    role: Literal["cycle_member", "depends_on_cycle"]
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cycle_length: StrictInt = Field(ge=1)
    cycle: list[PrecedenceStep] = Field(min_length=1, max_length=MAX_CYCLE_STEPS)

    @model_validator(mode="after")
    def ordered_witness(self) -> Self:
        if len(self.cycle) != min(self.cycle_length, MAX_CYCLE_STEPS):
            raise ValueError("Cycle witness must contain its complete bounded prefix")
        adjacent = zip(self.cycle, self.cycle[1:], strict=False)
        if any(left.after != right.before for left, right in adjacent):
            raise ValueError("Cycle steps must form a contiguous precedence path")
        if self.cycle_length == len(self.cycle) and self.cycle[-1].after != self.cycle[0].before:
            raise ValueError("A complete cycle witness must return to its starting item")
        return self


def cycle_description(issue: DependencyIssue) -> str:
    """Render only validated identifiers and fixed edge labels, never work prose."""
    result = str(issue.cycle[0].before)
    for step in issue.cycle:
        label = "blocks" if step.relationship_type == "blocks" else "parent-child (child to parent)"
        result += f" --{label}--> {step.after}"
    omitted = issue.cycle_length - len(issue.cycle)
    if omitted:
        result += f"; {omitted} further edges in cycle {issue.cycle_id}"
    return result
