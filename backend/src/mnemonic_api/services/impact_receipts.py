"""The assessment mutation's permanent receipt binds its exact asserted disposition."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from mnemonic_api.impact_schemas import ImpactDispositionRead, ImpactDispositionRequest
from mnemonic_api.schemas import APIModel


def disposition_matches(
    project_id: UUID, target: Mapping[str, str], payload: APIModel, typed: APIModel
) -> bool:
    request, result = cast(ImpactDispositionRequest, payload), cast(ImpactDispositionRead, typed)
    return (
        result.project_id == project_id
        and str(result.assessment_id) == target.get("assessment_id")
        and result.version == request.expected_version + 1
        and all(
            getattr(result, field) == getattr(request, field)
            for field in (
                "candidate_reference",
                "disposition",
                "reason",
                "remediation_work_item_id",
                "actor",
            )
        )
    )
