"""Generate fresh MCP retry IDs and return them outside durable domain content."""

from typing import Any
from uuid import UUID, uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict

RETRY_ID_FIELDS = ("client_operation_id", "claim_request_id")
AUTOMATIC_ID_GUIDANCE = (
    "Omit the retry ID on a fresh call to have Mnemonic mint a UUID. Retain the returned "
    "mnemonic_generated_ids and all arguments privately. For an uncertain outcome, supply "
    "that ID explicitly with every other argument unchanged. Never repeat a keyless call "
    "after losing its response; reconcile with safe reads and request direction. "
    "Use generate_uuid for a one-off UUID needed before a call."
)


def new_uuid() -> str:
    return str(uuid4())


def allow_automatic_ids(argument_model: type[BaseModel]) -> None:
    """Keep strict explicit-ID types while allowing omission in the MCP schema."""
    for name in RETRY_ID_FIELDS:
        field = argument_model.model_fields.get(name)
        if field is not None:
            field.default_factory = new_uuid
            field.description = AUTOMATIC_ID_GUIDANCE


def prepare_ids(
    properties: dict[str, Any], arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    generated = {name: new_uuid() for name in RETRY_ID_FIELDS
                 if name in properties and name not in arguments}
    return {**arguments, **generated}, generated


def generated_id_result(result: Any, generated: dict[str, str]) -> CallToolResult:
    """Expose recovery controls in the MCP envelope, never in the domain response."""
    if isinstance(result, CallToolResult):
        envelope = result.model_copy(deep=True)
    elif isinstance(result, tuple):
        content, structured = result
        envelope = CallToolResult(content=content, structuredContent=structured)
    else:
        envelope = CallToolResult(content=result)
    envelope.meta = {**(envelope.meta or {}), "mnemonic_generated_ids": generated}
    controls = ", ".join(f"{name}={value}" for name, value in generated.items())
    envelope.content.append(TextContent(
        type="text",
        text=(f"Mnemonic generated {controls}. Keep this retry ID privately with the original "
              "arguments. If the outcome is uncertain, supply this ID explicitly and keep every "
              "other argument unchanged; do not repeat the call with the ID omitted. "
              "A definitive rejection requires a corrected new intent, not an exact retry."),
    ))
    return envelope


class GeneratedUUID(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uuid: UUID


def register_uuid_tool(server: FastMCP) -> None:
    @server.tool(annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=False, openWorldHint=False,
    ))
    async def generate_uuid() -> GeneratedUUID:
        """Mint one random UUIDv4 locally for use with Mnemonic. Takes no arguments or project and creates no stored record. Each call returns a new UUID; it does not recover a lost retry ID. Fresh writes and claims already mint their retry IDs when omitted. Use this tool when a UUID must be retained before a call, and preserve it with the exact arguments for retries."""
        return GeneratedUUID(uuid=uuid4())
