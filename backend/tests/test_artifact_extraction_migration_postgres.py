"""Upgrade seeds only current available content; extraction metadata blocks downgrade."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


def migrate(engine, target, *, downgrade=False):
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, target)


def test_upgrade_backfills_live_historical_deleted_and_pending_revisions(
    api, project, postgres_engine,
):
    migrate(postgres_engine, "0026_artifact_library", downgrade=True)
    identities = [uuid4() for _ in range(4)]
    with postgres_engine.begin() as connection:
        for index, identity in enumerate(identities):
            connection.execute(text("""
                INSERT INTO artifacts(id, project_id, filename, description, relative_path,
                                      revision, size_bytes, sha256, deleted_at)
                VALUES(:id, :project, 'seed.txt', '', :path, 2, 4, repeat('a',64),
                       CASE WHEN :deleted THEN clock_timestamp() ELSE NULL END)
            """), {
                "id": identity, "project": project["id"],
                "path": f"{project['id']}/{identity}/seed.txt", "deleted": index == 1,
            })
            connection.execute(text("""
                INSERT INTO artifact_revisions(artifact_id, revision, filename, description,
                                               size_bytes, sha256, related_work_item_ids)
                SELECT :id, revision, 'seed.txt', '', 4, repeat('a',64), '[]'::jsonb
                FROM generate_series(1,2) AS revision
            """), {"id": identity})
        for index, kind in ((2, "replace"), (3, "delete")):
            connection.execute(text("""
                INSERT INTO artifact_operations(id, project_id, client_operation_id, artifact_id,
                                                kind, state, fingerprint, intent)
                VALUES(:operation, :project, :operation, :artifact, :kind, 'pending',
                       repeat('b',64), '{}'::jsonb)
            """), {
                "operation": uuid4(), "project": project["id"],
                "artifact": identities[index], "kind": kind,
            })
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        records = connection.execute(text("""
            SELECT artifact_id, revision, status, normalized_text FROM artifact_extractions
        """)).all()
    actual = {(row.artifact_id, row.revision): row.status for row in records}
    assert actual == {
        (identities[0], 1): "superseded", (identities[0], 2): "pending",
        (identities[1], 1): "deleted", (identities[1], 2): "deleted",
        (identities[2], 1): "superseded", (identities[2], 2): "superseded",
        (identities[3], 1): "deleted", (identities[3], 2): "deleted",
    }
    assert all(row.normalized_text is None for row in records)
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0026_artifact_library", downgrade=True)


def test_empty_extraction_schema_supports_downgrade_and_upgrade(pristine_postgres_engine):
    migrate(pristine_postgres_engine, "0026_artifact_library", downgrade=True)
    migrate(pristine_postgres_engine, "head")
