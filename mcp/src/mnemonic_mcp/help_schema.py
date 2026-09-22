"""Navigate registered input schemas without expanding unrelated definitions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

Schema = dict[str, Any]


def resolve(schema: Schema, root: Schema) -> Schema:
    seen: set[str] = set()
    while isinstance(reference := schema.get("$ref"), str) and reference not in seen:
        seen.add(reference)
        if not reference.startswith("#/$defs/"):
            break
        name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
        target = root.get("$defs", {}).get(name)
        if not isinstance(target, dict):
            break
        schema = {**target, **{key: value for key, value in schema.items() if key != "$ref"}}
    return schema


def branches(schema: Schema, root: Schema) -> list[Schema]:
    schema = resolve(schema, root)
    choices = schema.get("anyOf", schema.get("oneOf", []))
    return [resolve(item, root) for item in choices if isinstance(item, dict)]


def nonnull(schema: Schema, root: Schema) -> Schema:
    schema = resolve(schema, root)
    choices = [item for item in branches(schema, root) if item.get("type") != "null"]
    if "type" not in schema and "properties" not in schema and len(choices) == 1:
        return choices[0]
    return schema


def item_schema(schema: Schema, root: Schema) -> Schema:
    schema = nonnull(schema, root)
    if schema.get("type") == "array":
        return nonnull(schema.get("items", {}), root)
    return schema


def variants(schema: Schema, root: Schema) -> dict[str, Schema]:
    schema = item_schema(schema, root)
    discriminator = schema.get("discriminator", {})
    mapping = discriminator.get("mapping", {})
    return {name: resolve({"$ref": reference}, root) for name, reference in mapping.items()}


def children(schema: Schema, root: Schema) -> dict[str, Schema]:
    schema = item_schema(schema, root)
    return {**schema.get("properties", {}), **variants(schema, root)}


@dataclass(frozen=True)
class SchemaLocation:
    schema: Schema
    path: tuple[str, ...]
    complete: bool = True


def locate(root: Schema, path: tuple[str, ...]) -> SchemaLocation:
    schema = root
    traversed: tuple[str, ...] = ()
    for part in path:
        child = children(schema, root).get(part)
        if child is None:
            return SchemaLocation(schema, traversed, complete=False)
        schema = child
        traversed += (part,)
    return SchemaLocation(schema, traversed)


def type_label(schema: Schema, root: Schema, *, depth: int = 0) -> str:
    schema = resolve(schema, root)
    if depth >= 3:
        return schema.get("type", "JSON value")
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=True)
    if "enum" in schema:
        values = schema["enum"]
        return "|".join(json.dumps(value) for value in values[:6]) + (
            "|…" if len(values) > 6 else ""
        )
    kind = schema.get("type")
    if kind is None:
        return _union_label(schema, root, depth)
    if kind == "array":
        return "array<" + type_label(schema.get("items", {}), root, depth=depth + 1) + ">"
    if kind == "object":
        return "object" if "properties" in schema else "map"
    return kind if schema.get("format") == "password" else schema.get("format", kind)


def _union_label(schema: Schema, root: Schema, depth: int) -> str:
    if choices := variants(schema, root):
        return "|".join(choices)
    options = branches(schema, root)
    if depth >= 3 or not options:
        return "JSON value"
    return "|".join(dict.fromkeys(type_label(item, root, depth=depth + 1) for item in options))


def constraints(schema: Schema, root: Schema) -> str:
    schema = nonnull(schema, root)
    labels = {
        "minimum": ">=", "maximum": "<=", "exclusiveMinimum": ">", "exclusiveMaximum": "<",
        "minLength": "minLength=", "maxLength": "maxLength=",
        "minItems": "minItems=", "maxItems": "maxItems=",
        "x-utf8-max-bytes": "maxUTF8Bytes=", "x-utf8-aggregate-max-bytes": "maxTotalUTF8Bytes=",
    }
    parts = [prefix + str(schema[key]) for key, prefix in labels.items() if key in schema]
    if pattern := schema.get("pattern"):
        parts.append("pattern=" + json.dumps(pattern) if len(pattern) <= 80 else "pattern constrained")
    if "enum" in schema:
        parts.append("allowed=" + ", ".join(json.dumps(value) for value in schema["enum"]))
    return "; ".join(parts)


def scoped_schema(schema: Schema, root: Schema) -> Schema:
    """A complete schema for this node, with only its transitively referenced definitions."""
    if schema is root:
        return root
    document = dict(schema)
    definitions: dict[str, Schema] = {}
    pending = [schema]
    while pending:
        for reference in _references(pending.pop()):
            name = reference.removeprefix("#/$defs/").replace("~1", "/").replace("~0", "~")
            if name not in definitions and name in root.get("$defs", {}):
                definitions[name] = root["$defs"][name]
                pending.append(definitions[name])
    if definitions:
        document["$defs"] = definitions
    return document


def _references(value: Any) -> list[str]:
    if isinstance(value, list):
        return [reference for item in value for reference in _references(item)]
    if not isinstance(value, dict):
        return []
    own = value.get("$ref")
    result = [own] if isinstance(own, str) and own.startswith("#/$defs/") else []
    return result + [reference for item in value.values() for reference in _references(item)]
