"""Rejected tools explain their accepted shape without exposing caller data."""

import copy
import json

import httpx
import pytest
from conftest import (
    API_KEY,
    CLIENT_OPERATION_ID,
    LOCAL_VALIDATION_CASES,
    PRIVATE_EXTRA_FIELD,
    PRIVATE_EXTRA_VALUE,
    PROJECT_ID,
    WORK_ID,
)
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.input_schema import InputValidationError, compact_input_schema
from mnemonic_mcp.server import build_server


def schema_from_error(message, name):
    _, hint = message.split(f"\nInput schema for {name} ", 1)
    header, encoded = hint.split("\n", 1)
    assert "all validation rules still apply" in header
    return json.loads(encoded)


def reject_http(request):
    pytest.fail("Locally invalid arguments must not reach the API")


def create_work_arguments():
    return {
        "project_id": PROJECT_ID, "title": "Example", "summary": "An example.",
        "client_operation_id": CLIENT_OPERATION_ID,
        "initial_checkpoint": {
            "prompt": "An example.", "source_client": "codex", "source_session_id": "s",
        },
    }


async def test_every_tool_rejection_carries_its_registered_input_shape(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    for tool in await server.list_tools():
        with pytest.raises(ToolError) as caught:
            await server.call_tool(tool.name, {PRIVATE_EXTRA_FIELD: PRIVATE_EXTRA_VALUE})
        message = str(caught.value)
        schema = schema_from_error(message, tool.name)
        Draft202012Validator.check_schema(schema)
        assert set(schema["properties"]) == set(tool.inputSchema["properties"])
        assert schema.get("required") == tool.inputSchema.get("required")
        assert schema["additionalProperties"] is False
        assert len(message) < 12_000
        assert PRIVATE_EXTRA_FIELD not in message
        assert PRIVATE_EXTRA_VALUE not in message
        assert API_KEY not in message


async def test_complete_work_error_contains_usable_nested_schema(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("complete_work", LOCAL_VALIDATION_CASES[0][1])
    schema = schema_from_error(str(caught.value), "complete_work")
    assert set(schema["required"]) == {
        "project_id", "work_item_id", "expected_version", "checkpoint", "client_operation_id",
    }
    assert "actor_client" not in schema["properties"]
    assert "actor_session_id" not in schema["properties"]
    definitions = schema["$defs"]
    assert definitions["CheckpointInput"]["required"] == [
        "prompt", "source_client", "source_session_id",
    ]
    assert definitions["ArtifactReferenceInput"]["required"] == [
        "artifact_type", "label", "reference",
    ]
    assert definitions["CommandVerificationInput"]["properties"]["verification_type"] == {
        "const": "command", "type": "string",
    }
    valid = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": 3,
        "checkpoint": {"prompt": "Completed.", "source_client": "codex", "source_session_id": "s"},
        "client_operation_id": CLIENT_OPERATION_ID,
        "subagent_transcripts": None,
        "job_completion_report": {"summary": "Completed.", "fyi_items": [], "prompt_revision": "r"},
        "completion_evidence": {
            "artifact_references": [
                {"artifact_type": "commit", "label": "Change", "reference": "a" * 40},
            ],
            "verification_results": [{
                "verification_type": "command", "name": "Tests", "outcome": "passed",
                "summary": "Tests passed.", "command": "pytest", "exit_code": 0,
            }],
        },
    }
    validator = Draft202012Validator(schema)
    validator.validate(valid)
    assert not validator.is_valid(LOCAL_VALIDATION_CASES[0][1])
    invalid = copy.deepcopy(valid)
    invalid["completion_evidence"]["verification_results"][0].pop("exit_code")
    assert not validator.is_valid(invalid)  # Preserve conditional command requirements.
    invalid = {**valid, "completion_evidence": None}
    assert not validator.is_valid(invalid)  # Optional does not mean nullable.
    valid["completion_evidence"]["verification_results"] = [{
        "verification_type": "observation", "name": "Review", "outcome": "passed",
        "summary": "Reviewed the result.",
    }]
    validator.validate(valid)  # Both discriminated variants resolve locally.


@pytest.mark.parametrize("detail", [
    [{"loc": ["body", "summary"], "type": "string_too_long",
      "input": PRIVATE_EXTRA_VALUE, "msg": PRIVATE_EXTRA_VALUE}],
    {"code": "work_summary_too_long", "message": PRIVATE_EXTRA_VALUE,
     "context": {"max_chars": 2048}},
])
async def test_api_input_rejection_includes_mcp_schema_and_stays_sanitized(settings, detail):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(422, json={"detail": detail})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("create_work", create_work_arguments())
    message = str(caught.value)
    schema = schema_from_error(message, "create_work")
    assert "title" in schema["required"]
    assert "summary" in schema["required"]
    assert PRIVATE_EXTRA_VALUE not in message
    assert len(requests) == 1


@pytest.mark.parametrize("name, arguments, guidance, fields", [
    ("search_transcript_contents", {"project_id": PROJECT_ID},
     "Supply query or its q alias", {"query", "q"}),
    ("search", {}, "Supply exactly one of project_id or project_ids", {"project_id", "project_ids"}),
    ("update_work", {"changes": {"title": "New title"}, "subagent_transcripts": []},
     "Subagent transcript assertions require a closeout transition", {"changes"}),
])
async def test_manual_input_rejection_retains_guidance_and_adds_schema(
    settings, name, arguments, guidance, fields,
):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool(name, arguments)
    message = str(caught.value)
    assert guidance in message
    schema = schema_from_error(message, name)
    assert fields <= schema["properties"].keys()


async def test_superseding_error_guidance_is_not_replaced_by_suppressed_input_error(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))

    @server.tool()
    async def superseded() -> str:
        try:
            raise InputValidationError("An earlier input rejection.")
        except InputValidationError:
            raise ToolError(UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME) from None

    with pytest.raises(ToolError) as caught:
        await server.call_tool("superseded", {})
    message = str(caught.value)
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert "Input schema" not in message
    assert "earlier input rejection" not in message


@pytest.mark.parametrize("response", [
    httpx.Response(409, json={"detail": {"code": "version_conflict", "context": {}}}),
    httpx.Response(503),
    httpx.Response(201, json={"unexpected": "malformed success"}),
])
async def test_execution_and_response_failures_do_not_claim_input_is_wrong(settings, response):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(lambda _: response)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("create_work", create_work_arguments())
    message = str(caught.value)
    assert "Input schema" not in message
    if response.status_code != 409:
        assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message


def test_compaction_preserves_keyword_named_fields_literals_and_recursive_refs():
    schema = {
        "title": "Remove this annotation",
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "default": "Omit this default"},
            "description": {"const": {"title": "Keep literal values", "default": 1}},
            "examples": {"$ref": "#/$defs/Node"},
        },
        "required": ["title"],
        "$defs": {"Node": {"anyOf": [
            {"type": "null"}, {"type": "array", "items": {"$ref": "#/$defs/Node"}},
        ]}},
        "dependentSchemas": {"title": {"required": ["description"]}},
    }
    original = copy.deepcopy(schema)
    compact = compact_input_schema(schema)
    assert schema == original
    assert "title" not in compact
    assert compact["properties"]["title"] == {"type": "string", "minLength": 1}
    assert compact["properties"]["description"] == schema["properties"]["description"]
    validator = Draft202012Validator(compact)
    validator.validate({"title": "x", "description": {"title": "Keep literal values", "default": 1},
                        "examples": [None, [None]]})
    assert not validator.is_valid({"title": "x"})


def test_compaction_marks_omitted_patterns_and_retains_short_ones():
    assert compact_input_schema({"type": "string", "pattern": "x" * 121}) == {
        "type": "string", "x-pattern-omitted": True,
    }
    assert compact_input_schema({"type": "string", "pattern": "^[a-z]+$"}) == {
        "type": "string", "pattern": "^[a-z]+$",
    }
