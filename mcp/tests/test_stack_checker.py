from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from mnemonic_mcp.config import Settings
from mnemonic_mcp.server import build_server

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def stack_checker() -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "check-stack.py"
    spec = importlib.util.spec_from_file_location("mnemonic_stack_checker", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stack_checker_requires_full_truthful_repository_declaration():
    checker = stack_checker()
    baseline = "A" * 40

    assert checker.full_commit_oid(baseline) == baseline.lower()
    assert checker.validated_repository_scope(
        baseline.lower(), ["src/**", "tests/test_*.py"]
    ) == (baseline.lower(), ["src/**", "tests/test_*.py"])
    with pytest.raises(argparse.ArgumentTypeError):
        checker.full_commit_oid("abcdef1")
    with pytest.raises(argparse.ArgumentTypeError):
        checker.validated_repository_scope(baseline.lower(), ["bad path"])


def test_stack_checker_pins_phase_10_rest_contract():
    checker = stack_checker()
    document = json.loads((REPOSITORY_ROOT / "docs" / "openapi.json").read_text())

    checker.validate_rest_contract(document)
    old_version = copy.deepcopy(document)
    old_version["info"]["version"] = "0.4.0"
    with pytest.raises(RuntimeError, match="REST API version"):
        checker.validate_rest_contract(old_version)
    required_scope = copy.deepcopy(document)
    required_scope["components"]["schemas"]["CheckpointCreate"]["required"].append(
        "affected_paths"
    )
    with pytest.raises(RuntimeError, match="incorrectly requires"):
        checker.validate_rest_contract(required_scope)
    required_baseline = copy.deepcopy(document)
    required_baseline["components"]["schemas"]["InitialCheckpointCreate"][
        "required"
    ].append("verified_against")
    with pytest.raises(RuntimeError, match="incorrectly requires"):
        checker.validate_rest_contract(required_baseline)
    nonempty_only = copy.deepcopy(document)
    nonempty_only["components"]["schemas"]["CheckpointCreate"]["properties"][
        "affected_paths"
    ]["minItems"] = 1
    with pytest.raises(RuntimeError, match="exact Phase 10"):
        checker.validate_rest_contract(nonempty_only)
    wrong_read_shape = copy.deepcopy(document)
    wrong_read_shape["components"]["schemas"]["CheckpointRead"]["properties"][
        "affected_paths"
    ]["type"] = "object"
    with pytest.raises(RuntimeError, match="full checkpoint reads"):
        checker.validate_rest_contract(wrong_read_shape)
    wrong_response = copy.deepcopy(document)
    wrong_response["paths"][
        "/api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints"
    ]["post"]["responses"]["201"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/CheckpointPointer"
    }
    with pytest.raises(RuntimeError, match="expected Phase 10 response"):
        checker.validate_rest_contract(wrong_response)
    leaked_pointer = copy.deepcopy(document)
    leaked_pointer["components"]["schemas"]["CheckpointPointer"]["properties"][
        "affected_paths"
    ] = {"type": "array"}
    with pytest.raises(RuntimeError, match="compact checkpoint pointers"):
        checker.validate_rest_contract(leaked_pointer)


async def test_stack_checker_pins_phase_10_mcp_contract():
    checker = stack_checker()
    server = build_server(Settings(api_key="x" * 32))
    tools = await server.list_tools()

    checker.validate_mcp_catalog(SimpleNamespace(tools=tools))
    required_scope = copy.deepcopy(tools)
    create_work = next(tool for tool in required_scope if tool.name == "create_work")
    create_work.inputSchema["$defs"]["CheckpointInput"]["required"].append(
        "affected_paths"
    )
    with pytest.raises(RuntimeError, match="incorrectly requires"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=required_scope))
    required_baseline = copy.deepcopy(tools)
    complete_work = next(
        tool for tool in required_baseline if tool.name == "complete_work"
    )
    complete_work.inputSchema["$defs"]["CheckpointInput"]["required"].append(
        "verified_against"
    )
    with pytest.raises(RuntimeError, match="incorrectly requires"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=required_baseline))
    leaked_pointer = copy.deepcopy(tools)
    search_work = next(tool for tool in leaked_pointer if tool.name == "search_work")
    search_work.outputSchema["$defs"]["CheckpointPointer"]["properties"][
        "affected_paths"
    ] = {"type": "array"}
    with pytest.raises(RuntimeError, match="compact pointer exposes"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=leaked_pointer))
    wrong_create_response = copy.deepcopy(tools)
    create_work = next(
        tool for tool in wrong_create_response if tool.name == "create_work"
    )
    create_work.outputSchema["properties"]["initial_checkpoint"] = {
        "$ref": "#/$defs/CheckpointPointer"
    }
    with pytest.raises(RuntimeError, match="does not bind the full checkpoint"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=wrong_create_response))
    wrong_add_response = copy.deepcopy(tools)
    add_checkpoint = next(
        tool for tool in wrong_add_response if tool.name == "add_checkpoint"
    )
    add_checkpoint.outputSchema["title"] = "CheckpointPointer"
    with pytest.raises(RuntimeError, match="full response|not the full checkpoint"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=wrong_add_response))
    wrong_complete_response = copy.deepcopy(tools)
    complete_work = next(
        tool for tool in wrong_complete_response if tool.name == "complete_work"
    )
    complete_work.outputSchema["properties"]["checkpoint"] = {
        "$ref": "#/$defs/CheckpointPointer"
    }
    with pytest.raises(RuntimeError, match="does not bind the full checkpoint"):
        checker.validate_mcp_catalog(SimpleNamespace(tools=wrong_complete_response))
