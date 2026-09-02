"""Closed, fail-safe persistence contract for idempotent client mutations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import UUID

from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from mnemonic_api.database import database_sqlstate
from mnemonic_api.errors import (
    client_operation_conflict,
    client_operation_secret_echo,
    client_operation_unavailable,
)
from mnemonic_api.models import ClientOperation
from mnemonic_api.schemas import (
    APIModel,
    CheckpointCreate,
    CheckpointRead,
    HumanGateRead,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    InitialRelationshipCreate,
    LeaseReleaseCreate,
    ProgressEventCreate,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipRemovalCreate,
    RelationshipRemovalResult,
    ReleaseResult,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkCreation,
    WorkDeferralCreate,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkEventRead,
    WorkItemCreate,
    WorkItemPatch,
    WorkItemRead,
)

type OperationKind = Literal[
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
]
REGISTERED_OPERATION_KINDS: tuple[OperationKind, ...] = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
)

REQUEST_FINGERPRINT_VERSION = 1
RESPONSE_CONTRACT_VERSION = 1
FINGERPRINT_SALT_BYTES = 32
FINGERPRINT_BYTES = 32
MAX_RESPONSE_BYTES = 1024 * 1024
FINGERPRINT_DOMAIN_SEPARATOR = b"mnemonic-client-operation-v1"
FORBIDDEN_RESPONSE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "claim_request_id",
        "client_operation_id",
        "cookie",
        "lease_token",
        "secret",
    }
)


@dataclass(frozen=True)
class OperationSpec:
    kind: OperationKind
    request_model: type[APIModel]
    response_model: type[APIModel]
    status_code: int
    target_fields: tuple[str, ...]
    mutation_applied_field: Literal["created", "removed", "released"] | None = None
    request_fingerprint_version: int = REQUEST_FINGERPRINT_VERSION
    response_contract_version: int = RESPONSE_CONTRACT_VERSION
    response_is_non_capability_bearing: bool = True


def _schema_contains_forbidden_response_field(model: type[BaseModel]) -> bool:
    def visit(value: object) -> bool:
        if isinstance(value, list):
            return any(visit(item) for item in value)
        if not isinstance(value, dict):
            return False
        properties = value.get("properties")
        if isinstance(properties, dict) and any(
            str(name).casefold() in FORBIDDEN_RESPONSE_FIELD_NAMES for name in properties
        ):
            return True
        return any(visit(item) for item in value.values())

    return visit(model.model_json_schema())


def _spec(
    kind: OperationKind,
    request_model: type[APIModel],
    response_model: type[APIModel],
    status_code: int,
    *target_fields: str,
    mutation_applied_field: Literal["created", "removed", "released"] | None = None,
) -> OperationSpec:
    if "client_operation_id" not in request_model.model_fields:
        raise RuntimeError(f"{kind} request is missing its client operation field")
    if _schema_contains_forbidden_response_field(response_model):
        raise RuntimeError(f"{kind} response contains capability or control data")
    if (
        mutation_applied_field is not None
        and mutation_applied_field not in response_model.model_fields
    ):
        raise RuntimeError(f"{kind} response is missing its mutation outcome field")
    return OperationSpec(
        kind=kind,
        request_model=request_model,
        response_model=response_model,
        status_code=status_code,
        target_fields=tuple(target_fields),
        mutation_applied_field=mutation_applied_field,
    )


_REGISTRY: dict[OperationKind, OperationSpec] = {
    "create_work": _spec("create_work", WorkItemCreate, WorkCreation, 201),
    "add_checkpoint": _spec(
        "add_checkpoint", CheckpointCreate, CheckpointRead, 201, "work_item_id"
    ),
    "append_event": _spec(
        "append_event", ProgressEventCreate, WorkEventRead, 201, "work_item_id"
    ),
    "add_relationship": _spec(
        "add_relationship",
        RelationshipCreate,
        RelationshipCreationResult,
        200,
        mutation_applied_field="created",
    ),
    "update_work": _spec("update_work", WorkItemPatch, WorkItemRead, 200, "work_item_id"),
    "defer_work": _spec(
        "defer_work", WorkDeferralCreate, WorkItemRead, 200, "work_item_id"
    ),
    "complete_work": _spec(
        "complete_work",
        WorkCompletionCreate,
        WorkCompletionRead,
        200,
        "work_item_id",
    ),
    "delete_work": _spec(
        "delete_work", WorkDeletionCreate, WorkDeletionRead, 200, "work_item_id"
    ),
    "remove_relationship": _spec(
        "remove_relationship",
        RelationshipRemovalCreate,
        RelationshipRemovalResult,
        200,
        "relationship_id",
        mutation_applied_field="removed",
    ),
    "release_claim": _spec(
        "release_claim",
        LeaseReleaseCreate,
        ReleaseResult,
        200,
        "work_item_id",
        mutation_applied_field="released",
    ),
    "request_human_input": _spec(
        "request_human_input",
        HumanGateRequestCreate,
        HumanGateRead,
        201,
        "work_item_id",
    ),
    "resolve_human_input": _spec(
        "resolve_human_input",
        HumanGateResolutionCreate,
        HumanGateRead,
        200,
        "work_item_id",
        "gate_id",
    ),
}
OPERATION_REGISTRY: Mapping[OperationKind, OperationSpec] = MappingProxyType(_REGISTRY)


@dataclass(frozen=True, repr=False)
class OperationIdentity:
    project_id: UUID
    client_operation_id: UUID


@dataclass(frozen=True, repr=False)
class PreparedOperation:
    spec: OperationSpec
    project_id: UUID
    identity: OperationIdentity | None
    target_envelope: Mapping[str, str] = field(repr=False)
    validated_wire_payload: APIModel = field(repr=False)
    domain_payload: APIModel = field(repr=False)
    canonical_bytes: bytes | None = field(repr=False)
    forbidden_response_values: frozenset[str] = field(repr=False)


@dataclass(frozen=True, repr=False)
class UnprotectedOperation:
    spec: OperationSpec
    project_id: UUID
    target_envelope: Mapping[str, str] = field(repr=False)
    domain_payload: APIModel = field(repr=False)
    forbidden_response_values: frozenset[str] = field(repr=False)
    classification: Literal["unprotected"] = field(default="unprotected", init=False)


@dataclass(frozen=True, repr=False)
class ReservedOperation:
    spec: OperationSpec
    receipt_id: int = field(repr=False)
    client_operation_id: UUID = field(repr=False)
    project_id: UUID
    target_envelope: Mapping[str, str] = field(repr=False)
    domain_payload: APIModel = field(repr=False)
    forbidden_response_values: frozenset[str] = field(repr=False)
    classification: Literal["reserved"] = field(default="reserved", init=False)


@dataclass(frozen=True, repr=False)
class ReplayedOperation:
    spec: OperationSpec
    status: int
    typed_body: APIModel = field(repr=False)
    response: JSONResponse = field(repr=False)
    mutation_applied: bool
    classification: Literal["replayed"] = field(default="replayed", init=False)


@dataclass(frozen=True, repr=False)
class CompletedOperation:
    spec: OperationSpec
    status: int
    typed_body: APIModel = field(repr=False)
    response: JSONResponse = field(repr=False)
    mutation_applied: bool
    classification: Literal["executed", "unprotected"]


type ReservationOutcome = UnprotectedOperation | ReservedOperation | ReplayedOperation
type CompletableOperation = UnprotectedOperation | ReservedOperation


def operation_spec(kind: OperationKind) -> OperationSpec:
    try:
        return OPERATION_REGISTRY[kind]
    except KeyError:
        raise client_operation_unavailable() from None


def _canonical_target(
    spec: OperationSpec,
    target_envelope: Mapping[str, UUID],
) -> dict[str, str]:
    if set(target_envelope) != set(spec.target_fields):
        raise client_operation_unavailable()
    if any(not isinstance(value, UUID) for value in target_envelope.values()):
        raise client_operation_unavailable()
    return {name: str(target_envelope[name]) for name in spec.target_fields}


def _canonical_payload(spec: OperationSpec, payload: APIModel) -> dict[str, Any]:
    dumped = payload.model_dump(
        mode="json",
        exclude={"client_operation_id"},
        exclude_defaults=False,
        exclude_none=False,
        exclude_unset=False,
    )
    if spec.kind == "create_work":
        relationships = cast(list[dict[str, Any]], dumped["initial_relationships"])
        for relationship in relationships:
            if relationship["type"] == "related":
                # Direction only identifies the new endpoint as source/target.
                # `related` is stored with normalized undirected endpoints, so
                # incoming and outgoing describe the same domain mutation.
                relationship["direction"] = "outgoing"
        dumped["initial_relationships"] = sorted(
            relationships,
            key=lambda item: (
                item["type"],
                item["direction"],
                item["other_work_item_id"],
                item.get("context_checkpoint_id") or "",
            ),
        )
    elif spec.kind == "add_relationship" and dumped["relationship_type"] == "related":
        source = dumped["source_work_item_id"]
        target = dumped["target_work_item_id"]
        if target < source:
            dumped["source_work_item_id"] = target
            dumped["target_work_item_id"] = source
    return dumped


def canonical_request_bytes(
    spec: OperationSpec,
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
) -> bytes:
    envelope = {
        "api_contract": "mnemonic-api-v1",
        "operation_kind": spec.kind,
        "project_id": str(project_id),
        "target": dict(target_envelope),
        "request": _canonical_payload(spec, payload),
    }
    try:
        return json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise client_operation_unavailable() from None


def request_fingerprint(salt: bytes, canonical_bytes: bytes) -> bytes:
    if len(salt) != FINGERPRINT_SALT_BYTES:
        raise ValueError("Client-operation fingerprint salt must be exactly 32 bytes")
    return hashlib.sha256(
        FINGERPRINT_DOMAIN_SEPARATOR + b"\x00" + salt + canonical_bytes
    ).digest()


def prepare_client_operation(
    kind: OperationKind,
    project_id: UUID,
    target_envelope: Mapping[str, UUID],
    payload: APIModel,
    *,
    known_secret_values: Iterable[str] = (),
) -> PreparedOperation:
    spec = operation_spec(kind)
    if not isinstance(payload, spec.request_model):
        raise client_operation_unavailable()
    forbidden_response_values = reject_client_operation_secret_echo(
        payload, known_secret_values=known_secret_values
    )
    canonical_target = _canonical_target(spec, target_envelope)
    operation_id = getattr(payload, "client_operation_id", None)
    if operation_id is not None and not isinstance(operation_id, UUID):
        raise client_operation_unavailable()

    # Revalidate only explicitly supplied domain fields. This prevents a control
    # field from crossing the service boundary while preserving patch field-set
    # semantics and the validated/defaulted values of every request class.
    domain_payload = spec.request_model.model_validate(
        payload.model_dump(exclude={"client_operation_id"}, exclude_unset=True)
    )
    identity = (
        OperationIdentity(project_id=project_id, client_operation_id=operation_id)
        if operation_id is not None
        else None
    )
    canonical = (
        canonical_request_bytes(spec, project_id, canonical_target, payload)
        if identity is not None
        else None
    )
    return PreparedOperation(
        spec=spec,
        project_id=project_id,
        identity=identity,
        target_envelope=MappingProxyType(canonical_target),
        validated_wire_payload=payload,
        domain_payload=domain_payload,
        canonical_bytes=canonical,
        forbidden_response_values=forbidden_response_values,
    )


def reject_client_operation_secret_echo(
    payload: APIModel,
    *,
    known_secret_values: Iterable[str] = (),
) -> frozenset[str]:
    """Reject exact control/capability echoes outside their designated fields."""
    operation_id = getattr(payload, "client_operation_id", None)
    if operation_id is None:
        return frozenset()
    operation_value = str(operation_id)
    lease_value = getattr(payload, "lease_token", None)
    external_values = {value for value in known_secret_values if value}
    known_values = {*external_values, operation_value}
    if operation_value in external_values:
        raise client_operation_secret_echo()
    if isinstance(lease_value, str):
        known_values.add(lease_value)
        if operation_value == lease_value or lease_value in external_values:
            raise client_operation_secret_echo()

    dumped = payload.model_dump(mode="json")

    def is_operation_id(value: object) -> bool:
        if isinstance(value, UUID):
            return value == operation_id
        if not isinstance(value, str):
            return False
        try:
            return UUID(value) == operation_id
        except (AttributeError, ValueError):
            return False

    def contains_echo(value: object, path: tuple[str, ...] = ()) -> bool:
        if isinstance(value, list):
            return any(contains_echo(item, path) for item in value)
        if isinstance(value, dict):
            for key, item in value.items():
                child_path = (*path, key)
                if len(child_path) == 1 and key in {
                    "client_operation_id",
                    "lease_token",
                }:
                    continue
                if key in known_values or is_operation_id(key):
                    return True
                if key.casefold() in FORBIDDEN_RESPONSE_FIELD_NAMES:
                    return True
                if contains_echo(item, child_path):
                    return True
            return False
        return is_operation_id(value) or (
            isinstance(value, str) and value in known_values
        )

    if contains_echo(dumped):
        raise client_operation_secret_echo()
    return frozenset(known_values)


def _contains_exact_response_value(
    value: object,
    forbidden_values: frozenset[str],
    operation_id: UUID | None = None,
) -> bool:
    def is_operation_id(candidate: object) -> bool:
        if operation_id is None:
            return False
        if isinstance(candidate, UUID):
            return candidate == operation_id
        if not isinstance(candidate, str):
            return False
        try:
            return UUID(candidate) == operation_id
        except (AttributeError, ValueError):
            return False

    if isinstance(value, list):
        return any(
            _contains_exact_response_value(item, forbidden_values, operation_id)
            for item in value
        )
    if isinstance(value, dict):
        return any(
            key in forbidden_values
            or is_operation_id(key)
            or key.casefold() in FORBIDDEN_RESPONSE_FIELD_NAMES
            or _contains_exact_response_value(item, forbidden_values, operation_id)
            for key, item in value.items()
        )
    return is_operation_id(value) or (
        isinstance(value, str) and value in forbidden_values
    )


def _set_receipt_timeouts(database: Session, wait_milliseconds: int) -> None:
    if isinstance(wait_milliseconds, bool) or not 1 <= wait_milliseconds <= 10_000:
        raise client_operation_unavailable()
    database.execute(text(f"SET LOCAL lock_timeout = '{wait_milliseconds}ms'"))
    database.execute(text(f"SET LOCAL statement_timeout = '{wait_milliseconds}ms'"))


def _restore_receipt_timeouts(database: Session) -> None:
    database.execute(text("SET LOCAL lock_timeout TO DEFAULT"))
    database.execute(text("SET LOCAL statement_timeout TO DEFAULT"))


def _rollback(database: Session) -> None:
    try:
        database.rollback()
    except SQLAlchemyError:
        pass


def _remaining_wait_milliseconds(database: Session, deadline: float) -> int:
    remaining = int((deadline - monotonic()) * 1000)
    if remaining < 1:
        _raise_unavailable(database)
    return min(remaining, 10_000)


def _raise_unavailable(database: Session) -> None:
    _rollback(database)
    raise client_operation_unavailable()


def _render_registered_response(
    spec: OperationSpec,
    value: object,
    *,
    stored_snapshot: bool = False,
) -> tuple[APIModel, dict[str, Any], JSONResponse]:
    try:
        provided_computed: dict[str, Any] = {}
        validation_value = value
        if isinstance(value, dict):
            computed_names = spec.response_model.model_computed_fields.keys()
            provided_computed = {
                key: value[key] for key in computed_names if key in value
            }
            validation_value = {
                key: item for key, item in value.items() if key not in computed_names
            }
        if stored_snapshot:
            if not isinstance(value, dict):
                raise TypeError("Stored response must be a JSON object")
            # Computed response fields are part of the canonical stored snapshot but
            # are deliberately not accepted as model input under extra="forbid".
            # Remove only the model-declared computed fields, then regenerate and
            # compare the complete body below so missing or tampered values fail closed.
            encoded = json.dumps(
                validation_value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            typed = spec.response_model.model_validate_json(encoded, strict=True)
        else:
            typed = spec.response_model.model_validate(validation_value)
        body = typed.model_dump(mode="json")
        if not isinstance(body, dict):
            raise TypeError("Registered response must serialize to a JSON object")
        if any(body.get(key) != item for key, item in provided_computed.items()):
            raise ValueError("Computed response fields are not canonical")
        if stored_snapshot and body != value:
            raise ValueError("Stored response is not the canonical registered JSON")
        response = JSONResponse(status_code=spec.status_code, content=body)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ValueError("Registered response exceeds the client-operation bound")
    except Exception:
        raise client_operation_unavailable() from None
    return typed, body, response


_CHECKPOINT_FIELDS = (
    "prompt",
    "source_client",
    "source_session_id",
    "source_model",
    "source_session_url",
    "repository_branch",
    "verified_against",
    "tags",
    "source_metadata",
)


def _checkpoint_matches_payload(checkpoint: APIModel, payload: APIModel) -> bool:
    return (
        getattr(checkpoint, "migration_origin", None) is None
        and getattr(checkpoint, "legacy_record_id", None) is None
        and all(
            getattr(checkpoint, name, None) == getattr(payload, name, None)
            for name in _CHECKPOINT_FIELDS
        )
    )


def _normalized_relationship_identity(
    relationship_type: str,
    source_work_item_id: UUID,
    target_work_item_id: UUID,
) -> tuple[str, UUID, UUID]:
    if relationship_type == "related" and str(target_work_item_id) < str(source_work_item_id):
        source_work_item_id, target_work_item_id = target_work_item_id, source_work_item_id
    return relationship_type, source_work_item_id, target_work_item_id


def _initial_relationship_order(
    relationship: InitialRelationshipCreate,
) -> tuple[str, str, str, str]:
    return (
        relationship.type,
        "outgoing" if relationship.type == "related" else relationship.direction,
        str(relationship.other_work_item_id),
        str(relationship.context_checkpoint_id or ""),
    )


def _created_relationship_matches_request(
    relationship: RelationshipEdgeRead,
    *,
    created_by_client: str,
    created_by_session_id: str,
    created_by_model: str | None,
    context_checkpoint_id: UUID | None,
) -> bool:
    context_owner = relationship.context_checkpoint_work_item_id
    if (
        relationship.created_by_client != created_by_client
        or relationship.created_by_session_id != created_by_session_id
        or relationship.created_by_model != created_by_model
        or relationship.context_checkpoint_id != context_checkpoint_id
        or (context_checkpoint_id is None) != (context_owner is None)
    ):
        return False
    if context_owner is None:
        return True
    if context_owner not in {
        relationship.source_work_item_id,
        relationship.target_work_item_id,
    }:
        return False
    return (
        relationship.relationship_type != "discovered-from"
        or context_owner == relationship.target_work_item_id
    )


def _response_matches_operation(
    spec: OperationSpec,
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
    mutation_applied: bool,
) -> bool:
    expected_applied = (
        True
        if spec.mutation_applied_field is None
        else getattr(typed, spec.mutation_applied_field, None)
    )
    if not isinstance(expected_applied, bool) or mutation_applied is not expected_applied:
        return False

    work_item_id = target_envelope.get("work_item_id")
    relationship_id = target_envelope.get("relationship_id")
    if spec.kind == "create_work":
        result = cast(WorkCreation, typed)
        request = cast(WorkItemCreate, payload)
        work = result.work_item
        if (
            work.project_id != project_id
            or work.version != 1
            or work.title != request.title
            or work.summary != request.summary
            or work.priority != request.priority
            or work.status != request.status
            or result.initial_checkpoint.work_item_id != work.id
            or result.initial_checkpoint.id != work.initial_checkpoint_id
            or result.initial_checkpoint.kind != "context"
            or not _checkpoint_matches_payload(
                result.initial_checkpoint,
                request.initial_checkpoint,
            )
        ):
            return False
        expected_relationships: dict[
            tuple[str, UUID, UUID],
            InitialRelationshipCreate,
        ] = {}
        for relationship in sorted(
            request.initial_relationships,
            key=_initial_relationship_order,
        ):
            if relationship.direction == "outgoing":
                source, target = work.id, relationship.other_work_item_id
            else:
                source, target = relationship.other_work_item_id, work.id
            expected_relationships.setdefault(
                _normalized_relationship_identity(relationship.type, source, target),
                relationship,
            )
        actual_relationships: set[tuple[str, UUID, UUID]] = set()
        for relationship in result.initial_relationships:
            identity = _normalized_relationship_identity(
                relationship.relationship_type,
                relationship.source_work_item_id,
                relationship.target_work_item_id,
            )
            expected = expected_relationships.get(identity)
            if (
                relationship.project_id != project_id
                or work.id
                not in {
                relationship.source_work_item_id,
                relationship.target_work_item_id,
                }
                or expected is None
                or not _created_relationship_matches_request(
                    relationship,
                    created_by_client=request.initial_checkpoint.source_client,
                    created_by_session_id=request.initial_checkpoint.source_session_id,
                    created_by_model=request.initial_checkpoint.source_model,
                    context_checkpoint_id=expected.context_checkpoint_id,
                )
            ):
                return False
            actual_relationships.add(identity)
        return (
            len(actual_relationships) == len(result.initial_relationships)
            and actual_relationships == set(expected_relationships)
        )
    if spec.kind == "add_checkpoint":
        result = cast(CheckpointRead, typed)
        request = cast(CheckpointCreate, payload)
        return (
            str(result.work_item_id) == work_item_id
            and result.kind == request.kind
            and _checkpoint_matches_payload(result, request)
        )
    if spec.kind == "append_event":
        result = cast(WorkEventRead, typed)
        request = cast(ProgressEventCreate, payload)
        return (
            result.project_id == project_id
            and str(result.work_item_id) == work_item_id
            and result.event_type == "progress"
            and result.body == request.body
            and result.model_dump(mode="json")["metadata"] == request.metadata
            and result.actor_client == request.actor.actor_client
            and result.actor_session_id == request.actor.actor_session_id
            and result.actor_model == request.actor.actor_model
            and result.origin == "live"
        )
    if spec.kind == "add_relationship":
        result = cast(RelationshipCreationResult, typed)
        request = cast(RelationshipCreate, payload)
        expected = _normalized_relationship_identity(
            request.relationship_type,
            request.source_work_item_id,
            request.target_work_item_id,
        )
        actual = _normalized_relationship_identity(
            result.relationship.relationship_type,
            result.relationship.source_work_item_id,
            result.relationship.target_work_item_id,
        )
        return (
            result.relationship.project_id == project_id
            and actual == expected
            and (
                not result.created
                or _created_relationship_matches_request(
                    result.relationship,
                    created_by_client=request.created_by_client,
                    created_by_session_id=request.created_by_session_id,
                    created_by_model=request.created_by_model,
                    context_checkpoint_id=request.context_checkpoint_id,
                )
            )
        )
    if spec.kind == "update_work":
        result = cast(WorkItemRead, typed)
        request = cast(WorkItemPatch, payload)
        changed_fields = request.model_fields_set - {
            "expected_version",
            "lease_token",
            "actor",
            "client_operation_id",
        }
        return (
            result.project_id == project_id
            and str(result.id) == work_item_id
            and result.version == request.expected_version + 1
            and all(
                getattr(result, field) == getattr(request, field)
                for field in changed_fields
            )
        )
    if spec.kind == "defer_work":
        result = cast(WorkItemRead, typed)
        request = cast(WorkDeferralCreate, payload)
        return (
            result.project_id == project_id
            and str(result.id) == work_item_id
            and result.version == request.expected_version + 1
            and result.status == "deferred"
        )
    if spec.kind == "complete_work":
        result = cast(WorkCompletionRead, typed)
        request = cast(WorkCompletionCreate, payload)
        return (
            result.work_item.project_id == project_id
            and str(result.work_item.id) == work_item_id
            and result.work_item.version == request.expected_version + 1
            and result.work_item.status == "done"
            and result.checkpoint.work_item_id == result.work_item.id
            and result.checkpoint.kind == "completion"
            and _checkpoint_matches_payload(result.checkpoint, request.checkpoint)
        )
    if spec.kind == "delete_work":
        result = cast(WorkDeletionRead, typed)
        request = cast(WorkDeletionCreate, payload)
        return (
            result.deleted is True
            and result.project_id == project_id
            and str(result.work_item_id) == work_item_id
            and result.version == request.expected_version + 1
        )
    if spec.kind == "remove_relationship":
        result = cast(RelationshipRemovalResult, typed)
        return (
            result.project_id == project_id
            and str(result.relationship_id) == relationship_id
        )
    if spec.kind == "release_claim":
        result = cast(ReleaseResult, typed)
        return str(result.work_item_id) == work_item_id
    if spec.kind == "request_human_input":
        result = cast(HumanGateRead, typed)
        request = cast(HumanGateRequestCreate, payload)
        revision = result.current_context_revision
        return (
            result.project_id == project_id
            and str(result.work_item_id) == work_item_id
            and result.gate_type == request.gate_type
            and result.question == request.question
            and result.requested_by_client == request.requested_by_client
            and result.requested_by_session_id == request.requested_by_session_id
            and result.requested_by_model == request.requested_by_model
            and result.status == "unresolved"
            and revision == result.requested_context_revision
            and not result.work_changed_since_request
            and not result.context_checkpoint_changed_since_request
            and not result.relationships_changed_since_request
            and not result.context_changed_since_request
            and result.resolved_at is None
            and result.resolution is None
        )
    if spec.kind == "resolve_human_input":
        result = cast(HumanGateRead, typed)
        request = cast(HumanGateResolutionCreate, payload)
        resolved_revision = result.resolved_context_revision
        gate_id = target_envelope.get("gate_id")
        if (
            result.project_id != project_id
            or str(result.work_item_id) != work_item_id
            or str(result.id) != gate_id
            or result.status != "resolved"
            or result.resolution != request.resolution
            or result.resolved_by_client != request.resolved_by_client
            or result.resolved_by_session_id != request.resolved_by_session_id
            or result.resolved_by_model != request.resolved_by_model
            or resolved_revision is None
            or result.current_context_revision != resolved_revision
            or request.reviewed_context_revision != resolved_revision
        ):
            return False
        return True
    return False


def reserve_client_operation(
    database: Session,
    prepared: PreparedOperation,
    *,
    wait_seconds: int,
) -> ReservationOutcome:
    if prepared.identity is None:
        return UnprotectedOperation(
            spec=prepared.spec,
            project_id=prepared.project_id,
            target_envelope=prepared.target_envelope,
            domain_payload=prepared.domain_payload,
            forbidden_response_values=prepared.forbidden_response_values,
        )
    canonical = prepared.canonical_bytes
    if canonical is None:
        _raise_unavailable(database)

    salt = secrets.token_bytes(FINGERPRINT_SALT_BYTES)
    fingerprint = request_fingerprint(salt, canonical)
    identity = prepared.identity
    deadline = monotonic() + wait_seconds
    try:
        # Session checkout is lazy. It is part of the same absolute budget as
        # the unique-index conflict wait, not a separate QueuePool allowance.
        database.connection()
        _set_receipt_timeouts(
            database,
            _remaining_wait_milliseconds(database, deadline),
        )
        receipt_id = database.execute(
            postgresql_insert(ClientOperation)
            .values(
                project_id=identity.project_id,
                client_operation_id=identity.client_operation_id,
                operation_kind=prepared.spec.kind,
                request_fingerprint_version=prepared.spec.request_fingerprint_version,
                request_fingerprint_salt=salt,
                request_fingerprint=fingerprint,
                response_contract_version=prepared.spec.response_contract_version,
                state="pending",
            )
            .on_conflict_do_nothing(constraint="uq_client_operations_scope")
            .returning(ClientOperation.id)
        ).scalar_one_or_none()
        if receipt_id is not None:
            _restore_receipt_timeouts(database)
            return ReservedOperation(
                spec=prepared.spec,
                receipt_id=receipt_id,
                client_operation_id=identity.client_operation_id,
                project_id=prepared.project_id,
                target_envelope=prepared.target_envelope,
                domain_payload=prepared.domain_payload,
                forbidden_response_values=prepared.forbidden_response_values,
            )

        # The conflict INSERT may have consumed almost the entire budget. Do
        # not grant the visibility read a fresh full timeout.
        _set_receipt_timeouts(
            database,
            _remaining_wait_milliseconds(database, deadline),
        )
        receipt = database.scalar(
            select(ClientOperation).where(
                ClientOperation.project_id == identity.project_id,
                ClientOperation.client_operation_id == identity.client_operation_id,
            )
        )
        _restore_receipt_timeouts(database)
    except SQLAlchemyTimeoutError:
        _raise_unavailable(database)
    except DBAPIError as exc:
        if database_sqlstate(exc) in {"55P03", "57014"}:
            _raise_unavailable(database)
        raise

    if receipt is None:
        _raise_unavailable(database)
    if receipt.operation_kind != prepared.spec.kind:
        _rollback(database)
        raise client_operation_conflict()
    if receipt.request_fingerprint_version != prepared.spec.request_fingerprint_version:
        _rollback(database)
        raise client_operation_conflict()
    if (
        receipt.response_contract_version
        != prepared.spec.response_contract_version
        or len(receipt.request_fingerprint_salt) != FINGERPRINT_SALT_BYTES
        or len(receipt.request_fingerprint) != FINGERPRINT_BYTES
    ):
        _raise_unavailable(database)
    candidate = request_fingerprint(receipt.request_fingerprint_salt, canonical)
    if not hmac.compare_digest(candidate, receipt.request_fingerprint):
        _rollback(database)
        raise client_operation_conflict()
    if (
        receipt.state != "completed"
        or receipt.response_status != prepared.spec.status_code
        or not isinstance(receipt.response_body, dict)
        or not isinstance(receipt.mutation_applied, bool)
    ):
        _raise_unavailable(database)
    try:
        typed, body, response = _render_registered_response(
            prepared.spec,
            receipt.response_body,
            stored_snapshot=True,
        )
    except Exception:
        _raise_unavailable(database)
    if _contains_exact_response_value(
        body,
        prepared.forbidden_response_values,
        prepared.identity.client_operation_id,
    ):
        _raise_unavailable(database)
    if not _response_matches_operation(
        prepared.spec,
        prepared.project_id,
        prepared.target_envelope,
        prepared.domain_payload,
        typed,
        receipt.mutation_applied,
    ):
        _raise_unavailable(database)
    return ReplayedOperation(
        spec=prepared.spec,
        status=prepared.spec.status_code,
        typed_body=typed,
        response=response,
        mutation_applied=receipt.mutation_applied,
    )


def complete_client_operation(
    database: Session,
    operation: CompletableOperation,
    public_result: object,
    *,
    mutation_applied: bool,
) -> CompletedOperation:
    if not isinstance(mutation_applied, bool):
        _raise_unavailable(database)
    try:
        typed, body, response = _render_registered_response(
            operation.spec,
            public_result,
        )
    except Exception:
        _raise_unavailable(database)
    if _contains_exact_response_value(
        body,
        operation.forbidden_response_values,
        (
            operation.client_operation_id
            if isinstance(operation, ReservedOperation)
            else None
        ),
    ):
        _rollback(database)
        raise client_operation_secret_echo()
    if not _response_matches_operation(
        operation.spec,
        operation.project_id,
        operation.target_envelope,
        operation.domain_payload,
        typed,
        mutation_applied,
    ):
        _raise_unavailable(database)

    if isinstance(operation, ReservedOperation):
        completed_id = database.execute(
            update(ClientOperation)
            .where(
                ClientOperation.id == operation.receipt_id,
                ClientOperation.state == "pending",
                ClientOperation.operation_kind == operation.spec.kind,
                ClientOperation.request_fingerprint_version
                == operation.spec.request_fingerprint_version,
                ClientOperation.response_contract_version
                == operation.spec.response_contract_version,
            )
            .values(
                state="completed",
                response_status=operation.spec.status_code,
                response_body=body,
                mutation_applied=mutation_applied,
                completed_at=func.greatest(
                    ClientOperation.created_at,
                    func.clock_timestamp(),
                ),
            )
            .returning(ClientOperation.id)
        ).scalar_one_or_none()
        if completed_id != operation.receipt_id:
            _raise_unavailable(database)
        classification: Literal["executed", "unprotected"] = "executed"
    elif isinstance(operation, UnprotectedOperation):
        classification = "unprotected"
    else:
        _raise_unavailable(database)

    return CompletedOperation(
        spec=operation.spec,
        status=operation.spec.status_code,
        typed_body=typed,
        response=response,
        mutation_applied=mutation_applied,
        classification=classification,
    )
