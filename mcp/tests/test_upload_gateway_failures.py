"""Gateway failure boundaries must disclose neither upstream prose nor private paths."""

import errno
import io
import json
from email.message import Message
from urllib.error import HTTPError

import httpx
import pytest
from conftest import API_KEY
from test_upload_grants import CONTENT, headers, intent, send

from mnemonic_mcp import upload_gateway
from mnemonic_mcp.upload_errors import upstream_failure


@pytest.mark.parametrize(
    "status,body,content_type",
    [
        (500, b"private upstream path /service/secret", "text/plain"),
        (401, json.dumps({"detail": API_KEY}).encode(), "application/json"),
        (503, json.dumps({"detail": {"code": API_KEY}}).encode(), "application/json"),
        (
            503,
            b'{"detail":{"code":"artifact_storage_unavailable","context":{"cause":"secret"}}}',
            "application/json",
        ),
        (
            409,
            b'{"detail":{"code":"artifact_revision_conflict","code":"secret"}}',
            "application/json",
        ),
        (500, b'{"detail":{"code":"artifact_revision_conflict"}}', "application/json"),
        (
            503,
            b'{"detail":{"code":"artifact_library_disabled","context":{"max_bytes":true}}}',
            "application/json",
        ),
    ],
)
async def test_upstream_errors_never_relay_arbitrary_bytes(settings, status, body, content_type):
    response, calls = await send(
        settings,
        intent(),
        handler=lambda request: httpx.Response(
            status,
            content=body,
            headers={"Content-Type": content_type},
        ),
    )
    assert len(calls) == 1 and response.status_code == 502
    assert response.json() == {"detail": {"code": "artifact_upload_outcome_unknown"}}
    assert API_KEY not in response.text and "secret" not in response.text


@pytest.mark.parametrize(
    "status,code,context",
    [
        (409, "artifact_revision_conflict", {}),
        (409, "client_operation_conflict", {}),
        (404, "artifact_not_found", {}),
        (413, "artifact_too_large", {"max_bytes": 7}),
        (503, "artifact_library_disabled", {"max_bytes": 0}),
        (
            503,
            "artifact_storage_unavailable",
            {"cause": "storage_full", "attempt_not_committed": False},
        ),
        (
            503,
            "artifact_storage_unavailable",
            {"cause": "storage_permission_denied", "attempt_not_committed": True},
        ),
    ],
)
def test_controlled_error_envelopes_preserve_recovery_without_private_details(
    status, code, context
):
    response = upstream_failure(
        httpx.Response(
            status,
            json={
                "detail": {
                    "code": code,
                    "message": API_KEY,
                    "context": {**context, "private_path": API_KEY},
                },
                "private": API_KEY,
            },
        )
    )
    assert response.status_code == status
    assert json.loads(response.body) == {
        "detail": {"code": code, **({"context": context} if context else {})}
    }
    assert API_KEY.encode() not in response.body


@pytest.mark.parametrize("stage", ["create", "write"])
@pytest.mark.parametrize(
    "number,cause",
    [
        (errno.ENOSPC, "storage_full"),
        (errno.EDQUOT, "storage_full"),
        (errno.EACCES, "storage_permission_denied"),
        (errno.EROFS, "storage_read_only"),
        (errno.EIO, "storage_unavailable"),
    ],
)
async def test_local_staging_failure_stops_for_repair_before_upstream(
    settings,
    monkeypatch,
    stage,
    number,
    cause,
):
    files = []

    class FailingFile(io.BytesIO):
        def write(self, value):
            raise OSError(number, "private filesystem detail", "/private/mcp/tmp")

    def temporary_file():
        if stage == "create":
            raise OSError(number, "private filesystem detail", "/private/mcp/tmp")
        result = FailingFile()
        files.append(result)
        return result

    monkeypatch.setattr(upload_gateway.tempfile, "TemporaryFile", temporary_file)
    response, calls = await send(settings, intent())
    assert response.status_code == 503 and not calls
    assert response.json() == {
        "detail": {
            "code": "artifact_storage_unavailable",
            "context": {
                "cause": cause,
                "attempt_not_committed": True,
                "storage_boundary": "mcp_upload_staging",
            },
        }
    }
    assert all(file.closed for file in files)
    assert "/private" not in response.text
    # Test the shipped client remedy as well as the HTTP envelope.
    import runpy
    from pathlib import Path

    helper = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "plugin/scripts/upload_artifact.py")
    )
    response_headers = Message()
    response_headers["Content-Type"] = "application/json"
    response_headers["Content-Length"] = str(len(response.content))
    error = HTTPError(
        "http://localhost/mcp", 503, "unused", response_headers, io.BytesIO(response.content)
    )
    with error:
        message = str(helper["http_failure"](error))
    assert "MCP temporary upload storage" in message
    assert "Stop retries until it is repaired" in message
    assert "never generate a new operation UUID" in message


async def test_busy_gateway_does_not_consume_body(settings, monkeypatch):
    monkeypatch.setattr(upload_gateway, "UPLOAD_SLOTS", 0)
    read = False

    async def body():
        nonlocal read
        read = True
        yield CONTENT

    response, calls = await send(settings, intent(), body=body(), supplied=headers(intent()))
    assert response.status_code == 503 and not calls and not read
    assert response.json()["detail"]["code"] == "artifact_upload_busy"


async def test_buffered_flush_and_close_failure_remains_a_staging_repair(settings, monkeypatch):
    import hashlib

    writes = []

    class FullDisk(io.BytesIO):
        def write(self, value):
            writes.append(len(value))
            raise OSError(errno.ENOSPC, "private staging flush detail")

    raw = FullDisk()
    buffered = io.BufferedRandom(raw)
    monkeypatch.setattr(upload_gateway.tempfile, "TemporaryFile", lambda: buffered)
    body = b"small buffered upload"
    uploaded = intent(size_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    response, calls = await send(settings, uploaded, body=body)
    assert response.status_code == 503 and not calls
    assert response.json()["detail"]["context"] == {
        "cause": "storage_full",
        "attempt_not_committed": True,
        "storage_boundary": "mcp_upload_staging",
    }
    assert len(writes) == 2 and raw.closed and buffered.closed


async def test_cleanup_failure_after_forwarding_cannot_claim_noncommit(settings, monkeypatch):
    class CloseFailure(io.BytesIO):
        def close(self):
            super().close()
            raise OSError(errno.EIO, "private post-forward cleanup detail")

    monkeypatch.setattr(upload_gateway.tempfile, "TemporaryFile", CloseFailure)
    response, calls = await send(settings, intent())
    assert len(calls) == 1 and response.status_code == 502
    assert response.json() == {"detail": {"code": "artifact_upload_outcome_unknown"}}
