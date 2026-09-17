"""Ranking metadata rejects misleading availability and completeness combinations."""

import pytest
from pydantic import ValidationError

from mnemonic_api.errors import ApplicationError
from mnemonic_api.search_ranking import (
    SemanticDisposition,
    completed_semantic,
    unavailable_semantic,
)


@pytest.mark.parametrize("change", [
    {"inference": {"status": "completed"}, "candidate_scope": "none"},
    {"inference": {"status": "completed"}, "candidate_scope": "full_scope",
     "partial_vectors": True, "comparison_incomplete": False},
    {"inference": {"status": "not_requested", "reason": "model_failure"}},
    {"inference": {"status": "unavailable", "reason": "arbitrary provider message"}},
    {"cache_refresh": {"status": "completed", "reason": "cache_refresh_failed"}},
])
def test_semantic_disposition_rejects_incoherent_or_unreviewed_states(change):
    with pytest.raises(ValidationError):
        SemanticDisposition.model_validate(change)


def test_sanitized_errors_only_accept_reviewed_semantic_shape():
    valid = unavailable_semantic("capacity_exhausted").model_dump()
    assert ApplicationError(503, "semantic_unavailable", "Unavailable",
                            context={"semantic": valid}).detail["context"]["semantic"] == valid
    unsafe = {**valid, "provider_response": "untrusted private text"}
    assert ApplicationError(503, "semantic_unavailable", "Unavailable",
                            context={"semantic": unsafe}).detail["context"] == {}
    assert completed_semantic().comparison_incomplete is False
