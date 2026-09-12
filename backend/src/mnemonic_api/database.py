from collections.abc import Iterator
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import CursorResult, Engine, Result
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.config import Settings
from mnemonic_api.pool_deadlines import DeadlineQueuePool
from mnemonic_api.summary_limits import DEFAULT_WORK_SUMMARY_MAX_CHARS


def database_sqlstate(error: DBAPIError) -> str | None:
    """Return a driver SQLSTATE without exposing driver-specific exception types."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    return sqlstate if isinstance(sqlstate, str) else None


def rows_affected(result: Result[Any]) -> int:
    """Row count of an INSERT, UPDATE, or DELETE executed through a Session.

    ``Session.execute`` is typed as returning a plain ``Result``, but a DML
    statement always yields a ``CursorResult`` at runtime.
    """
    return cast(CursorResult[Any], result).rowcount


def begin_coherent_read(database: Session, *, read_only: bool = True) -> None:
    """Pin a snapshot before the first query of a composite projected response."""
    mode = ", READ ONLY" if read_only else ""
    database.execute(text(f"SET TRANSACTION ISOLATION LEVEL REPEATABLE READ{mode}"))


def build_engine(settings: Settings) -> Engine:
    engine = create_engine(
        settings.database_url.get_secret_value(),
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
        poolclass=DeadlineQueuePool,
        pool_size=5,
        max_overflow=10,
        # A protected mutation has one end-to-end receipt-reservation budget.
        # Checkout cannot consume a longer, independent wait before PostgreSQL's
        # lock/statement timeout begins.
        pool_timeout=settings.client_operation_wait_seconds,
        connect_args={
            "connect_timeout": min(5, settings.client_operation_wait_seconds)
        },
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _ensure_utc_timezone(connection, _record) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SET TIME ZONE 'UTC'")

    return engine


def build_session_factory(
    engine: Engine, *, work_summary_max_chars: int = DEFAULT_WORK_SUMMARY_MAX_CHARS
) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, expire_on_commit=False,
        info={"work_summary_max_chars": work_summary_max_chars},
    )


def get_session(request: Request) -> Iterator[Session]:
    # A failed request closes and rolls back its uncommitted transaction.
    with request.app.state.session_factory() as session:
        session.info["prompt_root"] = request.app.state.settings.prompt_root
        yield session


# Route parameter type for the request-scoped session above.
Database = Annotated[Session, Depends(get_session)]
