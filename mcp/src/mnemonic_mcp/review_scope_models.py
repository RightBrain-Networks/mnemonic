"""Context-free review coordination notices; detailed evidence stays in checkpoints."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Count = Annotated[StrictInt, Field(ge=0)]


class ReviewScopeChanged(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: UUID
    project_id: UUID
    review_work_item_id: UUID
    old_scope_version: Count
    new_scope_version: Count
    episodes_added: Count
    captured_code_impact_rows_added: Count | None
    distinct_references_in_added_rows: Count | None
    distinct_decisions_in_added_rows: Count | None

    @model_validator(mode="after")
    def coherent_growth(self) -> Self:
        if self.new_scope_version <= self.old_scope_version or (
            self.episodes_added != self.new_scope_version - self.old_scope_version
        ):
            raise ValueError("A scope notice must describe added assessment episodes")
        counts = (self.captured_code_impact_rows_added, self.distinct_references_in_added_rows,
                  self.distinct_decisions_in_added_rows)
        if any(value is None for value in counts) != all(value is None for value in counts):
            raise ValueError("Unknown historical attachment counts must all remain unknown")
        if (counts[0] is not None and counts[1] is not None and counts[2] is not None
                and not counts[0] >= counts[1] >= counts[2]):
            raise ValueError("Distinct identities cannot exceed captured candidate occurrences")
        return self


class ReviewScopeHolder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holder_client: str = Field(min_length=1, max_length=80)
    holder_session_id: str = Field(min_length=1, max_length=200)
    expires_at: datetime


class ExtendedDecisionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_scope_changed: ReviewScopeChanged
    holder: ReviewScopeHolder | None
