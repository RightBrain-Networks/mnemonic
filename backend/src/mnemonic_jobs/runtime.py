"""A bounded RabbitMQ consumer and recoverable PostgreSQL outbox dispatcher.

The database owns job state. Delivery is at least once, including after broker
data loss, so handlers must use their domain fences and idempotent publication.
Blocking handlers run in a thread while AMQP and database heartbeats keep running.
"""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractIncomingMessage
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from tenacity import AsyncRetrying, wait_random_exponential

from mnemonic_jobs.ledger import (
    JobContext,
    LostLease,
    PermanentJobError,
    RetryJob,
    claim_job,
    finish_job,
    heartbeat_job,
    reserve_dispatch,
)
from mnemonic_jobs.models import BackgroundJob

logger = logging.getLogger(__name__)
Handler = Callable[[JobContext], dict[str, Any] | None]
Scheduler = Callable[[Session], None]


@dataclass
class WorkerState:
    connected: bool = False
    last_dispatch: float = 0
    max_dispatch_age: float = 90

    @property
    def healthy(self) -> bool:
        return self.connected and time.monotonic() - self.last_dispatch < self.max_dispatch_age


def encode_message(job_id: UUID) -> bytes:
    return json.dumps({"version": 1, "job_id": str(job_id)}, separators=(",", ":")).encode()


def decode_message(body: bytes) -> UUID:
    if len(body) > 256:
        raise ValueError("Job message exceeds its fixed identifier envelope")
    data = json.loads(body)
    if not isinstance(data, dict) or set(data) != {"version", "job_id"}:
        raise ValueError("Invalid job envelope")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported job envelope version")
    if not isinstance(data["job_id"], str):
        raise ValueError("Invalid job identifier")
    identifier = UUID(data["job_id"])
    if str(identifier) != data["job_id"]:
        raise ValueError("Job identifiers must be canonical UUIDs")
    return identifier


def _transaction(factory: sessionmaker[Session], operation: Callable[[Session], Any]) -> Any:
    with factory.begin() as database:
        return operation(database)


def _reserve(factory: sessionmaker[Session], schedule: Scheduler) -> list[UUID]:
    # Scheduling and publishing reservations use separate short transactions so
    # queue delivery never waits for network IO while holding a database lock.
    _transaction(factory, schedule)
    return _transaction(factory, lambda database: reserve_dispatch(database))


async def _dispatch(channel: AbstractChannel, factory: sessionmaker[Session], schedule: Scheduler,
                    state: WorkerState, queue_name: str, poll_seconds: float) -> None:
    while True:
        identifiers = await asyncio.to_thread(_reserve, factory, schedule)
        for identifier in identifiers:
            confirmation = await channel.default_exchange.publish(
                aio_pika.Message(
                    encode_message(identifier), content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT, message_id=str(identifier),
                    expiration=300,
                ), routing_key=queue_name, mandatory=True, timeout=10,
            )
            if confirmation is None or confirmation is False:
                raise RuntimeError("Job publication was not confirmed")
        state.last_dispatch = time.monotonic()
        await asyncio.sleep(poll_seconds)


async def _heartbeat(factory: sessionmaker[Session], context: JobContext,
                     lease_seconds: int) -> None:
    while True:
        await asyncio.sleep(lease_seconds / 4)
        try:
            owned = await asyncio.to_thread(
                _transaction, factory,
                lambda database: heartbeat_job(database, context, lease_seconds=lease_seconds),
            )
        except Exception:
            # Never emit database exceptions, which may contain identifiers or
            # connection strings. A failed heartbeat must fence this attempt.
            context.lost.set()
            return
        if not owned:
            context.lost.set()
            return


def _invoke(handler: Handler | None, context: JobContext) -> tuple[dict | None, PermanentJobError
                                                                | None]:
    try:
        if handler is None:
            raise PermanentJobError("unsupported_job_kind")
        result = handler(context)
        if result is not None and (not isinstance(result, dict) or len(
            json.dumps(result, ensure_ascii=True, allow_nan=False).encode()
        ) > 8000):
            raise PermanentJobError("invalid_job_result")
        return result, None
    except LostLease:
        context.lost.set()
        return None, None
    except PermanentJobError as error:
        return None, error
    except Exception as error:
        logger.warning("Job handler failed (%s)", type(error).__name__)
        return None, RetryJob("job_execution_failed", min(600, 2 ** min(context.attempts, 9)))


async def _execute(factory: sessionmaker[Session], context: JobContext, handler: Handler | None,
                   lease_seconds: int) -> None:
    heartbeat = asyncio.create_task(_heartbeat(factory, context, lease_seconds))
    invocation = asyncio.create_task(asyncio.to_thread(_invoke, handler, context))
    try:
        result, error = await asyncio.shield(invocation)
        if not context.lost.is_set():
            await asyncio.to_thread(_transaction, factory, lambda database: finish_job(
                database, context, result=result, error=error,
            ))
    except asyncio.CancelledError:
        context.lost.set()
        # Do not detach a still-running handler on connection loss or shutdown.
        # Domain transactions check ownership before their final publication.
        with suppress(Exception):
            await asyncio.shield(invocation)
        raise
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


def _claim(factory: sessionmaker[Session], identifier: UUID,
           lease_seconds: int) -> tuple[bool, JobContext | None]:
    with factory.begin() as database:
        exists = database.scalar(select(BackgroundJob.id).where(
            BackgroundJob.id == identifier
        )) is not None
        return exists, claim_job(database, identifier, lease_seconds=lease_seconds)


async def _deliver(message: AbstractIncomingMessage, factory: sessionmaker[Session],
                   handlers: dict[str, Handler], lease_seconds: int) -> None:
    try:
        identifier = decode_message(message.body)
    except (ValueError, TypeError, UnicodeError):
        await message.reject(requeue=False)
        return
    exists, context = await asyncio.to_thread(_claim, factory, identifier, lease_seconds)
    if not exists:
        await message.reject(requeue=False)
        return
    if context is not None:
        await _execute(factory, context, handlers.get(context.kind), lease_seconds)
    # Claim/completion/retry was committed before this ACK. Lost ownership and
    # duplicate messages are safe to ACK: the ledger remains redeliverable.
    await message.ack()


async def _declare(channel: AbstractChannel, queue_name: str):
    dead_name = f"{queue_name}.dead"
    await channel.declare_queue(dead_name, durable=True, arguments={
        "x-queue-type": "quorum", "x-max-length": 1000, "x-message-ttl": 604800000,
    })
    return await channel.declare_queue(queue_name, durable=True, arguments={
        "x-queue-type": "quorum", "x-max-length": 10000, "x-overflow": "reject-publish",
        "x-delivery-limit": 10, "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": dead_name,
    })


async def _consume(channel: AbstractChannel, factory: sessionmaker[Session],
                   handlers: dict[str, Handler], queue_name: str, lease_seconds: int) -> None:
    queue = await _declare(channel, queue_name)
    async with queue.iterator() as messages:
        async for message in messages:
            await _deliver(message, factory, handlers, lease_seconds)
    raise ConnectionError("Job consumer stopped")


async def _watch_connection(connection: AbstractConnection, state: WorkerState) -> None:
    closed = asyncio.Event()

    def disconnected(*_args: object) -> None:
        state.connected = False
        closed.set()

    # aio-pika's closed() future only resolves on explicit close(). Transport
    # failures also invoke close_callbacks, including while a handler is blocked.
    connection.close_callbacks.add(disconnected)
    try:
        if not connection.connected.is_set():
            disconnected()
        await closed.wait()
        raise ConnectionError("Job connection closed")
    finally:
        state.connected = False
        connection.close_callbacks.discard(disconnected)


async def _connection(factory: sessionmaker[Session], broker_url: str,
                      handlers: dict[str, Handler], schedule: Scheduler, state: WorkerState,
                      queue_name: str, poll_seconds: float, lease_seconds: int) -> None:
    connection = await aio_pika.connect(broker_url, timeout=10, heartbeat=30)
    async with connection:
        publisher = await connection.channel(publisher_confirms=True, on_return_raises=True)
        consumer = await connection.channel()
        await consumer.set_qos(prefetch_count=1)
        await _declare(publisher, queue_name)
        state.connected = True
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_watch_connection(connection, state))
            tasks.create_task(_dispatch(publisher, factory, schedule, state, queue_name,
                                        poll_seconds))
            tasks.create_task(_consume(consumer, factory, handlers, queue_name, lease_seconds))


async def run_worker(factory: sessionmaker[Session], broker_url: str,
                     handlers: dict[str, Handler], schedule: Scheduler, *,
                     state: WorkerState | None = None, queue_name: str = "mnemonic.jobs",
                     poll_seconds: float = 5, lease_seconds: int = 120) -> None:
    """Run until cancelled; jittered reconnect never logs a credential-bearing URL."""
    if poll_seconds <= 0 or lease_seconds < 20:
        raise ValueError("Invalid job polling or lease duration")
    state = state or WorkerState()
    # aio-pika/aiormq otherwise log broker URL values on transport errors.
    for name in ("aio_pika", "aiormq"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    async for attempt in AsyncRetrying(wait=wait_random_exponential(multiplier=1, max=30)):
        with attempt:
            try:
                await _connection(factory, broker_url, handlers, schedule, state, queue_name,
                                  poll_seconds, lease_seconds)
            except Exception as error:
                logger.warning("Job transport unavailable (%s)", type(error).__name__)
                raise
            finally:
                state.connected = False
