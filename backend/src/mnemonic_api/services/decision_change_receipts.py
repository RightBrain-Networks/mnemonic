"""Permanent change-operation receipts bind the admitted action and exact scope."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from mnemonic_api.decision_change_schemas import DecisionChangeRead, DecisionChangeUpdateRequest
from mnemonic_api.schemas import APIModel


def change_matches(
    project_id: UUID, target: Mapping[str, str], payload: APIModel, typed: APIModel,
) -> bool:
    request = cast(DecisionChangeUpdateRequest, payload)
    result = cast(DecisionChangeRead, typed)
    if result.project_id != project_id or result.assessment_count != result.scope_version:
        return False
    if request.action == "adopt":
        return (result.review_work_item_id == request.work_item_id
                and result.assessment_count >= len(request.assessment_ids))
    if request.action == "admit":
        return result.parent_change_id == request.change_id and result.id != request.change_id
    return (result.id == request.change_id
            and result.scope_version == request.expected_scope_version
            and result.acknowledged_scope_version == result.scope_version)
