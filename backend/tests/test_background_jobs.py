"""Broker messages are identifier-only and ACK follows durable state changes."""

import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mnemonic_jobs import JobContext, PermanentJobError, RetryJob
from mnemonic_jobs.runtime import _deliver, _invoke, decode_message, encode_message


@pytest.mark.parametrize("body", [
    b"[]", b"null", b"{}", b"x" * 257, b"\xff", b"not JSON",
    b'{"version":true,"job_id":"invalid"}',
    b'{"version":1,"job_id":1}', b'{"version":2,"job_id":"invalid"}',
    b'{"version":1,"job_id":"invalid","path":"/secret"}',
])
def test_invalid_envelope_rejects_untrusted_input(body):
    with pytest.raises((ValueError, UnicodeError)):
        decode_message(body)


def test_message_contains_no_domain_payload():
    identifier = uuid4()
    assert json.loads(encode_message(identifier)) == {"version": 1, "job_id": str(identifier)}
    assert decode_message(encode_message(identifier)) == identifier


def test_safe_failure_codes_and_transient_exception_redaction(caplog):
    context = JobContext(uuid4(), "backup_create", {}, uuid4(), 1)

    def failed(_context):
        raise OSError("private source path and credential-bearing URL")

    result, error = _invoke(failed, context)
    assert result is None
    assert isinstance(error, RetryJob)
    assert error.code == "job_execution_failed"
    assert "private source" not in caplog.text
    with pytest.raises(ValueError):
        PermanentJobError("private/path")


def test_deliver_acknowledges_only_after_durable_execution(monkeypatch):
    calls = []
    context = JobContext(uuid4(), "backup_create", {}, uuid4(), 1)
    message = AsyncMock(body=encode_message(context.job_id))

    def claim(*_args):
        calls.append("claim_committed")
        return True, context

    async def execute(*_args):
        calls.append("result_committed")

    async def ack():
        calls.append("ack")

    message.ack.side_effect = ack
    monkeypatch.setattr("mnemonic_jobs.runtime._claim", claim)
    monkeypatch.setattr("mnemonic_jobs.runtime._execute", execute)
    asyncio.run(_deliver(message, None, {}, 120))
    assert calls == ["claim_committed", "result_committed", "ack"]


def test_deliver_does_not_acknowledge_uncommitted_database_failure(monkeypatch):
    context = JobContext(uuid4(), "backup_create", {}, uuid4(), 1)
    message = AsyncMock(body=encode_message(context.job_id))
    monkeypatch.setattr("mnemonic_jobs.runtime._claim", lambda *_args: (True, context))
    monkeypatch.setattr("mnemonic_jobs.runtime._execute", AsyncMock(side_effect=ConnectionError))
    with pytest.raises(ConnectionError):
        asyncio.run(_deliver(message, None, {}, 120))
    message.ack.assert_not_called()


@pytest.mark.parametrize("body", [b"malformed", encode_message(uuid4())],
                         ids=["malformed", "unknown"])
def test_malformed_or_unknown_jobs_are_dead_lettered(body, monkeypatch):
    message = AsyncMock(body=body)
    monkeypatch.setattr("mnemonic_jobs.runtime._claim", lambda *_args: (False, None))
    asyncio.run(_deliver(message, None, {}, 120))
    message.reject.assert_awaited_once_with(requeue=False)
    message.ack.assert_not_called()
