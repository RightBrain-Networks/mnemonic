"""Storage faults distinguish this attempt from the durable operation UUID's history."""

import httpx
import pytest
from conftest import CLIENT_OPERATION_ID
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolRequest, CallToolRequestParams
from test_artifacts import ARTIFACT_ID, artifact, download_arguments, upload_arguments

from mnemonic_mcp.api import (
    UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME,
    MnemonicAPI,
    TransportEffect,
    _raise_for_response_error,
)
from mnemonic_mcp.artifact_policy import DISABLED_MESSAGE
from mnemonic_mcp.server import build_server

CAUSES = {
    "storage_owner_mismatch": ("ownership", "UID/GID"),
    "storage_permission_denied": ("permission denied", "directory traversal"),
    "storage_full": ("full", "quota"),
    "storage_read_only": ("read-only", "writable mount"),
    "storage_integrity": ("integrity", "inspect and repair"),
    "storage_unavailable": ("unavailable", "storage mount"),
}
PRIVATE = "/private/operator/storage: secret upstream diagnostic"
THIS_ATTEMPT = "This attempt did not commit an artifact mutation"
UNCERTAIN = "The mutation outcome remains uncertain"


def response(*, status=503, code="artifact_storage_unavailable", context=None):
    return httpx.Response(status, json={"detail": {
        "code": code, "message": PRIVATE, "context": context,
    }})


def error_message(result, *, effect=TransportEffect.RECEIPT_PROTECTED_WRITE):
    with pytest.raises(ToolError) as raised:
        _raise_for_response_error(result, "POST", "artifacts", semantic_read=False, effect=effect)
    return str(raised.value)


def assert_storage_guidance(message, cause):
    for expected in CAUSES[cause]:
        assert expected in message
    assert "MNEMONIC_ARTIFACT_ROOT" in message
    assert "Stop automatic retries pending operator repair" in message
    assert "Make at most one exact retry" not in message
    assert PRIVATE not in message
    assert "/private" not in message
    assert "untrusted_extra" not in message


def assert_retained_intent(message):
    assert "original client_operation_id (operation UUID)" in message
    assert "exact arguments and bytes" in message
    assert "Do not generate a replacement UUID" in message


@pytest.mark.parametrize("cause", CAUSES)
@pytest.mark.parametrize("not_committed", [True, False])
def test_storage_faults_have_local_cause_and_operator_remedy(cause, not_committed):
    message = error_message(response(context={
        "cause": cause, "attempt_not_committed": not_committed, "untrusted_extra": PRIVATE,
    }))
    assert_storage_guidance(message, cause)
    assert_retained_intent(message)
    if not_committed:
        assert THIS_ATTEMPT in message
        assert "does not resolve any earlier or concurrent attempt with the same operation UUID" in (
            message
        )
    else:
        assert THIS_ATTEMPT not in message
        assert UNCERTAIN in message
        assert "durable intent or receipt may already exist" in message


@pytest.mark.parametrize("flag", [None, 0, 1, -1, 1.0, "true", "false", "1", [], {}, [True]])
def test_storage_noncommit_flag_requires_literal_true(flag):
    message = error_message(response(context={
        "cause": "storage_permission_denied", "attempt_not_committed": flag,
    }))
    assert_storage_guidance(message, "storage_permission_denied")
    assert THIS_ATTEMPT not in message
    assert UNCERTAIN in message
    assert_retained_intent(message)


def test_missing_noncommit_flag_keeps_operation_uncertain():
    message = error_message(response(context={"cause": "storage_permission_denied"}))
    assert UNCERTAIN in message
    assert THIS_ATTEMPT not in message


@pytest.mark.parametrize("context", [
    None, [], "storage_permission_denied", {}, {"attempt_not_committed": True},
    {"cause": None}, {"cause": []}, {"cause": {}}, {"cause": 1},
    {"cause": "unrecognized", "attempt_not_committed": True},
    {"cause": PRIVATE, "attempt_not_committed": True},
])
def test_unclassified_or_malformed_storage_response_preserves_original_unknown(context):
    assert error_message(response(context=context)) == UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME


@pytest.mark.parametrize("status,code", [
    (500, "artifact_storage_unavailable"), (502, "artifact_storage_unavailable"),
    (504, "artifact_storage_unavailable"), (503, "artifact_operation_unavailable"),
    (503, "other_unavailable"), (503, []), (503, {}),
])
def test_storage_guidance_requires_exact_status_and_code(status, code):
    result = response(status=status, code=code, context={
        "cause": "storage_permission_denied", "attempt_not_committed": True,
    })
    assert error_message(result) == UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME


@pytest.mark.parametrize("status", [400, 409, 422])
def test_nonserver_status_cannot_assert_storage_noncommit(status):
    message = error_message(response(status=status, context={
        "cause": "storage_permission_denied", "attempt_not_committed": True,
    }))
    assert "MNEMONIC_ARTIFACT_ROOT" not in message
    assert THIS_ATTEMPT not in message
    assert PRIVATE not in message


@pytest.mark.parametrize("cause", CAUSES)
def test_storage_safe_reads_have_no_mutation_claims(cause):
    message = error_message(response(context={
        "cause": cause, "attempt_not_committed": True,
    }), effect=TransportEffect.SAFE_READ)
    assert_storage_guidance(message, cause)
    for forbidden in ("mutation", "UUID", "receipt", "commit", "exact arguments"):
        assert forbidden not in message


@pytest.mark.parametrize("effect", [None, "receipt_protected_write", "safe_read", [], {}, True])
def test_untyped_transport_effect_cannot_assert_noncommit(effect):
    message = error_message(response(context={
        "cause": "storage_permission_denied", "attempt_not_committed": True,
    }), effect=effect)
    assert_storage_guidance(message, "storage_permission_denied")
    assert THIS_ATTEMPT not in message
    assert UNCERTAIN in message
    assert_retained_intent(message)


async def public_error(settings, tool, arguments, handler, *, wire=False, enabled=True):
    calls = []

    def streamed(request):
        if request.url.path == "/api/v1/artifacts/status":
            result = httpx.Response(200, json={
                "enabled": enabled, "max_bytes": 1024 if enabled else 0, "message": PRIVATE,
            })
        else:
            calls.append(request)
            result = handler(request)
        return httpx.Response(result.status_code, headers=result.headers,
                              stream=httpx.ByteStream(result.content))

    server = build_server(settings, MnemonicAPI(settings, httpx.MockTransport(streamed)))
    if wire:
        request = CallToolRequest(params=CallToolRequestParams(name=tool, arguments=arguments))
        result = await server._mcp_server.request_handlers[CallToolRequest](request)
        assert result.root.isError is True
        message = " ".join(content.text for content in result.root.content)
    else:
        with pytest.raises(ToolError) as raised:
            await server.call_tool(tool, arguments)
        message = str(raised.value)
    return message, calls


@pytest.mark.parametrize("wire", [False, True])
@pytest.mark.parametrize("tool", ["upload_artifact", "replace_artifact"])
@pytest.mark.parametrize("not_committed", [True, False])
async def test_public_upload_and_replace_report_sanitized_fault_with_configuration(
    settings, wire, tool, not_committed,
):
    args = upload_arguments()
    if tool == "replace_artifact":
        args.update(artifact_id=ARTIFACT_ID, expected_revision=1)
    message, calls = await public_error(settings, tool, args, lambda request: response(context={
        "cause": "storage_owner_mismatch", "attempt_not_committed": not_committed,
        "untrusted_extra": PRIVATE,
    }), wire=wire)
    assert_storage_guidance(message, "storage_owner_mismatch")
    assert_retained_intent(message)
    assert (THIS_ATTEMPT in message) is not_committed
    assert "Last observed configuration: Artifact library enabled." in message
    assert "Configured upload limit: 1024 bytes" in message
    assert len(calls) == 1
    assert calls[0].headers["X-Client-Operation-ID"] == CLIENT_OPERATION_ID


@pytest.mark.parametrize("wire", [False, True])
async def test_public_download_reports_storage_fault_without_write_claim(settings, wire):
    def handler(request):
        if request.url.path.endswith("/content"):
            return response(context={"cause": "storage_integrity", "attempt_not_committed": False})
        return httpx.Response(200, json=artifact())

    message, calls = await public_error(
        settings, "download_artifact", download_arguments(), handler, wire=wire,
    )
    assert_storage_guidance(message, "storage_integrity")
    assert "Last observed configuration" in message
    for forbidden in ("mutation outcome", "operation UUID", "did not commit", "durable intent"):
        assert forbidden not in message
    assert len(calls) == 2


@pytest.mark.parametrize("wire", [False, True])
@pytest.mark.parametrize("failure", ["dropped", "timeout", "unclassified", "malformed"])
async def test_public_unknown_storage_transport_keeps_original_guidance(settings, wire, failure):
    def handler(request):
        if failure == "dropped":
            raise httpx.RemoteProtocolError(PRIVATE, request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(PRIVATE, request=request)
        if failure == "malformed":
            return httpx.Response(503, content=PRIVATE.encode())
        return response(context={"cause": "unrecognized", "attempt_not_committed": True})

    message, calls = await public_error(
        settings, "upload_artifact", upload_arguments(), handler, wire=wire,
    )
    assert UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME in message
    assert message.split(" Last observed configuration:")[0].endswith(
        UNKNOWN_IDEMPOTENT_MUTATION_OUTCOME
    )
    assert PRIVATE not in message
    assert len(calls) == 1


@pytest.mark.parametrize("wire", [False, True])
async def test_disabled_preflight_retains_existing_guidance_without_dispatch(settings, wire):
    message, calls = await public_error(
        settings, "upload_artifact", upload_arguments(),
        lambda request: pytest.fail("Disabled library must not dispatch an artifact operation"),
        wire=wire, enabled=False,
    )
    assert DISABLED_MESSAGE in message
    assert "MNEMONIC_ARTIFACT_ROOT" not in message
    assert calls == []
