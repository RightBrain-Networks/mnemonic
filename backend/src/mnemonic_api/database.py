from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.config import Settings


def build_engine(settings: Settings) -> Engine:
    engine = create_engine(
        settings.database_url.get_secret_value(),
        isolation_level="READ COMMITTED",
        pool_pre_ping=True,
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


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Iterator[Session]:
    # A failed request closes and rolls back its uncommitted transaction.
    with request.app.state.session_factory() as session:
        yield session
