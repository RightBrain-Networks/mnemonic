"""Bounded prose fixes and help links, using only sanitized paths and static schemas."""

from __future__ import annotations

from collections.abc import Iterable

from .command_help import help_call
from .help_schema import (
    Schema,
    SchemaLocation,
    constraints,
    item_schema,
    locate,
    type_label,
    variants,
)
from .input_errors import ValidationDetails
from .validation_rules import VALIDATION_RULES

_MAX_ISSUES = 3
_MAX_HINT_LENGTH = 260

_SIZE_HINTS = {
    "string_too_long": ("maxLength", "Shorten this string"),
    "string_too_short": ("minLength", "Lengthen this string"),
    "too_long": ("maxItems", "Supply fewer items"),
    "too_short": ("minItems", "Supply more items"),
}

_KIND_HINTS = {
    "extra_forbidden": "Remove unlisted fields.",
    "uuid_parsing": "Use a UUID string.", "uuid_type": "Use a UUID string.",
    "string_type": "Use a string.", "int_type": "Use an integer.",
    "int_parsing": "Use an integer.", "float_type": "Use a number.",
    "bool_type": "Use true or false.", "bool_parsing": "Use true or false.",
    "list_type": "Use a JSON array.", "dict_type": "Use a JSON object.",
    "model_type": "Use a JSON object with the documented fields.",
    "model_attributes_type": "Use a JSON object with the documented fields.",
    "datetime_parsing": "Use an ISO 8601 timestamp with a timezone.",
    "datetime_type": "Use an ISO 8601 timestamp with a timezone.",
    "json_invalid": "Use valid JSON.", "json_type": "Use JSON data.",
    "none_required": "This variant accepts only null; check the intended input variant.",
}


def _field_location(schema: Schema, field: str) -> SchemaLocation:
    path = tuple(field.split("."))
    found = locate(schema, path)
    if not found.complete and path[0] == "body":
        alternate = locate(schema, path[1:])
        if alternate.complete:
            return alternate
    return found


def _expected(node: Schema, schema: Schema) -> str:
    value = type_label(node, schema)
    required = item_schema(node, schema).get("required", [])
    if required:
        value += " with " + ", ".join(required)
    return value


def _repair(kind: str, node: Schema, root: Schema) -> str:
    if kind in VALIDATION_RULES:
        return VALIDATION_RULES[kind][1]
    if kind in _SIZE_HINTS:
        keyword, hint = _SIZE_HINTS[kind]
        value = item_schema(node, root).get(keyword) if keyword.endswith("Length") else node.get(keyword)
        return hint + (f"; {keyword}={value}." if value is not None else "; check the documented limit.")
    if kind == "missing":
        return "Supply " + _expected(node, root) + "."
    if kind in {"literal_error", "enum"}:
        return "Use " + type_label(node, root) + "."
    if (kind in {"union_tag_not_found", "union_tag_invalid"} or not kind) and (
        choices := variants(node, root)
    ):
        key = item_schema(node, root).get("discriminator", {}).get("propertyName", "variant")
        return "Choose " + key + ": " + ", ".join(choices) + "."
    if kind in _KIND_HINTS:
        return _KIND_HINTS[kind]
    if limits := constraints(node, root):
        return "Use " + type_label(node, root) + "; " + limits + "."
    return "Check this field's documented constraints."


def _issue(field: str, kinds: set[str], schema: Schema) -> tuple[str, tuple[str, ...]]:
    location = _field_location(schema, field)
    # Unknown nested keys are withheld by the sanitizer. Do not suggest removing
    # the known parent simply because an unlisted child was rejected.
    label = field if len(field) <= 150 else ".".join(location.path)
    suffix = " (" + ", ".join(sorted(kinds)) + ")" if kinds else ""
    kind = next(iter(sorted(kinds)), "")
    message = label + suffix + ": " + _repair(kind, location.schema, schema)
    if len(message) > _MAX_HINT_LENGTH:
        message = label + suffix + ": check the field's documented constraints."
    target = location.path
    if not children_for_help(location.schema, schema) and len(target) > 1:
        target = target[:-1]
    return message, target


def children_for_help(node: Schema, root: Schema) -> bool:
    item = item_schema(node, root)
    return bool(item.get("properties") or variants(item, root))


def _relevant_fields(details: ValidationDetails) -> list[tuple[str, set[str]]]:
    fields, _ = details
    return [(field, kinds) for field, kinds in sorted(fields.items()) if not (
        kinds == {"none_required"} and any(other.startswith(field + ".") for other in fields)
    )]


def _general_hint(kinds: set[str]) -> str:
    kind = next(iter(sorted(kinds)), "")
    return VALIDATION_RULES[kind][1] if kind in VALIDATION_RULES else _KIND_HINTS.get(
        kind, "Use the documented arguments.",
    )


def rejection_message(
    name: str, schema: Schema, details: ValidationDetails, *, hints: Iterable[str] = (),
) -> str:
    messages = list(dict.fromkeys(hints))[:2]
    fields = _relevant_fields(details)
    targets: list[tuple[str, ...]] = []
    for field, kinds in fields[:_MAX_ISSUES]:
        message, target = _issue(field, kinds, schema)
        messages.append(message)
        if target not in targets:
            targets.append(target)
    if not fields:
        kinds = details[1]
        label = " (" + ", ".join(sorted(kinds)) + ")" if kinds else ""
        messages.append("Check the command's fields and types" + label + ". " + (
            _general_hint(kinds)
        ))
    if len(fields) > _MAX_ISSUES:
        messages.append(f"{len(fields) - _MAX_ISSUES} more invalid fields; consult help.")
    target = next((path for path in targets if path), ())
    return "Mnemonic rejected the input. " + " ".join(messages) + " " + help_pointer(name, target)


def help_pointer(name: str, path: tuple[str, ...] = ()) -> str:
    return "Call " + help_call(name, path) + " for fields and guidance; " + (
        "use " + help_call(name, ("schema",)) + " only for the full schema."
    )
