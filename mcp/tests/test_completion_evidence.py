import base64
import copy
import importlib.metadata
import inspect
import io
import ipaddress
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import anyio
import httpx
import pytest
from conftest import (
    API_KEY,
    CHECKPOINT_ID,
    CLIENT_OPERATION_ID,
    NOW,
    PROJECT_ID,
    WORK_ID,
)
from jsonschema import Draft202012Validator, FormatChecker
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.message import SessionMessage
from mcp.types import CallToolResult, JSONRPCMessage, JSONRPCResponse
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from mnemonic_mcp.api import MnemonicAPI, TransportEffect
from mnemonic_mcp.models import (
    MAX_COMPLETION_EVENT_ID,
    MAX_COMPLETION_EXPECTED_VERSION,
    MAX_COMPLETION_WORK_VERSION,
    CompletionEvidenceArgument,
    CompletionEvidenceInput,
    CompletionEvidencePage,
    _validated_artifact_url,
)
from mnemonic_mcp.server import build_server, create_app
from mnemonic_mcp.transport import (
    COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES,
    MCP_REQUEST_MAX_BYTES,
    MCP_RESULT_MAX_BYTES,
    BoundedMCPIngressMiddleware,
    _bounded_stdin_reader,
    _send_stdio_record,
    validated_jsonrpc_document,
)
from mnemonic_mcp.validation import SanitizedFastMCP

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads(
    (REPOSITORY_ROOT / "tests" / "fixtures" / "completion-evidence-v1.json").read_text(
        encoding="utf-8"
    )
)
EVIDENCE_ADAPTER = TypeAdapter(CompletionEvidenceArgument)

VERIFICATION_ID = "670bdf0e-3ae5-4cff-b38a-0e0f2cff8d02"
ARTIFACT_ID = "939698f5-33aa-4210-bbc6-91df2799b2c7"
EVENT_ID = "481"
MCP_JSON_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}
MCP_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "phase-11-envelope-test", "version": "1.0"},
    },
}

EVIDENCE_INPUT = {
    "verification_results": [
        {
            "verification_type": "command",
            "name": "MCP tests",
            "outcome": "passed",
            "summary": "The focused suite completed successfully.",
            "command": "uv run pytest -q",
            "exit_code": 0,
            "observed_at": "2026-08-30T08:00:00-04:00",
            "observed_at_commit": "abcdef1",
        }
    ],
    "artifact_references": [
        {
            "artifact_type": "pull_request",
            "label": "Phase 11 pull request",
            "reference": "https://example.test/mnemonic/pull/11",
        }
    ],
}


def _structured(result):
    return result[1] if isinstance(result, tuple) else result


def _canonical_cursor(
    *,
    project_id: str = PROJECT_ID,
    work_item_id: str = WORK_ID,
    high_water: str = EVENT_ID,
    last_event: str = EVENT_ID,
) -> str:
    document = {
        "as_of_completion_event_id": high_water,
        "direction": "desc",
        "endpoint": "completion_evidence",
        "last_completion_event_id": last_event,
        "project_id": project_id,
        "v": 1,
        "work_item_id": work_item_id,
    }
    raw = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _worst_case_bounded_text(length: int) -> str:
    return "x" if length == 1 else "x" + "\x1b" * (length - 2) + "x"


def _checkpoint_pointer(
    checkpoint_id: str = CHECKPOINT_ID,
    *,
    maximum: bool = False,
) -> dict[str, object]:
    if maximum:
        return {
            "id": checkpoint_id,
            "work_item_id": WORK_ID,
            "kind": "completion",
            "source_client": _worst_case_bounded_text(80),
            "source_session_id": _worst_case_bounded_text(200),
            "source_model": _worst_case_bounded_text(120),
            "repository_branch": _worst_case_bounded_text(200),
            "verified_against": "f" * 64,
            "tags": [
                f"{index:02d}" + "\x1b" * 48
                for index in range(20)
            ],
            "migration_origin": "legacy-handoff-snapshot",
            "legacy_record_id": str(UUID(int=2**128 - 1)),
            "created_at": "2026-08-30T12:00:00.999999Z",
        }
    return {
        "id": checkpoint_id,
        "work_item_id": WORK_ID,
        "kind": "completion",
        "source_client": "codex",
        "source_session_id": "phase-11-session",
        "source_model": "gpt-5",
        "repository_branch": "work/phase11",
        "verified_against": "abcdef1",
        "tags": ["mcp"],
        "migration_origin": None,
        "legacy_record_id": None,
        "created_at": NOW,
    }


def _evidence_reads(checkpoint_id: str = CHECKPOINT_ID) -> tuple[dict, dict]:
    result = {
        **EVIDENCE_INPUT["verification_results"][0],
        "observed_at": NOW,
        "id": VERIFICATION_ID,
        "work_item_id": WORK_ID,
        "completion_checkpoint_id": checkpoint_id,
        "position": 0,
        "created_at": NOW,
    }
    artifact = {
        **EVIDENCE_INPUT["artifact_references"][0],
        "id": ARTIFACT_ID,
        "work_item_id": WORK_ID,
        "completion_checkpoint_id": checkpoint_id,
        "position": 0,
        "created_at": NOW,
    }
    return result, artifact


def _page(
    *,
    limit: int = 10,
    include_item: bool = True,
    lifecycle_status: str = "done",
    next_cursor: str | None = None,
) -> dict[str, object]:
    result, artifact = _evidence_reads()
    items = []
    if include_item:
        items.append(
            {
                "completion_event_id": EVENT_ID,
                "completion_checkpoint": _checkpoint_pointer(),
                "verification_results": [result],
                "artifact_references": [artifact],
            }
        )
    return {
        "work_item_id": WORK_ID,
        "work_version": 4,
        "lifecycle_status": lifecycle_status,
        "is_duplicate": False,
        "canonical_work_item_id": WORK_ID,
        "current_completion_checkpoint_id": (
            CHECKPOINT_ID if lifecycle_status == "done" and include_item else None
        ),
        "as_of_completion_event_id": EVENT_ID if include_item else None,
        "items": items,
        "total": len(items),
        "structured_completion_total": len(items),
        "limit": limit,
        "next_cursor": next_cursor,
    }


def _maximum_evidence_page() -> dict[str, object]:
    """Build ten exact-32,768-byte, 20-row episodes with worst-case JSON escaping."""
    items = []
    checkpoint_ids = [str(UUID(int=index + 1)) for index in range(10)]
    for episode_index, checkpoint_id in enumerate(checkpoint_ids):
        verification_results = []
        for position in range(20):
            summary_length = 1_624 if position < 8 else 1_623
            verification_results.append(
                {
                    "verification_type": "command",
                    "name": "n",
                    "outcome": "passed",
                    "summary": "x" + "\x01" * (summary_length - 1),
                    "command": "\x01",
                    "exit_code": 0,
                    "id": str(UUID(int=1_000 + episode_index * 20 + position)),
                    "work_item_id": WORK_ID,
                    "completion_checkpoint_id": checkpoint_id,
                    "position": position,
                    "created_at": "2026-08-30T12:00:00.999999Z",
                }
            )
        pointer = _checkpoint_pointer(checkpoint_id, maximum=True)
        items.append(
            {
                "completion_event_id": str(MAX_COMPLETION_EVENT_ID - episode_index),
                "completion_checkpoint": pointer,
                "verification_results": verification_results,
                "artifact_references": [],
            }
        )
    return {
        "work_item_id": WORK_ID,
        "work_version": 40,
        "lifecycle_status": "done",
        "is_duplicate": False,
        "canonical_work_item_id": WORK_ID,
        "current_completion_checkpoint_id": checkpoint_ids[0],
        "as_of_completion_event_id": str(MAX_COMPLETION_EVENT_ID),
        "items": items,
        "total": 10,
        "structured_completion_total": 10,
        "limit": 10,
        "next_cursor": None,
    }


def _compact_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _generated_evidence(case: dict[str, object]) -> dict[str, object]:
    generator = case["generator"]
    if generator == "repeat_valid_observation":
        return {
            "verification_results": [
                {
                    "verification_type": "observation",
                    "name": "Review",
                    "outcome": "passed",
                    "summary": "Reviewed.",
                }
                for _ in range(case["count"])
            ]
        }
    if generator == "fill_summary_to_aggregate_utf8_bytes":
        first = {
            "verification_type": "command",
            "name": "n",
            "outcome": "passed",
            "summary": "\U00010000" * 4_000,
            "command": "\U00010000" * 4_096,
            "exit_code": 0,
        }
        second = {
            "verification_type": "observation",
            "name": "n",
            "outcome": "passed",
            "summary": "s",
        }
        provisional = CompletionEvidenceInput.model_validate(
            {"verification_results": [first, second]}
        )
        current = sum(
            len(value.encode())
            for result in provisional.verification_results
            for value in (
                result.verification_type,
                result.name,
                result.outcome,
                result.summary,
                getattr(result, "command", ""),
            )
        )
        second["summary"] = "s" * (case["bytes"] - current + 1)
        return {"verification_results": [first, second]}
    if generator == "fill_text_with_u10000":
        return {
            "verification_results": [
                {
                    "verification_type": "observation",
                    "name": "Unicode boundary",
                    "outcome": "passed",
                    "summary": "\U00010000" * case["characters"],
                }
            ]
        }
    raise AssertionError(f"Unknown corpus generator: {generator}")


@pytest.mark.parametrize(
    "case",
    CONTRACT["cases"],
    ids=[case["case_id"] for case in CONTRACT["cases"]],
)
def test_shared_completion_evidence_contract(case):
    if not case["valid"]:
        with pytest.raises(ValidationError):
            EVIDENCE_ADAPTER.validate_python(case["semantic_input"])
        return

    evidence = EVIDENCE_ADAPTER.validate_python(case["semantic_input"])
    canonical = None if evidence.is_empty else evidence.model_dump(mode="json")
    assert canonical == case["canonical_output"]


@pytest.mark.parametrize(
    "case",
    CONTRACT["generated_boundaries"],
    ids=[case["case_id"] for case in CONTRACT["generated_boundaries"]],
)
def test_shared_completion_evidence_generated_boundaries(case):
    evidence = _generated_evidence(case)
    if case["valid"]:
        EVIDENCE_ADAPTER.validate_python(evidence)
    else:
        with pytest.raises(ValidationError):
            EVIDENCE_ADAPTER.validate_python(evidence)


def _assert_shared_concrete_schema_cases(schema_accepts) -> None:
    # Standard JSON Schema cannot compare selected properties across arbitrary
    # array items. x-unique-by exposes the sole stricter runtime pair rule.
    runtime_only_constraint = {"duplicate_artifact"}
    for case in CONTRACT["cases"]:
        accepted = schema_accepts(case["semantic_input"])
        if case["valid"]:
            assert accepted, case["case_id"]
        elif case["case_id"] not in runtime_only_constraint:
            assert not accepted, case["case_id"]
    duplicate_case = next(
        case for case in CONTRACT["cases"] if case["case_id"] == "duplicate_artifact"
    )
    assert schema_accepts(duplicate_case["semantic_input"])


def _assert_shared_generated_schema_cases(schema_accepts) -> None:
    for case in CONTRACT["generated_boundaries"]:
        accepted = schema_accepts(_generated_evidence(case))
        if case.get("error_class") == "aggregate_bytes":
            assert accepted
        else:
            assert accepted is case["valid"], case["case_id"]


def _assert_ipv6_artifact_schema_runtime_parity(reference_schema) -> None:
    validator = Draft202012Validator(reference_schema, format_checker=FormatChecker())

    def accepts(hostname: str, *, port: str = "") -> tuple[bool, bool]:
        url = f"https://[{hostname}]{port}/runs/1"
        schema_accepts = validator.is_valid(url)
        try:
            _validated_artifact_url(url)
        except ValueError:
            runtime_accepts = False
        else:
            runtime_accepts = True
        return schema_accepts, runtime_accepts

    nonzero_groups = ("1", "2", "3", "4", "5", "6", "7", "8")
    for mask in range(256):
        expanded = ":".join(
            "0" if mask & (1 << position) else nonzero_groups[position]
            for position in range(8)
        )
        canonical = str(ipaddress.IPv6Address(expanded))
        for hostname in {expanded, canonical}:
            expected = hostname == canonical
            assert accepts(hostname) == (expected, expected), (mask, hostname)
            assert accepts(hostname, port=":8443") == (expected, expected), (
                mask,
                hostname,
            )

    for hostname in (
        "2001:db8::1:2:3:4:5",
        "0::1",
        "1::0",
        "2001:0::1",
        "2001::0:1",
        "2001:0:0:1::1:1",
        "2001:0:0:1:2:3:4:5",
        "2001::1::1",
        "1:2:3:4:5:6:7:8:9",
        "2001:DB8::1",
        "2001:0db8::1",
        "::ffff:192.0.2.1",
        "fe80::1%eth0",
    ):
        assert accepts(hostname) == (False, False), hostname
        assert accepts(hostname, port=":8443") == (False, False), hostname

    for hostname in (
        "::",
        "::1",
        "1::",
        "0:1::",
        "::1:0",
        "2001:db8:0:1:2:3:4:5",
        "2001::1:0:0:1:1",
    ):
        assert accepts(hostname) == (True, True), hostname
        assert accepts(hostname, port=":8443") == (True, True), hostname


def _assert_shared_full_request_schema_cases(complete_validator, base_arguments) -> None:
    concrete_cases = {case["case_id"]: case for case in CONTRACT["cases"]}
    for case in CONTRACT["full_request_cases"]:
        arguments = {
            key: copy.deepcopy(value)
            for key, value in base_arguments.items()
            if key != "client_operation_id"
        }
        arguments["expected_version"] = case.get("expected_version", 1)
        evidence = case.get("completion_evidence", "__omitted__")
        evidence_case_id = case.get("completion_evidence_case_id")
        if evidence_case_id is not None:
            evidence = copy.deepcopy(
                concrete_cases[evidence_case_id]["semantic_input"]
            )
        if evidence != "__omitted__":
            arguments["completion_evidence"] = evidence
        operation_id = case["client_operation_id"]
        if operation_id != "__omitted__":
            arguments["client_operation_id"] = operation_id
        assert complete_validator.is_valid(arguments) is case["surface_expectations"][
            "mcp"
        ], case["case_id"]


def test_completion_evidence_count_and_byte_limits_are_inclusive():
    item = {
        "verification_type": "observation",
        "name": "n",
        "outcome": "passed",
        "summary": "s",
    }
    CompletionEvidenceInput.model_validate({"verification_results": [item] * 20})
    with pytest.raises(ValidationError, match="20"):
        CompletionEvidenceInput.model_validate({"verification_results": [item] * 21})

    commands = [
        {
            "verification_type": "command",
            "name": "n" * length,
            "outcome": "passed",
            "summary": "s" * 4000,
            "command": "c" * 4096,
            "exit_code": 0,
        }
        for length in (200, 130, 1, 1)
    ]
    CompletionEvidenceInput.model_validate({"verification_results": commands})
    commands[1]["name"] += "n"
    with pytest.raises(ValidationError, match="aggregate"):
        CompletionEvidenceInput.model_validate({"verification_results": commands})


async def test_completion_tools_list_schema_matches_runtime_evidence_contract(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    complete_input = tools["complete_work"].inputSchema
    complete_output = tools["complete_work"].outputSchema
    history_input = tools["list_completion_evidence"].inputSchema
    history_output = tools["list_completion_evidence"].outputSchema

    assert complete_input["properties"]["completion_evidence"] == {
        "$ref": "#/$defs/CompletionEvidenceInput",
        "title": "Completion Evidence",
    }
    assert complete_input["properties"]["expected_version"]["maximum"] == (
        MAX_COMPLETION_EXPECTED_VERSION
    )
    assert "completion_evidence" not in complete_input["required"]
    assert complete_output["properties"]["completion_evidence"] == {
        "$ref": "#/$defs/CompletionEvidencePayloadRead",
        "title": "Completion Evidence",
    }
    assert "completion_evidence" not in complete_output["required"]
    assert history_input["properties"]["cursor"] == {
        "maxLength": 4096,
        "minLength": 1,
        "pattern": "^[A-Za-z0-9_-]+$",
        "title": "Cursor",
        "type": "string",
    }
    assert "cursor" not in history_input["required"]
    assert history_output["properties"]["work_version"]["maximum"] == (
        MAX_COMPLETION_WORK_VERSION
    )
    assert history_output["properties"]["total"]["maximum"] == MAX_COMPLETION_EVENT_ID
    assert history_output["properties"]["structured_completion_total"]["maximum"] == (
        MAX_COMPLETION_EVENT_ID
    )

    canonical_timestamp = history_output["$defs"]["CommandVerificationRead"][
        "properties"
    ]["observed_at"]
    assert canonical_timestamp["maxLength"] == 27
    assert canonical_timestamp["pattern"].endswith(r"Z(?![\s\S])")
    pointer_timestamp = history_output["$defs"]["CheckpointPointer"][
        "properties"
    ]["created_at"]
    assert pointer_timestamp["pattern"] == canonical_timestamp["pattern"]
    event_id_schema = history_output["$defs"]["CompletionEvidenceEpisodeRead"][
        "properties"
    ]["completion_event_id"]
    event_id_validator = Draft202012Validator(event_id_schema)
    assert event_id_validator.is_valid(str(MAX_COMPLETION_EVENT_ID))
    assert not event_id_validator.is_valid(str(MAX_COMPLETION_EVENT_ID + 1))

    definitions = complete_input["$defs"]
    command_schema = definitions["CommandVerificationInput"]
    observation_schema = definitions["ObservationVerificationInput"]
    for schema, optional_fields in (
        (command_schema, ("exit_code", "observed_at", "observed_at_commit")),
        (observation_schema, ("observed_at", "observed_at_commit")),
    ):
        for field_name in optional_fields:
            field_schema = schema["properties"][field_name]
            assert "default" not in field_schema
            assert '"type": "null"' not in json.dumps(field_schema, sort_keys=True)

    assert command_schema["oneOf"] == [
        {
            "properties": {
                "outcome": {"const": "passed"},
                "exit_code": {"const": 0, "type": "integer"},
            },
            "required": ["exit_code"],
        },
        {
            "properties": {
                "outcome": {"const": "failed"},
                "exit_code": {
                    "anyOf": [
                        {
                            "minimum": -2147483648,
                            "maximum": -1,
                            "type": "integer",
                        },
                        {
                            "minimum": 1,
                            "maximum": 2147483647,
                            "type": "integer",
                        },
                    ]
                },
            },
            "required": ["exit_code"],
        },
        {
            "properties": {"outcome": {"const": "inconclusive"}},
            "not": {"required": ["exit_code"]},
        },
    ]

    artifact_branches = definitions["ArtifactReferenceInput"]["oneOf"]
    assert [branch["properties"]["artifact_type"] for branch in artifact_branches] == [
        {"const": "commit"},
        {"const": "branch"},
        {"const": "repository_path"},
        {
            "enum": [
                "pull_request",
                "test_run",
                "external_issue",
                "build_artifact",
            ]
        },
    ]
    assert artifact_branches[0]["properties"]["reference"] == {
        "maxLength": 64,
        "minLength": 7,
        "pattern": "^[0-9a-f]{7,64}$",
        "type": "string",
    }
    assert artifact_branches[1]["properties"]["reference"]["maxLength"] == 200
    assert artifact_branches[1]["properties"]["reference"]["x-utf8-max-bytes"] == 800
    assert artifact_branches[2]["properties"]["reference"]["maxLength"] == 512
    assert artifact_branches[3]["properties"]["reference"]["format"] == "uri"
    assert artifact_branches[3]["properties"]["reference"]["maxLength"] == 2000
    _assert_ipv6_artifact_schema_runtime_parity(
        artifact_branches[3]["properties"]["reference"]
    )

    evidence_schema = definitions["CompletionEvidenceInput"]
    artifact_collection_schema = evidence_schema["properties"]["artifact_references"]
    assert artifact_collection_schema["x-unique-by"] == [
        "artifact_type",
        "reference",
    ]
    assert evidence_schema["x-utf8-aggregate-max-bytes"] == 32_768
    assert len(evidence_schema["allOf"]) == 20
    assert evidence_schema["allOf"][0]["if"]["properties"] == {
        "verification_results": {"minItems": 1}
    }
    assert evidence_schema["allOf"][0]["then"]["properties"] == {
        "artifact_references": {"maxItems": 19}
    }
    assert evidence_schema["allOf"][-1]["then"]["properties"] == {
        "artifact_references": {"maxItems": 0}
    }
    payload_schema = complete_output["$defs"]["CompletionEvidencePayloadRead"]
    assert len(payload_schema["allOf"]) == 20
    assert payload_schema["anyOf"] == [
        {
            "properties": {"verification_results": {"minItems": 1}},
            "required": ["verification_results"],
        },
        {
            "properties": {"artifact_references": {"minItems": 1}},
            "required": ["artifact_references"],
        },
    ]

    Draft202012Validator.check_schema(complete_input)
    complete_validator = Draft202012Validator(
        complete_input,
        format_checker=FormatChecker(),
    )
    base_arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": 1,
        "checkpoint": {
            "prompt": "Complete.",
            "source_client": "codex",
            "source_session_id": "schema-test",
        },
        "client_operation_id": CLIENT_OPERATION_ID,
    }

    def schema_accepts(evidence: object) -> bool:
        return complete_validator.is_valid(
            {**base_arguments, "completion_evidence": evidence}
        )

    _assert_shared_concrete_schema_cases(schema_accepts)
    _assert_shared_generated_schema_cases(schema_accepts)
    _assert_shared_full_request_schema_cases(complete_validator, base_arguments)

    command = {
        "verification_type": "command",
        "name": "Tests",
        "outcome": "passed",
        "summary": "Observed.",
        "command": "pytest",
        "exit_code": 0,
    }
    assert schema_accepts({"verification_results": [command]})
    assert not schema_accepts(
        {"verification_results": [{key: value for key, value in command.items() if key != "exit_code"}]}
    )
    assert not schema_accepts(
        {"verification_results": [{**command, "outcome": "failed"}]}
    )
    assert schema_accepts(
        {"verification_results": [{**command, "outcome": "failed", "exit_code": 1}]}
    )
    assert schema_accepts(
        {
            "verification_results": [
                {
                    key: value
                    for key, value in {**command, "outcome": "inconclusive"}.items()
                    if key != "exit_code"
                }
            ]
        }
    )
    assert not schema_accepts(
        {
            "verification_results": [
                {**command, "outcome": "inconclusive", "exit_code": 1}
            ]
        }
    )
    assert not schema_accepts(
        {"verification_results": [{**command, "observed_at": None}]}
    )

    observation = {
        "verification_type": "observation",
        "name": "Review",
        "outcome": "passed",
        "summary": "Observed.",
    }
    commits = [
        {
            "artifact_type": "commit",
            "label": f"Commit {index}",
            "reference": f"{index:07x}",
        }
        for index in range(10)
    ]
    assert schema_accepts(
        {"verification_results": [observation] * 10, "artifact_references": commits}
    )
    assert not schema_accepts(
        {"verification_results": [observation] * 11, "artifact_references": commits}
    )
    assert not schema_accepts(None)
    Draft202012Validator.check_schema(history_input)
    Draft202012Validator.check_schema(history_output)
    history_validator = Draft202012Validator(
        history_input,
        format_checker=FormatChecker(),
    )
    assert history_validator.is_valid(
        {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    )
    assert not history_validator.is_valid(
        {"project_id": PROJECT_ID, "work_item_id": WORK_ID, "cursor": None}
    )


def test_complete_work_runtime_expected_version_is_strict_and_incrementable(settings):
    tool = build_server(settings)._tool_manager.get_tool("complete_work")
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": MAX_COMPLETION_EXPECTED_VERSION,
        "checkpoint": {
            "prompt": "Complete.",
            "source_client": "codex",
            "source_session_id": "version-bound-test",
        },
        "client_operation_id": CLIENT_OPERATION_ID,
    }
    model = tool.fn_metadata.arg_model
    assert model.model_validate(arguments).expected_version == MAX_COMPLETION_EXPECTED_VERSION
    for invalid in (MAX_COMPLETION_EXPECTED_VERSION + 1, "1", True):
        with pytest.raises(ValidationError):
            model.model_validate({**arguments, "expected_version": invalid})


async def test_complete_work_freezes_and_returns_ordered_evidence(
    settings, work_item, checkpoint
):
    checkpoint_input = {
        "prompt": "Completed the Phase 11 MCP contract.",
        "source_client": "codex",
        "source_session_id": "phase-11-session",
    }
    checkpoint_read = {
        **checkpoint,
        **checkpoint_input,
        "kind": "completion",
        "source_model": None,
        "source_session_url": None,
        "repository_branch": None,
        "verified_against": None,
        "tags": [],
        "source_metadata": {},
    }
    result_read, artifact_read = _evidence_reads()
    seen = []

    def handler(request):
        seen.append(request)
        assert json.loads(request.content) == {
            "client_operation_id": CLIENT_OPERATION_ID,
            "expected_version": 3,
            "checkpoint": {
                **checkpoint_input,
                "source_model": None,
                "source_session_url": None,
                "repository_branch": None,
                "verified_against": None,
                "tags": [],
                "source_metadata": {},
            },
            "completion_evidence": {
                **EVIDENCE_INPUT,
                "verification_results": [
                    {**EVIDENCE_INPUT["verification_results"][0], "observed_at": NOW}
                ],
            },
        }
        return httpx.Response(
            200,
            json={
                "work_item": {**work_item, "status": "done", "version": 4},
                "checkpoint": checkpoint_read,
                "completion_evidence": {
                    "verification_results": [result_read],
                    "artifact_references": [artifact_read],
                },
            },
        )

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(
        await server.call_tool(
            "complete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "checkpoint": checkpoint_input,
                "client_operation_id": CLIENT_OPERATION_ID,
                "completion_evidence": EVIDENCE_INPUT,
            },
        )
    )
    assert result["completion_evidence"]["verification_results"][0]["position"] == 0
    assert result["completion_evidence"]["artifact_references"][0]["position"] == 0
    assert len(seen) == 1


@pytest.mark.parametrize("evidence", [None, {}], ids=["omitted", "empty"])
async def test_complete_work_omits_empty_evidence(
    settings, work_item, checkpoint, evidence
):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "work_item": {**work_item, "status": "done", "version": 4},
                "checkpoint": {**checkpoint, "kind": "completion"},
            },
        )

    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": 3,
        "checkpoint": {
            "prompt": checkpoint["prompt"],
            "source_client": checkpoint["source_client"],
            "source_session_id": checkpoint["source_session_id"],
            "source_model": checkpoint["source_model"],
            "source_session_url": checkpoint["source_session_url"],
            "repository_branch": checkpoint["repository_branch"],
            "verified_against": checkpoint["verified_against"],
            "tags": checkpoint["tags"],
            "source_metadata": checkpoint["source_metadata"],
        },
        "client_operation_id": CLIENT_OPERATION_ID,
    }
    if evidence is not None:
        arguments["completion_evidence"] = evidence
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(await server.call_tool("complete_work", arguments))
    assert "completion_evidence" not in result
    assert "completion_evidence" not in seen[0]


async def test_complete_work_rejects_explicit_null_before_dispatch(settings):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="completion_evidence"):
        await server.call_tool(
            "complete_work",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "expected_version": 3,
                "checkpoint": {
                    "prompt": "Complete.",
                    "source_client": "codex",
                    "source_session_id": "session",
                },
                "client_operation_id": CLIENT_OPERATION_ID,
                "completion_evidence": None,
            },
        )
    assert seen == []


@pytest.mark.parametrize(
    "mode",
    [
        "absent",
        "null",
        "empty",
        "changed",
        "wrong-parent",
        "noncanonical-time",
        "duplicate-key",
        "unrequested",
    ],
)
async def test_complete_work_treats_incoherent_evidence_success_as_unknown(
    settings, work_item, checkpoint, mode
):
    checkpoint_input = {
        name: checkpoint[name]
        for name in (
            "prompt",
            "source_client",
            "source_session_id",
            "source_model",
            "source_session_url",
            "repository_branch",
            "verified_against",
            "tags",
            "source_metadata",
        )
    }
    result_read, artifact_read = _evidence_reads()
    document = {
        "work_item": {**work_item, "status": "done", "version": 4},
        "checkpoint": {**checkpoint, "kind": "completion"},
        "completion_evidence": {
            "verification_results": [result_read],
            "artifact_references": [artifact_read],
        },
    }
    if mode == "absent":
        document.pop("completion_evidence")
    elif mode == "null":
        document["completion_evidence"] = None
    elif mode == "empty":
        document["completion_evidence"] = {
            "verification_results": [],
            "artifact_references": [],
        }
    elif mode == "changed":
        result_read["summary"] = "A different valid assertion."
    elif mode == "wrong-parent":
        artifact_read["completion_checkpoint_id"] = VERIFICATION_ID
    elif mode == "noncanonical-time":
        result_read["observed_at"] = "2026-08-30T08:00:00-04:00"

    raw_response = _compact_json(document)
    if mode == "duplicate-key":
        raw_response = raw_response.replace(
            b'"completion_evidence":',
            b'"completion_evidence":null,"completion_evidence":',
            1,
        )
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, content=raw_response)

    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": 3,
        "checkpoint": checkpoint_input,
        "client_operation_id": CLIENT_OPERATION_ID,
    }
    if mode != "unrequested":
        arguments["completion_evidence"] = EVIDENCE_INPUT
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="operation may already have committed"):
        await server.call_tool("complete_work", arguments)
    assert attempts == 1


async def test_list_completion_evidence_sends_one_bounded_identity_get(settings):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=TrackingStream([json.dumps(_page()).encode()]))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(
        await server.call_tool(
            "list_completion_evidence",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )
    assert result["items"][0]["completion_event_id"] == EVENT_ID
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path.endswith(f"/{WORK_ID}/completion-evidence")
    assert dict(seen[0].url.params) == {"limit": "10"}


@pytest.mark.parametrize("status", [201, 206])
async def test_list_completion_evidence_requires_exact_http_200(settings, status):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status,
            stream=TrackingStream([_compact_json(_page())]),
        )

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool(
            "list_completion_evidence",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    assert attempts == 1


async def test_list_completion_evidence_preserves_cursor_and_limit(settings):
    cursor = _canonical_cursor()

    def handler(request):
        assert dict(request.url.params) == {"limit": "3", "cursor": cursor}
        page = _page(limit=3)
        page["items"][0]["completion_event_id"] = "480"
        page["current_completion_checkpoint_id"] = VERIFICATION_ID
        page["as_of_completion_event_id"] = EVENT_ID
        page["total"] = 2
        page["structured_completion_total"] = 2
        return httpx.Response(200, stream=TrackingStream([json.dumps(page).encode()]))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(
        await server.call_tool(
            "list_completion_evidence",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "limit": 3,
                "cursor": cursor,
            },
        )
    )
    assert result["items"][0]["completion_event_id"] == "480"
    assert result["as_of_completion_event_id"] == EVENT_ID


async def test_list_completion_evidence_rejects_explicit_null_cursor_before_dispatch(
    settings,
):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500)

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="cursor"):
        await server.call_tool(
            "list_completion_evidence",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "cursor": None,
            },
        )
    assert seen == []


@pytest.mark.parametrize(
    "mutate,cursor",
    [
        (
            lambda page: page.update(
                {"current_completion_checkpoint_id": VERIFICATION_ID}
            ),
            None,
        ),
        (lambda page: page.update({"as_of_completion_event_id": "482"}), None),
        (lambda page: None, _canonical_cursor()),
    ],
    ids=["wrong-current-pointer", "head-not-at-high-water", "cursor-boundary-repeated"],
)
async def test_list_completion_evidence_rejects_request_incoherent_success(
    settings, mutate, cursor
):
    document = _page()
    mutate(document)

    def handler(request):
        return httpx.Response(
            200,
            stream=TrackingStream([json.dumps(document).encode()]),
        )

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    arguments = {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    if cursor is not None:
        arguments["cursor"] = cursor
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool("list_completion_evidence", arguments)


async def test_list_completion_evidence_allows_done_null_tombstone_state(settings):
    document = _page()
    document["current_completion_checkpoint_id"] = None

    def handler(request):
        return httpx.Response(
            200,
            stream=TrackingStream([_compact_json(document)]),
        )

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(
        await server.call_tool(
            "list_completion_evidence",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )
    )
    assert result["lifecycle_status"] == "done"
    assert result["current_completion_checkpoint_id"] is None


async def test_completion_continuation_allows_newer_live_current_pointer(settings):
    cursor = _canonical_cursor()
    document = _page()
    document["items"][0]["completion_event_id"] = "480"
    document["current_completion_checkpoint_id"] = VERIFICATION_ID
    document["total"] = 2
    document["structured_completion_total"] = 2

    def handler(request):
        return httpx.Response(
            200,
            stream=TrackingStream([_compact_json(document)]),
        )

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    result = _structured(
        await server.call_tool(
            "list_completion_evidence",
            {
                "project_id": PROJECT_ID,
                "work_item_id": WORK_ID,
                "cursor": cursor,
            },
        )
    )
    assert result["items"][0]["completion_event_id"] == "480"
    assert result["current_completion_checkpoint_id"] == VERIFICATION_ID


def test_completion_evidence_page_strict_json_accepts_numeric_maxima():
    document = _page(limit=1, next_cursor=_canonical_cursor())
    document.update(
        {
            "work_version": MAX_COMPLETION_WORK_VERSION,
            "total": MAX_COMPLETION_EVENT_ID,
            "structured_completion_total": MAX_COMPLETION_EVENT_ID,
        }
    )

    page = CompletionEvidencePage.model_validate_json(
        json.dumps(document),
        strict=True,
    )

    assert page.work_version == MAX_COMPLETION_WORK_VERSION
    assert page.total == MAX_COMPLETION_EVENT_ID
    assert page.structured_completion_total == MAX_COMPLETION_EVENT_ID


@pytest.mark.parametrize(
    ("field_name", "maximum"),
    [
        ("work_version", MAX_COMPLETION_WORK_VERSION),
        ("total", MAX_COMPLETION_EVENT_ID),
        ("structured_completion_total", MAX_COMPLETION_EVENT_ID),
    ],
)
def test_completion_evidence_page_strict_json_rejects_numeric_overflow(
    field_name,
    maximum,
):
    document = _page()
    document[field_name] = maximum + 1

    with pytest.raises(ValidationError):
        CompletionEvidencePage.model_validate_json(json.dumps(document), strict=True)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"extra": "rejected"}),
        lambda value: value.update({"structured_completion_total": 0}),
        lambda value: value["items"][0]["verification_results"][0].update(
            {"work_item_id": "17956493-a5bc-49ae-a099-ead952f2dec8"}
        ),
        lambda value: value["items"][0]["artifact_references"][0].update(
            {"position": 1}
        ),
        lambda value: value["items"][0]["verification_results"][0].update(
            {"observed_at": "2026-08-30T08:00:00-04:00"}
        ),
        lambda value: value["items"][0]["verification_results"][0].update(
            {"created_at": "2026-08-30T08:00:00-04:00"}
        ),
        lambda value: value["items"][0]["artifact_references"][0].update(
            {"created_at": "2026-08-30T08:00:00-04:00"}
        ),
        lambda value: value["items"][0]["completion_checkpoint"].update(
            {"created_at": "2026-08-30T08:00:00-04:00"}
        ),
        lambda value: value["items"][0]["completion_checkpoint"].update(
            {"kind": "context"}
        ),
    ],
)
def test_completion_evidence_page_rejects_adversarial_successes(mutate):
    document = _page()
    mutate(document)
    with pytest.raises(ValidationError):
        CompletionEvidencePage.model_validate(document)


def _two_episode_page() -> dict[str, object]:
    document = _page()
    second = copy.deepcopy(document["items"][0])
    second_checkpoint_id = str(UUID(int=2_001))
    second["completion_event_id"] = "480"
    second["completion_checkpoint"]["id"] = second_checkpoint_id
    second["verification_results"][0].update(
        {
            "id": str(UUID(int=2_002)),
            "completion_checkpoint_id": second_checkpoint_id,
        }
    )
    second["artifact_references"][0].update(
        {
            "id": str(UUID(int=2_003)),
            "completion_checkpoint_id": second_checkpoint_id,
        }
    )
    document["items"].append(second)
    document["total"] = 2
    document["structured_completion_total"] = 2
    return document


async def test_list_completion_evidence_rejects_wrong_complete_head_structured_total(
    settings,
):
    document = _two_episode_page()
    document["items"][1]["verification_results"] = []
    document["items"][1]["artifact_references"] = []

    def handler(request):
        return httpx.Response(200, stream=TrackingStream([_compact_json(document)]))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="unexpected response"):
        await server.call_tool(
            "list_completion_evidence",
            {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        )


@pytest.mark.parametrize(
    "family",
    ["verification_results", "artifact_references"],
)
def test_completion_evidence_page_rejects_child_id_reuse_across_episodes(family):
    document = _two_episode_page()
    document["items"][1][family][0]["id"] = document["items"][0][family][0]["id"]
    with pytest.raises(ValidationError, match="child IDs"):
        CompletionEvidencePage.model_validate(document)


def test_completion_evidence_page_rejects_empty_nonzero_history():
    document = _page(include_item=False)
    document["as_of_completion_event_id"] = EVENT_ID
    document["total"] = 1
    with pytest.raises(ValidationError, match="must contain an episode"):
        CompletionEvidencePage.model_validate(document)


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, failure: Exception | None = None):
        self.chunks = chunks
        self.failure = failure
        self.pulls = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.pulls += 1
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed = True


async def _bounded_api_call(settings, handler):
    api = MnemonicAPI(settings, httpx.MockTransport(handler))
    return await api.request(
        "GET",
        f"projects/{PROJECT_ID}/work-items/{WORK_ID}/completion-evidence",
        params={"limit": 10},
        response_model=CompletionEvidencePage,
        effect=TransportEffect.SAFE_READ,
        strict_wire_response=True,
        bounded_identity_response=True,
    )


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize(
    "coding", ["gzip", "br", "deflate", "identity, gzip", "", "identity, identity"]
)
async def test_history_rejects_nonidentity_before_poison_body(
    settings, status, coding
):
    stream = TrackingStream([], failure=AssertionError("body was pulled"))

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            status,
            headers={"Content-Encoding": coding},
            stream=stream,
        )

    with pytest.raises(ToolError, match="safe read"):
        await _bounded_api_call(settings, handler)
    assert stream.pulls == 0
    assert stream.closed is True


@pytest.mark.parametrize("coding", [None, "IdEnTiTy"])
async def test_history_accepts_absent_or_mixed_case_identity(settings, coding):
    stream = TrackingStream([json.dumps(_page()).encode()])
    headers = {} if coding is None else {"Content-Encoding": coding}

    def handler(request):
        return httpx.Response(200, headers=headers, stream=stream)

    result = await _bounded_api_call(settings, handler)
    assert isinstance(result, CompletionEvidencePage)
    assert stream.pulls == 1
    assert stream.closed is True


@pytest.mark.parametrize(
    "value",
    ["+3145729", " 3145729", "3145729 ", "3_145_729"],
)
async def test_history_does_not_trust_invalid_oversize_content_length(settings, value):
    stream = TrackingStream([_compact_json(_page())])

    def handler(request):
        return httpx.Response(200, headers={"Content-Length": value}, stream=stream)

    result = await _bounded_api_call(settings, handler)
    assert isinstance(result, CompletionEvidencePage)
    assert stream.pulls == 1


async def test_history_accepts_exact_three_mib_identity_body(settings):
    document = json.dumps(_page(), separators=(",", ":")).encode()
    raw = document + b" " * (COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES - len(document))
    stream = TrackingStream([raw])

    def handler(request):
        return httpx.Response(200, stream=stream)

    result = await _bounded_api_call(settings, handler)
    assert isinstance(result, CompletionEvidencePage)
    assert stream.closed is True


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Length": "not-a-number"},
        {"Content-Length": "-1"},
        {"Content-Length": "1"},
        {"Transfer-Encoding": "chunked"},
    ],
)
async def test_history_rejects_streamed_three_mib_plus_one(settings, headers):
    document = json.dumps(_page(), separators=(",", ":")).encode()
    raw = document + b" " * (
        COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES + 1 - len(document)
    )
    stream = TrackingStream([raw])

    def handler(request):
        return httpx.Response(200, headers=headers, stream=stream)

    with pytest.raises(ToolError, match="safe read"):
        await _bounded_api_call(settings, handler)
    assert stream.closed is True


async def test_history_rejects_declared_oversize_without_pulling(settings):
    stream = TrackingStream([], failure=AssertionError("body was pulled"))

    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Length": str(COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES + 1)},
            stream=stream,
        )

    with pytest.raises(ToolError, match="safe read"):
        await _bounded_api_call(settings, handler)
    assert stream.pulls == 0
    assert stream.closed is True


async def test_history_rejects_max_plus_one_before_copying_second_chunk(settings):
    stream = TrackingStream(
        [b" " * COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES, b"x"],
    )

    def handler(request):
        return httpx.Response(200, headers={"Content-Length": "1"}, stream=stream)

    with pytest.raises(ToolError, match="safe read"):
        await _bounded_api_call(settings, handler)
    assert stream.pulls == 2
    assert stream.closed is True


async def test_history_decodes_utf8_only_after_stream_eof(settings):
    stream = TrackingStream([json.dumps(_page()).encode(), b"\xff"])

    def handler(request):
        return httpx.Response(200, stream=stream)

    with pytest.raises(ToolError, match="safe read"):
        await _bounded_api_call(settings, handler)
    assert stream.pulls == 2
    assert stream.closed is True


def _scope(headers=()):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("localhost", 8001),
        "root_path": "",
    }


async def _run_http_guard(raw: bytes, *, chunks: int = 1, headers=()):
    calls = []
    sent = []
    step = max(1, len(raw) // chunks)
    parts = [raw[index : index + step] for index in range(0, len(raw), step)]

    async def receive():
        part = parts.pop(0)
        return {
            "type": "http.request",
            "body": part,
            "more_body": bool(parts),
        }

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        calls.append(await receive())
        await JSONResponse({"ok": True})(scope, receive, send)

    middleware = BoundedMCPIngressMiddleware(downstream)
    await middleware(_scope(headers), receive, send)
    return calls, sent


def _padded_notification(size: int) -> bytes:
    raw = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


@pytest.mark.parametrize("chunks", [1, 37])
async def test_http_ingress_accepts_exact_one_mib(chunks):
    raw = _padded_notification(MCP_REQUEST_MAX_BYTES)
    calls, sent = await _run_http_guard(raw, chunks=chunks)
    assert calls[0]["body"] == raw
    assert sent[0]["status"] == 200


def test_real_streamable_http_accepts_exact_one_mib_initialize(settings):
    raw = _compact_json(MCP_INITIALIZE)
    request_body = raw + b" " * (MCP_REQUEST_MAX_BYTES - len(raw))

    with TestClient(create_app(settings), base_url="http://localhost:8001") as client:
        response = client.post(
            "/mcp",
            content=request_body,
            headers=MCP_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["id"] == MCP_INITIALIZE["id"]


@pytest.mark.parametrize("chunks", [1, 37])
async def test_http_ingress_rejects_one_mib_plus_one(chunks):
    raw = _padded_notification(MCP_REQUEST_MAX_BYTES + 1)
    calls, sent = await _run_http_guard(raw, chunks=chunks)
    assert calls == []
    assert sent[0]["status"] == 413


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"\xff",
        b"null",
        b"[]",
        b'[{"jsonrpc":"2.0"}]',
        b'{"jsonrpc":"2.0"}{"jsonrpc":"2.0"}',
        b'{"jsonrpc":"2.0","method":"ping","method":"tools/list"}',
        b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":1e999,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":null,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":1.0,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":"bad id","method":"ping"}',
    ],
)
async def test_http_ingress_rejects_transport_shapes_without_reflection(raw):
    calls, sent = await _run_http_guard(raw)
    assert calls == []
    assert sent[0]["status"] == 400
    body = next(message["body"] for message in sent if message["type"] == "http.response.body")
    assert raw not in body


async def test_http_ingress_rejects_nonidentity_before_receive():
    pulled = False

    async def receive():
        nonlocal pulled
        pulled = True
        raise AssertionError("body was pulled")

    sent = []

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("request was dispatched")

    middleware = BoundedMCPIngressMiddleware(downstream)
    await middleware(_scope([(b"content-encoding", b"gzip")]), receive, send)
    assert pulled is False
    assert sent[0]["status"] == 415


async def test_http_ingress_rejects_declared_oversize_before_receive():
    pulled = False

    async def receive():
        nonlocal pulled
        pulled = True
        raise AssertionError("body was pulled")

    sent = []

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        raise AssertionError("request was dispatched")

    middleware = BoundedMCPIngressMiddleware(downstream)
    length = str(MCP_REQUEST_MAX_BYTES + 1).encode()
    await middleware(_scope([(b"content-length", length)]), receive, send)
    assert pulled is False
    assert sent[0]["status"] == 413


@pytest.mark.parametrize(
    "value",
    ["+1048577", " 1048577", "1048577 ", "1_048_577"],
)
async def test_http_ingress_does_not_trust_invalid_content_length(value):
    raw = b'{"jsonrpc":"2.0","method":"notifications/initialized"}'
    calls, sent = await _run_http_guard(
        raw,
        headers=[(b"content-length", value.encode())],
    )
    assert calls[0]["body"] == raw
    assert sent[0]["status"] == 200


@pytest.mark.parametrize(
    "request_id",
    [-(2**63), 2**63 - 1, "a", "x" * 128, "A_z-9.:"],
)
def test_shared_request_id_guard_accepts_exact_domain(request_id):
    document = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "ping"})
    assert validated_jsonrpc_document(document.encode())["id"] == request_id


@pytest.mark.parametrize(
    "request_id",
    [-(2**63) - 1, 2**63, None, True, False, 1.5, "", "x" * 129, "bad id", "é"],
)
def test_shared_request_id_guard_rejects_outside_domain(request_id):
    document = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": "ping"})
    with pytest.raises(ValueError):
        validated_jsonrpc_document(document.encode())


def test_shared_request_id_guard_converts_python_huge_integer_failure_to_violation():
    document = b'{"jsonrpc":"2.0","id":' + b"9" * 5_000 + b',"method":"ping"}'
    with pytest.raises(ValueError, match="invalid UTF-8 or JSON"):
        validated_jsonrpc_document(document)


async def test_huge_integer_transport_failure_is_static_and_terminal():
    document = b'{"jsonrpc":"2.0","id":' + b"9" * 5_000 + b',"method":"ping"}'
    calls, sent = await _run_http_guard(document)
    assert calls == []
    assert sent[0]["status"] == 400
    body = next(message["body"] for message in sent if message["type"] == "http.response.body")
    assert document not in body
    assert await _stdio_values(document + b"\n") == []


async def _stdio_values(raw: bytes):
    sender, receiver = anyio.create_memory_object_stream(10)
    stdin = anyio.wrap_file(io.BytesIO(raw))
    await _bounded_stdin_reader(stdin, sender)
    values = []
    async with receiver:
        async for value in receiver:
            values.append(value)
    return values


async def test_stdio_adapter_accepts_exact_one_mib_record():
    values = await _stdio_values(_padded_notification(MCP_REQUEST_MAX_BYTES) + b"\n")
    assert len(values) == 1
    assert isinstance(values[0], SessionMessage)


async def test_stdio_adapter_rejection_is_terminal_and_discards_later_record():
    later = b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
    values = await _stdio_values(
        _padded_notification(MCP_REQUEST_MAX_BYTES + 1) + b"\n" + later
    )
    assert values == []


@pytest.mark.parametrize("raw", [b"[]\n", b"null\n", b"\xff\n", b"bad-json\n"])
async def test_stdio_adapter_transport_rejections_emit_nothing(raw):
    assert await _stdio_values(raw) == []


async def test_stdio_adapter_passes_object_semantic_errors_to_sdk_stream():
    with pytest.raises(ValidationError) as direct_validation:
        JSONRPCMessage.model_validate({})

    values = await _stdio_values(b"{}\n")
    assert len(values) == 1
    error = values[0]
    assert type(error) is type(direct_validation.value)
    assert isinstance(error, ValidationError)
    assert error.errors(include_url=False) == direct_validation.value.errors(
        include_url=False
    )


async def test_stdio_adapter_forwards_the_original_sdk_exception(monkeypatch):
    expected = RuntimeError("SDK semantic validation sentinel")

    def reject_document(cls, document):
        raise expected

    monkeypatch.setattr(JSONRPCMessage, "model_validate", classmethod(reject_document))
    sender, receiver = anyio.create_memory_object_stream(1)
    assert await _send_stdio_record(b"{}", sender) is True
    await sender.aclose()
    assert await receiver.receive() is expected


def _stdio_subprocess_environment() -> dict[str, str]:
    return {
        **os.environ,
        "MNEMONIC_API_KEY": API_KEY,
        "PYTHONPATH": str(REPOSITORY_ROOT / "mcp" / "src"),
    }


def test_real_stdio_semantic_error_emits_no_response_and_never_logs_caller_content():
    marker = "SEMANTIC_INVALID_STDIO_CALLER_MARKER_2c8f81"
    invalid = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": [marker],
    }
    valid = {**MCP_INITIALIZE, "id": 2}
    completed = subprocess.run(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        input=_compact_json(invalid) + b"\n" + _compact_json(valid) + b"\n",
        capture_output=True,
        check=False,
        timeout=10,
        env=_stdio_subprocess_environment(),
    )
    assert completed.returncode == 0
    records = completed.stdout.splitlines()
    assert len(records) == 1
    assert json.loads(records[0])["id"] == 2
    assert marker.encode() not in completed.stderr
    assert b"MCP stream message was invalid." in completed.stderr


@pytest.mark.parametrize(
    "invalid_record",
    [
        pytest.param(b"\xff\n", id="invalid-utf8"),
        pytest.param(b"not-json\n", id="invalid-json"),
        pytest.param(b"null\n", id="scalar"),
        pytest.param(b"[]\n", id="array"),
        pytest.param(b'[{"jsonrpc":"2.0","method":"ping"}]\n', id="batch"),
        pytest.param(
            b'{"jsonrpc":"2.0","id":null,"method":"ping"}\n',
            id="invalid-id",
        ),
    ],
)
def test_real_stdio_transport_rejection_is_terminal_before_later_record(
    invalid_record,
):
    later = _compact_json(MCP_INITIALIZE) + b"\n"
    completed = subprocess.run(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        input=invalid_record + later,
        capture_output=True,
        check=False,
        timeout=10,
        env=_stdio_subprocess_environment(),
    )
    assert completed.returncode == 0
    assert completed.stdout == b""


def test_real_stdio_entrypoint_accepts_exact_one_mib_record():
    process = subprocess.Popen(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_stdio_subprocess_environment(),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        raw = _compact_json(MCP_INITIALIZE)
        record = raw + b" " * (MCP_REQUEST_MAX_BYTES - len(raw))
        process.stdin.write(record + b"\n")
        process.stdin.flush()
        response_record = process.stdout.readline()
        process.stdin.close()
        returncode = process.wait(timeout=10)
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    assert returncode == 0, stderr.decode(errors="replace")
    assert json.loads(response_record)["id"] == MCP_INITIALIZE["id"]


def test_real_stdio_entrypoint_rejects_max_plus_one_and_later_record():
    valid = _compact_json(MCP_INITIALIZE) + b"\n"
    oversized = _padded_notification(MCP_REQUEST_MAX_BYTES + 1) + b"\n"
    completed = subprocess.run(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        input=oversized + valid,
        capture_output=True,
        check=False,
        timeout=10,
        env=_stdio_subprocess_environment(),
    )
    assert completed.returncode == 0
    assert completed.stdout == b""


def test_real_stdio_preserves_frame_completed_before_terminal_violation():
    process = subprocess.Popen(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_stdio_subprocess_environment(),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(_compact_json(MCP_INITIALIZE) + b"\n")
        process.stdin.flush()
        completed_frame = process.stdout.readline()
        assert json.loads(completed_frame)["id"] == MCP_INITIALIZE["id"]

        process.stdin.write(b"[]\n" + _compact_json({**MCP_INITIALIZE, "id": 2}) + b"\n")
        process.stdin.close()
        returncode = process.wait(timeout=10)
        remaining = process.stdout.read()
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    assert returncode == 0, stderr.decode(errors="replace")
    assert completed_frame.endswith(b"\n")
    assert remaining == b""


def test_real_stdio_valid_request_racing_terminal_violation_has_only_valid_frames():
    process = subprocess.Popen(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_stdio_subprocess_environment(),
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(_compact_json(MCP_INITIALIZE) + b"\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["id"] == MCP_INITIALIZE["id"]

        initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        ping = {"jsonrpc": "2.0", "id": 9, "method": "ping"}
        process.stdin.write(
            _compact_json(initialized) + b"\n" + _compact_json(ping) + b"\n[]\n"
        )
        process.stdin.close()
        returncode = process.wait(timeout=10)
        raced_output = process.stdout.read()
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    assert returncode == 0, stderr.decode(errors="replace")
    for record in raced_output.splitlines(keepends=True):
        if record.endswith(b"\n"):
            assert json.loads(record)["id"] == ping["id"]
        else:
            with pytest.raises((json.JSONDecodeError, UnicodeDecodeError)):
                json.loads(record)


def test_pinned_sdk_private_stdio_seam_is_explicit():
    assert importlib.metadata.version("mcp") == "1.29.1"
    source = inspect.getsource(SanitizedFastMCP.run_stdio_async)
    assert "bounded_stdio_server" in source
    assert "self._mcp_server.run" in source
    assert "create_initialization_options" in source
    constructor = inspect.getsource(SanitizedFastMCP.__init__)
    assert "server_version" in constructor
    assert "self._mcp_server.version = server_version" in constructor


async def test_largest_legal_complete_call_fits_one_mib_ingress(settings):
    prompt = _worst_case_bounded_text(100_000)
    results = [
        {
            "verification_type": "observation",
            "name": "n",
            "outcome": "passed",
            "summary": "x" + "\x01" * ((1_621 if position < 8 else 1_620) - 1),
        }
        for position in range(20)
    ]
    path_lengths = [512, *([252] * 62), 248]
    affected_paths = [
        f"p{index:02d}/" + "a" * (length - 4)
        for index, length in enumerate(path_lengths)
    ]
    source_session_url_prefix = "https://example.test/"
    source_metadata = {"m": "\x1b" * 2_729}
    assert sum(len(path) for path in affected_paths) == 16_384
    assert len(json.dumps(source_metadata, ensure_ascii=False).encode()) == 16_383
    assert len(
        json.dumps({"m": source_metadata["m"] + "\x1b"}, ensure_ascii=False).encode()
    ) > 16_384
    arguments = {
        "project_id": PROJECT_ID,
        "work_item_id": WORK_ID,
        "expected_version": MAX_COMPLETION_EXPECTED_VERSION,
        "checkpoint": {
            "prompt": prompt,
            "source_client": _worst_case_bounded_text(80),
            "source_session_id": _worst_case_bounded_text(200),
            "source_model": _worst_case_bounded_text(120),
            "source_session_url": source_session_url_prefix
            + "a" * (2_000 - len(source_session_url_prefix)),
            "repository_branch": _worst_case_bounded_text(200),
            "verified_against": "f" * 64,
            "affected_paths": affected_paths,
            "tags": [f"{index:02d}" + "\x1b" * 48 for index in range(20)],
            "source_metadata": source_metadata,
        },
        "client_operation_id": CLIENT_OPERATION_ID,
        "completion_evidence": {"verification_results": results},
        "lease_token": _worst_case_bounded_text(200),
    }
    evidence = CompletionEvidenceInput.model_validate(arguments["completion_evidence"])
    assert len(evidence.verification_results) == 20
    message = {
        "jsonrpc": "2.0",
        "id": "x" * 128,
        "method": "tools/call",
        "params": {"name": "complete_work", "arguments": arguments},
    }
    tool = build_server(settings)._tool_manager.get_tool("complete_work")
    validated_arguments = tool.fn_metadata.arg_model.model_validate(arguments)
    assert validated_arguments.completion_evidence is not None

    raw = _compact_json(message)
    assert len(raw) > 800_000
    assert len(raw) <= MCP_REQUEST_MAX_BYTES
    assert validated_jsonrpc_document(raw)["id"] == "x" * 128
    calls, sent = await _run_http_guard(raw, chunks=37)
    assert calls[0]["body"] == raw
    assert sent[0]["status"] == 200


async def test_typed_page_keeps_both_sdk_representations_under_twelve_mib(settings):
    page = _maximum_evidence_page()
    raw_page = _compact_json(page)
    assert len(raw_page) <= COMPLETION_EVIDENCE_RESPONSE_MAX_BYTES
    CompletionEvidencePage.model_validate_json(raw_page, strict=True)

    def handler(request):
        return httpx.Response(200, stream=TrackingStream([raw_page]))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    direct = await server.call_tool(
        "list_completion_evidence",
        {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
    )
    assert isinstance(direct, tuple)
    content, structured = direct
    result = CallToolResult(
        content=content,
        structuredContent=structured,
        isError=False,
    )
    response = JSONRPCResponse(
        jsonrpc="2.0",
        id="x" * 128,
        result=result.model_dump(by_alias=True, exclude_none=True),
    )
    record = response.model_dump_json(by_alias=True, exclude_none=True).encode() + b"\n"
    assert json.loads(content[0].text) == structured
    assert len(structured["items"]) == 10
    assert all(len(item["verification_results"]) == 20 for item in structured["items"])
    assert len(record) <= MCP_RESULT_MAX_BYTES


def test_maximum_page_fits_actual_streamable_http_result(settings):
    page = _maximum_evidence_page()
    raw_page = _compact_json(page)

    def handler(request):
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, stream=TrackingStream([raw_page]))

    app = create_app(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    call_id = "x" * 128
    request = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "list_completion_evidence",
            "arguments": {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        },
    }
    with TestClient(app, base_url="http://localhost:8001") as client:
        initialized = client.post(
            "/mcp",
            content=_compact_json(MCP_INITIALIZE),
            headers=MCP_JSON_HEADERS,
        )
        assert initialized.status_code == 200
        response = client.post(
            "/mcp",
            content=_compact_json(request),
            headers=MCP_JSON_HEADERS,
        )

    assert response.status_code == 200
    assert len(response.content) <= MCP_RESULT_MAX_BYTES
    result = response.json()["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert len(result["structuredContent"]["items"]) == 10


def test_maximum_page_fits_actual_stdio_result_record():
    page = _maximum_evidence_page()
    raw_page = _compact_json(page)
    requests = []

    class EvidenceHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(
                {
                    "path": self.path,
                    "accept_encoding": self.headers.get("Accept-Encoding"),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw_page)))
            self.end_headers()
            self.wfile.write(raw_page)

        def log_message(self, format, *args):
            pass

    rest_server = ThreadingHTTPServer(("127.0.0.1", 0), EvidenceHandler)
    rest_thread = threading.Thread(target=rest_server.serve_forever, daemon=True)
    rest_thread.start()
    call_id = "x" * 128
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    call = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "list_completion_evidence",
            "arguments": {"project_id": PROJECT_ID, "work_item_id": WORK_ID},
        },
    }
    source_dir = str(REPOSITORY_ROOT / "mcp" / "src")
    process = subprocess.Popen(
        [sys.executable, "-m", "mnemonic_mcp", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            **os.environ,
            "PYTHONPATH": source_dir,
            "MNEMONIC_API_KEY": API_KEY,
            "MNEMONIC_API_URL": f"http://127.0.0.1:{rest_server.server_port}",
        },
    )
    try:
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(_compact_json(MCP_INITIALIZE) + b"\n")
        process.stdin.flush()
        initialize_record = process.stdout.readline()
        assert json.loads(initialize_record)["id"] == 1

        process.stdin.write(_compact_json(initialized) + b"\n")
        process.stdin.write(_compact_json(call) + b"\n")
        process.stdin.flush()
        response_record = process.stdout.readline()
        process.stdin.close()
        returncode = process.wait(timeout=10)
        assert process.stderr is not None
        stderr = process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        rest_server.shutdown()
        rest_server.server_close()
        rest_thread.join()

    assert returncode == 0, stderr.decode(errors="replace")
    assert json.loads(response_record)["id"] == call_id
    assert response_record.endswith(b"\n")
    assert len(response_record) <= MCP_RESULT_MAX_BYTES
    result = json.loads(response_record)["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert requests == [
        {
            "path": (
                f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}/"
                "completion-evidence?limit=10"
            ),
            "accept_encoding": "identity",
        }
    ]


def test_context_resource_and_prompt_models_remain_evidence_free():
    from mnemonic_mcp.models import WorkContext

    schema = json.dumps(WorkContext.model_json_schema(), sort_keys=True)
    assert "completion_evidence" not in schema
    assert "verification_results" not in schema
    assert "artifact_references" not in schema


def test_http_middleware_order_authenticates_before_body_guard(settings):
    app = create_app(settings)
    stack = app.build_middleware_stack()
    names = []
    while hasattr(stack, "app"):
        names.append(type(stack).__name__)
        stack = stack.app
    assert names.index("LocalAccessMiddleware") < names.index(
        "BoundedMCPIngressMiddleware"
    )
