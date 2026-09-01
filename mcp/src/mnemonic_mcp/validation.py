"""Strict, secret-safe validation at the local FastMCP tool boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, ClassVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import AnyFunction, Icon, ToolAnnotations
from pydantic import ConfigDict, ValidationError

VALIDATION_FIELDS = frozenset(
    {
        "name",
        "slug",
        "description",
        "repository_url",
        "project_id",
        "work_item_id",
        "source_work_item_id",
        "target_work_item_id",
        "other_work_item_id",
        "relative_to_work_item_id",
        "checkpoint_id",
        "context_checkpoint_id",
        "context_checkpoint_work_item_id",
        "handoff_id",
        "relationship_id",
        "relationship_type",
        "type",
        "direction",
        "title",
        "summary",
        "priority",
        "initial_checkpoint",
        "initial_relationships",
        "checkpoint",
        "changes",
        "kind",
        "prompt",
        "source_client",
        "source_session_id",
        "source_model",
        "source_session_url",
        "repository_branch",
        "verified_against",
        "tags",
        "created_by_client",
        "created_by_session_id",
        "created_by_model",
        "source_metadata",
        "status",
        "body",
        "q",
        "semantic",
        "tag",
        "view",
        "order",
        "recent_limit",
        "limit",
        "offset",
        "expected_version",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
        "lease_token",
    }
)


class _SDKValidationLogFilter(logging.Filter):
    """Remove user-supplied values from MCP SDK envelope-validation logs."""

    _REPLACEMENTS: ClassVar[dict[str, str]] = {
        "Failed to validate request:": "MCP request parameters were invalid.",
        "Message that failed validation:": "Invalid MCP request details were suppressed.",
        "Failed to validate notification:": "MCP notification parameters were invalid.",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        for prefix, replacement in self._REPLACEMENTS.items():
            if rendered.startswith(prefix):
                record.msg = replacement
                record.args = ()
                break
        return True


_SDK_VALIDATION_LOG_FILTER = _SDKValidationLogFilter()


def install_sdk_validation_log_filter() -> None:
    """Install one narrow root filter for SDK logs emitted through logging.warning."""
    root_logger = logging.getLogger()
    if not any(item is _SDK_VALIDATION_LOG_FILTER for item in root_logger.filters):
        root_logger.addFilter(_SDK_VALIDATION_LOG_FILTER)


def validation_error_message(fields: Iterable[str]) -> str:
    """Build a stable rejection without rendering values or arbitrary field names."""
    safe_fields = sorted(set(fields) & VALIDATION_FIELDS)
    if safe_fields:
        return f"Mnemonic rejected the input. Check: {', '.join(safe_fields)}."
    return "Mnemonic rejected the input. Check the field names and constraints."


def _pydantic_validation_error(error: BaseException) -> ValidationError | None:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ValidationError):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def _validation_fields(error: ValidationError) -> set[str]:
    fields: set[str] = set()
    for item in error.errors(
        include_url=False, include_context=False, include_input=False
    ):
        location = item.get("loc", ())
        if isinstance(location, tuple | list):
            fields.update(
                part
                for part in location
                if isinstance(part, str) and part in VALIDATION_FIELDS
            )
    return fields


class SanitizedFastMCP(FastMCP[Any]):
    """Per-server strict argument models and value-free validation errors."""

    def add_tool(
        self,
        fn: AnyFunction,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = None,
    ) -> None:
        super().add_tool(
            fn,
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )

        # mcp==1.29 creates a dynamic model per tool. Harden only this server's
        # just-registered model, then refresh the schema captured by Tool.
        tool = self._tool_manager.get_tool(name or fn.__name__)
        if tool is None:  # pragma: no cover - registration either returns or raises
            raise RuntimeError("FastMCP did not retain the registered tool.")
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = ConfigDict(
            **{
                **argument_model.model_config,
                "extra": "forbid",
                "hide_input_in_errors": True,
            }
        )
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tool_manager.get_tool(name)
        try:
            return await super().call_tool(name, arguments)
        except ToolError as error:
            validation_error = _pydantic_validation_error(error)
            if (
                validation_error is None
                or tool is None
                or validation_error.title != tool.fn_metadata.arg_model.__name__
            ):
                raise
            raise ToolError(
                validation_error_message(_validation_fields(validation_error))
            ) from None
