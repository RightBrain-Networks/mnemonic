"""Explicit reuse provenance remains sealed, recoverable and checked during restore."""

import io
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_backup.archive import BackupError, restore_project

from .test_decision_impact_postgres import changed
from .test_knowledge_postgres import path
from .test_product_decision_migration_postgres import migrate
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_supersession_reuse_postgres import setup_supersession, unchanged

pytestmark = pytest.mark.postgres


def declared_revision(api, project):
    target, old, links, replacement, _ = setup_supersession(api, project)
    revised = changed(api, project, target, replacement, "Replacement with unrelated annotation",
                      change={"unchanged_supersessions": [unchanged(old, replacement)]}, **links)
    return revised


@pytest.mark.parametrize("mutation", [
    "UPDATE decision_supersession_reuses SET reason='Rewritten history'",
    "DELETE FROM decision_supersession_reuses",
    "TRUNCATE decision_supersession_reuses",
])
def test_declaration_history_cannot_be_rewritten(api, project, postgres_engine, mutation):
    declared_revision(api, project)
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(mutation))


def test_declaration_cannot_be_added_after_source_revision_transaction(
    api, project, postgres_engine,
):
    revised = declared_revision(api, project)
    with postgres_engine.begin() as connection:
        row = connection.execute(
            text("SELECT * FROM decision_supersession_reuses")
        ).mappings().one()
        values = dict(row)
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text("DELETE FROM decision_supersession_reuses"))
    names = ",".join(values)
    parameters = ",".join(":" + name for name in values)
    with pytest.raises(DBAPIError, match="sealed supersession"):
        with postgres_engine.begin() as connection:
            connection.execute(text(f"INSERT INTO decision_supersession_reuses ({names}) "
                                    f"VALUES ({parameters})"), values)
    assert api.get(path(project) + f"/decisions/{revised['reference']}").status_code == 200


def test_backup_roundtrip_preserves_declaration_and_refuses_destructive_downgrade(
    api, project, postgres_engine,
):
    revised = declared_revision(api, project)
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    after = _snapshot(postgres_engine, project)
    assert after["decision_supersession_reuses"] == before["decision_supersession_reuses"]
    assert api.get(path(project) + f"/decisions/{revised['reference']}").json()["change"] \
        == revised["change"]
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0059_domain_decision_reviews", downgrade=True)


@pytest.mark.parametrize("damage", ["missing", "reason", "governing", "target"])
def test_restore_rejects_invented_or_missing_reuse_declaration(
    api, project, postgres_engine, damage,
):
    declared_revision(api, project)
    before = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)

    def corrupt(_header, rows):
        declaration = rows["decision_supersession_reuses"][0]
        if damage == "missing":
            rows["decision_supersession_reuses"] = []
        elif damage == "reason":
            declaration["reason"] = "Invented after the original request"
        else:
            declaration[f"{damage}_revision_id"] = declaration["revision_id"]

    with pytest.raises(BackupError):
        restore_project(
            postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(archive, corrupt)),
        )
    assert _snapshot(postgres_engine, project) == before
