"""Optional authored finding identity; unknown grouping stays explicit."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from mnemonic_api.schemas import APIModel

FindingIdentifier = Annotated[
    str, StringConstraints(min_length=1, max_length=100,
                           pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"),
]
RemediationTarget = Literal["code", "decision-record", "both"]


class ImpactFindingSummary(APIModel):
    named_finding_count: int = Field(ge=0)
    unlabelled_change_required_count: int = Field(ge=0)
    change_required_row_count: int = Field(ge=0)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        labelled = self.change_required_row_count - self.unlabelled_change_required_count
        if not 0 <= self.named_finding_count <= labelled or (
            bool(self.named_finding_count) != bool(labelled)
        ):
            raise ValueError("Finding counts disagree with labelled and unlabelled rows")
        return self
