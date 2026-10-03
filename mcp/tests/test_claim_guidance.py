"""Implementation keeps its capability separate from potentially oversized context."""

import copy
import json

import httpx
import pytest
from conftest import API_KEY, CLAIM_REQUEST_ID, PROJECT_ID, WORK_ID
from mcp.server.fastmcp.exceptions import ToolError
from test_tools import adapter, protected_tool_arguments, structured

from mnemonic_mcp.api import UNKNOWN_CLAIM_OUTCOME, UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME
from mnemonic_mcp.command_help import render_help
from mnemonic_mcp.server import INSTRUCTIONS, build_server


async def test_discovery_and_help_direct_implementation_to_separate_claim_and_recall(settings):
    server = build_server(settings)
    catalog = {tool.name: tool for tool in await server.list_tools()}
    assert "Authorized implementation: claim_work then recall_work" in INSTRUCTIONS
    assert "Warm: claim_and_recall,get_code_review" in INSTRUCTIONS
    for name in ("search_work", "list_ready_work", "claim_work", "claim_and_recall"):
        description = catalog[name].description
        assert description is not None
        assert "claim_work" in description and "recall_work" in description
        assert "implementation" in description.casefold()
    for name in ("claim_work", "claim_and_recall"):
        for suffix in (" usage", " details"):
            page = render_help(name + suffix, catalog)
            assert "implementation" in page.casefold()
            assert "recall_work" in page
    prompt = next(prompt for prompt in await server.list_prompts() if prompt.name == "resume_work")
    assert "claim_work then recall_work" in prompt.description


async def test_large_context_recall_does_not_carry_the_separately_acquired_token(
    settings, claim_receipt, active_work_context,
):
    context = copy.deepcopy(active_work_context)
    context["initial_checkpoint"]["prompt"] = "Large handoff context.\n" * 3200
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path.endswith("/claim"):
            return httpx.Response(200, json=claim_receipt)
        assert request.url.path.endswith("/context")
        assert claim_receipt["lease_token"] not in request.content.decode()
        return httpx.Response(200, json=context)

    server = adapter(settings, handler)
    claimed = structured(await server.call_tool("claim_work", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID,
        "holder_client": claim_receipt["holder_client"],
        "holder_session_id": claim_receipt["holder_session_id"],
        "claim_request_id": claim_receipt["claim_request_id"], "session_transcript": None,
    }))
    recalled = structured(await server.call_tool("recall_work", {
        "project_id": PROJECT_ID, "work_item_id": WORK_ID,
    }))
    assert claimed == claim_receipt
    assert len(json.dumps(claimed).encode()) < 2048
    assert len(json.dumps(recalled).encode()) > 67_000
    assert claim_receipt["lease_token"] not in json.dumps(recalled)
    assert recalled["initial_checkpoint"]["prompt"] == context["initial_checkpoint"]["prompt"]
    root = f"/api/v1/projects/{PROJECT_ID}/work-items/{WORK_ID}"
    assert requests == [("POST", root + "/claim"), ("GET", root + "/context")]


@pytest.mark.parametrize("tool", ["claim_work", "claim_and_recall", "complete_work"])
@pytest.mark.parametrize("status", [422, 503])
async def test_missing_transcript_has_a_specific_remedy_without_weakening_unknown_retries(
    settings, tool, status,
):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"detail": {
            "code": "transcript_source_missing", "message": f"private {API_KEY}",
            "context": {"attempt_not_committed": True, "instructions": API_KEY},
        }})

    if tool == "complete_work":
        arguments = protected_tool_arguments()[tool]
    else:
        arguments = {
            "project_id": PROJECT_ID, "work_item_id": WORK_ID,
            "holder_client": "codex", "holder_session_id": "native-session",
            "claim_request_id": CLAIM_REQUEST_ID,
            "session_transcript": {"client": "codex", "path": "/approved/rollout.jsonl"},
        }
    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(tool, arguments)
    message = str(caught.value)
    assert API_KEY not in message
    assert len(requests) == 1
    if status == 422:
        assert "transcript_source_missing:" in message
        assert "same-path mounts in API and worker" in message
        assert "explicit null" in message
        assert "definitive rejection" in message
    else:
        expected = (
            UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME if tool == "complete_work"
            else UNKNOWN_CLAIM_OUTCOME
        )
        assert expected in message
        assert "explicit null" not in message
