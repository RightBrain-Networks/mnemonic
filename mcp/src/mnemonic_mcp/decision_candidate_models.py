"""Strict grouped navigation over per-episode code-impact obligations."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, model_validator

from .impact_models import (
    Disposition,
    FindingIdentifier,
    Identifier,
    ImpactModel,
    Reference,
    RemediationTarget,
    ReusedDispositionSource,
)
from .response_validation import validate_page_items

Count = Annotated[StrictInt, Field(ge=0)]
Limit = Annotated[StrictInt, Field(ge=1, le=100)]


class CandidateDispositionSummary(ImpactModel):
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


class DecisionCandidateOccurrence(ImpactModel):
    assessment_id: UUID
    assessment_version: Count
    before_reference: Reference
    after_reference: Reference
    trigger: Literal["revision", "supersession"]
    candidate_reference: Reference
    relationship: Literal["origin", "depends-on", "related-only"]
    related_direction: Literal["incoming"] | None
    latest_disposition: CandidateDispositionSummary | None

    @model_validator(mode="after")
    def coherent_occurrence(self) -> Self:
        if (self.relationship == "related-only") != (self.related_direction == "incoming"):
            raise ValueError("Only captured incoming related citations are required occurrences")
        if self.latest_disposition is not None and (
            self.latest_disposition.version > self.assessment_version
            if self.latest_disposition.reused_from is None else (
                self.latest_disposition.reused_from.assessment_id == self.assessment_id
                or self.latest_disposition.id != self.latest_disposition.reused_from.disposition_id
                or self.latest_disposition.version
                    != self.latest_disposition.reused_from.disposition_version
            )
        ):
            raise ValueError("Latest disposition cannot exceed the assessment version")
        return self


class DecisionCandidateGroup(ImpactModel):
    decision_id: Identifier
    occurrence_count: Count
    revision_count: Count
    unassessed_count: Count
    change_required_count: Count
    captured_references: list[Reference]
    references_complete: StrictBool
    occurrences: list[DecisionCandidateOccurrence]
    next_occurrence_offset: Count | None

    @model_validator(mode="after")
    def coherent_group(self) -> Self:
        if not 1 <= self.revision_count <= self.occurrence_count or (
            self.unassessed_count + self.change_required_count > self.occurrence_count
        ):
            raise ValueError("Grouped counts must describe the same required occurrences")
        references = sorted({item.candidate_reference for item in self.occurrences})
        if len(references) > self.revision_count or self.captured_references != references or any(
            ref.rsplit("-", 1)[0] != self.decision_id for ref in references
        ) or self.references_complete != (len(references) == self.revision_count):
            raise ValueError("Captured references must describe the returned occurrence page")
        return self


def _valid_group_page(group: DecisionCandidateGroup, limit: int, offset: int) -> None:
    validate_page_items(group.occurrences, total=group.occurrence_count, limit=limit,
                        offset=offset, key=lambda item: (item.assessment_id,
                                                        item.candidate_reference))
    end = offset + len(group.occurrences)
    expected = end if end < group.occurrence_count else None
    if group.next_occurrence_offset != expected:
        raise ValueError("Occurrence continuation must identify the next reachable page")
    pending = sum(item.latest_disposition is None for item in group.occurrences)
    changes = sum(item.latest_disposition is not None and item.latest_disposition.disposition
                  == "change-required" for item in group.occurrences)
    if pending > group.unassessed_count or changes > group.change_required_count:
        raise ValueError("Occurrence counts exceed their complete group totals")
    if offset == 0 and expected is None and (
        pending != group.unassessed_count or changes != group.change_required_count
    ):
        raise ValueError("Complete occurrence pages must reproduce the group totals")


class DecisionChangeCandidatesRead(ImpactModel):
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
    max_occurrences_per_page: Literal[200]

    @model_validator(mode="after")
    def coherent_scope(self) -> Self:
        if self.assessment_count != self.scope_version or (
            self.acknowledged_scope_version > self.scope_version
        ):
            raise ValueError("Grouped review must retain exact current assessment scope")
        if not self.decision_count <= self.revision_count <= self.occurrence_count or (
            self.unassessed_count + self.change_required_count > self.occurrence_count
        ):
            raise ValueError("Global grouped candidate counts must agree")
        if self.total > self.decision_count or self.total != (
            self.decision_count if self.decision_id is None else int(self.total > 0)
        ):
            raise ValueError("Filtered total must count selected decision groups")
        if self.limit * self.occurrence_limit > self.max_occurrences_per_page:
            raise ValueError("Requested group and occurrence limits exceed the response bound")
        return self

    @model_validator(mode="after")
    def coherent_pages(self) -> Self:
        validate_page_items(self.items, total=self.total, limit=self.limit, offset=self.offset,
                            key=lambda item: item.decision_id)
        if self.decision_id is not None and any(
            item.decision_id != self.decision_id for item in self.items
        ):
            raise ValueError("Grouped results must match the exact decision filter")
        end = self.offset + len(self.items)
        if self.next_offset != (end if end < self.total else None):
            raise ValueError("Grouped continuation must identify the next reachable page")
        for group in self.items:
            _valid_group_page(group, self.occurrence_limit, self.occurrence_offset)
        if any(sum(getattr(group, key) for group in self.items) > getattr(self, key) for key in (
            "occurrence_count", "revision_count", "unassessed_count", "change_required_count",
        )):
            raise ValueError("Selected group counts exceed the whole-change totals")
        return self

    @model_validator(mode="after")
    def complete_totals(self) -> Self:
        if [item.decision_id for item in self.items] != sorted(
            item.decision_id for item in self.items
        ):
            raise ValueError("Decision groups must retain deterministic identifier order")
        if self.decision_id is None and self.offset == 0 and self.next_offset is None and any(
            sum(getattr(group, key) for group in self.items) != getattr(self, key) for key in (
                "occurrence_count", "revision_count", "unassessed_count", "change_required_count",
            )
        ):
            raise ValueError("Complete group pages must reproduce whole-change totals")
        return self
