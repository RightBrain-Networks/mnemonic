"""HTTP boundary: no database driver, database credentials, or API imports."""

from typing import Any, TypeVar

import httpx
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, ValidationError

from .config import Settings

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
_VALIDATION_FIELDS = frozenset({
    "name", "slug", "description", "repository_url", "project_id", "handoff_id",
    "title", "summary", "prompt", "source_client", "source_session_id", "source_model",
    "source_session_url", "repository_branch", "verified_against", "tags", "source_metadata",
    "status", "q", "tag", "limit", "offset", "expected_version",
})


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
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.settings.api_url.rstrip('/')}/api/v1/",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=httpx.Timeout(20.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.request(method, path, params=params, json=payload)
        except httpx.RequestError:
            if method in {"POST", "PATCH", "DELETE"}:
                raise ToolError(
                    "Mnemonic API is unavailable; the write outcome is unknown. "
                    "Search or recall before retrying to avoid duplicate or conflicting changes."
                ) from None
            raise ToolError("Mnemonic API is unavailable. Check service health and try again.") from None

        if response.status_code in {401, 403}:
            raise ToolError("Mnemonic API authentication failed. Check the services' API-key configuration.")
        if response.status_code == 404:
            raise ToolError("The requested project or hand-off was not found in this project.")
        if response.status_code == 409:
            if method in {"PATCH", "DELETE"}:
                raise ToolError(
                    "Version conflict. Recall the latest hand-off and review the changes before retrying."
                )
            raise ToolError("A project with this slug already exists. List projects before creating another.")
        if response.status_code == 422:
            fields: set[str] = set()
            try:
                detail = response.json().get("detail", [])
                if isinstance(detail, list):
                    for error in detail:
                        if isinstance(error, dict) and isinstance(error.get("loc"), list):
                            fields.update(
                                item for item in error["loc"]
                                if isinstance(item, str) and item in _VALIDATION_FIELDS
                            )
            except (ValueError, AttributeError):
                pass
            hint = f" Check: {', '.join(sorted(fields))}." if fields else " Check the field constraints."
            raise ToolError(f"Mnemonic rejected the input.{hint}")
        if not 200 <= response.status_code < 300:
            raise ToolError("Mnemonic API could not complete this request. Check service health before retrying.")
        if response_model is None:
            if response.status_code != 204:
                raise ToolError("Mnemonic API returned an unexpected response. Check the service versions.")
            return None
        try:
            return response_model.model_validate(response.json())
        except (ValueError, ValidationError):
            raise ToolError("Mnemonic API returned an unexpected response. Check the service versions.") from None
