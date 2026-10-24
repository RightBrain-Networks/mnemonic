"""Explicit substantive changes and versioned review obligations."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictInt, StringConstraints, model_validator

from .impact_models import ImpactActor, ImpactModel, Reference

ChangeReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)
]


class DecisionChangeInput(ImpactModel):
    kind: Literal["substantive", "record-existing", "maintenance"] = "substantive"
    change_id: UUID | None = None
    reason: ChangeReason | None = None
    governing_reference: Reference | None = None

    @model_validator(mode="after")
    def classification(self) -> Self:
        if self.kind != "substantive" and self.reason is None:
            raise ValueError("Non-substantive revisions require a reason")
        if self.kind == "record-existing" and (
            self.change_id is None or self.governing_reference is None
        ):
            raise ValueError("Recording an existing ruling requires its change and exact reference")
        if self.kind != "record-existing" and self.governing_reference is not None:
            raise ValueError("Only record-existing declares a governing revision")
        return self


class DecisionRevisionChange(DecisionChangeInput):
    source_work_item_id: UUID | None
    requires_admission: StrictBool = Field(default=False, exclude_if=lambda v: not v)


class DecisionChangeRead(ImpactModel):
    id: UUID
    project_id: UUID
    origin_reference: Reference | None
    review_work_item_id: UUID | None
    review_work_project_id: UUID | None
    review_closed: StrictBool
    successor_change_id: UUID | None
    parent_change_id: UUID | None
    scope_version: Annotated[StrictInt, Field(ge=0)]
    acknowledged_scope_version: Annotated[StrictInt, Field(ge=0)]
    assessment_count: Annotated[StrictInt, Field(ge=0)]
    revision_count: Annotated[StrictInt, Field(ge=0)]
    pending_revision_count: Annotated[StrictInt, Field(ge=0)]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def coherent_acknowledgement(self) -> Self:
        if self.acknowledged_scope_version > self.scope_version:
            raise ValueError("Acknowledgement cannot exceed the current scope")
        if self.assessment_count != self.scope_version:
            raise ValueError("Scope version must count its attached assessment obligations")
        return self


class DecisionChangeUpdate(ImpactActor):
    actor_client: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    actor_model: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    action: Literal["adopt", "acknowledge"]
    client_operation_id: UUID
    change_id: UUID | None = None
    work_item_id: UUID | None = None
    expected_work_version: Annotated[StrictInt, Field(ge=1)] | None = None
    expected_scope_version: Annotated[StrictInt, Field(ge=0)] | None = None
    assessment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    lease_token: Annotated[str, Field(min_length=1, max_length=200)] | None = None

    @model_validator(mode="after")
    def action_arguments(self) -> Self:
        if self.action == "adopt":
            if self.work_item_id is None or self.expected_work_version is None \
                    or not self.assessment_ids or len(set(self.assessment_ids)) != len(
                        self.assessment_ids
                    ) or self.change_id is not None or self.expected_scope_version is not None:
                raise ValueError("Adoption requires work, its version and unique assessment IDs")
        elif self.change_id is None or self.expected_scope_version is None \
                or self.work_item_id is not None or self.expected_work_version is not None \
                or self.assessment_ids:
            raise ValueError("Acknowledgement requires the change and current scope version")
        return self
