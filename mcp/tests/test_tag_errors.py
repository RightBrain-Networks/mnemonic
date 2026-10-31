"""Tag refusals identify the invalid input without exposing caller or server prose."""

import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from test_artifacts import call as call_artifact
from test_artifacts import upload_arguments
from test_tools import adapter, protected_tool_arguments

from mnemonic_mcp.api import TransportEffect, _raise_for_response_error
from mnemonic_mcp.input_errors import InputValidationError

PRIVATE = "private-tag-value"
GUIDANCE = {
    "tags_required": ("top-level tags", "list_tags", "explicit null"),
    "checkpoint_tags_retired": ("checkpoint tags", "empty", "create_work", "update_work"),
    "unknown_tags": ("tags", "list_tags", "catalog", "explicit null"),
}


def refusal(code):
    return httpx.Response(422, json={"detail": {
        "code": code, "message": PRIVATE, "context": {"tags": [PRIVATE]},
    }})


def assert_guidance(message, code):
    for phrase in GUIDANCE[code]:
        assert phrase in message
    assert PRIVATE not in message
    assert "could not complete" not in message


@pytest.mark.parametrize("code", GUIDANCE)
def test_tag_refusals_are_input_validation_errors(code):
    with pytest.raises(InputValidationError) as caught:
        _raise_for_response_error(
            refusal(code), "POST", "work-items", semantic_read=False,
            effect=TransportEffect.RECEIPT_PROTECTED_WRITE,
        )
    assert_guidance(str(caught.value), code)


@pytest.mark.parametrize("name, code", [
    ("create_work", "tags_required"),
    ("create_work", "checkpoint_tags_retired"),
    ("create_work", "unknown_tags"),
    ("add_checkpoint", "checkpoint_tags_retired"),
    ("complete_work", "checkpoint_tags_retired"),
    ("update_work", "unknown_tags"),
])
async def test_work_tag_refusals_reach_tools_with_repair_hints(settings, name, code):
    arguments = protected_tool_arguments()[name]
    if name == "create_work":
        arguments["discovered_from_work_item_id"] = None
        if code != "tags_required":
            arguments["tags"] = None
    if code == "checkpoint_tags_retired":
        field = "initial_checkpoint" if name == "create_work" else "checkpoint"
        arguments[field]["tags"] = [PRIVATE]
    elif code == "unknown_tags":
        fields = arguments["changes"] if name == "update_work" else arguments
        fields["tags"] = [PRIVATE]
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        if code == "tags_required":
            assert "tags" not in payload  # Historical omission reaches the API unchanged.
        elif code == "checkpoint_tags_retired":
            checkpoint = payload if name == "add_checkpoint" else payload[field]
            assert checkpoint["tags"] == [PRIVATE]
        else:
            assert payload["tags"] == [PRIVATE]
        return refusal(code)

    with pytest.raises(ToolError) as caught:
        await adapter(settings, handler).call_tool(name, arguments)
    message = str(caught.value)
    assert_guidance(message, code)
    assert f'help({{"topic":"{name}' in message
    assert len(requests) == 1


async def test_artifact_unknown_tags_refusal_has_the_same_repair_guidance(settings):
    requests = []

    def handler(request):
        requests.append(request)
        assert json.loads(request.headers["X-Artifact-Metadata"])["tags"] == [PRIVATE]
        return refusal("unknown_tags")

    with pytest.raises(ToolError) as caught:
        await call_artifact(
            settings, "upload_artifact", upload_arguments(tags=[PRIVATE]), handler,
        )
    assert_guidance(str(caught.value), "unknown_tags")
    assert 'help({"topic":"upload_artifact' in str(caught.value)
    assert len(requests) == 1
