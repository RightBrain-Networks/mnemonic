"""Atomic assessment tools preserve one exact request and reject mismatched successes."""

import copy
import json
from uuid import uuid4

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_knowledge_impact_tools import (
    ACTOR,
    ASSESSMENT_ID,
    disposition,
    disposition_arguments,
)
from test_tools import adapter, structured


def arguments():
    source = disposition_arguments()
    return {key: source[key] for key in ("project_id", "assessment_id", "expected_version", "actor",
                                        "client_operation_id")} | {"items": [{
        key: source[key] for key in ("candidate_reference", "disposition", "reason",
                                    "remediation_work_item_id")
    }, {"candidate_reference": "ADR-00002-0", "disposition": "not-affected",
        "reason": "The recorded implementation does not consume the changed rule.",
        "remediation_work_item_id": None}]}


def response():
    return {"project_id": PROJECT_ID, "assessment_id": ASSESSMENT_ID, "version": 2,
            "items": [disposition(), disposition(id=str(uuid4()), version=2,
                                                  **arguments()["items"][1])]}


@pytest.mark.parametrize("token", [None, "private-review-capability"])
async def test_batch_request_and_response_retain_all_ordered_items_and_optional_token(settings, token):
    args = arguments()
    if token is not None:
        args["lease_token"] = token

    def handler(request):
        assert request.url.path.endswith(f"/assessments/{ASSESSMENT_ID}/dispositions/batch")
        assert json.loads(request.content) == {key: value for key, value in args.items()
                                               if key not in {"project_id", "assessment_id"}}
        return httpx.Response(201, json=response())

    result = structured(await adapter(settings, handler).call_tool(
        "decision_assessment_append_batch", args,
    ))
    assert result["version"] == 2
    assert [item["candidate_reference"] for item in result["items"]] == [
        item["candidate_reference"] for item in args["items"]
    ]
    assert "lease_token" not in result and all("lease_token" not in row for row in result["items"])


@pytest.mark.parametrize("change", ["missing", "extra", "order", "duplicate-id", "duplicate-ref",
                                    "version", "final-version", "actor", "reason", "remediation",
                                    "project", "assessment", "unknown", "token"])
async def test_batch_malformed_or_unbound_success_remains_unknown(settings, change):
    result = response()
    if change == "missing":
        result["items"].pop()
    elif change == "extra":
        result["items"].append(disposition())
    elif change == "order":
        result["items"].reverse()
    else:
        target, key, value = {
            "duplicate-id": (result["items"][1], "id", result["items"][0]["id"]),
            "duplicate-ref": (result["items"][1], "candidate_reference",
                              result["items"][0]["candidate_reference"]),
            "version": (result["items"][1], "version", 3),
            "final-version": (result, "version", 3),
            "actor": (result["items"][1], "actor", {**ACTOR, "actor_session_id": "different"}),
            "reason": (result["items"][1], "reason", "Invented reason"),
            "remediation": (result["items"][1], "remediation_work_item_id", WORK_ID),
            "project": (result["items"][1], "project_id", WORK_ID),
            "assessment": (result, "assessment_id", WORK_ID),
            "unknown": (result, "unexpected", "not a field"),
            "token": (result, "lease_token", "private-capability"),
        }[change]
        target[key] = value
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(201, json=result)).call_tool(
            "decision_assessment_append_batch", arguments(),
        )


@pytest.mark.parametrize("invalid", ["empty", "oversized", "duplicate", "reason", "unpinned",
                                     "item-actor", "item-version", "negative", "token"])
async def test_invalid_batch_is_refused_before_dispatch(settings, invalid):
    args = arguments()
    if invalid == "empty":
        args["items"] = []
    elif invalid == "oversized":
        args["items"] *= 51
    elif invalid == "duplicate":
        args["items"][1]["candidate_reference"] = args["items"][0]["candidate_reference"]
    elif invalid == "reason":
        args["items"][1]["reason"] = " "
    elif invalid == "unpinned":
        args["items"][1]["candidate_reference"] = "ADR-00002"
    elif invalid == "item-actor":
        args["items"][1]["actor"] = ACTOR
    elif invalid == "item-version":
        args["items"][1]["expected_version"] = 1
    elif invalid == "negative":
        args["expected_version"] = -1
    else:
        args["lease_token"] = " "
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=response())

    with pytest.raises(ToolError):
        await adapter(settings, handler).call_tool("decision_assessment_append_batch", args)
    assert not calls


async def test_batch_uncertain_write_mints_one_id_without_replaying(settings):
    attempts = []

    def handler(request):
        attempts.append(json.loads(request.content))
        raise httpx.ReadTimeout("private service detail")

    args = arguments()
    args.pop("client_operation_id")
    result = await adapter(settings, handler).call_tool("decision_assessment_append_batch", args)
    assert result.isError and len(attempts) == 1
    assert attempts[0]["client_operation_id"] == result.meta["mnemonic_generated_ids"][
        "client_operation_id"
    ]
    assert attempts[0]["items"] == args["items"]


async def test_batch_exact_explicit_retry_preserves_uuid_token_and_order(settings):
    attempts = []
    returned = response()

    def handler(request):
        attempts.append(json.loads(request.content))
        return httpx.Response(201, json=returned)

    args = arguments() | {"lease_token": "private-capability"}
    server = adapter(settings, handler)
    for _ in range(2):
        await server.call_tool("decision_assessment_append_batch", copy.deepcopy(args))
    assert attempts[0] == attempts[1]
    assert attempts[0]["client_operation_id"] == CLIENT_OPERATION_ID


async def test_single_disposition_accepts_private_optional_lease_without_echoing_it(settings):
    args = disposition_arguments() | {"lease_token": "private-capability"}

    def handler(request):
        assert json.loads(request.content)["lease_token"] == args["lease_token"]
        return httpx.Response(201, json=disposition())

    result = structured(await adapter(settings, handler).call_tool("decision_assessment_append", args))
    assert "lease_token" not in result
