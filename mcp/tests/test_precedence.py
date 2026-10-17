"""Cycle evidence survives the adapter; malformed evidence is never rendered."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from mnemonic_mcp.api import _application_error_message
from mnemonic_mcp.models import Readiness
from mnemonic_mcp.precedence_models import DependencyIssue

PARENT = "00000000-0000-4000-8000-000000000001"
CHILD = "00000000-0000-4000-8000-000000000002"


def issue():
    return {
        "kind": "deadlock",
        "role": "cycle_member",
        "cycle_id": "a" * 64,
        "cycle_length": 2,
        "cycle": [
            {
                "relationship_id": None,
                "relationship_type": kind,
                "source_work_item_id": PARENT,
                "target_work_item_id": CHILD,
            }
            for kind in ["blocks", "parent-child"]
        ],
    }


def test_refusal_names_safe_ordered_path_and_ignores_server_prose():
    context = {"dependency_issue": issue(), "private": "secret work prose"}
    message = _application_error_message("relationship_cycle", context)
    assert PARENT in message and CHILD in message
    assert "blocks" in message and "child to parent" in message
    assert "secret" not in message
    broken = deepcopy(context)
    broken["dependency_issue"]["cycle"][0]["source_work_item_id"] = "secret work prose"
    assert "secret" not in _application_error_message("relationship_cycle", broken)
    with pytest.raises(ValidationError):
        DependencyIssue.model_validate(broken["dependency_issue"])


def test_deadlock_readiness_cannot_be_ready_or_ordinary_blocked(readiness):
    value = {
        **readiness,
        "dependency_issue": issue(),
        "display_state": "deadlocked",
        "is_ready": False,
    }
    assert Readiness.model_validate(value).dependency_issue.kind == "deadlock"
    with pytest.raises(ValidationError):
        Readiness.model_validate({**value, "is_ready": True})
    with pytest.raises(ValidationError):
        Readiness.model_validate({**value, "display_state": "blocked"})
    warning = {**readiness, "dependency_issue": {**issue(), "kind": "planning-cycle"}}
    assert Readiness.model_validate(warning).is_ready == readiness["is_ready"]


@pytest.mark.parametrize("mismatch", [False, True])
async def test_parent_replacement_binds_response_to_exact_requirement(
    settings,
    relationship,
    mismatch,
):
    import json
    from uuid import uuid4

    import httpx
    from mcp.server.fastmcp.exceptions import ToolError
    from test_tools import adapter

    old = str(uuid4())
    edge = {**relationship, "relationship_type": "parent-child", "requires_child_closeout": True}

    def handler(request):
        body = json.loads(request.content)
        assert body["requires_child_closeout"] is True
        assert body["replace_relationship_id"] == old
        result = {"relationship": edge, "created": True, "replaced_relationship_id": old}
        if mismatch:
            result["relationship"] = {**edge, "requires_child_closeout": False}
        return httpx.Response(200, json=result)

    server = adapter(settings, handler)
    arguments = {
        key: edge[key]
        for key in [
            "project_id",
            "relationship_type",
            "source_work_item_id",
            "target_work_item_id",
            "created_by_client",
            "created_by_session_id",
            "created_by_model",
        ]
    } | {
        "requires_child_closeout": True,
        "replace_relationship_id": old,
        "client_operation_id": str(uuid4()),
    }
    if mismatch:
        with pytest.raises(ToolError, match="may already have committed"):
            await server.call_tool("add_relationship", arguments)
    else:
        await server.call_tool("add_relationship", arguments)
