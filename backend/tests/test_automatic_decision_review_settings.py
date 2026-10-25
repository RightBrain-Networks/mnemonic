"""Decision review policy validation is strict and uses ordinary work priorities."""

import pytest
from pydantic import ValidationError

from mnemonic_api.application.browser_policy.projects import settings_patch
from mnemonic_api.knowledge_schemas import DomainRenameRequest
from mnemonic_api.schemas import ProjectSettingsPatch


@pytest.mark.parametrize("changes", [
    {"automatic_decision_reviews": True},
    {"automatic_decision_reviews": False},
    {"decision_review_priority": 0},
    {"decision_review_priority": 41},
    {"decision_review_priority": 100},
    {"automatic_decision_reviews": True, "decision_review_priority": 40},
])
def test_project_decision_review_settings_are_no_longer_editable(changes):
    payload = {"expected_revision": "1", **changes}
    with pytest.raises(ValidationError):
        ProjectSettingsPatch.model_validate(payload)
    assert not settings_patch(payload)


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


@pytest.mark.parametrize("priority", [0, 41, 100])
def test_domain_review_priority_accepts_ordinary_work_range(priority):
    result = DomainRenameRequest(expected_version=1, decision_review_priority=priority)
    assert result.model_dump() == {
        "expected_version": 1, "decision_review_priority": priority,
    }


@pytest.mark.parametrize("priority", [None, True, "40", 1.5, -1, 101])
def test_domain_review_priority_rejects_noncanonical_values(priority):
    with pytest.raises(ValidationError):
        DomainRenameRequest(expected_version=1, decision_review_priority=priority)
