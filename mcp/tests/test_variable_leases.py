"""Variable duration forwarding and cold-safe status reads at the MCP boundary."""

import json

import httpx
import pytest
from conftest import CLAIM_REQUEST_ID, LEASE_TOKEN, OTHER_WORK_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_tools import adapter, structured

from mnemonic_mcp.lease_models import LeaseSettingsRead
from mnemonic_mcp.server import build_server


def _arguments(tool_name):
    scope = {"project_id": PROJECT_ID, "work_item_id": WORK_ID}
    if tool_name == "renew_claim":
        return {**scope, "lease_token": LEASE_TOKEN}
    return {
        **scope, "holder_client": "claude-code", "holder_session_id": "claiming-session",
        "claim_request_id": CLAIM_REQUEST_ID, "session_transcript": None,
    }


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall", "renew_claim"])
@pytest.mark.parametrize("minutes", [None, 10, 45, 180])
async def test_duration_is_forwarded_exactly_without_hardcoded_project_limits(
    settings, claim_receipt, active_work_context, tool_name, minutes,
):
    requests = []
    response = claim_receipt if tool_name != "claim_and_recall" else {
        "lease": claim_receipt, "context": active_work_context,
    }

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    arguments = _arguments(tool_name)
    if minutes is not None:
        arguments["lease_minutes"] = minutes
    await adapter(settings, handler).call_tool(tool_name, arguments)
    assert len(requests) == 1
    if minutes is None:
        assert "lease_minutes" not in requests[0]
    else:
        assert requests[0]["lease_minutes"] == minutes
        assert type(requests[0]["lease_minutes"]) is int


@pytest.mark.parametrize("tool_name", ["claim_work", "claim_and_recall", "renew_claim"])
@pytest.mark.parametrize("minutes", [None, True, False, 0, -1, 12.5, "15", 2147483648])
async def test_invalid_duration_is_rejected_before_backend(settings, tool_name, minutes):
    def handler(request):
        pytest.fail("Invalid duration reached the backend")

    with pytest.raises(ToolError, match="lease_minutes"):
        await adapter(settings, handler).call_tool(
            tool_name, {**_arguments(tool_name), "lease_minutes": minutes},
        )


async def test_status_only_preserves_current_settings_without_loading_context(settings, work_context):
    response = {
        "work_item_id": WORK_ID, "project_id": PROJECT_ID, "status": "pending", "version": 3,
        "readiness": work_context["readiness"],
        "lease_settings": {"default_minutes": 25, "minimum_minutes": 20, "maximum_minutes": 240},
    }
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}"
        assert dict(request.url.params) == {"status_only": "true"}
        return httpx.Response(200, json=response)

    actual = structured(await adapter(settings, handler).call_tool("get_work", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID, "status_only": True,
    }))
    assert actual == response
    assert len(requests) == 1


@pytest.mark.parametrize("corruption", ["wrong_work", "context", "invalid_settings"])
async def test_status_only_rejects_context_or_mismatched_metadata(
    settings, work_context, corruption,
):
    response = {
        "work_item_id": WORK_ID, "project_id": PROJECT_ID, "status": "pending", "version": 3,
        "readiness": work_context["readiness"], "lease_settings": work_context["lease_settings"],
    }
    if corruption == "wrong_work":
        response["work_item_id"] = OTHER_WORK_ID
        response["readiness"] = {**response["readiness"], "canonical_work_item_id": OTHER_WORK_ID}
    elif corruption == "context":
        response["summary"] = "Untrusted author rationale must never reach a cold reviewer."
    else:
        response["lease_settings"] = {
            "default_minutes": 15, "minimum_minutes": 20, "maximum_minutes": 120,
        }
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response)

    with pytest.raises(ToolError, match="unexpected response|outside the requested exact scope"):
        await adapter(settings, handler).call_tool("get_work", {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID, "status_only": True,
        })
    assert len(requests) == 1


async def test_catalog_exposes_optional_nonnullable_duration_and_two_exact_read_shapes(settings):
    tools = {tool.name: tool for tool in await build_server(settings).list_tools()}
    assert len(tools) == 54
    for name in ("claim_work", "claim_and_recall", "renew_claim"):
        schema = tools[name].inputSchema
        duration = schema["properties"]["lease_minutes"]
        if "$ref" in duration:
            duration = schema["$defs"][duration["$ref"].rsplit("/", 1)[1]]
        assert duration["type"] == "integer"
        assert duration["minimum"] == 1
        assert duration["maximum"] == 2147483647
        assert "lease_minutes" not in schema["required"]
    assert tools["get_work"].inputSchema["properties"]["status_only"]["default"] is False
    output = tools["get_work"].outputSchema
    assert output["type"] == "object"
    assert set(output["$defs"]["WorkStatusRead"]["properties"]) == {
        "work_item_id", "project_id", "status", "version", "readiness", "lease_settings",
    }
    assert "lease_settings" in tools["recall_work"].outputSchema["required"]


@pytest.mark.parametrize("values", [(15, 20, 120), (15, 10, 12), (True, 1, 120)])
def test_returned_lease_settings_must_be_strict_and_ordered(values):
    with pytest.raises(ValidationError):
        LeaseSettingsRead.model_validate(dict(zip(
            ["default_minutes", "minimum_minutes", "maximum_minutes"], values, strict=True,
        )))

