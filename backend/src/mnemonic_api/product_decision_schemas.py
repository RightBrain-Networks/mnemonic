"""Product decision identities and sparse edits on the human question workflow."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator
from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.decision_link_schemas import DecisionDependency, Identifier, SupersessionDetail


class ProductDecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, allow_inf_nan=False)


class HumanGateDecisionMetadata(ProductDecisionModel):
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] | MISSING = MISSING
    related_decision_ids: Annotated[list[Identifier], Field(max_length=100)] | MISSING = MISSING
    related_work_item_ids: Annotated[list[UUID], Field(max_length=100)] | MISSING = MISSING
    related_artifact_ids: Annotated[list[UUID], Field(max_length=100)] | MISSING = MISSING
    depends_on: Annotated[list[DecisionDependency], Field(max_length=100)] | MISSING = MISSING
    supersedes: Annotated[list[Identifier], Field(max_length=100)] | MISSING = MISSING
    supersession_details: Annotated[
        list[SupersessionDetail], Field(max_length=100)
    ] | MISSING = MISSING

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Titles cannot contain NUL characters")
        value.encode("utf-8")
        return value


class HumanGateDecisionRead(ProductDecisionModel):
    originating_work_project_id: UUID | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    metadata: HumanGateDecisionMetadata
    sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    document_id: UUID
    domain_id: UUID
    decision_id: Identifier
    reference: str
    revision: Annotated[StrictInt, Field(ge=0)]
    state: Literal["proposed", "accepted"]


class DecisionHumanGateRead(ProductDecisionModel):
    work_project_id: UUID
    gate_id: UUID
    work_item_id: UUID
    status: Literal["unresolved", "resolved"]
    question_version: Annotated[StrictInt, Field(ge=1)]


def validate_gate_decision(
    decision: HumanGateDecisionRead | None, *, question: str, question_version: int,
    resolution: str | None, status: str, work_item_id: UUID, project_id: UUID,
) -> None:
    """Old immutable receipts may omit the pointer; supplied pointers must be exact."""
    if decision is None:
        return
    import hashlib

    from mnemonic_api.product_decision_content import product_decision_content

    expected = question_version if status == "resolved" else question_version - 1
    if (decision.revision != expected
            or decision.state != ("accepted" if status == "resolved" else "proposed")
            or not decision.decision_id.startswith("PDR-")
            or decision.reference != f"{decision.decision_id}-{decision.revision}"):
        raise ValueError("Product decision identity must match its question and answer revision")
    metadata = decision.metadata.model_dump()
    if set(metadata) != set(HumanGateDecisionMetadata.model_fields):
        raise ValueError("Product decision reads require the complete metadata snapshot")
    moved_origin = decision.originating_work_project_id
    if moved_origin is not None and (moved_origin == project_id or status != "resolved"):
        raise ValueError("Only a resolved historical question can have a moved work origin")
    if moved_origin is None and work_item_id not in metadata["related_work_item_ids"]:
        raise ValueError("Product decisions must retain their originating work link")
    content = product_decision_content(question, resolution)
    if decision.sha256 != hashlib.sha256(content.encode("utf-8")).hexdigest():
        raise ValueError("Product decision hash must match the exact question and answer")
