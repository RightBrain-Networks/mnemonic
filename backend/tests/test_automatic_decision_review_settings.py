"""Decision review policy validation is strict and uses ordinary work priorities."""

import pytest
from pydantic import ValidationError

from mnemonic_api.application.browser_policy.projects import settings_patch
from mnemonic_api.schemas import ProjectSettingsPatch


@pytest.mark.parametrize("changes", [
    {"automatic_decision_reviews": True},
    {"automatic_decision_reviews": False},
    {"decision_review_priority": 0},
    {"decision_review_priority": 41},
    {"decision_review_priority": 100},
    {"automatic_decision_reviews": True, "decision_review_priority": 40},
])
def test_decision_review_settings_accept_boolean_and_ordinary_priority_range(changes):
    payload = {"expected_revision": "1", **changes}
    assert ProjectSettingsPatch.model_validate(payload).model_dump(exclude_unset=True) == payload
    assert settings_patch(payload)


@pytest.mark.parametrize("changes", [
    {"automatic_decision_reviews": None},
    {"automatic_decision_reviews": "true"},
    {"automatic_decision_reviews": 1},
    {"decision_review_priority": None},
    {"decision_review_priority": True},
    {"decision_review_priority": "40"},
    {"decision_review_priority": 1.5},
    {"decision_review_priority": -1},
    {"decision_review_priority": 101},
])
def test_decision_review_settings_reject_noncanonical_values(changes):
    payload = {"expected_revision": "1", **changes}
    with pytest.raises(ValidationError):
        ProjectSettingsPatch.model_validate(payload)
    assert not settings_patch(payload)
