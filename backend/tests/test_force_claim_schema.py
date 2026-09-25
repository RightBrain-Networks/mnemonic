"""Replacing a live capability requires an explicit boolean request."""

import pytest
from pydantic import ValidationError

from mnemonic_api.schemas import WorkClaimCreate

IDENTITY = {"holder_client": "codex", "holder_session_id": "session", "claim_request_id": "claim"}


def test_force_defaults_to_false():
    assert WorkClaimCreate.model_validate(IDENTITY).force is False


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false"])
def test_force_rejects_coerced_values(value):
    with pytest.raises(ValidationError):
        WorkClaimCreate.model_validate({**IDENTITY, "force": value})
