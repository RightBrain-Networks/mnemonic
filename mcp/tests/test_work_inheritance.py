"""Forward only explicit exclusions and reject mismatched creation receipts."""

import json

import httpx
import pytest
from conftest import OTHER_CHECKPOINT_ID, OTHER_WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, structured
from test_work_lineage import base_creation as lineage_base_creation
from test_work_lineage import creation_arguments, creation_response

base_creation = lineage_base_creation


def excluded_response(base_creation):
    response = creation_response(base_creation)
    response["work_item"]["decision_change_inheritance_exclusion"] = {
        "origin_work_item_id": OTHER_WORK_ID, "origin_context_checkpoint_id": OTHER_CHECKPOINT_ID,
        "reason": "An independent follow-up.", "created_at": response["work_item"]["created_at"],
    }
    return response


async def test_default_true_preserves_historical_payload(settings, base_creation):
    response = creation_response(base_creation)
    def handler(request):
        body = json.loads(request.content)
        assert "inherit_decision_change" not in body
        assert "decision_change_inheritance_reason" not in body
        return httpx.Response(201, json=response)
    result = await adapter(settings, handler).call_tool("create_work", {
        **creation_arguments(), "inherit_decision_change": True,
    })
    assert structured(result) == response


async def test_false_roundtrip_retains_exact_reason(settings, base_creation):
    response = excluded_response(base_creation)
    reason = "  An independent follow-up.\n"
    response["work_item"]["decision_change_inheritance_exclusion"]["reason"] = reason
    def handler(request):
        body = json.loads(request.content)
        assert body["inherit_decision_change"] is False
        assert body["decision_change_inheritance_reason"] == reason
        return httpx.Response(201, json=response)
    result = await adapter(settings, handler).call_tool("create_work", {
        **creation_arguments(), "inherit_decision_change": False,
        "decision_change_inheritance_reason": reason,
    })
    assert structured(result) == response


@pytest.mark.parametrize("fault", ["missing", "reason", "origin", "checkpoint", "created_at"])
async def test_mismatched_exclusions_are_unknown_write_outcomes(settings, base_creation, fault):
    response = excluded_response(base_creation)
    exclusion = response["work_item"]["decision_change_inheritance_exclusion"]
    if fault == "missing":
        del response["work_item"]["decision_change_inheritance_exclusion"]
    elif fault == "origin":
        exclusion["origin_work_item_id"] = response["work_item"]["id"]
    elif fault == "checkpoint":
        exclusion["origin_context_checkpoint_id"] = response["initial_checkpoint"]["id"]
    else:
        exclusion[fault] = "Different" if fault == "reason" else "2020-01-01T00:00:00Z"
    with pytest.raises(ToolError, match="unknown"):
        await adapter(settings, lambda request: httpx.Response(201, json=response)).call_tool(
            "create_work", {**creation_arguments(), "inherit_decision_change": False,
                            "decision_change_inheritance_reason": "An independent follow-up."},
        )


async def test_unrequested_exclusion_is_unknown(settings, base_creation):
    response = excluded_response(base_creation)
    with pytest.raises(ToolError, match="unknown"):
        await adapter(settings, lambda request: httpx.Response(201, json=response)).call_tool(
            "create_work", creation_arguments(),
        )


@pytest.mark.parametrize("reason", [" ", "x" * 4001, "bad\x00text", 3])
async def test_reason_contract_refuses_invalid_text_before_http(settings, reason):
    def handler(request):
        raise AssertionError("Invalid typed text must not dispatch")
    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("create_work", {
            **creation_arguments(), "inherit_decision_change": False,
            "decision_change_inheritance_reason": reason,
        })
