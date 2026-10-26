"""Bounded read-only grouping of required candidate occurrences within one change."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictInt, model_validator

from mnemonic_api.impact_finding_schemas import FindingIdentifier, RemediationTarget
from mnemonic_api.impact_schemas import Disposition, Identifier, Reference, ReusedDispositionSource
from mnemonic_api.schemas import APIModel

Count = Annotated[StrictInt, Field(ge=0)]
Limit = Annotated[int, Field(ge=1, le=100)]


class DecisionChangeCandidatesQuery(APIModel):
    decision_id: Identifier | None = None
    limit: Limit = 20
    offset: Annotated[int, Field(ge=0)] = 0
    occurrence_limit: Limit = 10
    occurrence_offset: Annotated[int, Field(ge=0)] = 0

    @model_validator(mode="after")
    def bounded_occurrences(self) -> Self:
        if self.limit * self.occurrence_limit > 200:
            raise ValueError("Group limit multiplied by occurrence limit must not exceed 200")
        return self


class CandidateDispositionSummary(APIModel):
    reused_from: ReusedDispositionSource | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    id: UUID
    version: Annotated[StrictInt, Field(ge=1)]
    disposition: Disposition
    remediation_work_item_id: UUID | None

    finding_id: FindingIdentifier | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    remediation_target: RemediationTarget | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def target_matches_disposition(self) -> Self:
        if self.remediation_target is not None and self.disposition != "change-required":
            raise ValueError("A remediation target applies only to change-required dispositions")
        return self


class DecisionCandidateOccurrence(APIModel):
    assessment_id: UUID
    assessment_version: Count
    before_reference: Reference
    after_reference: Reference
    trigger: Literal["revision", "supersession"]
    candidate_reference: Reference
    relationship: Literal["origin", "depends-on", "related-only"]
    related_direction: Literal["incoming"] | None
    latest_disposition: CandidateDispositionSummary | None


class DecisionCandidateGroup(APIModel):
    decision_id: Identifier
    occurrence_count: Count
    revision_count: Count
    unassessed_count: Count
    change_required_count: Count
    captured_references: list[Reference]
    references_complete: bool
    occurrences: list[DecisionCandidateOccurrence]
    next_occurrence_offset: Count | None


class DecisionChangeCandidatesRead(APIModel):
    project_id: UUID
    change_id: UUID
    scope_version: Count
    acknowledged_scope_version: Count
    assessment_count: Count
    observed_at: datetime
    decision_id: Identifier | None
    decision_count: Count
    revision_count: Count
    occurrence_count: Count
    unassessed_count: Count
    change_required_count: Count
    items: list[DecisionCandidateGroup]
    total: Count
    limit: Limit
    offset: Count
    next_offset: Count | None
    occurrence_limit: Limit
    occurrence_offset: Count
    max_occurrences_per_page: Literal[200] = 200
