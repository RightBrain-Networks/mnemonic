"""Read-only, progressively disclosed MCP command help."""

from __future__ import annotations

import json
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import Tool, ToolAnnotations
from pydantic import Field

from .help_guides import FIELD_NOTES, GUIDES
from .help_schema import (
    Schema,
    children,
    constraints,
    item_schema,
    locate,
    nonnull,
    resolve,
    scoped_schema,
    type_label,
    variants,
)


def help_call(name: str = "", path: tuple[str, ...] = ()) -> str:
    topic = " ".join((name, *path)).strip()
    arguments = {"topic": topic} if topic else {}
    return "help(" + json.dumps(arguments, separators=(",", ":")) + ")"


def _index(tools: dict[str, Tool]) -> str:
    groups: dict[str, list[str]] = {}
    for name in sorted(tools):
        group = GUIDES.get(name, ("Other", "", ""))[0]
        groups.setdefault(group, []).append(name)
    lines = [
        "Mnemonic commands",
        'Call help({"topic":"<command>"}) for arguments; append usage for workflow,',
        'field names to drill down, or schema for full JSON Schema. Example:',
        'help({"topic":"complete_work completion_evidence artifact_references"})',
    ]
    lines += [group + ": " + ", ".join(names) for group, names in groups.items()]
    return "\n".join(lines)


def _fields(schema: Schema, root: Schema) -> list[str]:
    node = item_schema(schema, root)
    properties = node.get("properties", {})
    required = set(node.get("required", []))
    lines = []
    if required:
        lines.append("Required: " + "; ".join(
            name + ": " + type_label(value, root)
            for name, value in properties.items() if name in required
        ))
    optional = [name for name in properties if name not in required]
    if optional:
        lines.append("Optional: " + ", ".join(optional))
    if node.get("additionalProperties") is False:
        lines.append("Only the listed fields are accepted.")
    if choices := variants(node, root):
        selector = node.get("discriminator", {}).get("propertyName", "variant")
        lines.append(f"Choose {selector}: " + ", ".join(choices))
    return lines


def _notes(name: str, path: tuple[str, ...], schema: Schema, root: Schema) -> list[str]:
    lines = []
    if path and path[-1] in FIELD_NOTES:
        lines.append(FIELD_NOTES[path[-1]])
    if not path and name == "complete_work":
        lines.append("Fresh calls also require job_completion_report and explicit "
                     "subagent_transcripts; include any active lease_token.")
    node = item_schema(schema, root)
    if node.get("title") == "CommandVerificationInput":
        lines.append(FIELD_NOTES["exit_code"])
    return lines


def _page(tool: Tool, path: tuple[str, ...], schema: Schema) -> str:
    root = tool.inputSchema
    lines = [" ".join((tool.name, *path))]
    if not path:
        lines.append(GUIDES.get(tool.name, ("", "Registered MCP command.", ""))[1])
    else:
        lines.append("Type: " + type_label(schema, root))
        if nonnull(schema, root).get("type") == "array":
            lines.append("Fields below describe each array item.")
        if limits := constraints(schema, root):
            lines.append(limits)
        node = resolve(schema, root)
        if "default" in node:
            default = json.dumps(node["default"], separators=(",", ":"))
            lines.append("Default: " + (default if len(default) <= 120 else "see schema"))
    lines += _notes(tool.name, path, schema, root) + _fields(schema, root)
    if children(schema, root):
        lines.append("Drill down: append a listed field/variant to topic.")
    if not path:
        lines.append("Workflow: " + help_call(tool.name, ("usage",)))
    else:
        lines.append("Up: " + help_call(tool.name, path[:-1]))
    lines.append("Full schema: " + help_call(tool.name, (*path, "schema")))
    return "\n".join(lines)


def _usage(tool: Tool) -> str:
    guide = GUIDES.get(tool.name)
    lines = [tool.name + " usage", guide[2] if guide else "See the command's registered description."]
    required = tool.inputSchema.get("required", [])
    if "client_operation_id" in required:
        lines.append(FIELD_NOTES["client_operation_id"])
    if tool.name == "complete_work":
        lines.append("After an unknown outcome: at most one identical retry, then reconcile with safe "
                     "reads and request direction if still ambiguous.")
    lines.append("Arguments: " + help_call(tool.name))
    return "\n".join(lines)


def render_help(topic: str, tools: dict[str, Tool]) -> str:
    parts = topic.split()
    if not parts:
        return _index(tools)
    tool = tools.get(parts[0])
    if tool is None:
        return "Unknown command. Call " + help_call() + " to list commands."
    path = tuple(part.removesuffix("[]") for token in parts[1:] for part in token.split("."))
    if path == ("usage",):
        return _usage(tool)
    full_schema = bool(path and path[-1] == "schema")
    if full_schema:
        path = path[:-1]
    if len(path) > 12:
        return "Help paths support at most 12 levels. Start at " + help_call(tool.name) + "."
    location = locate(tool.inputSchema, path)
    if not location.complete:
        return "Unknown child topic; choose from the fields/variants below.\n" + _page(
            tool, location.path, location.schema,
        )
    if full_schema:
        return json.dumps(scoped_schema(location.schema, tool.inputSchema), separators=(",", ":"))
    return _page(tool, location.path, location.schema)


def register_help_tool(server: FastMCP) -> None:
    @server.tool(
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
        structured_output=False,
    )
    async def help(topic: Annotated[str, Field(max_length=400)] = "") -> str:
        """Compact command help, no project or API call. Empty topic lists commands. topic='<command>' shows arguments; append usage for workflow or field/variant names to drill down. topic='<command> schema' returns its full input JSON Schema; '<command> <field> schema' returns only that subtree. Example: topic='complete_work completion_evidence verification_results command'. Plain text, one page per call; schemas appear only on explicit request."""
        tools = {tool.name: tool for tool in await server.list_tools()}
        return render_help(topic, tools)
