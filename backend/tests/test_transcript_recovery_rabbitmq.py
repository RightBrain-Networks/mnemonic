"""Recover a published transcript through real RabbitMQ after its source disappears.

Native normalization, copy storage, transcript handlers, scheduler,
database ownership checks, and broker delivery use their production implementations.
"""

import asyncio
import os
from datetime import timedelta
from functools import partial
from threading import Event
from uuid import uuid4

import aio_pika
import pytest
from sqlalchemy import func, update

from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_job_queue import (
    enqueue_transcript_jobs,
    handle_transcript_copy,
    handle_transcript_index,
)
from mnemonic_jobs import runtime
from mnemonic_jobs.models import BackgroundJob
from mnemonic_jobs.runtime import WorkerState, encode_message, run_worker

from .test_background_jobs_rabbitmq import _cleanup, _event, _stop
from .test_transcript_recovery_postgres import _apply, _request_fixture

pytestmark = pytest.mark.postgres


@pytest.fixture
def broker_url():
    value = os.environ.get("RABBITMQ_TEST_URL")
    if not value:
        pytest.skip("Set RABBITMQ_TEST_URL to run real RabbitMQ integration tests")
    return value


def _transcript_state(factory, identifier):
    with factory() as database:
        row = database.get(Transcript, identifier)
        return (row.status, row.copy_status, row.copy_attempts, row.copy_sha256, row.source_path)


def _expire_copy_leases(factory, context, transcript_id):
    # Advance persisted deadlines after the disconnected invocation stops;
    # the worker still performs every normal claim and publication check.
    expired = func.clock_timestamp() - timedelta(seconds=1)
    with factory.begin() as database:
        database.execute(update(BackgroundJob).where(BackgroundJob.id == context.job_id).values(
            lease_expires_at=expired, publish_after=expired))
        database.execute(update(Transcript).where(Transcript.id == transcript_id).values(
            copy_lease_expires_at=expired))


async def _ready(factory, identifier):
    async with asyncio.timeout(20):
        while True:
            state = await asyncio.to_thread(_transcript_state, factory, identifier)
            assert state[0] != "failed" and state[1] != "failed"
            if state[0] == "ready":
                return state
            await asyncio.sleep(0.02)


def _observe_duplicates(monkeypatch):
    original_deliver = runtime._deliver
    marker, completed, deliveries = str(uuid4()), Event(), []

    async def deliver(message, *args, **kwargs):
        await original_deliver(message, *args, **kwargs)
        if message.message_id == marker:
            deliveries.append(message.message_id)
            if len(deliveries) == 5:
                completed.set()

    monkeypatch.setattr(runtime, "_deliver", deliver)
    return marker, completed


def test_disconnect_after_recovery_publication_adopts_pinned_copy_without_source(
    api, project, work_payload, tmp_path, postgres_engine, broker_url, monkeypatch,
):
    _, _, replacement, request = _request_fixture(
        api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    published, release = Event(), Event()
    captured, contexts, connections = [], [], []
    duplicate_marker, duplicates_processed = _observe_duplicates(monkeypatch)
    original_capture, original_connect = TranscriptStorage.capture, aio_pika.connect

    def capture(storage, *args, **kwargs):
        result = original_capture(storage, *args, **kwargs)
        captured.append(result)
        if len(captured) == 1:
            published.set()
            if not release.wait(25):
                raise TimeoutError("Published recovery copy was not released")
        return result

    def copy(context):
        contexts.append(context)
        return handle_transcript_copy(factory, settings, context)

    async def connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    async def scenario():
        queue_name = f"mnemonic.test.recovery.{uuid4().hex}"
        admin = await original_connect(broker_url)
        async with admin:
            channel = await admin.channel(publisher_confirms=True, on_return_raises=True)
            state = WorkerState()
            worker = asyncio.create_task(run_worker(factory, broker_url, {
                "transcript_copy": copy,
                "transcript_index": partial(handle_transcript_index, factory, settings),
            }, partial(enqueue_transcript_jobs, settings=settings), state=state,
                queue_name=queue_name, poll_seconds=0.05, lease_seconds=20))
            try:
                await _event(published)
                replacement.unlink()
                first = contexts[0]
                await connections[0].transport.connection.close()
                await _event(first.lost, timeout=3)
                assert not state.healthy
                release.set()
                async with asyncio.timeout(10):
                    while len(connections) < 2 or not state.healthy:
                        await asyncio.sleep(0.02)
                before_retry = await asyncio.to_thread(
                    _transcript_state, factory, request.transcript_id)
                assert before_retry[1:4] == ("processing", 1, None)
                await asyncio.to_thread(_expire_copy_leases, factory, first, request.transcript_id)
                assert await _ready(factory, request.transcript_id) == (
                    "ready", "ready", 2, request.expected_sha256, request.original_source_path)
                for _ in range(5):
                    await channel.default_exchange.publish(aio_pika.Message(
                        encode_message(first.job_id), message_id=duplicate_marker,
                    ), routing_key=queue_name, mandatory=True)
                await _event(duplicates_processed)
                assert len(captured) == 2 and captured[0] == captured[1]
                after_duplicates = await asyncio.to_thread(
                    _transcript_state, factory, request.transcript_id)
                assert after_duplicates == (
                    "ready", "ready", 2, request.expected_sha256, request.original_source_path)
            finally:
                release.set()
                await _stop(worker)
                await _cleanup(channel, queue_name)

    monkeypatch.setattr(TranscriptStorage, "capture", capture)
    monkeypatch.setattr(runtime.aio_pika, "connect", connect)
    asyncio.run(scenario())
