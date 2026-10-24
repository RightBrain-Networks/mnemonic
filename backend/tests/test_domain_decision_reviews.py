"""Domain overrides are strict, sparse patches preserve historical receipt shapes."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.knowledge_schemas import (
    DomainCreate,
    DomainCreateRequest,
    DomainRenameRequest,
    KnowledgeDomainRead,
)
from mnemonic_api.services.client_operations import canonical_request_bytes, operation_spec
from mnemonic_api.services.knowledge_receipts import created_domain_matches, renamed_domain_matches


@pytest.mark.parametrize("policy", ["inherit", "on", "off"])
def test_domain_policy_create_and_patch_accept_exact_values(policy):
    created = DomainCreateRequest(name="Architecture", prefix="adr",
                                  automatic_decision_reviews=policy)
    assert created.automatic_decision_reviews == policy
    patch = {"automatic_decision_reviews": policy, "expected_version": 1}
    assert DomainRenameRequest.model_validate(patch).model_dump() == patch


@pytest.mark.parametrize("policy", [None, True, False, 0, 1, "enabled", "ON", ""])
def test_domain_policy_rejects_noncanonical_values(policy):
    with pytest.raises(ValidationError):
        DomainCreateRequest(name="Architecture", prefix="ADR", automatic_decision_reviews=policy)
    with pytest.raises(ValidationError):
        DomainRenameRequest(automatic_decision_reviews=policy, expected_version=1)


@pytest.mark.parametrize("fields", [{}, {"name": None}, {"name": " "}])
def test_domain_patch_requires_name_or_policy_and_rejects_null(fields):
    with pytest.raises(ValidationError):
        DomainRenameRequest.model_validate({"expected_version": 1, **fields})


def test_inherited_create_preserves_historical_fingerprint_shape():
    project_id = uuid4()
    payload = DomainCreate(name="Architecture", prefix="ADR", client_operation_id=uuid4())
    canonical = json.loads(canonical_request_bytes(
        operation_spec("create_knowledge_domain"), project_id, {}, payload,
    ))
    assert canonical["request"] == {"name": "Architecture", "prefix": "ADR"}


def test_sparse_receipt_results_are_retained_without_inventing_policy():
    project_id, domain_id = uuid4(), uuid4()
    old = {"id": domain_id, "project_id": project_id, "name": "Architecture", "prefix": "ADR",
           "version": 1, "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
           "decision_count": 0}
    result = KnowledgeDomainRead.model_validate(old)
    assert "automatic_decision_reviews" not in result.model_dump()
    target = {"domain_id": str(domain_id)}
    assert created_domain_matches(project_id, {}, DomainCreateRequest(
        name="Architecture", prefix="ADR"), result)
    assert not created_domain_matches(project_id, {}, DomainCreateRequest(
        name="Architecture", prefix="ADR", automatic_decision_reviews="on"), result)
    assert renamed_domain_matches(project_id, target, DomainRenameRequest(
        name="Architecture", expected_version=1), result)
    assert not renamed_domain_matches(project_id, target, DomainRenameRequest(
        automatic_decision_reviews="inherit", expected_version=1), result)
