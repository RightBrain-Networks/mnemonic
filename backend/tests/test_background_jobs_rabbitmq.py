"""Exercise confirmed quorum-queue delivery with real PostgreSQL and RabbitMQ."""

import asyncio
import os
from contextlib import suppress
from datetime import timedelta
from threading import Event
from uuid import uuid4

import aio_pika
import pytest
from sqlalchemy import func, update
from sqlalchemy.orm import sessionmaker

from mnemonic_jobs import LostLease, RetryJob, enqueue_job
from mnemonic_jobs.ledger import claim_job, reserve_dispatch
from mnemonic_jobs.models import BackgroundJob
from mnemonic_jobs.runtime import WorkerState, _declare, encode_message, run_worker

pytestmark = pytest.mark.postgres


@pytest.fixture
def broker_url():
    value = os.environ.get("RABBITMQ_TEST_URL")
    if not value:
        pytest.skip("Set RABBITMQ_TEST_URL to run real RabbitMQ integration tests")
    return value


def _enqueue(factory):
    with factory.begin() as database:
        return enqueue_job(database, "backup_create", str(uuid4()), {"project_id": str(uuid4())})


def _status(factory, identifier):
    with factory() as database:
        row = database.get(BackgroundJob, identifier)
        return row.status, row.attempts, row.result


async def _completed(factory, identifier):
    async with asyncio.timeout(30):
        while True:
            status, attempts, result = await asyncio.to_thread(_status, factory, identifier)
            if status == "succeeded":
                return attempts, result
            assert status != "failed"
            await asyncio.sleep(0.05)


async def _stop(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _cleanup(channel, queue_name):
    for name in (queue_name, f"{queue_name}.dead"):
        queue = await channel.get_queue(name, ensure=False)
        await queue.delete(if_unused=False, if_empty=False)


def test_quorum_delivery_retry_reconnect_and_duplicate_suppression(
    pristine_postgres_engine, broker_url, monkeypatch,
):
    factory = sessionmaker(pristine_postgres_engine, expire_on_commit=False)
    calls, connections = [], []
    original_connect = aio_pika.connect

    async def connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def handle(context):
        calls.append(context.job_id)
        if context.attempts == 1:
            raise RetryJob("synthetic_transient", 1)
        return {"id": str(context.job_id)}

    async def scenario():
        queue_name = f"mnemonic.test.{uuid4().hex}"
        admin = await original_connect(broker_url)
        async with admin:
            channel = await admin.channel(publisher_confirms=True, on_return_raises=True)
            state = WorkerState()
            task = asyncio.create_task(run_worker(
                factory, broker_url, {"backup_create": handle}, lambda _database: None,
                state=state, queue_name=queue_name, poll_seconds=0.05,
            ))
            try:
                identifier = await asyncio.to_thread(_enqueue, factory)
                assert await _completed(factory, identifier) == (2, {"id": str(identifier)})
                assert state.healthy
                await connections[0].close()
                async with asyncio.timeout(30):
                    while len(connections) < 2 or not state.healthy:
                        await asyncio.sleep(0.05)
                await channel.default_exchange.publish(aio_pika.Message(encode_message(identifier)),
                                                       routing_key=queue_name, mandatory=True)
                second = await asyncio.to_thread(_enqueue, factory)
                assert (await _completed(factory, second))[0] == 2
                assert calls.count(identifier) == 2
                await _dead_letters(channel, queue_name)
            finally:
                await _stop(task)
                await _cleanup(channel, queue_name)
            assert not state.healthy

    monkeypatch.setattr("mnemonic_jobs.runtime.aio_pika.connect", connect)
    asyncio.run(scenario())


class BlockingHandler:
    def __init__(self, factory):
        self.factory = factory
        self.started, self.release, self.rejected = Event(), Event(), Event()
        self.contexts, self.published = [], []

    def __call__(self, context):
        self.contexts.append(context)
        if len(self.contexts) == 1:
            self.started.set()
            if not self.release.wait(40):
                raise TimeoutError("Blocked test handler was not released")
        try:
            with self.factory.begin() as database:
                context.assert_owned(database)
                self.published.append(context.lease_token)
        except LostLease:
            self.rejected.set()
            raise
        return {"published": True}


async def _event(event, timeout=10):
    async with asyncio.timeout(timeout):
        while not event.is_set():
            await asyncio.sleep(0.02)


def _lease(factory, identifier):
    with factory() as database:
        row = database.get(BackgroundJob, identifier)
        return row.lease_token, row.lease_expires_at


def test_blocked_handler_renews_lease_without_blocking_broker_loop(
    pristine_postgres_engine, broker_url,
):
    factory = sessionmaker(pristine_postgres_engine, expire_on_commit=False)
    handler = BlockingHandler(factory)

    async def scenario():
        queue_name = f"mnemonic.test.{uuid4().hex}"
        connection = await aio_pika.connect(broker_url)
        async with connection:
            channel = await connection.channel()
            state = WorkerState()
            task = asyncio.create_task(run_worker(
                factory, broker_url, {"backup_create": handler}, lambda _database: None,
                state=state, queue_name=queue_name, poll_seconds=0.05, lease_seconds=20,
            ))
            try:
                identifier = await asyncio.to_thread(_enqueue, factory)
                await _event(handler.started)
                token, initial_expiry = await asyncio.to_thread(_lease, factory, identifier)
                async with asyncio.timeout(10):
                    expiry = initial_expiry
                    while expiry <= initial_expiry + timedelta(seconds=3):
                        await asyncio.sleep(0.05)
                        current, expiry = await asyncio.to_thread(_lease, factory, identifier)
                assert current == token and not handler.release.is_set()
                assert state.healthy and not handler.contexts[0].lost.is_set()
                with factory.begin() as database:
                    assert claim_job(database, identifier) is None
                    assert reserve_dispatch(database) == []
                handler.release.set()
                assert await _completed(factory, identifier) == (1, {"published": True})
                assert handler.published == [token]
            finally:
                handler.release.set()
                await _stop(task)
                await _cleanup(channel, queue_name)

    asyncio.run(scenario())


@pytest.mark.parametrize("disconnect", ["connection", "transport"])
def test_disconnect_fences_blocked_handler_and_recovers_delivery(
    pristine_postgres_engine, broker_url, monkeypatch, disconnect,
):
    factory = sessionmaker(pristine_postgres_engine, expire_on_commit=False)
    handler, connections = BlockingHandler(factory), []
    original_connect = aio_pika.connect

    async def connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    async def scenario():
        queue_name = f"mnemonic.test.{uuid4().hex}"
        connection = await original_connect(broker_url)
        async with connection:
            channel = await connection.channel()
            state = WorkerState()
            task = asyncio.create_task(run_worker(
                factory, broker_url, {"backup_create": handler}, lambda _database: None,
                state=state, queue_name=queue_name, poll_seconds=0.05, lease_seconds=20,
            ))
            try:
                identifier = await asyncio.to_thread(_enqueue, factory)
                await _event(handler.started)
                old = handler.contexts[0]
                if disconnect == "transport":
                    await connections[0].transport.connection.close()
                else:
                    await connections[0].close()
                await _event(old.lost, timeout=3)
                assert not state.healthy
                handler.release.set()
                await _event(handler.rejected)
                await _recover_disconnected(factory, identifier, connections, state)
                assert await _completed(factory, identifier) == (2, {"published": True})
                assert len(handler.contexts) == 2
                assert handler.published == [handler.contexts[1].lease_token]
                assert handler.contexts[1].lease_token != old.lease_token
            finally:
                handler.release.set()
                await _stop(task)
                await _cleanup(channel, queue_name)

    monkeypatch.setattr("mnemonic_jobs.runtime.aio_pika.connect", connect)
    asyncio.run(scenario())


async def _recover_disconnected(factory, identifier, connections, state):
    async with asyncio.timeout(10):
        while len(connections) < 2 or not state.healthy:
            await asyncio.sleep(0.02)
    # The old invocation and its heartbeat have now stopped. Advance the durable
    # lease and outbox deadlines instead of waiting for the production 60s timer.
    with factory.begin() as database:
        database.execute(update(BackgroundJob).where(BackgroundJob.id == identifier).values(
            lease_expires_at=func.clock_timestamp() - timedelta(seconds=1),
            publish_after=func.clock_timestamp() - timedelta(seconds=1),
        ))


async def _dead_letters(channel, queue_name):
    for body in (b"malformed", encode_message(uuid4())):
        await channel.default_exchange.publish(aio_pika.Message(body), routing_key=queue_name)
    dead = await channel.get_queue(f"{queue_name}.dead")
    async with asyncio.timeout(10):
        for _ in range(2):
            message = None
            while message is None:
                message = await dead.get(fail=False)
                if message is None:
                    await asyncio.sleep(0.05)
            await message.ack()


def test_ledger_republishes_after_broker_loses_confirmed_message(
    pristine_postgres_engine, broker_url,
):
    factory = sessionmaker(pristine_postgres_engine, expire_on_commit=False)
    identifier = _enqueue(factory)
    with factory.begin() as database:
        assert reserve_dispatch(database) == [identifier]

    async def scenario():
        queue_name = f"mnemonic.test.{uuid4().hex}"
        connection = await aio_pika.connect(broker_url)
        async with connection:
            channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
            queue = await _declare(channel, queue_name)
            assert await channel.default_exchange.publish(aio_pika.Message(
                encode_message(identifier), delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ), routing_key=queue_name, mandatory=True)
            await queue.purge()  # Simulate broker data loss after a confirmed publication.
            with factory.begin() as database:
                database.execute(update(BackgroundJob).where(BackgroundJob.id == identifier).values(
                    publish_after=func.clock_timestamp() - timedelta(seconds=1),
                ))
            task = asyncio.create_task(run_worker(
                factory, broker_url, {"backup_create": lambda _context: {"recovered": True}},
                lambda _database: None, queue_name=queue_name, poll_seconds=0.05,
            ))
            try:
                assert await _completed(factory, identifier) == (1, {"recovered": True})
            finally:
                await _stop(task)
                await _cleanup(channel, queue_name)

    asyncio.run(scenario())
