"""One project-first lock and one bounded fresh-domain budget per mutation.

Receipt lookup/reservation happens before entering this scope. Statement limits
are recalculated from one monotonic deadline; PostgreSQL's transaction watchdog
is enabled once and never extended by an intervening statement. No network work
or LLM authoring belongs inside this scope.
"""

import math
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Error as DriverError
from sqlalchemy import Connection, event, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, client_operation_unavailable, not_found
from mnemonic_api.models import Project

DOMAIN_SECONDS = 10.0
LOCK_MILLISECONDS = 2000
_STATE_KEY = "mnemonic_project_mutation"
_TIMEOUT_STATES = frozenset({"55P03", "57014", "25P04", "40P01"})


class _DomainDeadlineExceeded(Exception):
    pass


@dataclass(frozen=True)
class _DomainBudget:
    project_ids: tuple[UUID, ...]
    deadline: float

    def remaining_milliseconds(self) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _DomainDeadlineExceeded
        return max(1, math.floor(remaining * 1000))


def _unavailable(protected: bool) -> ApplicationError:
    if protected:
        return client_operation_unavailable()
    return ApplicationError(
        503,
        "project_mutation_unavailable",
        "The project is temporarily busy. Read current state before retrying this change.",
    )


def _is_unavailable(error: DBAPIError | DriverError) -> bool:
    original = error.orig if isinstance(error, DBAPIError) else error
    state = getattr(original, "sqlstate", None)
    return (
        state in _TIMEOUT_STATES
        or (isinstance(state, str) and state.startswith("08"))
        or (isinstance(error, DBAPIError) and error.connection_invalidated)
    )


def _discard_failed_transaction(database: Session) -> None:
    try:
        database.rollback()
    except DBAPIError, DriverError:
        database.invalidate()


def _install_budget(connection: Connection, budget: _DomainBudget):
    def before_statement(
        _connection: Connection,
        cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        remaining = budget.remaining_milliseconds()
        # Integer values originate exclusively in this module. DBAPI execution
        # avoids recursively invoking this same SQLAlchemy statement listener.
        cursor.execute(f"SET LOCAL statement_timeout = '{remaining}ms'")
        cursor.execute(f"SET LOCAL lock_timeout = '{min(LOCK_MILLISECONDS, remaining)}ms'")

    remaining = budget.remaining_milliseconds()
    connection.exec_driver_sql(f"SET LOCAL transaction_timeout = '{remaining}ms'")
    event.listen(connection, "before_cursor_execute", before_statement)
    return before_statement


@contextmanager
def project_mutation(
    database: Session,
    project_id: UUID | None,
    *,
    additional_project_ids: Iterable[UUID] = (),
    protected: bool = False,
) -> Iterator[None]:
    """Bound fresh execution through its commit and lock every project in UUID order.

    None is only for project creation. Cross-project mutations provide their
    additional projects up front so opposite-direction moves cannot deadlock by
    taking source and destination locks in request order.
    """
    project_ids = tuple(
        sorted(
            ({project_id} if project_id is not None else set())
            | set(additional_project_ids),
            key=str,
        )
    )
    existing = database.info.get(_STATE_KEY)
    if isinstance(existing, _DomainBudget):
        if existing.project_ids != project_ids:
            raise RuntimeError("A project mutation cannot change its project scope")
        yield
        return
    budget = _DomainBudget(project_ids, time.monotonic() + DOMAIN_SECONDS)
    database.info[_STATE_KEY] = budget
    connection = None
    listener = None
    try:
        connection = database.connection()
        listener = _install_budget(connection, budget)
        for locked_project_id in project_ids:
            project = database.scalar(
                select(Project.id)
                .where(Project.id == locked_project_id)
                .with_for_update()
            )
            if project is None:
                raise not_found("project_not_found", "Project not found.")
        yield
    except _DomainDeadlineExceeded, PoolTimeoutError:
        _discard_failed_transaction(database)
        raise _unavailable(protected) from None
    except (DBAPIError, DriverError) as error:
        if not _is_unavailable(error):
            raise
        _discard_failed_transaction(database)
        raise _unavailable(protected) from None
    finally:
        if connection is not None and listener is not None:
            event.remove(connection, "before_cursor_execute", listener)
        database.info.pop(_STATE_KEY, None)
