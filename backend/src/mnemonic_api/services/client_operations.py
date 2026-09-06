"""Closed, fail-safe persistence contract for idempotent client mutations."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from types import MappingProxyType
from typing import Any, Literal, NoReturn, cast
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
from mnemonic_api.models import ClientOperation, WorkItemMove
from mnemonic_api.schemas import (
    APIModel,
    CheckpointCreate,
    CheckpointRead,
    HumanGateRead,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
    InitialRelationshipCreate,
    JobCompletionReportDismissalCreate,
    JobCompletionReportDismissalRequest,
    JobCompletionReportDismissalResult,
    JobCompletionReportFollowUpCreate,
    JobCompletionReportFollowUpRequest,
    JobCompletionReportFollowUpResult,
    LeaseReleaseCreate,
    MutationActor,
    ProgressEventCreate,
    RelationshipCreate,
    RelationshipCreationResult,
    RelationshipEdgeRead,
    RelationshipRemovalCreate,
    RelationshipRemovalResult,
    ReleaseResult,
    WorkCompletionCreate,
    WorkCompletionRead,
    WorkCompletionRequest,
    WorkCreation,
    WorkDeferralCreate,
    WorkDeletionCreate,
    WorkDeletionRead,
    WorkEventRead,
    WorkItemCreate,
    WorkItemPatch,
    WorkItemRead,
    WorkMergeCreate,
    WorkMergeRequest,
    WorkMergeResult,
    WorkMoveCreate,
    WorkMoveRead,
    WorkUpdateRead,
)

type OperationKind = Literal[
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "move_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
    "merge_work",
    "dismiss_job_completion_report",
    "create_job_completion_report_follow_up",
]
REGISTERED_OPERATION_KINDS: tuple[OperationKind, ...] = (
    "create_work",
    "add_checkpoint",
    "append_event",
    "add_relationship",
    "update_work",
    "defer_work",
    "move_work",
    "complete_work",
    "delete_work",
    "remove_relationship",
    "release_claim",
    "request_human_input",
    "resolve_human_input",
    "merge_work",
    "dismiss_job_completion_report",
    "create_job_completion_report_follow_up",
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
# The only top-level request fields that may carry control data.
_DESIGNATED_REQUEST_FIELDS = frozenset({"client_operation_id", "lease_token"})


@dataclass(frozen=True)
class OperationSpec:
    kind: OperationKind
    request_model: type[APIModel]
    response_model: type[APIModel]
    status_code: int
    target_fields: tuple[str, ...]
    domain_model: type[APIModel]
    mutation_applied_field: Literal["created", "removed", "released", "dismissed"] | None = None
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
    mutation_applied_field: Literal["created", "removed", "released", "dismissed"] | None = None,
    domain_model: type[APIModel] | None = None,
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
        domain_model=domain_model or request_model,
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
    "update_work": _spec("update_work", WorkItemPatch, WorkUpdateRead, 200, "work_item_id"),
    "defer_work": _spec(
        "defer_work", WorkDeferralCreate, WorkItemRead, 200, "work_item_id"
    ),
    "move_work": _spec(
        "move_work", WorkMoveCreate, WorkMoveRead, 200, "work_item_id"
    ),
    "complete_work": _spec(
        "complete_work",
        WorkCompletionCreate,
        WorkCompletionRead,
        200,
        "work_item_id",
        domain_model=WorkCompletionRequest,
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
    "merge_work": _spec(
        "merge_work",
        WorkMergeCreate,
        WorkMergeResult,
        201,
        "work_item_id",
        domain_model=WorkMergeRequest,
    ),
    "dismiss_job_completion_report": _spec(
        "dismiss_job_completion_report", JobCompletionReportDismissalCreate,
        JobCompletionReportDismissalResult, 200, "report_id", mutation_applied_field="dismissed",
        domain_model=JobCompletionReportDismissalRequest,
    ),
    "create_job_completion_report_follow_up": _spec(
        "create_job_completion_report_follow_up", JobCompletionReportFollowUpCreate,
        JobCompletionReportFollowUpResult, 201, "report_id",
        domain_model=JobCompletionReportFollowUpRequest,
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
    if spec.kind == "complete_work":
        reject_completion_evidence_secret_substrings(
            cast(WorkCompletionCreate, payload),
            known_secret_values=known_secret_values,
        )
    reject_report_secret_substrings(payload, known_secret_values=known_secret_values)
    reject_reference_secret_substrings(payload, known_secret_values=known_secret_values)
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
    domain_payload = spec.domain_model.model_validate(
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


def reject_completion_evidence_secret_substrings(
    payload: WorkCompletionCreate,
    *,
    known_secret_values: Iterable[str] = (),
) -> None:
    """Reject request-known secrets embedded inside durable evidence strings."""
    evidence = payload.completion_evidence
    if evidence is None:
        return
    exact_secrets = {value for value in known_secret_values if value}
    if payload.lease_token:
        exact_secrets.add(payload.lease_token)
    operation_id = payload.client_operation_id
    uuid_spellings: set[str] = set()
    if operation_id is not None:
        canonical = str(operation_id)
        uuid_spellings = {
            canonical,
            canonical.replace("-", ""),
            f"urn:uuid:{canonical}",
            f"{{{canonical}}}",
        }
    for value in _nested_strings(evidence.model_dump(mode="json")):
        if any(secret in value for secret in exact_secrets) or any(
            spelling in value.casefold() for spelling in uuid_spellings
        ):
            raise client_operation_secret_echo()


def reject_reference_secret_substrings(
    payload: APIModel, *, known_secret_values: Iterable[str] = (),
) -> None:
    references = getattr(payload, "external_references", None)
    if not references:
        return
    secrets_to_check = {value for value in known_secret_values if value}
    token = getattr(payload, "lease_token", None)
    if token:
        secrets_to_check.add(token)
    operation_id = getattr(payload, "client_operation_id", None)
    spellings: set[str] = set()
    if operation_id is not None:
        value = str(operation_id)
        spellings = {value, value.replace("-", ""), "urn:uuid:" + value, "{" + value + "}"}
    for reference in references:
        for value in (reference.url, reference.label or ""):
            if any(secret in value for secret in secrets_to_check) or any(
                spelling in value.casefold() for spelling in spellings
            ):
                raise client_operation_secret_echo()


def reject_report_secret_substrings(
    payload: APIModel, *, known_secret_values: Iterable[str] = (),
) -> None:
    report = getattr(payload, "job_completion_report", None)
    if report is None:
        return
    secrets_to_check = {value for value in known_secret_values if value}
    token = getattr(payload, "lease_token", None)
    if token:
        secrets_to_check.add(token)
    operation_id = getattr(payload, "client_operation_id", None)
    spellings = set()
    if operation_id is not None:
        value = str(operation_id)
        spellings = {value, value.replace("-", ""), "urn:uuid:" + value, "{" + value + "}"}
    for value in [report.summary, *report.fyi_items]:
        if any(secret in value for secret in secrets_to_check) or any(
            spelling in value.casefold() for spelling in spellings
        ):
            raise client_operation_secret_echo()


def _nested_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _nested_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _nested_strings(item)


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
    if _contains_control_echo(
        payload.model_dump(mode="json"),
        known_values,
        operation_id,
        designated_fields=_DESIGNATED_REQUEST_FIELDS,
    ):
        raise client_operation_secret_echo()
    return frozenset(known_values)


def _spells_operation_id(candidate: object, operation_id: UUID | None) -> bool:
    """Whether ``candidate`` is the operation UUID in any of its spellings."""
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


def _contains_control_echo(
    value: object,
    known_values: Collection[str],
    operation_id: UUID | None,
    *,
    designated_fields: frozenset[str] = frozenset(),
) -> bool:
    """Walk one JSON tree for an exact known value, operation UUID, or reserved key.

    ``designated_fields`` names the keys of ``value`` itself that legitimately
    carry control data (a request's own ``client_operation_id`` and
    ``lease_token``); those keys and their contents are skipped. Keys of nested
    objects are never designated.
    """
    if isinstance(value, list):
        return any(
            _contains_control_echo(
                item, known_values, operation_id, designated_fields=designated_fields
            )
            for item in value
        )
    if isinstance(value, dict):
        return any(
            key not in designated_fields
            and (
                key in known_values
                or _spells_operation_id(key, operation_id)
                or key.casefold() in FORBIDDEN_RESPONSE_FIELD_NAMES
                or _contains_control_echo(item, known_values, operation_id)
            )
            for key, item in value.items()
        )
    return _spells_operation_id(value, operation_id) or (
        isinstance(value, str) and value in known_values
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


def _raise_unavailable(database: Session) -> NoReturn:
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
    "affected_paths",
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


# A coherence check receives (project_id, target envelope, request payload,
# typed response) and answers whether that response could have come from
# executing that request.
type ResponseMatcher = Callable[[UUID, Mapping[str, str], APIModel, APIModel], bool]


def _expected_initial_relationships(
    request: WorkItemCreate,
    work_item_id: UUID,
) -> dict[tuple[str, UUID, UUID], InitialRelationshipCreate]:
    expected: dict[tuple[str, UUID, UUID], InitialRelationshipCreate] = {}
    for relationship in sorted(request.initial_relationships, key=_initial_relationship_order):
        if relationship.direction == "outgoing":
            source, target = work_item_id, relationship.other_work_item_id
        else:
            source, target = relationship.other_work_item_id, work_item_id
        expected.setdefault(
            _normalized_relationship_identity(relationship.type, source, target),
            relationship,
        )
    return expected


def _initial_relationships_match(
    project_id: UUID,
    request: WorkItemCreate,
    result: WorkCreation,
) -> bool:
    work = result.work_item
    expected_relationships = _expected_initial_relationships(request, work.id)
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


def _create_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
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
        or work.external_references != request.external_references
        or result.initial_checkpoint.work_item_id != work.id
        or result.initial_checkpoint.id != work.initial_checkpoint_id
        or result.initial_checkpoint.kind != "context"
        or not _checkpoint_matches_payload(
            result.initial_checkpoint,
            request.initial_checkpoint,
        )
    ):
        return False
    return _initial_relationships_match(project_id, request, result)


def _add_checkpoint_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(CheckpointRead, typed)
    request = cast(CheckpointCreate, payload)
    return (
        str(result.work_item_id) == target_envelope.get("work_item_id")
        and result.kind == request.kind
        and _checkpoint_matches_payload(result, request)
    )


def _append_event_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkEventRead, typed)
    request = cast(ProgressEventCreate, payload)
    return (
        result.project_id == project_id
        and str(result.work_item_id) == target_envelope.get("work_item_id")
        and result.event_type == "progress"
        and result.body == request.body
        and result.model_dump(mode="json")["metadata"] == request.metadata
        and result.actor_client == request.actor.actor_client
        and result.actor_session_id == request.actor.actor_session_id
        and result.actor_model == request.actor.actor_model
        and result.origin == "live"
    )


def _add_relationship_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
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


def _update_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkUpdateRead, typed)
    request = cast(WorkItemPatch, payload)
    changed_fields = request.model_fields_set - {
        "expected_version",
        "lease_token",
        "actor",
        "client_operation_id",
        "job_completion_report",
    }
    return (
        _report_matches(result.job_completion_report, request.job_completion_report, request.actor)
        and result.project_id == project_id
        and str(result.id) == target_envelope.get("work_item_id")
        and result.version == request.expected_version + 1
        and all(getattr(result, field) == getattr(request, field) for field in changed_fields)
    )


def _defer_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkItemRead, typed)
    request = cast(WorkDeferralCreate, payload)
    return (
        result.project_id == project_id
        and str(result.id) == target_envelope.get("work_item_id")
        and result.version == request.expected_version + 1
        and result.status == "deferred"
    )


def _move_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkMoveRead, typed)
    request = cast(WorkMoveCreate, payload)
    return (
        result.source_project_id == project_id
        and result.target_project_id == request.target_project_id
        and str(result.work_item.id) == target_envelope.get("work_item_id")
        and result.work_item.project_id == request.target_project_id
        and result.work_item.version == request.expected_version + 1
        and result.work_item.status == result.preserved_status
    )


def _move_work_fact_matches(
    database: Session,
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    if not _move_work_matches(project_id, target_envelope, payload, typed):
        return False
    result = cast(WorkMoveRead, typed)
    request = cast(WorkMoveCreate, payload)
    move = database.scalar(
        select(WorkItemMove).where(
            WorkItemMove.work_item_id == result.work_item.id,
            WorkItemMove.resulting_work_version == result.work_item.version,
        )
    )
    if move is None:
        return False
    actor = request.actor
    expected_actor = (
        ("unattributed", None, None, None)
        if actor is None
        else (
            "client",
            actor.actor_client,
            actor.actor_session_id,
            actor.actor_model,
        )
    )
    return (
        move.source_project_id == project_id == result.source_project_id
        and move.target_project_id == request.target_project_id == result.target_project_id
        and move.source_work_version == request.expected_version
        and move.resulting_work_version == request.expected_version + 1
        and move.preserved_status == result.preserved_status == result.work_item.status
        and move.created_at == result.work_item.updated_at
        and (
            move.actor_kind,
            move.actor_client,
            move.actor_session_id,
            move.actor_model,
        )
        == expected_actor
    )


def _complete_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkCompletionRead, typed)
    request = cast(WorkCompletionRequest, payload)
    return (
        result.work_item.project_id == project_id
        and str(result.work_item.id) == target_envelope.get("work_item_id")
        and result.work_item.version == request.expected_version + 1
        and result.work_item.status == "done"
        and result.checkpoint.work_item_id == result.work_item.id
        and result.checkpoint.kind == "completion"
        and _checkpoint_matches_payload(result.checkpoint, request.checkpoint)
        and _completion_evidence_matches(result, request)
        and _report_matches(result.job_completion_report, request.job_completion_report,
            MutationActor(actor_client=request.checkpoint.source_client,
                          actor_session_id=request.checkpoint.source_session_id,
                          actor_model=request.checkpoint.source_model))
    )


def _completion_evidence_matches(
    result: WorkCompletionRead,
    request: WorkCompletionRequest,
) -> bool:
    expected = request.completion_evidence
    actual = result.completion_evidence
    if expected is None or actual is None:
        return expected is None and actual is None
    if (
        len(expected.verification_results) != len(actual.verification_results)
        or len(expected.artifact_references) != len(actual.artifact_references)
    ):
        return False
    result_fields = (
        "verification_type",
        "name",
        "outcome",
        "summary",
        "observed_at",
        "observed_at_commit",
        "command",
        "exit_code",
    )
    artifact_fields = ("artifact_type", "label", "reference")
    return all(
        all(
            getattr(source, field, None) == getattr(target, field, None)
            for field in result_fields
        )
        for source, target in zip(
            expected.verification_results,
            actual.verification_results,
            strict=True,
        )
    ) and all(
        all(
            getattr(source, field, None) == getattr(target, field, None)
            for field in artifact_fields
        )
        for source, target in zip(
            expected.artifact_references,
            actual.artifact_references,
            strict=True,
        )
    )


def _delete_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkDeletionRead, typed)
    request = cast(WorkDeletionCreate, payload)
    return (
        result.deleted is True
        and result.project_id == project_id
        and str(result.work_item_id) == target_envelope.get("work_item_id")
        and result.version == request.expected_version + 1
    )


def _remove_relationship_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(RelationshipRemovalResult, typed)
    return (
        result.project_id == project_id
        and str(result.relationship_id) == target_envelope.get("relationship_id")
    )


def _release_claim_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(ReleaseResult, typed)
    return str(result.work_item_id) == target_envelope.get("work_item_id")


def _request_human_input_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(HumanGateRead, typed)
    request = cast(HumanGateRequestCreate, payload)
    revision = result.current_context_revision
    return (
        result.project_id == project_id
        and str(result.work_item_id) == target_envelope.get("work_item_id")
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


def _resolve_human_input_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(HumanGateRead, typed)
    request = cast(HumanGateResolutionCreate, payload)
    resolved_revision = result.resolved_context_revision
    return (
        result.project_id == project_id
        and str(result.work_item_id) == target_envelope.get("work_item_id")
        and str(result.id) == target_envelope.get("gate_id")
        and result.status == "resolved"
        and result.resolution == request.resolution
        and result.resolved_by_client == request.resolved_by_client
        and result.resolved_by_session_id == request.resolved_by_session_id
        and result.resolved_by_model == request.resolved_by_model
        and resolved_revision is not None
        and result.current_context_revision == resolved_revision
        and request.reviewed_context_revision == resolved_revision
    )


def _merge_work_matches(
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    result = cast(WorkMergeResult, typed)
    request = cast(WorkMergeRequest, payload)
    merge = result.merge
    return (
        merge.project_id == project_id
        and str(merge.source_work_item_id) == target_envelope.get("work_item_id")
        and merge.destination_work_item_id == request.destination_work_item_id
        and merge.reviewed_source_revision == request.reviewed_source_revision
        and merge.reviewed_destination_revision == request.reviewed_destination_revision
        and merge.rationale == request.rationale
        and merge.merged_by_client == request.merged_by_client
        and merge.merged_by_session_id == request.merged_by_session_id
        and merge.merged_by_model == request.merged_by_model
        and result.source_work_item.id == merge.source_work_item_id
        and result.destination_work_item.id == merge.destination_work_item_id
        and result.direct_destination.id == merge.destination_work_item_id
        and result.canonical_work_item.id == merge.destination_work_item_id
    )


def _report_matches(actual, expected, actor: MutationActor | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    return (
        actual.summary == expected.summary and actual.fyi_items == expected.fyi_items
        and actual.prompt_revision == expected.prompt_revision and actor is not None
        and all(getattr(actual, field) == getattr(actor, field)
                for field in ("actor_client", "actor_session_id", "actor_model"))
    )


def _dismiss_report_matches(project_id: UUID, target: Mapping[str, str],
                            payload: APIModel, typed: APIModel) -> bool:
    result = cast(JobCompletionReportDismissalResult, typed)
    request = cast(JobCompletionReportDismissalRequest, payload)
    return (result.project_id == project_id and str(result.report_id) == target.get("report_id")
        and (not result.dismissed or all(getattr(result.human_dismissal, field) == value
                                        for field, value in request.actor.model_dump().items())))


def _follow_up_matches(project_id: UUID, target: Mapping[str, str],
                       payload: APIModel, typed: APIModel) -> bool:
    result = cast(JobCompletionReportFollowUpResult, typed)
    request = cast(JobCompletionReportFollowUpRequest, payload)
    return (result.work_item.project_id == project_id
        and str(result.follow_up.report_id) == target.get("report_id")
        and all(getattr(result.work_item, field) == getattr(request, field)
                for field in ("title", "summary", "priority"))
        and _checkpoint_matches_payload(result.initial_checkpoint, request.initial_checkpoint)
        and all(getattr(result.follow_up, field) == value
                for field, value in request.actor.model_dump().items()))


_RESPONSE_MATCHERS: Mapping[OperationKind, ResponseMatcher] = MappingProxyType(
    {
        "create_work": _create_work_matches,
        "add_checkpoint": _add_checkpoint_matches,
        "append_event": _append_event_matches,
        "add_relationship": _add_relationship_matches,
        "update_work": _update_work_matches,
        "defer_work": _defer_work_matches,
        "move_work": _move_work_matches,
        "complete_work": _complete_work_matches,
        "delete_work": _delete_work_matches,
        "remove_relationship": _remove_relationship_matches,
        "release_claim": _release_claim_matches,
        "request_human_input": _request_human_input_matches,
        "resolve_human_input": _resolve_human_input_matches,
        "merge_work": _merge_work_matches,
        "dismiss_job_completion_report": _dismiss_report_matches,
        "create_job_completion_report_follow_up": _follow_up_matches,
    }
)
if set(_RESPONSE_MATCHERS) != set(OPERATION_REGISTRY):  # pragma: no cover - import guard
    raise RuntimeError("Every registered operation needs a response coherence check")


def _response_matches_operation(
    spec: OperationSpec,
    project_id: UUID,
    target_envelope: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
    mutation_applied: bool,
    database: Session | None = None,
) -> bool:
    expected_applied = (
        True
        if spec.mutation_applied_field is None
        else getattr(typed, spec.mutation_applied_field, None)
    )
    if not isinstance(expected_applied, bool) or mutation_applied is not expected_applied:
        return False
    matcher = _RESPONSE_MATCHERS.get(spec.kind)
    if matcher is None:
        return False
    if not matcher(project_id, target_envelope, payload, typed):
        return False
    return (
        spec.kind != "move_work"
        or database is None
        or _move_work_fact_matches(database, project_id, target_envelope, payload, typed)
    )


def reserve_client_operation(
    database: Session,
    prepared: PreparedOperation,
    *,
    wait_seconds: int,
) -> ReservationOutcome:
    identity = prepared.identity
    if identity is None:
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
    receipt = _insert_or_fetch_receipt(
        database,
        prepared.spec,
        identity,
        salt=salt,
        fingerprint=request_fingerprint(salt, canonical),
        deadline=monotonic() + wait_seconds,
    )
    if isinstance(receipt, int):
        return ReservedOperation(
            spec=prepared.spec,
            receipt_id=receipt,
            client_operation_id=identity.client_operation_id,
            project_id=prepared.project_id,
            target_envelope=prepared.target_envelope,
            domain_payload=prepared.domain_payload,
            forbidden_response_values=prepared.forbidden_response_values,
        )
    _require_same_request(database, prepared.spec, receipt, canonical)
    return _replay_completed_receipt(database, prepared, identity, receipt)


def _insert_or_fetch_receipt(
    database: Session,
    spec: OperationSpec,
    identity: OperationIdentity,
    *,
    salt: bytes,
    fingerprint: bytes,
    deadline: float,
) -> int | ClientOperation:
    """Reserve the key, or read the receipt that already holds it.

    Returns the new receipt id when this request won the reservation, otherwise
    the existing row. Both statements share one absolute wait budget.
    """
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
                operation_kind=spec.kind,
                request_fingerprint_version=spec.request_fingerprint_version,
                request_fingerprint_salt=salt,
                request_fingerprint=fingerprint,
                response_contract_version=spec.response_contract_version,
                state="pending",
            )
            .on_conflict_do_nothing(constraint="uq_client_operations_scope")
            .returning(ClientOperation.id)
        ).scalar_one_or_none()
        if receipt_id is not None:
            _restore_receipt_timeouts(database)
            return receipt_id

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
    return receipt


def _require_same_request(
    database: Session,
    spec: OperationSpec,
    receipt: ClientOperation,
    canonical: bytes,
) -> None:
    """Conflict when the receipt belongs to another request; unavailable when malformed."""
    if receipt.operation_kind != spec.kind:
        _rollback(database)
        raise client_operation_conflict()
    if receipt.request_fingerprint_version != spec.request_fingerprint_version:
        _rollback(database)
        raise client_operation_conflict()
    if (
        receipt.response_contract_version != spec.response_contract_version
        or len(receipt.request_fingerprint_salt) != FINGERPRINT_SALT_BYTES
        or len(receipt.request_fingerprint) != FINGERPRINT_BYTES
    ):
        _raise_unavailable(database)
    candidate = request_fingerprint(receipt.request_fingerprint_salt, canonical)
    if not hmac.compare_digest(candidate, receipt.request_fingerprint):
        _rollback(database)
        raise client_operation_conflict()


def _replay_completed_receipt(
    database: Session,
    prepared: PreparedOperation,
    identity: OperationIdentity,
    receipt: ClientOperation,
) -> ReplayedOperation:
    response_body = receipt.response_body
    mutation_applied = receipt.mutation_applied
    if (
        receipt.state != "completed"
        or receipt.response_status != prepared.spec.status_code
        or not isinstance(response_body, dict)
        or not isinstance(mutation_applied, bool)
    ):
        _raise_unavailable(database)
    try:
        typed, body, response = _render_registered_response(
            prepared.spec,
            response_body,
            stored_snapshot=True,
        )
    except Exception:
        _raise_unavailable(database)
    if _contains_control_echo(
        body,
        prepared.forbidden_response_values,
        identity.client_operation_id,
    ):
        _raise_unavailable(database)
    if not _response_matches_operation(
        prepared.spec,
        prepared.project_id,
        prepared.target_envelope,
        prepared.domain_payload,
        typed,
        mutation_applied,
        database,
    ):
        _raise_unavailable(database)
    return ReplayedOperation(
        spec=prepared.spec,
        status=prepared.spec.status_code,
        typed_body=typed,
        response=response,
        mutation_applied=mutation_applied,
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
    if _contains_control_echo(
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
        database,
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
