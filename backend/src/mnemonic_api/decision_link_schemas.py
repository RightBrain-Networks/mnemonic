"""Shared revision-pinned link inputs without workflow-schema dependencies."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}$")]
Revision = Annotated[StrictInt, Field(ge=0, le=2_147_483_647)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class DecisionDependency(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)

    decision_id: Identifier
    revision: Revision
    rationale: Reason

    @field_validator("rationale")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Reasons cannot contain NUL characters")
        value.encode("utf-8")
        return value


class SupersessionDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)

    decision_id: Identifier
    revision: Revision
    extent: Literal["full", "partial"]
    reason: Reason

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return DecisionDependency.valid_reason(value)
