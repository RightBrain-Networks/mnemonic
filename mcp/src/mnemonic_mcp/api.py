"""HTTP boundary: no database driver, database credentials, or API imports."""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, NoReturn, TypeVar

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from .config import Settings
from .validation import validation_details, validation_error_message

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
ResponseValidator = Callable[[BaseModel], bool]
_APPLICATION_ERRORS = {
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
}
_UNKNOWN_CLAIM_OUTCOME = (
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


_NOT_FOUND_MESSAGES = {
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


def _is_claim_operation(method: str, path: str) -> bool:
    return method == "POST" and path.endswith(("/claim", "/claim-and-recall"))


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


def _application_error_message(code: str, context: dict[str, object]) -> str | None:
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


def _raise_request_error(method: str, path: str, *, idempotent_mutation: bool) -> NoReturn:
    if method not in {"POST", "PATCH", "DELETE"}:
        raise ToolError(
            "Mnemonic API is unavailable. Check service health and try again."
        ) from None
    if idempotent_mutation:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None
    if _is_claim_operation(method, path):
        raise ToolError(_UNKNOWN_CLAIM_OUTCOME) from None
    raise ToolError(
        "Mnemonic API is unavailable; the write outcome is unknown. "
        "Search or recall before retrying to avoid duplicate or conflicting changes."
    ) from None


def _raise_server_uncertainty(
    response: httpx.Response,
    method: str,
    path: str,
    *,
    idempotent_mutation: bool,
) -> None:
    if response.status_code < 500:
        return
    if idempotent_mutation:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if _is_claim_operation(method, path):
        raise ToolError(_UNKNOWN_CLAIM_OUTCOME)


def _raise_application_error_response(
    response: httpx.Response,
    application_error: tuple[str, dict[str, object]] | None,
    *,
    idempotent_mutation: bool,
) -> None:
    if application_error is None or 200 <= response.status_code < 300:
        return
    error_code, error_context = application_error
    if idempotent_mutation and error_code == "client_operation_unavailable":
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if idempotent_mutation and error_code == "client_operation_conflict":
        raise ToolError(_CLIENT_OPERATION_CONFLICT)
    message = _application_error_message(error_code, error_context)
    if message is not None:
        raise ToolError(message)
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
    path: str,
    *,
    semantic_read: bool,
    idempotent_mutation: bool,
) -> None:
    if response.status_code == 422:
        pairs = _validation_error_pairs(response)
        raise ToolError(validation_error_message(*validation_details(pairs)))
    if response.status_code == 503 and semantic_read:
        raise ToolError("Mnemonic semantic search is unavailable. Retry with semantic disabled.")
    if 200 <= response.status_code < 300:
        return
    if idempotent_mutation:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)
    if _is_claim_operation(method, path):
        raise ToolError(_UNKNOWN_CLAIM_OUTCOME)
    raise ToolError(
        "Mnemonic API could not complete this request. Check service health before retrying."
    )


def _raise_for_response_error(
    response: httpx.Response,
    method: str,
    path: str,
    *,
    semantic_read: bool,
    idempotent_mutation: bool,
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
        method,
        path,
        idempotent_mutation=idempotent_mutation,
    )
    _raise_application_error_response(
        response,
        application_error,
        idempotent_mutation=idempotent_mutation,
    )
    _raise_remaining_response_error(
        response,
        method,
        path,
        semantic_read=semantic_read,
        idempotent_mutation=idempotent_mutation,
    )


def _validate_expected_status(
    response: httpx.Response,
    *,
    idempotent_mutation: bool,
    expected_status_code: int | None,
) -> None:
    if (
        idempotent_mutation
        and expected_status_code is not None
        and response.status_code != expected_status_code
    ):
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME)


def _validated_response_body[ModelT: BaseModel](
    response: httpx.Response,
    response_model: type[ModelT],
    *,
    idempotent_mutation: bool,
    response_validator: ResponseValidator | None,
) -> ModelT:
    wire_response = response.json()
    if not idempotent_mutation:
        return response_model.model_validate(wire_response)
    # A completed receipt is a frozen wire snapshot. Do not let Pydantic
    # coerce values, synthesize defaults, or silently normalize a malformed
    # success into something that looks authoritative to the caller.
    encoded_response = json.dumps(
        wire_response,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    parsed = response_model.model_validate_json(encoded_response, strict=True)
    if parsed.model_dump(mode="json") != wire_response:
        raise ValueError("non-canonical idempotent mutation response")
    if response_validator is not None and not response_validator(parsed):
        raise ValueError("incoherent idempotent mutation response")
    return parsed


def _raise_unexpected_response(
    method: str,
    path: str,
    *,
    idempotent_mutation: bool,
) -> NoReturn:
    if idempotent_mutation:
        raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None
    if _is_claim_operation(method, path):
        raise ToolError(_UNKNOWN_CLAIM_OUTCOME) from None
    raise ToolError(
        "Mnemonic API returned an unexpected response. Check the service versions."
    ) from None


def _parse_success_response[ModelT: BaseModel](
    response: httpx.Response,
    response_model: type[ModelT] | None,
    method: str,
    path: str,
    *,
    idempotent_mutation: bool,
    response_validator: ResponseValidator | None,
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
            idempotent_mutation=idempotent_mutation,
            response_validator=response_validator,
        )
    except (RecursionError, TypeError, ValueError, ValidationError):
        _raise_unexpected_response(method, path, idempotent_mutation=idempotent_mutation)


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
        idempotent_mutation: bool = False,
        expected_status_code: int | None = None,
        response_validator: ResponseValidator | None = None,
    ) -> ResponseModel | None:
        # A request-scoped client avoids sharing event-loop state across SDK
        # stateless HTTP sessions or stdio clients. No automatic write retries.
        semantic_read = method == "GET" and params is not None and params.get("semantic") is True
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.settings.api_url.rstrip('/')}/api/v1/",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                # The first opt-in semantic query can populate the API's derived
                # embedding cache. Ordinary reads and writes keep the shorter timeout.
                timeout=httpx.Timeout(60.0 if semantic_read else 20.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, params=params, json=payload)
        except httpx.RequestError:
            _raise_request_error(method, path, idempotent_mutation=idempotent_mutation)

        _raise_for_response_error(
            response,
            method,
            path,
            semantic_read=semantic_read,
            idempotent_mutation=idempotent_mutation,
        )
        _validate_expected_status(
            response,
            idempotent_mutation=idempotent_mutation,
            expected_status_code=expected_status_code,
        )
        return _parse_success_response(
            response,
            response_model,
            method,
            path,
            idempotent_mutation=idempotent_mutation,
            response_validator=response_validator,
        )
