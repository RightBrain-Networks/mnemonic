"""Search deadlines and failures are read semantics, never mutation receipts."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from uuid import UUID

from psycopg import Error as DriverError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read, database_sqlstate
from mnemonic_api.errors import ApplicationError
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project


def search_unavailable() -> ApplicationError:
    return ApplicationError(
        503,
        "search_temporarily_unavailable",
        "Search could not finish because the server is busy or its time limit was reached. "
        "Wait a few seconds and search again.",
    )


@contextmanager
def search_transaction(
    database: Session, projects: Sequence[UUID], *, artifacts: bool
) -> Iterator[None]:
    try:
        if artifacts:
            with project_mutation(
                database, projects[0], additional_project_ids=projects[1:], domain_seconds=120
            ):
                yield
        else:
            # Coherent corpus and hydration with no project row locks. The server
            # watchdog bounds index construction as well as database statements.
            begin_coherent_read(database)
            database.execute(text("SET LOCAL statement_timeout = '120s'"))
            database.execute(text("SET LOCAL transaction_timeout = '120s'"))
            for project_id in projects:
                require_project(database, project_id)
            yield
    except ApplicationError as error:
        if (
            not isinstance(error.detail, dict)
            or error.detail.get("code") != "project_mutation_unavailable"
        ):
            raise
        raise search_unavailable() from None
    except PoolTimeoutError:
        _discard_transaction(database)
        raise search_unavailable() from None
    except DBAPIError as error:
        state = database_sqlstate(error)
        if not (
            state in {"55P03", "57014", "25P04", "40P01"}
            or error.connection_invalidated
            or isinstance(state, str)
            and state.startswith("08")
        ):
            raise
        _discard_transaction(database)
        raise search_unavailable() from None


def _discard_transaction(database: Session) -> None:
    try:
        database.rollback()
    except DBAPIError, DriverError:
        database.invalidate()
