"""New optional assertions do not alter permanent default create fingerprints."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.schemas import WorkItemCreate


def test_default_true_has_the_original_serialization(work_payload):
    original = WorkItemCreate.model_validate(work_payload)
    explicit = WorkItemCreate.model_validate({**work_payload, "inherit_decision_change": True})
    assert original.model_dump(mode="json") == explicit.model_dump(mode="json")
    assert "inherit_decision_change" not in explicit.model_dump(mode="json")
    assert "decision_change_inheritance_reason" not in explicit.model_dump(mode="json")


@pytest.mark.parametrize("reason", ["\ud800", "x\x00y", "\t", "x" * 4001, False])
def test_reason_is_nonblank_bounded_valid_unicode(work_payload, reason):
    with pytest.raises(ValidationError):
        WorkItemCreate.model_validate({**work_payload,
            "discovered_from_work_item_id": uuid4(), "inherit_decision_change": False,
            "decision_change_inheritance_reason": reason})
