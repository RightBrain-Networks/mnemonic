"""Strict, secret-safe validation at the local FastMCP tool boundary."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import AnyFunction, Icon, ToolAnnotations
from pydantic import ConfigDict, ValidationError

from .input_errors import InputValidationError
from .input_hints import help_pointer, rejection_message
from .transport import bounded_stdio_server
from .validation_rules import VALIDATION_RULES

# Reviewed in docs/validation-vocabulary.json; test_validation_vocabulary.py pins this subset.
VALIDATION_FIELDS = frozenset(
    {
        "created_after",
        "created_before",
        "updated_after",
        "updated_before",
        "diagnostics", "query_mode", "work_fields", "topic",
        "tag_counts",
        "facets",
        "facet",
        "facet_order",
        "filters",
        "work_items",
        "artifacts",
        "transcripts",
        "sort",
        "by",
        "fulltext",
        "detail",
    "content_kinds", "segment_id", "expected_normalized_revision", "before", "after",
        "artifact_id",
        "include_deleted",
        "sensitive",
        "mime_type",
        "created_by_agent_session_id",
        "agent_session_id",
        "external_candidates",
        "session_transcript",
        "subagent_transcripts",
        "client",
        "transcript_id",
        "expected_sha256", "expected_text_sha256",
        "intent", "upload_intent", "api_origin", "expected_revision",
        "size_bytes", "sha256", "filename", "related_artifact_ids",
        "related_work_item_ids", "approval_token", "human_approved", "content_base64",

        "gate_id",
        "expected_question_version",
        "code_review_handoff",
        "scope",
        "repositories",
        "repository_key",
        "checkout_path",
        "object_format",
        "base_commit",
        "head_commit",
        "handoff",
        "change_summary",
        "decisions",
        "focus_areas",
        "traps",
        "validation_summary",
        "recommend_review",
        "answer",
        "follow_up_id",
        "expected_follow_up_version",
        "expected_review_version",
        "review_id",
        "scope_sha256",
        "mode",
        "purpose",
        "code_review_id",
        "supersede_code_review_id",
        "expected_code_review_version",
        "supersede_follow_up_id",
        "coverage",
        "limitations",
        "findings",
        "finding_key",
        "severity",
        "path",
        "location_side",
        "start_line",
        "end_line",
        "problem",
        "triggering_conditions",
        "impact",
        "evidence",
        "recommended_verification",
        "result",
        "availability",
        "code_review_required_min_priority",
        "code_review_optional_min_priority",
        "allow_remediation_code_reviews",
        "external_references",
        "external_url",
        "state",
        "state_observed_at",
        "url",
        "name",
        "slug",
        "description",
        "repository_url",
        "project_id", "project_ids",
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
        "completion_evidence",
        "job_completion_report",
        "fyi_items",
        "prompt_revision",
        "report_id",
        "dismissal",
        "start",
        "verification_results",
        "artifact_references",
        "verification_type",
        "outcome",
        "command",
        "exit_code",
        "observed_at",
        "observed_at_commit",
        "artifact_type",
        "label",
        "reference",
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
        "query",
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
        "lease_minutes",
        "lease_settings",
        "lease_default_minutes",
        "lease_minimum_minutes",
        "lease_maximum_minutes",
        "default_minutes",
        "minimum_minutes",
        "maximum_minutes",
        "status_only",
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
        *VALIDATION_RULES,
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
        "union_tag_not_found",
        "union_tag_invalid",
    }
)


class _SDKValidationLogFilter(logging.Filter):
    """Remove user-supplied values from MCP SDK envelope-validation logs."""

    _REPLACEMENTS: ClassVar[dict[str, str]] = {
        "Failed to validate request:": "MCP request parameters were invalid.",
        "Message that failed validation:": "Invalid MCP request details were suppressed.",
        "Failed to validate notification:": "MCP notification parameters were invalid.",
        "Received exception from stream:": "MCP stream message was invalid.",
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
    """Install narrow filters at both pinned SDK validation-log emission sites."""
    for logger_name in (None, "mcp.server.lowlevel.server"):
        logger = logging.getLogger(logger_name)
        if not any(item is _SDK_VALIDATION_LOG_FILTER for item in logger.filters):
            logger.addFilter(_SDK_VALIDATION_LOG_FILTER)


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
        hints = sorted({VALIDATION_RULES[kind][1] for field in safe_fields
                        for kind in field_types[field] if kind in VALIDATION_RULES})
        return f"Mnemonic rejected the input. Check: {rendered}." + (
            " " + " ".join(hints) if hints else ""
        )
    safe_types = sorted(set(unattributed_types) & VALIDATION_ERROR_TYPES)
    if safe_types:
        return (
            f"Mnemonic rejected the input ({', '.join(safe_types)}). "
            "Check the field names and constraints."
        )
    return "Mnemonic rejected the input. Check the field names and constraints."


def _exception_in_chain[ErrorT: BaseException](
    error: BaseException, error_type: type[ErrorT],
) -> ErrorT | None:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, error_type):
            return current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
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
        rule = VALIDATION_RULES.get(kind or "")
        if rule is not None and rule[0] is not None:
            parts = (rule[0],)
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


_CLAIM_KEY_HINT = (
    "Claim tools use claim_request_id as the retry key; client_operation_id is not accepted."
)
_ACTOR_HINT = "Put the actual author in checkpoint.source_client and checkpoint.source_session_id."
_TOOL_INPUT_HINTS = {
    ("complete_work", ("actor_client",)): _ACTOR_HINT,
    ("complete_work", ("actor_session_id",)): _ACTOR_HINT,
    ("claim_work", ("client_operation_id",)): _CLAIM_KEY_HINT,
    ("claim_and_recall", ("client_operation_id",)): _CLAIM_KEY_HINT,
    ("search", ("sources",)): (
        "Use facets to select work_items, artifacts and transcripts; sources is not accepted."
    ),
}


def _tool_validation_message(name: str, schema: dict[str, Any], error: ValidationError) -> str:
    hints = []
    # Only exact, reviewed mistakes select static hints. Never interpolate an
    # unknown field name or value, including nested metadata keys.
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        if item["type"] == "extra_forbidden":
            hint = _TOOL_INPUT_HINTS.get((name, item["loc"]))
            if hint is not None:
                hints.append(hint)
    return rejection_message(name, schema, _validation_details(error), hints=hints)


def _validate_subagent_closeout(name: str, arguments: dict[str, Any]) -> None:
    changes = arguments.get("changes")
    if name == "update_work" and isinstance(changes, dict):
        status = changes.get("status")
        closeout = isinstance(status, str) and status in {"wont-do", "promoted"}
        if not closeout and arguments.get("subagent_transcripts") is not None:
            raise InputValidationError(
                "Subagent transcript assertions require a closeout transition."
            )


class SanitizedFastMCP(FastMCP[Any]):
    """Per-server strict argument models and value-free validation errors."""

    def __init__(
        self,
        *args: Any,
        server_version: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        if not server_version:
            raise ValueError("The MCP application version must be nonempty.")
        # FastMCP 1.29 has no public version argument; both transports read this
        # low-level Server field when creating their initialization options.
        self._mcp_server.version = server_version

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
            _validate_subagent_closeout(name, arguments)
            return await super().call_tool(name, arguments)
        except ToolError as error:
            if tool is None:
                raise
            input_error = _exception_in_chain(error, InputValidationError)
            validation_error = _exception_in_chain(error, ValidationError)
            if input_error is not None:
                message = (
                    rejection_message(name, tool.parameters, input_error.details)
                    if input_error.details is not None
                    else str(input_error) + " " + help_pointer(name)
                )
            elif (
                validation_error is not None
                and validation_error.title == tool.fn_metadata.arg_model.__name__
            ):
                message = _tool_validation_message(name, tool.parameters, validation_error)
            else:
                raise
            raise ToolError(message) from None

    async def run_stdio_async(self) -> None:
        """Run through the bounded binary adapter at the pinned FastMCP seam."""
        async with bounded_stdio_server() as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )
