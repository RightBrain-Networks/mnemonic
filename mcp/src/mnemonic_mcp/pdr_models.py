"""Product decision metadata supplied with a human question."""

import hashlib
import json
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic.experimental.missing_sentinel import MISSING

from .impact_models import DecisionDependency, SupersessionDetail
from .knowledge_models import DecisionIdentifier


def product_decision_hash(question: str, resolution: str | None) -> str:
    """Independently verify the canonical v1 question/answer document projection."""
    content = question if resolution is None else (
        "## Question\n\n" + question + "\n\n## Product decision\n\n" + resolution
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class HumanGateDecisionMetadata(BaseModel):
    """Omission retains a field; an explicit empty list clears its optional links."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ] | MISSING = MISSING
    related_decision_ids: Annotated[
        list[DecisionIdentifier], Field(max_length=100)
    ] | MISSING = MISSING
    related_work_item_ids: Annotated[list[UUID], Field(max_length=100)] | MISSING = MISSING
    related_artifact_ids: Annotated[list[UUID], Field(max_length=100)] | MISSING = MISSING
    depends_on: Annotated[list[DecisionDependency], Field(max_length=100)] | MISSING = MISSING
    supersedes: Annotated[list[DecisionIdentifier], Field(max_length=100)] | MISSING = MISSING
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


def decision_metadata_payload(
    metadata: HumanGateDecisionMetadata | MISSING,
) -> dict[str, object]:
    if metadata is MISSING:
        return {}
    return {"decision_metadata": metadata.model_dump(mode="json", exclude_unset=True)}


def decision_metadata_matches(
    response: HumanGateDecisionMetadata,
    intent: HumanGateDecisionMetadata,
    primary_work_id: UUID,
) -> bool:
    """Compare explicit sparse edits, accounting for canonical additive links."""
    actual = response.model_dump(mode="json")
    requested = intent.model_dump(mode="json", exclude_unset=True)
    for key, value in requested.items():
        if key == "related_work_item_ids":
            value = [*value, str(primary_work_id)]
        elif key == "supersedes":
            value = [*value, *(detail["decision_id"] for detail in actual["supersession_details"])]
        if _comparable_metadata(actual[key]) != _comparable_metadata(value):
            return False
    return True


def _comparable_metadata(value: object) -> str | set[str]:
    if isinstance(value, list):
        return {json.dumps(item, sort_keys=True) for item in value}
    return json.dumps(value, sort_keys=True)
