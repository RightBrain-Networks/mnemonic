"""Fresh-domain waits roll back and preserve the caller's recovery contract."""

import time
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

import mnemonic_api.services.project_mutations as mutation_module
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Project
from mnemonic_api.services.project_mutations import project_mutation

pytestmark = pytest.mark.postgres


def _project(engine: Engine):
    with Session(engine) as database, database.begin():
        project = Project(name="Deadline test", slug="deadline-" + uuid4().hex)
        database.add(project)
        database.flush()
        return project.id


@pytest.mark.parametrize("protected", [False, True])
def test_project_lock_timeout_is_bounded_and_sanitized(postgres_engine: Engine, protected: bool):
    project_id = _project(postgres_engine)
    with postgres_engine.begin() as holder, Session(postgres_engine) as database:
        holder.execute(text("SELECT id FROM projects WHERE id=:p FOR UPDATE"), {"p": project_id})
        started = time.monotonic()
        with pytest.raises(ApplicationError) as caught:
            with project_mutation(database, project_id, protected=protected):
                pytest.fail("The held project lock must not be acquired")
        assert 1.5 <= time.monotonic() - started < 4
        assert caught.value.status_code == 503
        assert caught.value.detail["code"] == (
            "client_operation_unavailable" if protected else "project_mutation_unavailable"
        )
        assert database.scalar(text("SELECT 1")) == 1


def test_long_query_uses_one_domain_budget_and_discards_terminated_connection(
    postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    project_id = _project(postgres_engine)
    monkeypatch.setattr(mutation_module, "DOMAIN_SECONDS", 0.15)
    with Session(postgres_engine) as database:
        with pytest.raises(ApplicationError) as caught:
            with project_mutation(database, project_id, protected=True):
                database.execute(
                    text("UPDATE projects SET name='Must roll back' WHERE id=:p"), {"p": project_id}
                )
                database.execute(text("SELECT pg_sleep(0.4)"))
                database.commit()
        assert caught.value.detail["code"] == "client_operation_unavailable"
        assert (
            database.scalar(text("SELECT name FROM projects WHERE id=:p"), {"p": project_id})
            == "Deadline test"
        )


def test_statement_budget_decreases_without_resetting_watchdog(
    postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    project_id = _project(postgres_engine)
    monkeypatch.setattr(mutation_module, "DOMAIN_SECONDS", 1.0)
    with Session(postgres_engine) as database:
        with project_mutation(database, project_id):
            original_watchdog = database.scalar(text("SHOW transaction_timeout"))
            initial_statement = database.scalar(text("SHOW statement_timeout"))
            database.execute(text("SELECT pg_sleep(0.05)"))
            next_statement = database.scalar(text("SHOW statement_timeout"))
            assert database.scalar(text("SHOW transaction_timeout")) == original_watchdog
            assert float(next_statement.removesuffix("ms")) < float(
                initial_statement.removesuffix("ms")
            )
            database.commit()


def test_unregistered_pool_pressure_is_sanitized(postgres_engine: Engine):
    constrained = create_engine(postgres_engine.url, pool_size=1, max_overflow=0, pool_timeout=0.1)
    try:
        with constrained.connect(), Session(constrained) as database:
            with pytest.raises(ApplicationError) as caught:
                with project_mutation(database, None):
                    pytest.fail("A full pool must not start domain execution")
            assert caught.value.detail["code"] == "project_mutation_unavailable"
    finally:
        constrained.dispose()


def test_prior_reservation_time_does_not_consume_fresh_domain_budget(
    postgres_engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    project_id = _project(postgres_engine)
    monkeypatch.setattr(mutation_module, "DOMAIN_SECONDS", 0.15)
    with Session(postgres_engine) as database:
        database.execute(text("SELECT pg_sleep(0.2)"))
        with project_mutation(database, project_id, protected=True):
            database.execute(text("SELECT pg_sleep(0.03)"))
            database.commit()
