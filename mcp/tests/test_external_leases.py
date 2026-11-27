"""Resource reservations over the MCP boundary, including exact retry controls."""

import json
from uuid import UUID

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, EXPIRES_AT, LEASE_TOKEN, NOW, PROJECT_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter

RESOURCE_ID = "05e4e932-0c44-4e32-9be6-a44a24034e57"
LEASE_ID = "82fb3641-7b20-442d-8173-e92506080b0c"
SCOPE = {"project_id": PROJECT_ID, "resource_id": RESOURCE_ID}
HOLDER = {"holder_client": "codex", "holder_session_id": "native-thread"}
BASE = f"/api/v1/projects/{PROJECT_ID}/external-resources"
LEASE = {
    "id": LEASE_ID, "resource_id": RESOURCE_ID, **HOLDER, "quantity": 8,
    "acquired_at": NOW, "expires_at": EXPIRES_AT,
}
RESOURCE = {
    "id": RESOURCE_ID, "project_id": PROJECT_ID, "name": "pytest workers", "capacity": 16,
    "minimum_minutes": 10, "default_minutes": 15, "maximum_minutes": 120,
    "available": 8, "leases": [LEASE],
}


async def test_status_excludes_private_capabilities(settings):
    document = {"project_id": PROJECT_ID, "items": [RESOURCE]}

    def handler(request):
        assert request.method == "GET" and request.url.path == BASE
        return httpx.Response(200, json=document)

    server = adapter(settings, handler)
    args = {"project_id": PROJECT_ID}
    assert (await server.call_tool("list_external_resources", args))[1] == document
    document["items"] = [{**RESOURCE, "leases": [{**LEASE, "lease_token": LEASE_TOKEN}]}]
    with pytest.raises(ToolError, match="unexpected response") as raised:
        await server.call_tool("list_external_resources", args)
    assert LEASE_TOKEN not in str(raised.value)


async def test_claim_then_retry_can_recover_current_lease_after_resize(settings):
    seen = []
    def handler(request):
        assert request.method == "POST" and request.url.path == f"{BASE}/{RESOURCE_ID}/claim"
        body = json.loads(request.content)
        seen.append(body)
        assert body == {**HOLDER, "claim_request_id": body["claim_request_id"], "quantity": 8}
        return httpx.Response(200, json={
            **LEASE, "claim_quantity": 8, "quantity": 8 if len(seen) == 1 else 4,
            "claim_request_id": body["claim_request_id"], "lease_token": LEASE_TOKEN,
        })

    server = adapter(settings, handler)
    args = {**SCOPE, **HOLDER, "quantity": 8}
    claim = await server.call_tool("claim_external_lease", args)
    assert not claim.isError, claim.content
    assert claim.structuredContent["lease_token"] == LEASE_TOKEN
    retry = claim.meta["mnemonic_generated_ids"]
    assert UUID(retry["claim_request_id"]).version == 4
    current = (await server.call_tool("claim_external_lease", {**args, **retry}))[1]
    assert seen[0] == seen[1]
    assert current["id"] == LEASE_ID and current["quantity"] == 4 and current["claim_quantity"] == 8


@pytest.mark.parametrize("fields", [{}, {"lease_minutes": 90}, {"quantity": 3}, {"quantity": 10, "lease_minutes": 30}])
async def test_renew_sends_exact_duration_and_absolute_quantity(settings, fields):
    args = {**SCOPE, "lease_id": LEASE_ID, "lease_token": LEASE_TOKEN, **fields}
    def handler(request):
        assert request.url.path == f"{BASE}/{RESOURCE_ID}/leases/{LEASE_ID}/renew"
        body = json.loads(request.content)
        assert body == {
            "client_operation_id": body["client_operation_id"], "lease_token": LEASE_TOKEN, **fields,
        }
        return httpx.Response(200, json={**LEASE, "quantity": fields.get("quantity", 8)})

    result = await adapter(settings, handler).call_tool("renew_external_lease", args)
    assert result.structuredContent["quantity"] == fields.get("quantity", 8)
    assert UUID(result.meta["mnemonic_generated_ids"]["client_operation_id"]).version == 4


async def test_release_returns_receipt_for_requested_lease(settings):
    def handler(request):
        assert request.url.path == f"{BASE}/{RESOURCE_ID}/leases/{LEASE_ID}/release"
        assert json.loads(request.content) == {
            "lease_token": LEASE_TOKEN, "client_operation_id": CLIENT_OPERATION_ID,
        }
        return httpx.Response(200, json={**SCOPE, "lease_id": LEASE_ID, "released": True})

    result = await adapter(settings, handler).call_tool("release_external_lease", {
        **SCOPE, "lease_id": LEASE_ID, "lease_token": LEASE_TOKEN, "client_operation_id": CLIENT_OPERATION_ID,
    })
    assert result[1]["released"] is True


@pytest.mark.parametrize("quantity", [None, True, "8", 8.5, 0, -1, 4_294_967_296])
async def test_reject_invalid_quantities_before_claiming(settings, quantity):
    def handler(request):
        pytest.fail("Invalid claim reached the API.")
    args = {**SCOPE, **HOLDER, "claim_request_id": CLIENT_OPERATION_ID, "quantity": quantity}
    with pytest.raises(ToolError, match="quantity"):
        await adapter(settings, handler).call_tool("claim_external_lease", args)
