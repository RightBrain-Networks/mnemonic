"""Project-configured lease durations and explicit optional request values."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from pydantic.experimental.missing_sentinel import MISSING

LeaseMinutes = Annotated[StrictInt, Field(ge=1, le=2147483647)]
type LeaseMinutesArgument = LeaseMinutes | MISSING


class LeaseSettingsRead(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    default_minutes: LeaseMinutes
    minimum_minutes: LeaseMinutes
    maximum_minutes: LeaseMinutes

    @model_validator(mode="after")
    def enforce_ordered_durations(self) -> Self:
        if not self.minimum_minutes <= self.default_minutes <= self.maximum_minutes:
            raise ValueError("Lease durations must satisfy minimum <= default <= maximum.")
        return self


def lease_minutes_payload(minutes: LeaseMinutesArgument) -> dict[str, object]:
    """Omission selects the project's current default without accepting null."""
    return {} if minutes is MISSING else {"lease_minutes": minutes}
