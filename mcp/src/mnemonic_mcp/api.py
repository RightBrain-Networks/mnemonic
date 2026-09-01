"""HTTP boundary: no database driver, database credentials, or API imports."""

from datetime import datetime
from typing import Any, TypeVar

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from .config import Settings
from .validation import VALIDATION_FIELDS, validation_error_message

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
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
    "work_not_open": "This work item is not open and cannot perform that operation.",
    "work_blocked": "This work item has an unresolved blocker.",
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
    "relationship_exists": "That relationship already exists.",
    "parent_already_set": "That work item already has a parent.",
    "active_relationships": "Remove this work item's relationships before deleting it.",
}
_UNKNOWN_CLAIM_OUTCOME = (
    "Mnemonic API could not confirm the response; the claim outcome is unknown. Retry promptly "
    "with the exact same claim_request_id from this call. A new request ID can conflict, and search "
    "or recall cannot recover the lease token."
)


def _is_claim_operation(method: str, path: str) -> bool:
    return method == "POST" and path.endswith(("/claim", "/claim-and-recall"))


def _application_error(response: httpx.Response) -> tuple[str, dict[str, object]] | None:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
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
            if method in {"POST", "PATCH", "DELETE"}:
                if _is_claim_operation(method, path):
                    raise ToolError(_UNKNOWN_CLAIM_OUTCOME) from None
                raise ToolError(
                    "Mnemonic API is unavailable; the write outcome is unknown. "
                    "Search or recall before retrying to avoid duplicate or conflicting changes."
                ) from None
            raise ToolError(
                "Mnemonic API is unavailable. Check service health and try again."
            ) from None

        if response.status_code in {401, 403}:
            raise ToolError(
                "Mnemonic API authentication failed. Check the services' API-key configuration."
            )
        if response.status_code == 404:
            raise ToolError(
                "The requested project, work item, checkpoint, or relationship was not found "
                "in this project."
            )

        if response.status_code >= 500 and _is_claim_operation(method, path):
            raise ToolError(_UNKNOWN_CLAIM_OUTCOME)

        application_error = _application_error(response)
        if application_error is not None and not 200 <= response.status_code < 300:
            error_code, error_context = application_error
            message = _application_error_message(error_code, error_context)
            if message is not None:
                raise ToolError(message)
            raise ToolError(
                "Mnemonic could not complete this operation. Recall the current work state "
                "before retrying."
            )

        # Legacy deployments return string details, so keep the old conflict
        # interpretation during the compatibility window without exposing them.
        if response.status_code == 409:
            if method in {"PATCH", "DELETE"} or path.endswith(("/complete", "/delete")):
                raise ToolError(
                    "Version conflict. Recall the latest work item and review the changes "
                    "before retrying."
                )
            raise ToolError(
                "A project with this slug already exists. List projects before creating another."
            )
        if response.status_code == 422:
            fields: set[str] = set()
            try:
                detail = response.json().get("detail", [])
                if isinstance(detail, list):
                    for error in detail:
                        if isinstance(error, dict) and isinstance(error.get("loc"), list):
                            fields.update(
                                item
                                for item in error["loc"]
                                if isinstance(item, str) and item in VALIDATION_FIELDS
                            )
            except (ValueError, AttributeError):
                pass
            raise ToolError(validation_error_message(fields))
        if response.status_code == 503 and semantic_read:
            raise ToolError("Mnemonic semantic search is unavailable. Retry with semantic disabled.")
        if not 200 <= response.status_code < 300:
            if _is_claim_operation(method, path):
                raise ToolError(_UNKNOWN_CLAIM_OUTCOME)
            raise ToolError(
                "Mnemonic API could not complete this request. Check service health before retrying."
            )
        if response_model is None:
            if response.status_code != 204:
                raise ToolError(
                    "Mnemonic API returned an unexpected response. Check the service versions."
                )
            return None
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError):
            if _is_claim_operation(method, path):
                raise ToolError(_UNKNOWN_CLAIM_OUTCOME) from None
            raise ToolError(
                "Mnemonic API returned an unexpected response. Check the service versions."
            ) from None
