"""Transport headers must agree with the actual semantic failure's retry advice."""

import pytest

from mnemonic_api.errors import duplicate_suggestion_unavailable, semantic_unavailable


@pytest.mark.parametrize("error", [duplicate_suggestion_unavailable, semantic_unavailable])
@pytest.mark.parametrize("reason", [
    "capacity_exhausted", "deadline_exceeded", "model_failure", "vectors_pending",
])
def test_only_busy_capacity_returns_retry_after(error, reason):
    response = error(reason)
    disposition = response.detail["context"]["semantic"]
    if reason == "capacity_exhausted":
        assert response.headers == {"Retry-After": "1"}
        assert disposition["retry"] == {"max_attempts": 1, "after_seconds": 1}
        assert "Retry" in response.detail["message"]
    else:
        assert not response.headers
        assert disposition["retry"] is None
        assert "immediate retry is not recommended" in response.detail["message"]
