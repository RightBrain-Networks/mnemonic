"""Explicit, audited decision change grouping and review scope admission."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from mnemonic_api.schemas import APIModel

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8000)]
PinnedReference = Annotated[
    str, StringConstraints(pattern=r"^[A-Z]{1,5}-[0-9]{5,19}-[0-9]{1,10}$")
]


def _database_text(value: str | None) -> str | None:
    if value is not None:
        if "\x00" in value or not value.strip():
            raise ValueError("Text must be nonblank and cannot contain NUL characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("Text must be valid Unicode") from None
    return value


class UnchangedSupersession(APIModel):
    target_reference: PinnedReference
    governing_reference: PinnedReference
    reason: Reason
    valid_reason = field_validator("reason")(_database_text)


class DecisionChangeDeclaration(APIModel):
    kind: Literal["substantive", "record-existing", "maintenance"] = "substantive"
    change_id: UUID | None = None
    reason: Reason | None = None
    governing_reference: PinnedReference | None = None
    unchanged_supersessions: list[UnchangedSupersession] = Field(
        default_factory=list, max_length=100, exclude_if=lambda value: not value,
    )
    valid_reason = field_validator("reason")(_database_text)

    @model_validator(mode="after")
    def classification(self) -> Self:
        targets = [item.target_reference.rsplit("-", 1)[0]
                   for item in self.unchanged_supersessions]
        if len(targets) != len(set(targets)):
            raise ValueError("Unchanged supersession targets must name unique decisions")
        if self.kind != "substantive" and self.reason is None:
            raise ValueError("Non-substantive classifications require a reason")
        if self.kind == "record-existing" and (
            self.change_id is None or self.governing_reference is None
        ):
            raise ValueError("Recording a ruling requires its change and exact revision")
        if self.kind != "record-existing" and self.governing_reference is not None:
            raise ValueError("Only record-existing declares a governing revision")
        return self


class DecisionRevisionChangeRead(APIModel):
    requires_admission: bool = Field(default=False, exclude_if=lambda value: not value)
    change_id: UUID | None
    kind: Literal["substantive", "record-existing", "maintenance"]
    reason: str | None
    governing_reference: str | None
    source_work_item_id: UUID | None
    unchanged_supersessions: list[UnchangedSupersession] = Field(
        default_factory=list, exclude_if=lambda value: not value,
    )


class DecisionChangeRead(APIModel):
    pending_revision_count: int = Field(ge=0)
    id: UUID
    project_id: UUID
    origin_reference: str | None
    review_work_item_id: UUID | None
    review_work_project_id: UUID | None
    review_closed: bool
    successor_change_id: UUID | None
    parent_change_id: UUID | None
    scope_version: int = Field(ge=0)
    acknowledged_scope_version: int = Field(ge=0)
    assessment_count: int = Field(ge=0)
    revision_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def coherent_scope(self) -> Self:
        if (self.acknowledged_scope_version > self.scope_version
                or self.assessment_count != self.scope_version):
            raise ValueError("Assessment count and acknowledgement must match retained scope")
        if (self.review_work_item_id is None) != (self.review_work_project_id is None):
            raise ValueError("A review driver must identify its current project")
        if self.review_closed and self.review_work_item_id is None:
            raise ValueError("A driverless change has no closed review")
        if self.parent_change_id == self.id or self.successor_change_id == self.id:
            raise ValueError("A change cannot be its own parent or successor")
        return self


class DecisionChangeUpdateRequest(APIModel):
    action: Literal["adopt", "acknowledge", "admit"]
    change_id: UUID | None = None
    work_item_id: UUID | None = None
    expected_work_version: int | None = Field(default=None, ge=1)
    expected_scope_version: int | None = Field(default=None, ge=0)
    assessment_ids: list[UUID] = Field(default_factory=list, max_length=100)
    lease_token: str | None = None
    actor_client: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                  max_length=64)]
    actor_session_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                                      max_length=200)]
    actor_model: Annotated[str | None, Field(min_length=1, max_length=200)] = None
    valid_actor = field_validator("actor_client", "actor_session_id", "actor_model")(_database_text)

    @model_validator(mode="after")
    def action_fields(self) -> Self:
        if len(set(self.assessment_ids)) != len(self.assessment_ids):
            raise ValueError("Assessment IDs must be unique")
        if self.action == "adopt":
            if (not self.assessment_ids or self.work_item_id is None
                    or self.expected_work_version is None or self.change_id is not None
                    or self.expected_scope_version is not None):
                raise ValueError("Adoption requires work, current work version and assessments")
        elif (self.change_id is None or self.expected_scope_version is None
              or self.work_item_id is not None or self.expected_work_version is not None
              or self.assessment_ids):
            raise ValueError("Acknowledgement/admission requires change and current scope version")
        return self


class DecisionChangeUpdate(DecisionChangeUpdateRequest):
    client_operation_id: UUID
