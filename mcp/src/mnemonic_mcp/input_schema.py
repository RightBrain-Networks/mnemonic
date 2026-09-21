"""Compact, server-owned schema hints for rejected MCP arguments."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

_ANNOTATIONS = frozenset({"title", "description", "default", "examples"})
_SCHEMA_MAPS = frozenset({
    "$defs", "definitions", "properties", "patternProperties", "dependentSchemas",
})
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUES = frozenset({
    "items", "additionalProperties", "unevaluatedProperties", "propertyNames",
    "contains", "not", "if", "then", "else",
})
_MAX_PATTERN_LENGTH = 120


class InputValidationError(ToolError):
    """A sanitized input rejection, distinct from failed or uncertain execution."""


def compact_input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep shapes and constraints without prose, defaults or enormous regexes.

    Walk only JSON Schema positions: property names and literal enum/const values
    are data even when named 'title', 'description', or another schema keyword.
    References stay intact, so recursive definitions never need to be expanded.
    This function only receives the registered schema, never request arguments.
    """
    result = {}
    for key, value in schema.items():
        if key in _ANNOTATIONS:
            continue
        if key == "pattern" and len(value) > _MAX_PATTERN_LENGTH:
            result["x-pattern-omitted"] = True
        else:
            result[key] = _compact_value(key, value)
    # Lead with the tool's arguments; referenced definitions follow them.
    if "$defs" in result:
        result["$defs"] = result.pop("$defs")
    return result


def _compact_value(key: str, value: Any) -> Any:
    if key in _SCHEMA_MAPS:
        return {
            name: compact_input_schema(item) if isinstance(item, dict) else item
            for name, item in value.items()
        }
    if key in _SCHEMA_LISTS:
        return [compact_input_schema(item) if isinstance(item, dict) else item for item in value]
    if key in _SCHEMA_VALUES and isinstance(value, dict):
        return compact_input_schema(value)
    return value


def input_schema_hint(name: str, schema: dict[str, Any]) -> str:
    encoded = json.dumps(compact_input_schema(schema), separators=(",", ":"), ensure_ascii=True)
    return (
        f"\nInput schema for {name} (compact JSON Schema; descriptions/defaults and long "
        "patterns omitted; all validation rules still apply):\n" + encoded
    )
