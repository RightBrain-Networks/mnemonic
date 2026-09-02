"""Pure human-gate validation and cursor-scope contracts."""

import base64
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import HumanGateRead, HumanGateResolutionCreate
from mnemonic_api.services.gates import _decode_cursor, _encode_cursor


def _encoded_cursor_payload(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _assert_invalid_cursor(cursor: str, *, project_id, work_item_id=None) -> None:
    with pytest.raises(ApplicationError) as raised:
        _decode_cursor(
            cursor,
            endpoint="attention",
            project_id=project_id,
            work_item_id=work_item_id,
            status="unresolved",
            direction="asc",
        )
    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "code": "invalid_cursor",
        "message": "The continuation cursor is invalid for this scope or filter.",
        "context": {},
    }


def test_gate_cursor_round_trip_and_every_scope_field_is_bound():
    project_id = uuid4()
    work_item_id = uuid4()
    cursor = _encode_cursor(
        endpoint="attention",
        project_id=project_id,
        work_item_id=work_item_id,
        status="unresolved",
        direction="asc",
        last_sequence=17,
    )
    assert (
        _decode_cursor(
            cursor,
            endpoint="attention",
            project_id=project_id,
            work_item_id=work_item_id,
            status="unresolved",
            direction="asc",
        )
        == 17
    )

    payload = {
        "v": 1,
        "endpoint": "attention",
        "project_id": str(project_id),
        "work_item_id": str(work_item_id),
        "status": "unresolved",
        "direction": "asc",
        "last_sequence": 17,
    }
    mutations = {
        "wrong_version": {**payload, "v": 2},
        "boolean_version": {**payload, "v": True},
        "wrong_endpoint": {**payload, "endpoint": "history"},
        "wrong_project": {**payload, "project_id": str(uuid4())},
        "wrong_work": {**payload, "work_item_id": str(uuid4())},
        "wrong_status": {**payload, "status": "all"},
        "wrong_direction": {**payload, "direction": "desc"},
        "zero_sequence": {**payload, "last_sequence": 0},
        "boolean_sequence": {**payload, "last_sequence": True},
        "oversized_sequence": {**payload, "last_sequence": 2**63},
        "extra_key": {**payload, "extra": "not allowed"},
        "missing_key": {key: value for key, value in payload.items() if key != "status"},
    }
    for malformed in mutations.values():
        _assert_invalid_cursor(
            _encoded_cursor_payload(malformed),
            project_id=project_id,
            work_item_id=work_item_id,
        )

    for malformed in (
        base64.urlsafe_b64encode(b"\xff").rstrip(b"=").decode("ascii"),
        base64.urlsafe_b64encode(b"x" * 2049).rstrip(b"=").decode("ascii"),
        "not*base64",
    ):
        _assert_invalid_cursor(
            malformed,
            project_id=project_id,
            work_item_id=work_item_id,
        )


def _unresolved_gate_payload() -> dict:
    project_id = uuid4()
    work_item_id = uuid4()
    checkpoint_id = uuid4()
    created_at = datetime.now(UTC)
    return {
        "id": uuid4(),
        "project_id": project_id,
        "work_item_id": work_item_id,
        "gate_type": "human",
        "question": "Which boundary should be used?",
        "requested_by_client": "pytest-agent",
        "requested_by_session_id": "gate-contracts",
        "requested_by_model": "test-model",
        "requested_context_revision": {
            "work_version": 1,
            "context_checkpoint_id": checkpoint_id,
            "relationship_event_count": 0,
        },
        "created_at": created_at,
        "status": "unresolved",
        "current_context_revision": {
            "work_version": 1,
            "context_checkpoint_id": checkpoint_id,
            "relationship_event_count": 0,
        },
        "resolved_at": None,
        "resolution": None,
        "resolved_by_client": None,
        "resolved_by_session_id": None,
        "resolved_by_model": None,
        "resolved_context_revision": None,
    }


def test_human_gate_read_computes_drift_and_rejects_incoherent_resolution_state():
    payload = _unresolved_gate_payload()
    unresolved = HumanGateRead.model_validate(payload)
    assert unresolved.status == "unresolved"
    assert unresolved.context_changed_since_request is False
    assert unresolved.context_changed_at_resolution is None

    current_drift = deepcopy(payload)
    current_drift["current_context_revision"]["work_version"] = 2
    drifted = HumanGateRead.model_validate(current_drift)
    assert drifted.work_changed_since_request is True
    assert drifted.context_checkpoint_changed_since_request is False
    assert drifted.relationships_changed_since_request is False
    assert drifted.context_changed_since_request is True

    with pytest.raises(ValidationError, match="work_changed_since_request"):
        HumanGateRead.model_validate(
            {**payload, "work_changed_since_request": False}
        )

    unresolved_with_answer = {**payload, "resolution": "Impossible answer."}
    with pytest.raises(
        ValidationError,
        match="Unresolved gates cannot contain resolution fields",
    ):
        HumanGateRead.model_validate(unresolved_with_answer)

    resolved = {
        **payload,
        "status": "resolved",
        "current_context_revision": {
            **payload["current_context_revision"],
            "work_version": 2,
        },
        "resolved_at": payload["created_at"] + timedelta(seconds=1),
        "resolution": "Use the reviewed boundary.",
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "gate-contracts-human",
        "resolved_context_revision": {
            **payload["current_context_revision"],
            "work_version": 2,
        },
    }
    parsed_resolved = HumanGateRead.model_validate(resolved)
    assert parsed_resolved.context_changed_at_resolution is True

    advanced_after_resolution = HumanGateRead.model_validate(
        {
            **resolved,
            "current_context_revision": payload["current_context_revision"],
        }
    )
    assert advanced_after_resolution.context_changed_since_request is False
    assert advanced_after_resolution.context_changed_at_resolution is True


def test_resolution_requires_the_exact_reviewed_revision_and_rejects_retired_flag():
    payload = {
        "resolution": "Use the reviewed boundary.",
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "gate-contracts-human",
        "resolved_by_model": None,
    }
    with pytest.raises(ValidationError, match="reviewed_context_revision"):
        HumanGateResolutionCreate.model_validate(payload)

    with pytest.raises(ValidationError, match="acknowledge_context_change"):
        HumanGateResolutionCreate.model_validate(
            {
                **payload,
                "reviewed_context_revision": {
                    "work_version": 1,
                    "context_checkpoint_id": uuid4(),
                    "relationship_event_count": 0,
                },
                "acknowledge_context_change": False,
            }
        )
