"""Automatic cycles preserve historical admission provenance and storage fences."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from .test_decision_change_storage_postgres import _change, _findings
from .test_product_decision_migration_postgres import migrate

pytestmark = pytest.mark.postgres


def test_automatic_successor_is_unique_and_prevents_unsafe_downgrade(
    api, project, postgres_engine,
):
    parent = _change(postgres_engine, project)
    child = _change(postgres_engine, project, parent_change_id=parent,
                    admission_actor_client="mnemonic",
                    admission_actor_session_id=f"decision-change:{parent}")
    with pytest.raises(DBAPIError):
        _change(postgres_engine, project, parent_change_id=parent,
                admission_actor_client="mnemonic", admission_actor_session_id="duplicate")
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0070_backup_settings", downgrade=True)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT parent_change_id FROM decision_changes WHERE id=:id"),
                                 {"id": child}) == parent
    assert _findings(postgres_engine) == {}


def test_upgrade_preserves_historical_dashboard_successor_and_round_trips(
    api, project, postgres_engine,
):
    migrate(postgres_engine, "0070_backup_settings", downgrade=True)
    parent = _change(postgres_engine, project)
    _change(postgres_engine, project, parent_change_id=parent,
            admission_actor_client="dashboard", admission_actor_session_id="historical-owner")
    with postgres_engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM decision_changes ORDER BY id")).all()
    migrate(postgres_engine, "head")
    migrate(postgres_engine, "0070_backup_settings", downgrade=True)
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        after = connection.execute(text("SELECT * FROM decision_changes ORDER BY id")).all()
        assert after == before
    assert _findings(postgres_engine) == {}
