"""Strict, secret-safe validation at the local FastMCP tool boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
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
        "gate_type",
        "source_work_item_id",
        "destination_work_item_id",
        "target_work_item_id",
        "other_work_item_id",
        "context_checkpoint_id",
        "reviewed_source_revision",
        "reviewed_destination_revision",
        "work_version",
        "work_event_count",
        "relationship_id",
        "relationship_type",
        "type",
        "direction",
        "title",
        "summary",
        "question",
        "requested_by_client",
        "requested_by_session_id",
        "requested_by_model",
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
        "affected_paths",
        "tags",
        "created_by_client",
        "created_by_session_id",
        "created_by_model",
        "rationale",
        "merged_by_client",
        "merged_by_session_id",
        "merged_by_model",
        "source_metadata",
        "status",
        "body",
        "metadata",
        "event_type",
        "actor",
        "actor_client",
        "actor_session_id",
        "actor_model",
        "q",
        "semantic",
        "tag",
        "min_priority",
        "parent_work_item_id",
        "view",
        "duplicate_scope",
        "canonical_work_item_id",
        "order",
        "recent_limit",
        "limit",
        "recent_event_limit",
        "cursor",
        "offset",
        "expected_version",
        "holder_client",
        "holder_session_id",
        "claim_request_id",
        "client_operation_id",
        "lease_token",
    }
)

VALIDATION_ERROR_TYPES = frozenset(
    {
        "missing",
        "extra_forbidden",
        "value_error",
        "literal_error",
        "enum",
        "string_type",
        "string_too_short",
        "string_too_long",
        "string_pattern_mismatch",
        "uuid_parsing",
        "uuid_type",
        "int_type",
        "int_parsing",
        "float_type",
        "bool_type",
        "bool_parsing",
        "greater_than",
        "greater_than_equal",
        "less_than",
        "less_than_equal",
        "too_short",
        "too_long",
        "list_type",
        "dict_type",
        "model_type",
        "model_attributes_type",
        "json_invalid",
        "json_type",
        "url_parsing",
        "url_type",
        "datetime_parsing",
        "datetime_type",
        "none_required",
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


def _is_safe_path(path: str) -> bool:
    return bool(path) and all(part in VALIDATION_FIELDS for part in path.split("."))


def _rendered(field: str, types: Iterable[str]) -> str:
    safe_types = sorted(set(types) & VALIDATION_ERROR_TYPES)
    return f"{field} ({', '.join(safe_types)})" if safe_types else field


def validation_error_message(
    field_types: Mapping[str, Iterable[str]],
    unattributed_types: Iterable[str] = (),
) -> str:
    """Build a stable rejection without rendering values or arbitrary field names.

    Every part of every field path comes from VALIDATION_FIELDS and every error
    kind from VALIDATION_ERROR_TYPES, so neither can carry a caller-supplied value.
    """
    safe_fields = sorted(path for path in field_types if _is_safe_path(path))
    if safe_fields:
        rendered = ", ".join(_rendered(field, field_types[field]) for field in safe_fields)
        return f"Mnemonic rejected the input. Check: {rendered}."
    safe_types = sorted(set(unattributed_types) & VALIDATION_ERROR_TYPES)
    if safe_types:
        return (
            f"Mnemonic rejected the input ({', '.join(safe_types)}). "
            "Check the field names and constraints."
        )
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


def validation_details(
    locations_and_types: Iterable[tuple[object, object]],
) -> tuple[dict[str, set[str]], set[str]]:
    """Split raw pydantic (loc, type) pairs into allowlisted fields and error kinds."""
    field_types: dict[str, set[str]] = {}
    unattributed: set[str] = set()
    for location, raw_type in locations_and_types:
        kind = raw_type if isinstance(raw_type, str) and raw_type in VALIDATION_ERROR_TYPES else None
        parts = location if isinstance(location, tuple | list) else ()
        matched = [
            part for part in parts if isinstance(part, str) and part in VALIDATION_FIELDS
        ]
        if not matched:
            # extra_forbidden names the caller's own unknown key, so it is never
            # allowlisted; report the kind alone rather than echoing the key.
            if kind is not None:
                unattributed.add(kind)
            continue
        # Keep the whole allowlisted path so a nested field says where it lives.
        types = field_types.setdefault(".".join(matched), set())
        if kind is not None:
            types.add(kind)
    return field_types, unattributed


def _validation_details(error: ValidationError) -> tuple[dict[str, set[str]], set[str]]:
    return validation_details(
        (item.get("loc", ()), item.get("type"))
        for item in error.errors(
            include_url=False, include_context=False, include_input=False
        )
    )


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
        function_name = getattr(fn, "__name__", None)
        if name is None and not isinstance(function_name, str):
            raise TypeError("Tool callables must have a name.")
        tool_name = name if name is not None else function_name
        assert tool_name is not None
        tool = self._tool_manager.get_tool(tool_name)
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
                validation_error_message(*_validation_details(validation_error))
            ) from None
