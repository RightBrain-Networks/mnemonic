"""Detached browser reads retain native ownership until request cleanup completes."""

import asyncio
import threading
from uuid import uuid4

import anyio
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mnemonic_api.application.browser_boundary import BrowserBoundaryMiddleware
from mnemonic_api.application.browser_file_transport import FileResponsePolicy, execute_file_request
from mnemonic_api.config import Settings
from mnemonic_api.database import Database, database_sqlstate
from mnemonic_api.models import Project

PATH = "/api/mnemonic/projects"
CONFIG = Settings(database_url="postgresql://localhost:1/unavailable", api_key="secret-" * 8)


def request_scope(app):
    return {
        "type": "http", "method": "GET", "path": PATH, "query_string": b"", "app": app,
        "headers": [(b"host", b"localhost:3000")], "scheme": "http",
        "server": ("localhost", 3000), "client": ("test", 123), "root_path": "",
    }


async def dispatch(transport, app, receive, sent, pending):
    async def send(message):
        sent.append(message)

    if transport == "json":
        await BrowserBoundaryMiddleware(app, pending_tasks=pending)(
            request_scope(app), receive, send,
        )
    else:
        await execute_file_request(
            app, request_scope(app), receive, send, timeout=0.2, pending_tasks=pending,
            policy=FileResponsePolicy(json_maximum=1024, failure_detail="failed",
                                      redirect_detail="redirect", encoding_detail="encoded"),
        )


class SessionLifecycle:
    def __init__(self, engine, loop):
        self.engine = engine
        self.loop = loop
        self.entered = asyncio.Event()
        self.closed = asyncio.Event()
        self.finish = threading.Event()
        self.running = threading.Event()
        self.worker_finished = threading.Event()
        self.concurrent_close = False
        self.sessions = []

    def factory(self):
        owner = self

        class TrackingSession(Session):
            def close(self):
                owner.concurrent_close |= owner.running.is_set()
                super().close()
                owner.loop.call_soon_threadsafe(owner.closed.set)

        session = TrackingSession(self.engine)
        self.sessions.append(session)
        return session

    def read(self, session, statement):
        self.running.set()
        try:
            session.execute(statement).one()
            self.loop.call_soon_threadsafe(self.entered.set)
            assert self.finish.wait(5), "Test worker was not released"
            # A prematurely closed Session can silently reopen a transaction here.
            session.execute(statement).one()
            return {"ok": True}
        finally:
            self.running.clear()
            self.worker_finished.set()

    async def drain(self, pending):
        self.finish.set()
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 2)
        assert await anyio.to_thread.run_sync(self.worker_finished.wait, 2)
        for session in self.sessions:
            await anyio.to_thread.run_sync(session.close)


def assert_project_locked(engine, statement):
    with engine.connect() as connection:
        with pytest.raises(OperationalError) as caught:
            connection.execute(statement.with_for_update(nowait=True)).one()
        assert database_sqlstate(caught.value) == "55P03"


@pytest.mark.postgres
@pytest.mark.parametrize("transport", ["json", "file"])
@pytest.mark.parametrize("detach", ["timeout", "disconnect"])
def test_detached_sync_read_closes_session_only_after_worker_and_releases_project_lock(
    pristine_postgres_engine, monkeypatch, transport, detach,
):
    engine = pristine_postgres_engine
    with Session(engine) as setup:
        project = Project(name="Cancellation ownership", slug=uuid4().hex)
        setup.add(project)
        setup.flush()
        statement = select(Project.id).where(Project.id == project.id).with_for_update()
        setup.commit()
    monkeypatch.setattr("mnemonic_api.application.browser_boundary.request_timeout",
                        lambda *args: 0.2)

    async def scenario():
        app = FastAPI()
        app.state.settings = CONFIG
        lifecycle = SessionLifecycle(engine, asyncio.get_running_loop())
        app.state.session_factory = lifecycle.factory

        @app.get(PATH)
        def read(session: Database):
            return lifecycle.read(session, statement)

        async def receive():
            await lifecycle.entered.wait()
            if detach == "disconnect":
                return {"type": "http.disconnect"}
            await asyncio.Event().wait()

        sent, pending = [], set()
        wire = asyncio.create_task(dispatch(transport, app, receive, sent, pending))
        try:
            await asyncio.wait_for(lifecycle.entered.wait(), 1)
            await asyncio.wait_for(wire, 1)
            assert [item["status"] for item in sent if "status" in item] == (
                [502] if detach == "timeout" else []
            )
            await asyncio.sleep(0.02)
            assert not lifecycle.closed.is_set(), "Session closed while its worker was running"
            assert not lifecycle.concurrent_close
            assert pending, "Native work must remain tracked after the wire request detaches"
            assert_project_locked(engine, statement)
            lifecycle.finish.set()
            await asyncio.wait_for(lifecycle.closed.wait(), 1)
            await asyncio.wait_for(asyncio.gather(*pending), 1)
            assert lifecycle.worker_finished.is_set()
            assert not lifecycle.concurrent_close
            # Cleanup, without GC or a server transaction watchdog, releases the lock.
            with engine.begin() as connection:
                connection.execute(statement.with_for_update(nowait=True)).one()
            assert all(not session.in_transaction() for session in lifecycle.sessions)
        finally:
            await lifecycle.drain(pending)
            await wire

    asyncio.run(scenario())


@pytest.mark.parametrize("transport", ["json", "file"])
def test_cancelled_async_read_closes_session_when_default_worker_capacity_is_exhausted(
    monkeypatch, transport,
):
    monkeypatch.setattr("mnemonic_api.application.browser_boundary.request_timeout",
                        lambda *args: 0.2)

    async def scenario():
        app = FastAPI()
        app.state.settings = CONFIG
        entered, cancelled, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        limiter = anyio.to_thread.current_default_thread_limiter()
        borrower = object()

        class TrackingSession(Session):
            def close(self):
                super().close()
                loop.call_soon_threadsafe(closed.set)

        app.state.session_factory = TrackingSession

        @app.get(PATH)
        async def read(session: Database):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def receive():
            await entered.wait()
            # Dependency acquisition has completed; consume the default pool's
            # remaining capacity before cancellation starts dependency cleanup.
            limiter.total_tokens = 1
            await limiter.acquire_on_behalf_of(borrower)
            return {"type": "http.disconnect"}

        pending, sent = set(), []
        original_tokens = limiter.total_tokens
        try:
            await asyncio.wait_for(dispatch(transport, app, receive, sent, pending), 1)
            await asyncio.wait_for(cancelled.wait(), 1)
            await asyncio.wait_for(closed.wait(), 1)
            await asyncio.wait_for(asyncio.gather(*pending), 1)
            assert sent == []
        finally:
            limiter.release_on_behalf_of(borrower)
            limiter.total_tokens = original_tokens

    asyncio.run(scenario())
