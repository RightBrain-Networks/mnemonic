"""Rejected tools explain their accepted shape without exposing caller data."""

import httpx
import pytest
from conftest import (
    API_KEY,
    CLIENT_OPERATION_ID,
    LOCAL_VALIDATION_CASES,
    PRIVATE_EXTRA_FIELD,
    PRIVATE_EXTRA_VALUE,
    PROJECT_ID,
)
from mcp.server.fastmcp.exceptions import ToolError

from mnemonic_mcp.api import UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME, MnemonicAPI
from mnemonic_mcp.input_errors import InputValidationError
from mnemonic_mcp.server import build_server


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


async def test_every_tool_rejection_gives_bounded_hints_without_values(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    for tool in await server.list_tools():
        with pytest.raises(ToolError) as caught:
            await server.call_tool(tool.name, {PRIVATE_EXTRA_FIELD: PRIVATE_EXTRA_VALUE})
        message = str(caught.value)
        assert "Input schema" not in message and '"$defs"' not in message
        assert "help(" in message
        assert len(message) < 1200, tool.name
        assert PRIVATE_EXTRA_FIELD not in message
        assert PRIVATE_EXTRA_VALUE not in message
        assert API_KEY not in message


async def test_complete_work_repair_explains_author_and_missing_checkpoint(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("complete_work", LOCAL_VALIDATION_CASES[0][1])
    message = str(caught.value)
    assert "Put the actual author in checkpoint.source_client and checkpoint.source_session_id" in message
    assert "checkpoint (missing): Supply object with prompt, source_client, source_session_id" in message
    assert 'help({"topic":"complete_work checkpoint"})' in message
    assert "none_required" not in message  # Suppress union noise when child repairs exist.
    assert len(message) < 650


async def test_verification_discriminator_error_selects_its_help_page(settings):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    arguments = dict(LOCAL_VALIDATION_CASES[0][1])
    arguments.pop("actor_client")
    arguments.pop("actor_session_id")
    arguments["checkpoint"] = {"prompt": "Done", "source_client": "codex", "source_session_id": "s"}
    arguments["completion_evidence"] = {"verification_results": [{"name": PRIVATE_EXTRA_VALUE}]}
    with pytest.raises(ToolError) as caught:
        await server.call_tool("complete_work", arguments)
    message = str(caught.value)
    assert "Choose verification_type: command, observation" in message
    assert 'help({"topic":"complete_work completion_evidence verification_results"})' in message
    assert PRIVATE_EXTRA_VALUE not in message


@pytest.mark.parametrize("detail", [
    [{"loc": ["body", "summary"], "type": "string_too_long",
      "input": PRIVATE_EXTRA_VALUE, "msg": PRIVATE_EXTRA_VALUE}],
    {"code": "work_summary_too_long", "message": PRIVATE_EXTRA_VALUE,
     "context": {"max_chars": 2048}},
])
async def test_api_input_rejection_includes_help_and_stays_sanitized(settings, detail):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(422, json={"detail": detail})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("create_work", create_work_arguments())
    message = str(caught.value)
    assert 'help({"topic":"create_work' in message
    assert "Input schema" not in message
    assert len(message) < 1200
    if isinstance(detail, list):
        assert "Shorten this string" in message
        assert "minLength" not in message
    assert PRIVATE_EXTRA_VALUE not in message
    assert len(requests) == 1


@pytest.mark.parametrize("name, arguments, guidance", [
    ("search_transcript_contents", {"project_id": PROJECT_ID},
     "Supply query or its q alias"),
    ("search", {}, "Supply exactly one of project_id or project_ids"),
    ("update_work", {"changes": {"title": "New title"}, "subagent_transcripts": []},
     "Subagent transcript assertions require a closeout transition"),
])
async def test_manual_input_rejection_retains_guidance_and_adds_help(
    settings, name, arguments, guidance,
):
    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(reject_http)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool(name, arguments)
    message = str(caught.value)
    assert guidance in message
    assert f'help({{"topic":"{name}"}})' in message
    assert "Input schema" not in message


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


@pytest.mark.parametrize("location", [["body", "created_before"], []])
async def test_reviewed_cross_field_repair_survives_api_validation(settings, location):
    def handler(request):
        return httpx.Response(422, json={"detail": [{
            "loc": location, "type": "search_datetime_timezone_required",
            "msg": PRIVATE_EXTRA_VALUE, "input": PRIVATE_EXTRA_VALUE,
        }]})

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(handler)))
    with pytest.raises(ToolError) as caught:
        await server.call_tool("search_work", {"project_id": PROJECT_ID})
    message = str(caught.value)
    assert "Include a timezone offset or Z in each search date bound" in message
    assert 'help({"topic":"search_work' in message
    assert PRIVATE_EXTRA_VALUE not in message
