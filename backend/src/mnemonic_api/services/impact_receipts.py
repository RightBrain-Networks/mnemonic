"""The assessment mutation's permanent receipt binds its exact asserted disposition."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from mnemonic_api.impact_schemas import (
    ImpactDispositionBatchRead,
    ImpactDispositionBatchRequest,
    ImpactDispositionRead,
    ImpactDispositionRequest,
)
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


def disposition_batch_matches(
    project_id: UUID, target: Mapping[str, str], payload: APIModel, typed: APIModel,
) -> bool:
    request = cast(ImpactDispositionBatchRequest, payload)
    result = cast(ImpactDispositionBatchRead, typed)
    return (
        result.project_id == project_id
        and str(result.assessment_id) == target.get("assessment_id")
        and result.version == request.expected_version + len(request.items)
        and len(result.items) == len(request.items)
        and len({item.id for item in result.items}) == len(result.items)
        and all(disposition_matches(project_id, target, ImpactDispositionRequest(
            **item.model_dump(), actor=request.actor,
            expected_version=request.expected_version + offset,
        ), actual) for offset, (item, actual) in enumerate(
            zip(request.items, result.items, strict=True),
        ))
    )
