"""HTTP boundary: no database driver, database credentials, or API imports."""

import asyncio
import json
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from typing import Any, NoReturn, TypeVar
from uuid import UUID

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from .config import Settings
from .transport import (
    COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES,
    MCP_STREAM_CHUNK_BYTES,
    declared_oversize_values,
    identity_content_encoding_values,
)
from .validation import validation_details, validation_error_message

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
ResponseValidator = Callable[[BaseModel], bool]


class TransportEffect(StrEnum):
    """Closed retry/uncertainty classification shared with the REST contract."""

    SAFE_READ = "safe_read"
    RECEIPT_PROTECTED_WRITE = "receipt_protected_write"
    LEASE_CLAIM = "lease_claim"


_APPLICATION_ERRORS = {
    "initial_status_must_be_pending": "Fresh work must start pending before a report-bearing closeout.",
    "job_completion_report_required": (
        "Every fresh Done, Won't do, or Promoted closeout requires a job completion report. "
        "Read get_project_settings and author its summary and FYIs before creating a new intent."
    ),
    "job_completion_report_not_applicable": (
        "A job completion report is accepted only for an actual reportable closeout transition."
    ),
    "job_report_prompt_changed": (
        "Project settings changed. Read get_project_settings, review the report against the "
        "current prompt, and prepare a new intent only after this definitive rejection."
    ),
    "project_activity_unavailable": "Project activity is unavailable; retry this safe read later.",
    "job_completion_report_unavailable": "Report history is unavailable; retry this safe read later.",
    "project_settings_unavailable": "Project settings are unavailable; do not invent a report prompt.",
    "project_settings_changed": "Project settings changed. Read current settings before editing.",
    "invalid_activity_cursor": "That activity cursor is invalid for this project or head.",
    "invalid_report_cursor": "That report cursor is invalid for this project or filter.",
    "activity_stream_changed": (
        "The project activity stream changed. Establish an explicit fresh snapshot before "
        "rebootstrapping; do not silently skip history by starting at now."
    ),
    "project_mutation_unavailable": (
        "The project mutation could not finish. Reconcile current state using its existing "
        "retry rules before continuing."
    ),
    "client_operation_id_required": "This mutation requires a retained client_operation_id.",
    "slug_conflict": "A project with this slug already exists. List projects before creating another.",
    "semantic_unavailable": (
        "Mnemonic semantic search is unavailable. Retry with semantic disabled."
    ),
    "version_conflict": (
        "Version conflict. Recall the latest work item and review its changes before retrying."
    ),
    "invalid_status_transition": (
        "This lifecycle transition is not allowed. Recall the latest work item and choose a permitted transition."
    ),
    "work_not_pending": "This work item is not pending and cannot perform that operation.",
    "completion_episode_unsealed": (
        "This work was completed before Mnemonic recorded completion episodes, so it cannot be "
        "reopened. Its history is retained; save any continuation as new work."
    ),
    "work_blocked": "This work item has an unresolved blocker.",
    "work_gated": (
        "This work item has unresolved human input. Inspect its current context or the human "
        "attention queue; do not bypass the gate with another claim or terminal mutation."
    ),
    "invalid_cursor": "That page cursor is invalid for this project, work item, or filter.",
    "gate_secret_echo": (
        "Mnemonic rejected the human-gate request because request-known credential or operation "
        "control data appeared in durable content. Remove it and create a genuinely corrected intent."
    ),
    "lease_held": "This work item has an active claim.",
    "lease_expired": "This work claim has expired. Recall the work state before retrying.",
    "lease_token_mismatch": "The work claim does not match the current active claim.",
    "claim_request_expired": (
        "That claim request can no longer be resumed. Claim again with a new claim_request_id."
    ),
    "relationship_cycle": "That relationship would create a cycle.",
    "relationship_context_invalid": (
        "Discovery context must belong to the originating target work item."
    ),
    "parent_already_set": "That work item already has a parent.",
    "active_relationships": "Remove this work item's relationships before deleting it.",
    "event_secret_echo": (
        "Mnemonic rejected progress because a request-known secret matched a persisted field."
    ),
    "client_operation_secret_echo": (
        "Mnemonic rejected the mutation because operation or capability material appeared in "
        "a persisted field. Remove it; changing an argument makes this a new intent and requires "
        "a new client_operation_id."
    ),
    "duplicate_merge_required": (
        "Generic duplicate marks are closed to fresh writes. Review both exact work contexts "
        "and use merge_work when an authoritative permanent merge is intended."
    ),
    "duplicate_self": "A work item cannot be merged into itself.",
    "work_duplicate": (
        "This exact work item is a retained duplicate audit record and cannot be mutated."
    ),
    "work_already_duplicate": "This merge source is already a retained duplicate.",
    "duplicate_destination_not_canonical": (
        "The merge destination is no longer canonical. Recall both exact work items again."
    ),
    "duplicate_context_changed": (
        "A reviewed merge context changed. Recall both exact work items and review them again."
    ),
    "duplicate_source_gate_unresolved": (
        "Resolve the source work item's human gate, including as no longer needed when appropriate, "
        "then recall both exact work items again."
    ),
    "duplicate_structural_relationships": (
        "Reconcile every source blocks and parent-child relationship before merging."
    ),
    "duplicate_depth_exceeded": "That merge would exceed the maximum canonical path depth.",
    "duplicate_relationship_frozen": (
        "A relationship retained by an authoritative duplicate merge cannot be removed."
    ),
    "duplicate_graph_invalid": (
        "Mnemonic's canonical duplicate graph is invalid. Stop authority-changing work and "
        "ask an operator to run the local integrity audit."
    ),
    "duplicate_suggestion_busy": (
        "Mnemonic duplicate suggestions are busy. Retry after one second, or continue creating "
        "the distinct work item without suggestions."
    ),
    "request_body_too_large": (
        "The duplicate-suggestion draft exceeded the request limit. Reduce its valid draft fields "
        "before retrying; creation remains independent."
    ),
    "duplicate_suggestion_unavailable": (
        "Mnemonic duplicate suggestions are unavailable. Retry later, or continue creating the "
        "distinct work item without suggestions."
    ),
}
UNKNOWN_CLAIM_OUTCOME = (
    "Mnemonic API could not confirm the response; the claim outcome is unknown. Retry promptly "
    "with the exact same claim_request_id from this call. A new request ID can conflict, and search "
    "or recall cannot recover the lease token."
)
UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME = (
    "Mnemonic could not confirm this idempotent mutation response; the operation may already "
    "have committed. Retry the same tool only if you still retain both its client_operation_id "
    "and the complete exact tool argument object, with every argument unchanged. If either was "
    "lost, or if any argument would change, do not generate or substitute a new UUID: stop, "
    "inspect current state where safe, and request direction."
)
_CLIENT_OPERATION_CONFLICT = (
    "Mnemonic rejected this client_operation_id because it is already bound to a different "
    "successful request. On an asserted exact retry, treat this as a caller-safety incident: "
    "do not retry or generate a replacement UUID; stop and request direction."
)
_SAFE_READ_FAILURE = (
    "Mnemonic API is unavailable; it could not complete this safe read. "
    "Check service health and try again."
)
_EXTENDED_READ_TIMEOUT_SECONDS = 60.0


class BoundedIdentityResponseViolation(ValueError):
    """An evidence-history response failed before safe JSON interpretation."""


async def _bounded_identity_response(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    max_bytes: int,
) -> httpx.Response:
    """Read a bounded amount of identity-coded bytes before UTF-8/JSON handling."""
    async with client.stream(
        method,
        path,
        params=params,
        json=payload,
        headers={"Accept-Encoding": "identity"},
    ) as response:
        if not identity_content_encoding_values(
            response.headers.get_list("content-encoding")
        ):
            raise BoundedIdentityResponseViolation("non-identity response coding")
        if declared_oversize_values(
            response.headers.get_list("content-length"),
            max_bytes,
        ):
            raise BoundedIdentityResponseViolation("oversized declared response")

        body = bytearray()
        async for chunk in response.aiter_raw(chunk_size=MCP_STREAM_CHUNK_BYTES):
            if len(chunk) > max_bytes - len(body):
                raise BoundedIdentityResponseViolation("oversized streamed response")
            body.extend(chunk)

        raw_body = bytes(body)
        try:
            raw_body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise BoundedIdentityResponseViolation("response is not UTF-8") from error
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=raw_body,
            request=response.request,
        )


async def _dispatch_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    bounded_identity_response: bool,
    response_max_bytes: int,
) -> httpx.Response:
    if bounded_identity_response:
        return await _bounded_identity_response(
            client,
            method,
            path,
            params=params,
            payload=payload,
            max_bytes=response_max_bytes,
        )
    return await client.request(method, path, params=params, json=payload)


_NOT_FOUND_MESSAGES = {
    "job_completion_report_not_found": "Job completion report not found in this project.",
    "project_not_found": (
        "Project not found. Use list_projects to resolve the correct project_id."
    ),
    "work_item_not_found": (
        "Work item not found in this project. Search this project again, or confirm the "
        "project_id is the one that holds it."
    ),
    "checkpoint_not_found": (
        "Checkpoint not found on this work item. Use list_checkpoints to see its history."
    ),
    "relationship_not_found": (
        "Relationship not found in this project. Use list_relationships to see current edges."
    ),
}
_UNKNOWN_NOT_FOUND = (
    "The requested project, work item, checkpoint, or relationship was not found in this project."
)


def _not_found_message(response: httpx.Response) -> str:
    """Say which entity kind missed, so the caller knows what to re-resolve."""
    application_error = _application_error(response)
    if application_error is None:
        return _UNKNOWN_NOT_FOUND
    return _NOT_FOUND_MESSAGES.get(application_error[0], _UNKNOWN_NOT_FOUND)


def _application_error(response: httpx.Response) -> tuple[str, dict[str, object]] | None:
    try:
        detail = response.json().get("detail")
    except (RecursionError, TypeError, ValueError, AttributeError):
        return None
    if not isinstance(detail, dict):
        return None
    code = detail.get("code")
    if not isinstance(code, str):
        return None
    context = detail.get("context")
    return code, context if isinstance(context, dict) else {}


def _safe_context_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        return None
    return value if all(character.isprintable() for character in value) else None


def _safe_expiry(value: object) -> str | None:
    text = _safe_context_text(value, max_length=64)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return text if parsed.tzinfo is not None else None


def _safe_uuid(value: object) -> str | None:
    text = _safe_context_text(value, max_length=36)
    if text is None:
        return None
    try:
        parsed = UUID(text)
    except ValueError:
        return None
    canonical = str(parsed)
    return canonical if text == canonical else None


def _application_error_message(code: str, context: dict[str, object]) -> str | None:
    if code == "work_duplicate":
        canonical_id = _safe_uuid(context.get("canonical_work_item_id"))
        if canonical_id is not None:
            return (
                _APPLICATION_ERRORS[code]
                + f" Its canonical work item is {canonical_id}."
            )
        return _APPLICATION_ERRORS[code]
    if code != "lease_held":
        return _APPLICATION_ERRORS.get(code)

    holder_client = _safe_context_text(context.get("holder_client"), max_length=80)
    expires_at = _safe_expiry(context.get("expires_at"))
    if holder_client is not None and expires_at is not None:
        return f"This work item has an active claim held by {holder_client} until {expires_at}."
    if holder_client is not None:
        return f"This work item has an active claim held by {holder_client}."
    if expires_at is not None:
        return f"This work item has an active claim until {expires_at}."
    return _APPLICATION_ERRORS[code]


def _raise_request_error(method: str, *, effect: TransportEffect | None) -> NoReturn:
    if effect == TransportEffect.SAFE_READ:
        raise ToolError(_SAFE_READ_FAILURE) from None
    if method not in {"POST", "PATCH", "DELETE"}:
        raise ToolError(
            "Mnemonic API is unavailable. Check service health and try again."
        ) from None
    if effect == TransportEffect.RECEIPT_PROTECTED_WRITE:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None
    if effect == TransportEffect.LEASE_CLAIM:
        raise ToolError(UNKNOWN_CLAIM_OUTCOME) from None
    raise ToolError(
        "Mnemonic API is unavailable; the write outcome is unknown. "
        "Search or recall before retrying to avoid duplicate or conflicting changes."
    ) from None


def _raise_server_uncertainty(
    response: httpx.Response,
    *,
    application_error: tuple[str, dict[str, object]] | None,
    effect: TransportEffect | None,
) -> None:
    if response.status_code < 500:
        return
    if (
        effect == TransportEffect.RECEIPT_PROTECTED_WRITE
        and application_error is not None
        and application_error[0] == "duplicate_graph_invalid"
    ):
        return
    if effect == TransportEffect.RECEIPT_PROTECTED_WRITE:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if effect == TransportEffect.LEASE_CLAIM:
        raise ToolError(UNKNOWN_CLAIM_OUTCOME)


def _raise_application_error_response(
    response: httpx.Response,
    application_error: tuple[str, dict[str, object]] | None,
    *,
    effect: TransportEffect | None,
) -> None:
    if application_error is None or 200 <= response.status_code < 300:
        return
    error_code, error_context = application_error
    if (
        effect == TransportEffect.RECEIPT_PROTECTED_WRITE
        and error_code == "client_operation_unavailable"
    ):
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if (
        effect == TransportEffect.RECEIPT_PROTECTED_WRITE
        and error_code == "client_operation_conflict"
    ):
        raise ToolError(_CLIENT_OPERATION_CONFLICT)
    message = _application_error_message(error_code, error_context)
    if message is not None:
        raise ToolError(message)
    if effect == TransportEffect.SAFE_READ:
        raise ToolError(_SAFE_READ_FAILURE)
    raise ToolError(
        "Mnemonic could not complete this operation. Recall the current work state "
        "before retrying."
    )


def _validation_error_pairs(response: httpx.Response) -> list[tuple[object, object]]:
    try:
        detail = response.json().get("detail", [])
    except (RecursionError, TypeError, ValueError, AttributeError):
        return []
    if not isinstance(detail, list):
        return []
    return [
        (error.get("loc"), error.get("type")) for error in detail if isinstance(error, dict)
    ]


def _raise_remaining_response_error(
    response: httpx.Response,
    method: str,
    *,
    semantic_read: bool,
    effect: TransportEffect | None,
) -> None:
    if response.status_code == 422:
        pairs = _validation_error_pairs(response)
        raise ToolError(validation_error_message(*validation_details(pairs)))
    if response.status_code == 503 and semantic_read:
        raise ToolError("Mnemonic semantic search is unavailable. Retry with semantic disabled.")
    if 200 <= response.status_code < 300:
        return
    if effect == TransportEffect.RECEIPT_PROTECTED_WRITE:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if effect == TransportEffect.LEASE_CLAIM:
        raise ToolError(UNKNOWN_CLAIM_OUTCOME)
    if effect == TransportEffect.SAFE_READ:
        raise ToolError(_SAFE_READ_FAILURE)
    raise ToolError(
        "Mnemonic API could not complete this request. Check service health before retrying."
    )


def _raise_for_response_error(
    response: httpx.Response,
    method: str,
    path: str,
    *,
    semantic_read: bool,
    effect: TransportEffect | None,
) -> None:
    if response.status_code in {401, 403}:
        raise ToolError(
            "Mnemonic API authentication failed. Check the services' API-key configuration."
        )
    if response.status_code == 404:
        raise ToolError(_not_found_message(response))
    application_error = _application_error(response)
    _raise_server_uncertainty(
        response,
        application_error=application_error,
        effect=effect,
    )
    _raise_application_error_response(
        response,
        application_error,
        effect=effect,
    )
    _raise_remaining_response_error(
        response,
        method,
        semantic_read=semantic_read,
        effect=effect,
    )


def _validate_expected_status(
    response: httpx.Response,
    *,
    effect: TransportEffect | None,
    expected_status_code: int | None,
) -> None:
    if expected_status_code is None or response.status_code == expected_status_code:
        return
    _raise_unexpected_response("", "", effect=effect)


def _validated_response_body[ModelT: BaseModel](
    response: httpx.Response,
    response_model: type[ModelT],
    *,
    effect: TransportEffect | None,
    response_validator: ResponseValidator | None,
    strict_wire_response: bool,
) -> ModelT:
    strict_wire = (
        effect == TransportEffect.RECEIPT_PROTECTED_WRITE or strict_wire_response
    )
    if not strict_wire:
        wire_response = response.json()
        parsed = response_model.model_validate(wire_response)
    else:
        # Completed receipts and explicitly classified safe reads use canonical
        # wire validation. Do not coerce values, synthesize defaults, or silently
        # normalize a malformed success into something authoritative-looking.
        encoded_response = response.content.decode("utf-8", errors="strict")
        wire_response = json.loads(
            encoded_response,
            object_pairs_hook=_response_object_without_duplicate_keys,
            parse_constant=_invalid_response_constant,
        )
        parsed = response_model.model_validate_json(encoded_response, strict=True)
        if parsed.model_dump(mode="json") != wire_response:
            raise ValueError("non-canonical response")
    if response_validator is not None and not response_validator(parsed):
        raise ValueError("incoherent response")
    return parsed


def _response_object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate response key")
        document[key] = value
    return document


def _invalid_response_constant(value: str) -> NoReturn:
    raise ValueError(f"invalid response constant: {value}")


def _raise_unexpected_response(
    method: str,
    path: str,
    *,
    effect: TransportEffect | None,
) -> NoReturn:
    if effect == TransportEffect.RECEIPT_PROTECTED_WRITE:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None
    if effect == TransportEffect.LEASE_CLAIM:
        raise ToolError(UNKNOWN_CLAIM_OUTCOME) from None
    raise ToolError(
        "Mnemonic API returned an unexpected response. Check the service versions."
    ) from None


def _parse_success_response[ModelT: BaseModel](
    response: httpx.Response,
    response_model: type[ModelT] | None,
    method: str,
    path: str,
    *,
    effect: TransportEffect | None,
    response_validator: ResponseValidator | None,
    strict_wire_response: bool,
) -> ModelT | None:
    if response_model is None:
        if response.status_code != 204:
            raise ToolError(
                "Mnemonic API returned an unexpected response. Check the service versions."
            )
        return None
    try:
        return _validated_response_body(
            response,
            response_model,
            effect=effect,
            response_validator=response_validator,
            strict_wire_response=strict_wire_response,
        )
    except (RecursionError, TypeError, ValueError, ValidationError):
        _raise_unexpected_response(method, path, effect=effect)


class MnemonicAPI:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        response_model: type[ResponseModel] | None = None,
        effect: TransportEffect | None = None,
        expected_status_code: int | None = None,
        response_validator: ResponseValidator | None = None,
        extended_read_timeout: bool = False,
        strict_wire_response: bool = False,
        bounded_identity_response: bool = False,
        response_max_bytes: int = COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES,
    ) -> ResponseModel | None:
        # A request-scoped client avoids sharing event-loop state across SDK
        # stateless HTTP sessions or stdio clients. No automatic write retries.
        semantic_read = method == "GET" and params is not None and params.get("semantic") is True
        if extended_read_timeout and effect != TransportEffect.SAFE_READ:
            raise ValueError("Extended read timeout requires an explicit safe-read effect.")
        if strict_wire_response and effect != TransportEffect.SAFE_READ:
            raise ValueError("Explicit strict wire validation requires a safe-read effect.")
        if bounded_identity_response and (
            effect != TransportEffect.SAFE_READ or method != "GET"
        ):
            raise ValueError("Bounded identity responses require an explicit safe GET.")
        if not 1 <= response_max_bytes <= COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES:
            raise ValueError("Invalid bounded response byte limit.")
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.settings.api_url.rstrip('/')}/api/v1/",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                # The first opt-in semantic query can populate the API's derived
                # embedding cache. Ordinary reads and writes keep the shorter timeout.
                timeout=httpx.Timeout(
                    (
                        _EXTENDED_READ_TIMEOUT_SECONDS
                        if semantic_read or extended_read_timeout
                        else 20.0
                    ),
                    connect=5.0,
                ),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                if semantic_read or extended_read_timeout:
                    # httpx timeouts bound individual transport phases. The
                    # outer deadline is the actual end-to-end request ceiling.
                    async with asyncio.timeout(_EXTENDED_READ_TIMEOUT_SECONDS):
                        response = await _dispatch_request(
                            client,
                            method,
                            path,
                            params=params,
                            payload=payload,
                            bounded_identity_response=bounded_identity_response,
                            response_max_bytes=response_max_bytes,
                        )
                else:
                    response = await _dispatch_request(
                        client,
                        method,
                        path,
                        params=params,
                        payload=payload,
                        bounded_identity_response=bounded_identity_response,
                        response_max_bytes=response_max_bytes,
                    )
        except (
            BoundedIdentityResponseViolation,
            TimeoutError,
            httpx.RequestError,
        ):
            _raise_request_error(method, effect=effect)

        _raise_for_response_error(
            response,
            method,
            path,
            semantic_read=semantic_read,
            effect=effect,
        )
        _validate_expected_status(
            response,
            effect=effect,
            expected_status_code=expected_status_code,
        )
        return _parse_success_response(
            response,
            response_model,
            method,
            path,
            effect=effect,
            response_validator=response_validator,
            strict_wire_response=strict_wire_response,
        )
