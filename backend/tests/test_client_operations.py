"""Unit contract for Phase 6 request identity and response registration."""

import hashlib
import json
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.errors import ApplicationError
from mnemonic_api.schemas import (
    CheckpointCreate,
    EventMetadata,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    LeaseReleaseCreate,
    LeaseTokenCreate,
    MutationActor,
    ProgressEventCreate,
    ProjectCreate,
    ProjectPatch,
    RelationshipCreate,
    RelationshipRemovalCreate,
    WorkClaimCreate,
    WorkCompletionCreate,
    WorkDeferralCreate,
    WorkDeletionCreate,
    WorkItemCreate,
    WorkItemPatch,
)
from mnemonic_api.services.client_operations import (
    OPERATION_REGISTRY,
    REGISTERED_OPERATION_KINDS,
    CompletedOperation,
    ReservedOperation,
    UnprotectedOperation,
    _render_registered_response,
    _response_matches_operation,
    canonical_request_bytes,
    complete_client_operation,
    operation_spec,
    prepare_client_operation,
    reject_client_operation_secret_echo,
    request_fingerprint,
    reserve_client_operation,
)

PROJECT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORK_ID = UUID("20000000-0000-0000-0000-000000000001")
OTHER_WORK_ID = UUID("20000000-0000-0000-0000-000000000002")
CHECKPOINT_ID = UUID("30000000-0000-0000-0000-000000000001")
GATE_ID = UUID("35000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("40000000-0000-0000-0000-000000000001")
OPERATION_ID_SPELLINGS = (
    str(OPERATION_ID).upper(),
    OPERATION_ID.hex,
    OPERATION_ID.hex.upper(),
    "{" + str(OPERATION_ID).upper() + "}",
    "urn:uuid:" + str(OPERATION_ID).upper(),
)


def actor() -> dict[str, str]:
    return {
        "actor_client": "pytest",
        "actor_session_id": "phase-6-unit",
        "actor_model": "test-model",
    }


def checkpoint() -> dict[str, object]:
    return {
        "prompt": "Preserve exact checkpoint text.\r\n",
        "source_client": "pytest",
        "source_session_id": "phase-6-unit",
        "source_model": "test-model",
        "tags": ["Idempotency", "backend"],
        "source_metadata": {"nested": {"b": 2, "a": "one"}},
    }


def work_payload(**changes: object) -> WorkItemCreate:
    values: dict[str, object] = {
        "title": "Implement durable receipt",
        "summary": "Bind one client operation to one successful mutation.",
        "initial_checkpoint": checkpoint(),
        "client_operation_id": OPERATION_ID,
    }
    values.update(changes)
    return WorkItemCreate.model_validate(values)


def canonical_vector_cases():
    checkpoint_request = CheckpointCreate(
        **checkpoint(),
        kind="progress",
        lease_token="lease-capability",
        client_operation_id=OPERATION_ID,
    )
    progress_request = ProgressEventCreate(
        body="Implemented café without normalizing e\u0301.\r\n",
        metadata={"nested": {"b": 2, "a": [True, None]}},
        actor=MutationActor(**actor()),
        lease_token="lease-capability",
        client_operation_id=OPERATION_ID,
    )
    relationship_request = RelationshipCreate(
        relationship_type="related",
        source_work_item_id=OTHER_WORK_ID,
        target_work_item_id=WORK_ID,
        created_by_client="pytest",
        created_by_session_id="phase-6-unit",
        created_by_model="test-model",
        client_operation_id=OPERATION_ID,
    )
    update_request = WorkItemPatch(
        expected_version=7,
        title="Keep exact Unicode café",
        actor=MutationActor(**actor()),
        lease_token="lease-capability",
        client_operation_id=OPERATION_ID,
    )
    deferral_request = WorkDeferralCreate(
        expected_version=8,
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    completion_request = WorkCompletionCreate(
        expected_version=8,
        checkpoint=checkpoint(),
        lease_token="lease-capability",
        client_operation_id=OPERATION_ID,
    )
    deletion_request = WorkDeletionCreate(
        expected_version=9,
        lease_token="lease-capability",
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    removal_request = RelationshipRemovalCreate(
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    release_request = LeaseReleaseCreate(
        lease_token="lease-capability",
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    gate_request = HumanGateRequestCreate(
        question="Choose the durable release boundary.\r\n",
        requested_by_client="pytest",
        requested_by_session_id="phase-7-unit",
        requested_by_model="test-model",
        client_operation_id=OPERATION_ID,
    )
    gate_resolution = HumanGateResolutionCreate(
        resolution="Use the reviewed branch boundary.",
        resolved_by_client="dashboard",
        resolved_by_session_id="phase-7-human",
        reviewed_context_revision={
            "work_version": 9,
            "context_checkpoint_id": CHECKPOINT_ID,
            "relationship_event_count": 4,
        },
        client_operation_id=OPERATION_ID,
    )

    checkpoint_fields = {
        "prompt": "Preserve exact checkpoint text.\r\n",
        "source_client": "pytest",
        "source_session_id": "phase-6-unit",
        "source_model": "test-model",
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": ["idempotency", "backend"],
        "source_metadata": {"nested": {"b": 2, "a": "one"}},
    }
    actor_fields = {
        "actor_client": "pytest",
        "actor_session_id": "phase-6-unit",
        "actor_model": "test-model",
    }
    return [
        (
            "create_work",
            {},
            work_payload(
                initial_relationships=[
                    {
                        "type": "related",
                        "direction": "incoming",
                        "other_work_item_id": OTHER_WORK_ID,
                    },
                    {
                        "type": "blocks",
                        "direction": "outgoing",
                        "other_work_item_id": WORK_ID,
                    },
                ]
            ),
            {
                "title": "Implement durable receipt",
                "summary": "Bind one client operation to one successful mutation.",
                "priority": 0,
                "status": "pending",
                "initial_checkpoint": checkpoint_fields,
                "initial_relationships": [
                    {
                        "type": "blocks",
                        "direction": "outgoing",
                        "other_work_item_id": str(WORK_ID),
                        "context_checkpoint_id": None,
                    },
                    {
                        "type": "related",
                        "direction": "outgoing",
                        "other_work_item_id": str(OTHER_WORK_ID),
                        "context_checkpoint_id": None,
                    },
                ],
            },
        ),
        (
            "add_checkpoint",
            {"work_item_id": WORK_ID},
            checkpoint_request,
            {
                **checkpoint_fields,
                "kind": "progress",
                "lease_token": "lease-capability",
            },
        ),
        (
            "append_event",
            {"work_item_id": WORK_ID},
            progress_request,
            {
                "event_type": "progress",
                "body": "Implemented café without normalizing e\u0301.\r\n",
                "metadata": {"nested": {"b": 2, "a": [True, None]}},
                "actor": actor_fields,
                "lease_token": "lease-capability",
            },
        ),
        (
            "add_relationship",
            {},
            relationship_request,
            {
                "relationship_type": "related",
                "source_work_item_id": str(WORK_ID),
                "target_work_item_id": str(OTHER_WORK_ID),
                "created_by_client": "pytest",
                "created_by_session_id": "phase-6-unit",
                "created_by_model": "test-model",
                "context_checkpoint_id": None,
            },
        ),
        (
            "update_work",
            {"work_item_id": WORK_ID},
            update_request,
            {
                "expected_version": 7,
                "title": "Keep exact Unicode café",
                "summary": None,
                "priority": None,
                "status": None,
                "lease_token": "lease-capability",
                "actor": actor_fields,
            },
        ),
        (
            "defer_work",
            {"work_item_id": WORK_ID},
            deferral_request,
            {"expected_version": 8, "actor": actor_fields},
        ),
        (
            "complete_work",
            {"work_item_id": WORK_ID},
            completion_request,
            {
                "expected_version": 8,
                "checkpoint": checkpoint_fields,
                "lease_token": "lease-capability",
            },
        ),
        (
            "delete_work",
            {"work_item_id": WORK_ID},
            deletion_request,
            {
                "expected_version": 9,
                "lease_token": "lease-capability",
                "actor": actor_fields,
            },
        ),
        (
            "remove_relationship",
            {"relationship_id": CHECKPOINT_ID},
            removal_request,
            {"actor": actor_fields},
        ),
        (
            "release_claim",
            {"work_item_id": WORK_ID},
            release_request,
            {"lease_token": "lease-capability", "actor": actor_fields},
        ),
        (
            "request_human_input",
            {"work_item_id": WORK_ID},
            gate_request,
            {
                "gate_type": "human",
                "question": "Choose the durable release boundary.\r\n",
                "requested_by_client": "pytest",
                "requested_by_session_id": "phase-7-unit",
                "requested_by_model": "test-model",
            },
        ),
        (
            "resolve_human_input",
            {"work_item_id": WORK_ID, "gate_id": GATE_ID},
            gate_resolution,
            {
                "resolution": "Use the reviewed branch boundary.",
                "resolved_by_client": "dashboard",
                "resolved_by_session_id": "phase-7-human",
                "resolved_by_model": None,
                "reviewed_context_revision": {
                    "work_version": 9,
                    "context_checkpoint_id": str(CHECKPOINT_ID),
                    "relationship_event_count": 4,
                },
            },
        ),
    ]


CANONICAL_DIGESTS = {
    "create_work": "f1a948b168261e4a32647460b7c1d97ad85eb461db3c9a748a9f4598d84948d5",
    "add_checkpoint": "d6e36ee2602c684acecffbef620d1d025c8fd1c53773faefe90619c3876fa353",
    "append_event": "d683fb8eb06712e11e428c2c53a6dcb76cf599e9dba7d1f1de0806ba629d1dc4",
    "add_relationship": "ab063fbc4de9ebdf312e8a2261371f66bed0d4c521f268d79a7286e0a5fb1ce2",
    "update_work": "a2132547ab61bc8b4f5141daf252f9dc8b56fd69d3fde7be3d38b97a6f4071b8",
    "defer_work": "c9413cf7e9a09505b112229516847ef452d39c96c65fa58a79c9019d58e5184a",
    "complete_work": "ce8f9e979f02cdefd0c4e7e0949775c7dc7098aa9c18d3779e034c2a9d7a09b5",
    "delete_work": "a7a4bdbc1b8351e50f8b449bf7d6b911381bc51625a2ffa9947cd4acb0762bd0",
    "remove_relationship": "dd9ade0a4af557a223f23ddb660f8bef4a02aa4ff635a034bf44dc2b7263e59e",
    "release_claim": "c126a78e24676588e683387709035ecbedde7b6d2f6d1799bf2e36f71678fb8e",
    "request_human_input": "ca768c0d25c3abe9b966af1612c0f102418b748f1a795135594223ee8749a114",
    "resolve_human_input": "34371216cc7e87183d1a96cffd20ccc0da4b1cb17074e5074eeacd351419e4fb",
}


def response_vector_cases():
    created_at = "2026-09-01T12:00:00Z"
    updated_at = "2026-09-01T12:00:01Z"
    relationship_id = UUID("50000000-0000-0000-0000-000000000001")

    def work(*, status="pending", version=2):
        return {
            "id": str(WORK_ID),
            "project_id": str(PROJECT_ID),
            "title": "Frozen response work",
            "summary": "Freeze the public response-v1 representation.",
            "status": status,
            "priority": 42,
            "initial_checkpoint_id": str(CHECKPOINT_ID),
            "version": version,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def checkpoint_response(*, kind="progress"):
        return {
            "id": str(CHECKPOINT_ID),
            "work_item_id": str(WORK_ID),
            "kind": kind,
            "prompt": "Frozen checkpoint.\r\n",
            "source_client": "pytest",
            "source_session_id": "phase-6-response-v1",
            "source_model": "test-model",
            "source_session_url": None,
            "repository_branch": "work/phase-6",
            "verified_against": "abcdef1",
            "tags": ["idempotency", "response-v1"],
            "source_metadata": {"nested": {"stable": True}},
            "migration_origin": None,
            "legacy_record_id": None,
            "created_at": created_at,
        }

    relationship = {
        "id": str(relationship_id),
        "project_id": str(PROJECT_ID),
        "relationship_type": "blocks",
        "source_work_item_id": str(WORK_ID),
        "target_work_item_id": str(OTHER_WORK_ID),
        "context_checkpoint_work_item_id": str(WORK_ID),
        "context_checkpoint_id": str(CHECKPOINT_ID),
        "created_by_client": "pytest",
        "created_by_session_id": "phase-6-response-v1",
        "created_by_model": "test-model",
        "created_at": created_at,
    }
    progress_event = {
        "id": 17,
        "project_id": str(PROJECT_ID),
        "work_item_id": str(WORK_ID),
        "event_type": "progress",
        "actor_kind": "client",
        "actor_client": "pytest",
        "actor_session_id": "phase-6-response-v1",
        "actor_model": "test-model",
        "body": "Frozen progress body.",
        "checkpoint_id": None,
        "lease_generation_id": None,
        "lease_release_id": None,
        "relationship_id": None,
        "relationship_source_work_item_id": None,
        "relationship_target_work_item_id": None,
        "relationship_context_checkpoint_work_item_id": None,
        "relationship_context_checkpoint_id": None,
        "relationship_direction": None,
        "counterpart_work_item_id": None,
        "metadata_version": 1,
        "metadata": {"nested": {"stable": True}},
        "origin": "live",
        "created_at": created_at,
    }
    deletion_input = {
        "project_id": str(PROJECT_ID),
        "work_item_id": str(WORK_ID),
        "version": 3,
    }
    deletion_response = {"deleted": True, **deletion_input}
    unresolved_gate = {
        "id": str(GATE_ID),
        "project_id": str(PROJECT_ID),
        "work_item_id": str(WORK_ID),
        "gate_type": "human",
        "question": "Choose the durable release boundary.",
        "requested_by_client": "pytest",
        "requested_by_session_id": "phase-7-unit",
        "requested_by_model": "test-model",
        "requested_context_revision": {
            "work_version": 2,
            "context_checkpoint_id": str(CHECKPOINT_ID),
            "relationship_event_count": 0,
        },
        "created_at": created_at,
        "status": "unresolved",
        "current_context_revision": {
            "work_version": 2,
            "context_checkpoint_id": str(CHECKPOINT_ID),
            "relationship_event_count": 0,
        },
        "work_changed_since_request": False,
        "context_checkpoint_changed_since_request": False,
        "relationships_changed_since_request": False,
        "context_changed_since_request": False,
        "resolved_at": None,
        "resolution": None,
        "resolved_by_client": None,
        "resolved_by_session_id": None,
        "resolved_by_model": None,
        "resolved_context_revision": None,
        "context_changed_at_resolution": None,
    }
    resolved_gate = {
        **unresolved_gate,
        "status": "resolved",
        "current_context_revision": {
            "work_version": 3,
            "context_checkpoint_id": str(CHECKPOINT_ID),
            "relationship_event_count": 4,
        },
        "work_changed_since_request": True,
        "relationships_changed_since_request": True,
        "context_changed_since_request": True,
        "resolved_at": updated_at,
        "resolution": "Use the reviewed branch boundary.",
        "resolved_by_client": "dashboard",
        "resolved_by_session_id": "phase-7-human",
        "resolved_by_model": None,
        "resolved_context_revision": {
            "work_version": 3,
            "context_checkpoint_id": str(CHECKPOINT_ID),
            "relationship_event_count": 4,
        },
        "context_changed_at_resolution": True,
    }
    return [
        (
            "create_work",
            {
                "work_item": work(version=1),
                "initial_checkpoint": checkpoint_response(kind="context"),
                "initial_relationships": [],
            },
            {
                "work_item": work(version=1),
                "initial_checkpoint": checkpoint_response(kind="context"),
                "initial_relationships": [],
            },
        ),
        ("add_checkpoint", checkpoint_response(), checkpoint_response()),
        ("append_event", progress_event, progress_event),
        (
            "add_relationship",
            {"relationship": relationship, "created": True},
            {"relationship": relationship, "created": True},
        ),
        ("update_work", work(), work()),
        (
            "defer_work",
            work(status="deferred", version=3),
            work(status="deferred", version=3),
        ),
        (
            "complete_work",
            {
                "work_item": work(status="done", version=3),
                "checkpoint": checkpoint_response(kind="completion"),
            },
            {
                "work_item": work(status="done", version=3),
                "checkpoint": checkpoint_response(kind="completion"),
            },
        ),
        ("delete_work", deletion_input, deletion_response),
        (
            "remove_relationship",
            {
                "project_id": str(PROJECT_ID),
                "relationship_id": str(relationship_id),
                "removed": True,
            },
            {
                "project_id": str(PROJECT_ID),
                "relationship_id": str(relationship_id),
                "removed": True,
            },
        ),
        (
            "release_claim",
            {"work_item_id": str(WORK_ID), "released": False},
            {"work_item_id": str(WORK_ID), "released": False},
        ),
        ("request_human_input", unresolved_gate, unresolved_gate),
        ("resolve_human_input", resolved_gate, resolved_gate),
    ]


RESPONSE_V1_DIGESTS = {
    "create_work": "93cb560f7ffd325519ebb6e458e008b336e283aa265248648bf1bb153eedd4ee",
    "add_checkpoint": "124d8e821e32d5f00ff5eff55fec4d4fecafa71b9bf9479efa24cf3696609a89",
    "append_event": "7c5c8c3113922c11b6b7f505adf9a3efcccf9f6e07eef8b846babdece9c7473d",
    "add_relationship": "353f244332110d5dd32d25ab2c58b05798e9ccbbbe4fed7adb44c373b08cc4b5",
    "update_work": "8cf62c7bbf17f7dac076f467c53dd59abdc273196ab999607488fcb53dc726da",
    "defer_work": "9759d6a3da79bfde4b41c21cf413039ce808122d09e6ade99446cd40dad58280",
    "complete_work": "623f9200fb93c69396ccbd971c1050cd81e79fdde6718d19c591962b6713f276",
    "delete_work": "5a15f8bd7a23ac3b5a0545914e60a6e3e2f3306327fb28e1386074292690a5e9",
    "remove_relationship": "e71f2ae31da622edb038d3ea5e83da22fd88397c63e463de420a60aa60a8e7d4",
    "release_claim": "a12ffef2c559e02d33d223cafd7f0fea6456f55a53ed21b3ae04abe39eb674f2",
    "request_human_input": "41185134969745dd81cf4b6b97c29843bbf2f5649ed09bc078e73ab30f8e96be",
    "resolve_human_input": "ffef30d660f61f41ec9beb75031afd2b95ef716d8e113c89f72942fc7525fb55",
}


@pytest.mark.parametrize(
    ("kind", "target", "payload", "expected_request"),
    canonical_vector_cases(),
    ids=REGISTERED_OPERATION_KINDS,
)
def test_every_registered_operation_has_a_frozen_canonical_and_digest_vector(
    kind, target, payload, expected_request
):
    prepared = prepare_client_operation(kind, PROJECT_ID, target, payload)
    expected_envelope = {
        "api_contract": "mnemonic-api-v1",
        "operation_kind": kind,
        "project_id": str(PROJECT_ID),
        "target": {name: str(value) for name, value in target.items()},
        "request": expected_request,
    }
    expected_bytes = json.dumps(
        expected_envelope,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert prepared.canonical_bytes == expected_bytes
    assert str(OPERATION_ID).encode() not in expected_bytes
    assert request_fingerprint(bytes(range(32)), expected_bytes).hex() == CANONICAL_DIGESTS[kind]


@pytest.mark.parametrize(
    ("kind", "source", "expected_body"),
    response_vector_cases(),
    ids=REGISTERED_OPERATION_KINDS,
)
def test_every_registered_operation_has_a_frozen_response_v1_vector(
    kind, source, expected_body
):
    spec = operation_spec(kind)

    typed, body, response = _render_registered_response(spec, source)
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert spec.response_contract_version == 1
    assert typed.model_dump(mode="json") == expected_body
    assert body == expected_body
    assert response.status_code == spec.status_code
    assert json.loads(response.body) == expected_body
    assert hashlib.sha256(canonical).hexdigest() == RESPONSE_V1_DIGESTS[kind]


def test_gate_response_replay_regenerates_computed_fields_and_refuses_tampering():
    spec = operation_spec("request_human_input")
    source = dict(response_vector_cases()[-2][1])
    _, canonical, _ = _render_registered_response(spec, source)

    typed, replayed, _ = _render_registered_response(
        spec,
        canonical,
        stored_snapshot=True,
    )

    assert typed.context_changed_since_request is False
    assert replayed == canonical

    tampered = {**canonical, "context_changed_since_request": True}
    with pytest.raises(ApplicationError) as captured:
        _render_registered_response(spec, tampered, stored_snapshot=True)
    assert captured.value.detail["code"] == "client_operation_unavailable"

    unknown = {**canonical, "unknown_projection": False}
    with pytest.raises(ApplicationError) as captured:
        _render_registered_response(spec, unknown, stored_snapshot=True)
    assert captured.value.detail["code"] == "client_operation_unavailable"


def test_registry_is_closed_and_non_capability_bearing():
    assert tuple(OPERATION_REGISTRY) == REGISTERED_OPERATION_KINDS
    assert len(OPERATION_REGISTRY) == 12
    assert {
        spec.request_model.__name__ for spec in OPERATION_REGISTRY.values()
    } == {
        "WorkItemCreate",
        "CheckpointCreate",
        "ProgressEventCreate",
        "RelationshipCreate",
        "WorkItemPatch",
        "WorkDeferralCreate",
        "WorkCompletionCreate",
        "WorkDeletionCreate",
        "RelationshipRemovalCreate",
        "LeaseReleaseCreate",
        "HumanGateRequestCreate",
        "HumanGateResolutionCreate",
    }
    for kind, spec in OPERATION_REGISTRY.items():
        assert spec.kind == kind
        assert "client_operation_id" in spec.request_model.model_fields
        assert "client_operation_id" not in spec.response_model.model_fields
        assert spec.response_is_non_capability_bearing is True
        schema_text = json.dumps(spec.response_model.model_json_schema()).casefold()
        assert '"lease_token"' not in schema_text
        assert '"claim_request_id"' not in schema_text


def test_exactly_covered_request_models_accept_the_optional_uuid():
    operation_id = uuid4()
    covered = [
        CheckpointCreate(**checkpoint(), client_operation_id=operation_id),
        work_payload(client_operation_id=operation_id),
        ProgressEventCreate(
            body="Implemented the receipt.",
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        RelationshipCreate(
            relationship_type="blocks",
            source_work_item_id=WORK_ID,
            target_work_item_id=OTHER_WORK_ID,
            created_by_client="pytest",
            created_by_session_id="phase-6-unit",
            client_operation_id=operation_id,
        ),
        WorkItemPatch(
            expected_version=1,
            title="Updated",
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        WorkDeferralCreate(
            expected_version=1,
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        WorkCompletionCreate(
            expected_version=1,
            checkpoint=checkpoint(),
            client_operation_id=operation_id,
        ),
        WorkDeletionCreate(
            expected_version=1,
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        RelationshipRemovalCreate(
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        LeaseReleaseCreate(
            lease_token="capability",
            actor=MutationActor(**actor()),
            client_operation_id=operation_id,
        ),
        HumanGateRequestCreate(
            question="Choose a boundary.",
            requested_by_client="pytest",
            requested_by_session_id="phase-7-unit",
            client_operation_id=operation_id,
        ),
        HumanGateResolutionCreate(
            resolution="Use the reviewed boundary.",
            resolved_by_client="dashboard",
            resolved_by_session_id="phase-7-human",
            reviewed_context_revision={
                "work_version": 1,
                "context_checkpoint_id": CHECKPOINT_ID,
                "relationship_event_count": 0,
            },
            client_operation_id=operation_id,
        ),
    ]
    assert all(item.client_operation_id == operation_id for item in covered)
    assert all(str(operation_id) not in repr(item) for item in covered)

    for excluded_model in (ProjectCreate, ProjectPatch, WorkClaimCreate, LeaseTokenCreate):
        assert "client_operation_id" not in excluded_model.model_fields


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (
            WorkItemPatch,
            {"expected_version": 1, "title": "Updated"},
        ),
        (
            WorkDeletionCreate,
            {"expected_version": 1},
        ),
        (
            WorkDeferralCreate,
            {"expected_version": 1},
        ),
        (
            RelationshipRemovalCreate,
            {},
        ),
        (
            LeaseReleaseCreate,
            {"lease_token": "capability"},
        ),
    ],
)
def test_actor_is_conditionally_required_for_keyed_requests(model, payload):
    assert model.model_validate(payload).actor is None
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "client_operation_id": OPERATION_ID})
    parsed = model.model_validate(
        {
            **payload,
            "actor": actor(),
            "client_operation_id": OPERATION_ID,
        }
    )
    assert parsed.actor == MutationActor(**actor())


def test_operation_control_field_is_not_a_patch_edit():
    with pytest.raises(ValidationError):
        WorkItemPatch(
            expected_version=1,
            actor=MutationActor(**actor()),
            client_operation_id=OPERATION_ID,
        )


@pytest.mark.parametrize(
    "metadata",
    [
        {"client_operation_id": "not-history"},
        {"Client_Operation_ID": "not-history"},
        {"nested": [{"CLIENT_OPERATION_ID": "not-history"}]},
    ],
)
def test_progress_request_reserves_operation_key_without_reinterpreting_history(metadata):
    with pytest.raises(ValidationError):
        ProgressEventCreate(
            body="Progress.",
            metadata=metadata,
            actor=MutationActor(**actor()),
        )
    assert TypeAdapter(EventMetadata).validate_python(metadata) == metadata


def test_canonicalization_expands_defaults_and_normalizes_domain_equivalence():
    omitted = work_payload(
        initial_relationships=[
            {
                "type": "blocks",
                "direction": "outgoing",
                "other_work_item_id": OTHER_WORK_ID,
            },
            {
                "type": "related",
                "direction": "incoming",
                "other_work_item_id": WORK_ID,
            },
        ]
    )
    explicit = work_payload(
        priority=0,
        status="pending",
        initial_relationships=list(reversed(omitted.initial_relationships)),
    )
    first = prepare_client_operation("create_work", PROJECT_ID, {}, omitted)
    second = prepare_client_operation("create_work", PROJECT_ID, {}, explicit)
    assert first.canonical_bytes == second.canonical_bytes
    assert first.canonical_bytes is not None
    canonical_text = first.canonical_bytes.decode()
    assert str(OPERATION_ID) not in canonical_text
    assert "\\r\\n" in canonical_text

    low, high = sorted((WORK_ID, OTHER_WORK_ID))
    forward = RelationshipCreate(
        relationship_type="related",
        source_work_item_id=low,
        target_work_item_id=high,
        created_by_client="pytest",
        created_by_session_id="phase-6-unit",
        client_operation_id=OPERATION_ID,
    )
    reverse = RelationshipCreate(
        relationship_type="related",
        source_work_item_id=high,
        target_work_item_id=low,
        created_by_client="pytest",
        created_by_session_id="phase-6-unit",
        client_operation_id=uuid4(),
    )
    assert prepare_client_operation(
        "add_relationship", PROJECT_ID, {}, forward
    ).canonical_bytes == prepare_client_operation(
        "add_relationship", PROJECT_ID, {}, reverse
    ).canonical_bytes

    incoming_initial = work_payload(
        initial_relationships=[
            {
                "type": "related",
                "direction": "incoming",
                "other_work_item_id": OTHER_WORK_ID,
            }
        ]
    )
    outgoing_initial = work_payload(
        initial_relationships=[
            {
                "type": "related",
                "direction": "outgoing",
                "other_work_item_id": OTHER_WORK_ID,
            }
        ],
        client_operation_id=uuid4(),
    )
    assert prepare_client_operation(
        "create_work", PROJECT_ID, {}, incoming_initial
    ).canonical_bytes == prepare_client_operation(
        "create_work", PROJECT_ID, {}, outgoing_initial
    ).canonical_bytes



def test_canonical_fingerprint_vector_and_domain_projection():
    payload = WorkItemPatch(
        expected_version=7,
        title="Keep exact Unicode café",
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    prepared = prepare_client_operation(
        "update_work",
        PROJECT_ID,
        {"work_item_id": WORK_ID},
        payload,
    )
    assert prepared.canonical_bytes == canonical_request_bytes(
        operation_spec("update_work"),
        PROJECT_ID,
        {"work_item_id": str(WORK_ID)},
        payload,
    )
    assert prepared.domain_payload.client_operation_id is None
    assert "client_operation_id" not in prepared.domain_payload.model_fields_set
    assert prepared.domain_payload.model_fields_set == {
        "expected_version",
        "title",
        "actor",
    }
    assert request_fingerprint(bytes(range(32)), prepared.canonical_bytes).hex() == (
        "fc6a5b89b1c05b7962957faba7f3f61893a8bd9312ed7ef3ef6cf1ebe7b53525"
    )


def test_secret_echo_guard_is_exact_and_does_not_reject_designated_fields():
    lease_token = "lease-capability-value"
    base = ProgressEventCreate(
        body="Ordinary progress.",
        actor=MutationActor(**actor()),
        lease_token=lease_token,
        client_operation_id=OPERATION_ID,
    )
    reject_client_operation_secret_echo(base, known_secret_values=["b" * 32])

    for changes, known in [
        ({"body": str(OPERATION_ID)}, []),
        ({"body": lease_token}, []),
        ({"metadata": {"nested": "b" * 32}}, ["b" * 32]),
    ]:
        payload = base.model_copy(update=changes)
        with pytest.raises(ApplicationError) as captured:
            reject_client_operation_secret_echo(payload, known_secret_values=known)
        assert captured.value.status_code == 422
        assert captured.value.detail["code"] == "client_operation_secret_echo"
        assert captured.value.detail["context"] == {}

    substring = base.model_copy(update={"body": f"prefix-{OPERATION_ID}-suffix"})
    reject_client_operation_secret_echo(substring, known_secret_values=["b" * 32])


@pytest.mark.parametrize("spelling", OPERATION_ID_SPELLINGS)
@pytest.mark.parametrize(
    "location",
    [
        "checkpoint_prompt",
        "checkpoint_source",
        "event_body",
        "event_actor",
        "metadata_key",
        "metadata_value",
    ],
)
def test_operation_uuid_equivalents_cannot_cross_into_persisted_fields(
    spelling: str,
    location: str,
):
    if location.startswith("checkpoint"):
        values = checkpoint()
        values[
            "prompt" if location == "checkpoint_prompt" else "source_client"
        ] = spelling
        payload = CheckpointCreate(
            **values,
            client_operation_id=spelling,
        )
    else:
        event_actor = actor()
        body = "Ordinary progress."
        metadata = {}
        if location == "event_body":
            body = spelling
        elif location == "event_actor":
            event_actor["actor_client"] = spelling
        elif location == "metadata_key":
            metadata = {spelling: "ordinary"}
        else:
            metadata = {"nested": [spelling]}
        payload = ProgressEventCreate(
            body=body,
            actor=MutationActor(**event_actor),
            metadata=metadata,
            client_operation_id=spelling,
        )

    with pytest.raises(ApplicationError) as captured:
        reject_client_operation_secret_echo(payload)

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert captured.value.detail["context"] == {}


@pytest.mark.parametrize("spelling", OPERATION_ID_SPELLINGS)
def test_operation_uuid_equivalent_is_allowed_only_in_designated_field(
    spelling: str,
):
    payload = ProgressEventCreate(
        body="Ordinary progress.",
        actor=MutationActor(**actor()),
        client_operation_id=spelling,
    )
    reject_client_operation_secret_echo(payload)


@pytest.mark.parametrize(
    "forbidden_value",
    [
        "b" * 32,
        "lease-capability-value",
        str(OPERATION_ID),
        *OPERATION_ID_SPELLINGS,
    ],
)
def test_registered_response_rejects_exact_request_known_values(
    forbidden_value: str,
):
    payload = WorkItemPatch(
        expected_version=1,
        priority=2,
        lease_token="lease-capability-value",
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    prepared = prepare_client_operation(
        "update_work",
        PROJECT_ID,
        {"work_item_id": WORK_ID},
        payload,
        known_secret_values=["b" * 32],
    )
    operation = ReservedOperation(
        spec=prepared.spec,
        receipt_id=1,
        client_operation_id=OPERATION_ID,
        project_id=prepared.project_id,
        target_envelope=prepared.target_envelope,
        domain_payload=prepared.domain_payload,
        forbidden_response_values=prepared.forbidden_response_values,
    )
    database = Mock(spec=Session)

    with pytest.raises(ApplicationError) as captured:
        complete_client_operation(
            database,
            operation,
            {
                "id": WORK_ID,
                "project_id": PROJECT_ID,
                "title": forbidden_value,
                "summary": "Existing response content must not copy request-known control data.",
                "status": "pending",
                "priority": 2,
                "initial_checkpoint_id": CHECKPOINT_ID,
                "version": 2,
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-01T00:00:01Z",
            },
            mutation_applied=True,
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert captured.value.detail["context"] == {}
    database.rollback.assert_called_once_with()
    database.execute.assert_not_called()


def test_response_coherence_rejects_impossible_live_creation_snapshots():
    created_at = "2026-09-01T00:00:00Z"
    initial = checkpoint()
    create_payload = work_payload()
    create_spec = operation_spec("create_work")
    poisoned_creation = create_spec.response_model.model_validate(
        {
            "work_item": {
                "id": WORK_ID,
                "project_id": PROJECT_ID,
                "title": create_payload.title,
                "summary": create_payload.summary,
                "status": create_payload.status,
                "priority": create_payload.priority,
                "initial_checkpoint_id": CHECKPOINT_ID,
                "version": 2,
                "created_at": created_at,
                "updated_at": created_at,
            },
            "initial_checkpoint": {
                "id": CHECKPOINT_ID,
                "work_item_id": WORK_ID,
                "kind": "context",
                **initial,
                "source_session_url": None,
                "repository_branch": None,
                "verified_against": None,
                "tags": ["idempotency", "backend"],
                "migration_origin": None,
                "legacy_record_id": None,
                "created_at": created_at,
            },
            "initial_relationships": [],
        }
    )
    assert not _response_matches_operation(
        create_spec,
        PROJECT_ID,
        {},
        create_payload,
        poisoned_creation,
        True,
    )

    checkpoint_payload = CheckpointCreate(
        **initial,
        kind="progress",
        client_operation_id=OPERATION_ID,
    )
    checkpoint_spec = operation_spec("add_checkpoint")
    poisoned_checkpoint = checkpoint_spec.response_model.model_validate(
        {
            "id": CHECKPOINT_ID,
            "work_item_id": WORK_ID,
            "kind": "progress",
            **initial,
            "source_session_url": None,
            "repository_branch": None,
            "verified_against": None,
            "tags": ["idempotency", "backend"],
            "migration_origin": "legacy-comment",
            "legacy_record_id": uuid4(),
            "created_at": created_at,
        }
    )
    assert not _response_matches_operation(
        checkpoint_spec,
        PROJECT_ID,
        {"work_item_id": str(WORK_ID)},
        checkpoint_payload,
        poisoned_checkpoint,
        True,
    )

    relationship_payload = RelationshipCreate(
        relationship_type="blocks",
        source_work_item_id=WORK_ID,
        target_work_item_id=OTHER_WORK_ID,
        created_by_client="pytest",
        created_by_session_id="phase-6-unit",
        created_by_model="test-model",
        context_checkpoint_id=CHECKPOINT_ID,
        client_operation_id=OPERATION_ID,
    )
    relationship_spec = operation_spec("add_relationship")
    poisoned_relationship = relationship_spec.response_model.model_validate(
        {
            "relationship": {
                "id": uuid4(),
                "project_id": PROJECT_ID,
                "relationship_type": "blocks",
                "source_work_item_id": WORK_ID,
                "target_work_item_id": OTHER_WORK_ID,
                "context_checkpoint_work_item_id": WORK_ID,
                "context_checkpoint_id": uuid4(),
                "created_by_client": "pytest",
                "created_by_session_id": "wrong-session",
                "created_by_model": "wrong-model",
                "created_at": created_at,
            },
            "created": True,
        }
    )
    assert not _response_matches_operation(
        relationship_spec,
        PROJECT_ID,
        {},
        relationship_payload,
        poisoned_relationship,
        True,
    )


def test_defer_response_coherence_requires_the_new_deferred_version():
    request = WorkDeferralCreate(
        expected_version=4,
        actor=MutationActor(**actor()),
        client_operation_id=OPERATION_ID,
    )
    spec = operation_spec("defer_work")
    base_response = {
        "id": WORK_ID,
        "project_id": PROJECT_ID,
        "title": "Deferred response",
        "summary": "The stored response must represent the deferral.",
        "status": "deferred",
        "priority": 4,
        "initial_checkpoint_id": CHECKPOINT_ID,
        "version": 5,
        "created_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:01Z",
    }
    coherent = spec.response_model.model_validate(base_response)
    wrong_status = spec.response_model.model_validate(
        {**base_response, "status": "pending"}
    )
    wrong_version = spec.response_model.model_validate(
        {**base_response, "version": 6}
    )

    assert _response_matches_operation(
        spec,
        PROJECT_ID,
        {"work_item_id": str(WORK_ID)},
        request,
        coherent,
        True,
    )
    for impossible in (wrong_status, wrong_version):
        assert not _response_matches_operation(
            spec,
            PROJECT_ID,
            {"work_item_id": str(WORK_ID)},
            request,
            impossible,
            True,
        )


@pytest.mark.parametrize(
    "reserved_key",
    [
        "LeAsE_ToKeN",
        "CLAIM_REQUEST_ID",
        "Client_Operation_Id",
        "Authorization",
        "API_KEY",
        "Cookie",
        "SeCrEt",
    ],
)
def test_keyed_checkpoint_metadata_rejects_case_varied_reserved_names(
    reserved_key: str,
):
    payload = CheckpointCreate(
        **{
            **checkpoint(),
            "source_metadata": {"outer": [{reserved_key: "not-a-known-value"}]},
        },
        kind="progress",
        client_operation_id=OPERATION_ID,
    )

    with pytest.raises(ApplicationError) as captured:
        prepare_client_operation(
            "add_checkpoint",
            PROJECT_ID,
            {"work_item_id": WORK_ID},
            payload,
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == "client_operation_secret_echo"
    assert captured.value.detail["context"] == {}


def test_unprotected_completion_renders_without_touching_the_database():
    payload = WorkDeletionCreate(expected_version=1)
    prepared = prepare_client_operation(
        "delete_work",
        PROJECT_ID,
        {"work_item_id": WORK_ID},
        payload,
    )
    database = Mock(spec=Session)
    reserved = reserve_client_operation(database, prepared, wait_seconds=10)
    assert isinstance(reserved, UnprotectedOperation)
    completed = complete_client_operation(
        database,
        reserved,
        {
            "project_id": PROJECT_ID,
            "work_item_id": WORK_ID,
            "version": 2,
        },
        mutation_applied=True,
    )
    assert isinstance(completed, CompletedOperation)
    assert completed.classification == "unprotected"
    assert completed.status == 200
    assert json.loads(completed.response.body) == {
        "deleted": True,
        "project_id": str(PROJECT_ID),
        "work_item_id": str(WORK_ID),
        "version": 2,
    }
    database.assert_not_called()


def test_wait_setting_is_bounded_and_engine_declares_read_committed():
    settings = Settings(database_url="postgresql://localhost/example", api_key="x" * 32)
    assert settings.client_operation_wait_seconds == 10
    for invalid in (0, 11):
        with pytest.raises(ValidationError):
            Settings(
                database_url="postgresql://localhost/example",
                api_key="x" * 32,
                client_operation_wait_seconds=invalid,
            )
    engine = build_engine(settings)
    try:
        assert engine.dialect._on_connect_isolation_level == "READ COMMITTED"
        assert engine.pool.timeout() == settings.client_operation_wait_seconds
    finally:
        engine.dispose()
