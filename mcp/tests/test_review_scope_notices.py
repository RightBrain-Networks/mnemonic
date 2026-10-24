"""Scope coordination remains bound to the requested work and current change."""

import httpx
import pytest
from conftest import LEASE_TOKEN, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from test_knowledge_tools import arguments, decision
from test_tools import adapter, structured

from mnemonic_mcp.models import ClaimReceipt
from mnemonic_mcp.review_scope_models import ReviewScopeChanged


@pytest.mark.parametrize("kind", ["single", "batch"])
@pytest.mark.parametrize("with_token", [False, True])
async def test_disposition_notice_requires_progress_token(settings, kind, with_token):
    from test_assessment_batch_tools import arguments as batch_arguments
    from test_assessment_batch_tools import response as batch_response
    from test_knowledge_impact_tools import disposition, disposition_arguments

    args = batch_arguments() if kind == "batch" else disposition_arguments()
    result = batch_response() if kind == "batch" else disposition()
    result["review_scope_changed"] = NOTICE
    name = "decision_assessment_append" + ("_batch" if kind == "batch" else "")
    server = adapter(settings, lambda request: httpx.Response(201, json=result))
    if with_token:
        args["lease_token"] = LEASE_TOKEN
        assert structured(await server.call_tool(name, args)) == result
    else:
        with pytest.raises(ToolError, match="operation may already have committed"):
            await server.call_tool(name, args)


async def test_batch_rejects_notice_repeated_inside_disposition_items(settings):
    from test_assessment_batch_tools import arguments, response

    result = response()
    result["items"][0]["review_scope_changed"] = NOTICE
    with pytest.raises(ToolError, match="operation may already have committed"):
        await adapter(settings, lambda request: httpx.Response(201, json=result)).call_tool(
            "decision_assessment_append_batch", {**arguments(), "lease_token": LEASE_TOKEN},
        )

CHANGE_ID = "d3fd15e5-c09e-41ce-8ecd-2a3110667aa4"
NOTICE = {
    "change_id": CHANGE_ID, "project_id": PROJECT_ID, "review_work_item_id": WORK_ID,
    "old_scope_version": 4, "new_scope_version": 9, "episodes_added": 5,
    "captured_code_impact_rows_added": 189, "distinct_references_in_added_rows": 71,
    "distinct_decisions_in_added_rows": 50,
}


async def test_renew_exposes_exact_coordination_notice(settings, claim_receipt):
    response = {**claim_receipt, "review_scope_changed": NOTICE}
    result = structured(await adapter(settings, lambda request: httpx.Response(
        200, json=response,
    )).call_tool("renew_claim", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID, "lease_token": LEASE_TOKEN,
    }))
    assert result == response


async def test_wrong_work_notice_is_an_unresolved_renewal(settings, claim_receipt):
    response = {**claim_receipt, "review_scope_changed": {**NOTICE,
                                                         "review_work_item_id": PROJECT_ID}}
    with pytest.raises(ToolError, match="unexpected response"):
        await adapter(settings, lambda request: httpx.Response(200, json=response)).call_tool(
            "renew_claim", {"project_id": PROJECT_ID, "work_item_id": WORK_ID,
                            "lease_token": LEASE_TOKEN},
        )


@pytest.mark.parametrize("change", [
    {"episodes_added": 4}, {"new_scope_version": 4},
    {"distinct_references_in_added_rows": 190}, {"distinct_decisions_in_added_rows": 72},
    {"captured_code_impact_rows_added": None}, {"author_session": "context-must-not-leak"},
])
def test_incoherent_or_context_bearing_notices_are_rejected(change):
    with pytest.raises(ValidationError):
        ReviewScopeChanged.model_validate(NOTICE | change)


def test_historical_unknown_counts_are_explicit_and_claim_defaults_stay_sparse(claim_receipt):
    value = NOTICE | dict.fromkeys(("captured_code_impact_rows_added",
                                   "distinct_references_in_added_rows",
                                   "distinct_decisions_in_added_rows"))
    assert ReviewScopeChanged.model_validate(value).captured_code_impact_rows_added is None
    assert ClaimReceipt.model_validate(claim_receipt).model_dump(mode="json") == claim_receipt


async def test_decision_writer_receives_extended_review_with_holder(settings):
    change = {"change_id": CHANGE_ID, "kind": "substantive", "reason": None,
              "governing_reference": None, "source_work_item_id": None}
    response = {"decision": decision(change=change), "created": True, "extended_review": {
        "review_scope_changed": NOTICE,
        "holder": {"holder_client": "codex", "holder_session_id": "active-reviewer",
                   "expires_at": "2026-08-30T12:15:00Z"},
    }}
    result = structured(await adapter(settings, lambda request: httpx.Response(
        200, json=response,
    )).call_tool("decision_put", arguments()))
    assert result["extended_review"]["review_scope_changed"] == NOTICE
