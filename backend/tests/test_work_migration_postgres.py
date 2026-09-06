"""Populated legacy-to-canonical migration parity on an isolated schema."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

from .conftest import BACKEND_DIR

pytestmark = pytest.mark.postgres


def test_populated_legacy_history_backfills_exactly_and_freezes_legacy_tables():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    api_key = "migration-test-key-that-is-long-enough"
    settings = Settings(database_url=raw_url, api_key=api_key)
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_migration_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    options = f"-c search_path={schema} -c timezone=UTC"
    engine = create_engine(
        url.update_query_dict({"options": options}),
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    project_id = uuid4()
    handoff_id = uuid4()
    open_id = uuid4()
    done_id = uuid4()
    promoted_id = uuid4()
    ordinary_comment_id = uuid4()
    created_at = datetime(2025, 6, 1, 12, 30, tzinfo=UTC)
    updated_at = created_at + timedelta(hours=3)
    deleted_at = updated_at + timedelta(hours=2)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0003_handoff_comments")
            connection.execute(
                text(
                    """
                    INSERT INTO projects (
                        id, name, slug, description, repository_url, created_at, updated_at
                    ) VALUES (
                        :id, 'Migration project', 'migration-project', '', NULL,
                        :created_at, :created_at
                    )
                    """
                ),
                {"id": project_id, "created_at": created_at},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO handoffs (
                        id, project_id, title, summary, prompt, source_client,
                        source_session_id, source_model, source_session_url,
                        repository_branch, verified_against, tags, source_metadata,
                        status, version, created_at, updated_at, deleted_at
                    ) VALUES (
                        :id, :project_id, :title, :summary, :prompt, :source_client,
                        :source_session_id, :source_model, :source_session_url,
                        :repository_branch, :verified_against,
                        ARRAY['Migration', 'Exact']::varchar[],
                        CAST(:source_metadata AS jsonb), :status, :version,
                        :created_at, :updated_at, :deleted_at
                    )
                    """
                ),
                {
                    "id": handoff_id,
                    "project_id": project_id,
                    "title": "  Preserved title  ",
                    "summary": "Preserved summary.",
                    "prompt": "  Exact legacy prompt.\r\nUnicode café 日本語.  ",
                    "source_client": "legacy-client",
                    "source_session_id": "opaque-legacy-session",
                    "source_model": "legacy-model",
                    "source_session_url": "https://example.com/legacy/session",
                    "repository_branch": "legacy/branch",
                    "verified_against": "abcdef1",
                    "source_metadata": '{"nested":{"number":2,"valid":true}}',
                    "status": "wont-do",
                    "version": 7,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "deleted_at": deleted_at,
                },
            )
            for work_id, status in [
                (open_id, "open"),
                (done_id, "done"),
                (promoted_id, "promoted"),
            ]:
                connection.execute(
                    text(
                        """
                        INSERT INTO handoffs (
                            id, project_id, title, summary, prompt, source_client,
                            source_session_id, status, version, created_at, updated_at
                        ) VALUES (
                            :id, :project_id, :title, :summary, :prompt,
                            'legacy-client', :source_session_id, :status, 1,
                            :created_at, :created_at
                        )
                        """
                    ),
                    {
                        "id": work_id,
                        "project_id": project_id,
                        "title": f"Legacy {status} work",
                        "summary": f"Migration coverage for {status} lifecycle state.",
                        "prompt": f"Exact {status} checkpoint.",
                        "source_session_id": f"{status}-session",
                        "status": status,
                        "created_at": created_at,
                    },
                )
            connection.execute(
                text("UPDATE handoffs SET tags = ARRAY['MixedLegacy'] WHERE id = :id"),
                {"id": open_id},
            )
            # One comment deliberately reuses the hand-off UUID. The other is
            # collision-free and must preserve its UUID exactly.
            connection.execute(
                text(
                    """
                    INSERT INTO handoff_comments (
                        id, handoff_id, body, kind, source_client,
                        source_session_id, source_model, created_at
                    ) VALUES
                    (
                        :collision_id, :handoff_id, :progress, 'comment',
                        'legacy-client', 'progress-session', NULL, :progress_at
                    ),
                    (
                        :ordinary_id, :done_id, :completion, 'work-summary',
                        'legacy-client', 'completion-session', 'completion-model',
                        :completion_at
                    )
                    """
                ),
                {
                    "collision_id": handoff_id,
                    "ordinary_id": ordinary_comment_id,
                    "handoff_id": handoff_id,
                    "done_id": done_id,
                    "progress": "  Exact progress.\n  ",
                    "completion": "Exact legacy completion.",
                    "progress_at": created_at + timedelta(hours=1),
                    "completion_at": created_at + timedelta(hours=2),
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO handoff_embeddings (
                        handoff_id, model, digest, vector, updated_at
                    ) VALUES (
                        :handoff_id, 'stale-legacy-model', :digest,
                        ARRAY[0.25, 0.75]::real[], :updated_at
                    )
                    """
                ),
                {
                    "handoff_id": handoff_id,
                    "digest": "a" * 64,
                    "updated_at": updated_at,
                },
            )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0005_work_graph_backfill")

        with engine.connect() as connection:
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert current_revision == "0005_work_graph_backfill"
            work = connection.execute(
                text("SELECT * FROM work_items WHERE id = :id"), {"id": handoff_id}
            ).mappings().one()
            assert work["id"] == handoff_id
            assert work["initial_checkpoint_id"] == handoff_id
            assert work["title"] == "  Preserved title  "
            assert work["status"] == "wont-do"
            assert work["priority"] == 0
            assert work["version"] == 7
            assert work["created_at"] == created_at
            assert work["updated_at"] == updated_at
            assert work["deleted_at"] == deleted_at

            initial = connection.execute(
                text("SELECT * FROM checkpoints WHERE id = :id"), {"id": handoff_id}
            ).mappings().one()
            assert initial["work_item_id"] == handoff_id
            assert initial["prompt"] == "  Exact legacy prompt.\r\nUnicode café 日本語.  "
            assert initial["source_client"] == "legacy-client"
            assert initial["source_session_id"] == "opaque-legacy-session"
            assert initial["source_session_url"] == "https://example.com/legacy/session"
            assert initial["repository_branch"] == "legacy/branch"
            assert initial["verified_against"] == "abcdef1"
            assert initial["tags"] == ["Migration", "Exact"]
            assert initial["source_metadata"] == {"nested": {"number": 2, "valid": True}}
            assert initial["migration_origin"] == "legacy-handoff-snapshot"
            assert initial["legacy_record_id"] == handoff_id
            assert initial["created_at"] == created_at

            collision = connection.execute(
                text(
                    "SELECT * FROM checkpoints "
                    "WHERE migration_origin = 'legacy-comment' AND legacy_record_id = :id"
                ),
                {"id": handoff_id},
            ).mappings().one()
            assert collision["id"] != handoff_id
            assert collision["kind"] == "progress"
            assert collision["prompt"] == "  Exact progress.\n  "
            assert collision["source_session_url"] is None
            assert collision["tags"] == []
            assert collision["source_metadata"] == {}

            ordinary = connection.execute(
                text("SELECT * FROM checkpoints WHERE id = :id"),
                {"id": ordinary_comment_id},
            ).mappings().one()
            assert ordinary["legacy_record_id"] == ordinary_comment_id
            assert ordinary["kind"] == "completion"
            assert ordinary["prompt"] == "Exact legacy completion."
            assert ordinary["source_session_id"] == "completion-session"
            assert ordinary["source_model"] == "completion-model"
            assert connection.execute(text("SELECT count(*) FROM work_items")).scalar_one() == 4
            assert connection.execute(text("SELECT count(*) FROM checkpoints")).scalar_one() == 6
            lifecycle_rows = dict(
                connection.execute(text("SELECT id, status FROM work_items")).all()
            )
            assert lifecycle_rows == {
                handoff_id: "wont-do",
                open_id: "open",
                done_id: "done",
                promoted_id: "promoted",
            }
            assert connection.execute(
                text("SELECT count(*) FROM handoff_embeddings")
            ).scalar_one() == 1
            assert connection.execute(
                text("SELECT count(*) FROM work_item_embeddings")
            ).scalar_one() == 0

        with pytest.raises(DBAPIError, match="legacy Mnemonic tables are read-only"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE handoffs SET title = 'rewritten' WHERE id = :id"),
                    {"id": handoff_id},
                )
        with pytest.raises(DBAPIError, match="checkpoints are immutable"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM checkpoints WHERE id = :id"), {"id": ordinary_comment_id}
                )

        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0025_cross_project_relationships"
            assert connection.execute(text("SELECT to_regclass('handoffs')")).scalar_one() is None
            assert connection.execute(
                text("SELECT to_regclass('handoff_comments')")
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT to_regclass('handoff_embeddings')")
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT to_regclass('work_leases')")
            ).scalar_one() == "work_leases"
            assert connection.execute(
                text("SELECT to_regclass('work_events')")
            ).scalar_one() == "work_events"
            assert connection.execute(
                text("SELECT count(*) FROM work_events WHERE origin = 'backfill'")
            ).scalar_one() == 7

        with TestClient(create_app(settings, engine=engine)) as client:
            client.headers["Authorization"] = f"Bearer {api_key}"
            canonical_base = f"/api/v1/projects/{project_id}/work-items"
            for visible_id in [open_id, done_id, promoted_id]:
                assert client.get(f"{canonical_base}/{visible_id}").status_code == 200
            assert client.get(f"{canonical_base}/{handoff_id}").status_code == 404
            assert client.get(canonical_base, params={"status": "all"}).json()["total"] == 3
            legacy_tag_match = client.get(canonical_base, params={"tag": "mixedlegacy"})
            assert legacy_tag_match.status_code == 200
            assert [
                item["summary"]["work_item"]["id"]
                for item in legacy_tag_match.json()["items"]
            ] == [str(open_id)]
            context = client.get(f"{canonical_base}/{open_id}/context")
            assert context.status_code == 200
            assert context.json()["initial_checkpoint"]["migration_origin"] == (
                "legacy-handoff-snapshot"
            )
            assert context.json()["initial_checkpoint"]["legacy_record_id"] == str(open_id)
            assert context.json()["initial_checkpoint"]["prompt"] == "Exact open checkpoint."

        # The deterministic collision mapping is stable for this source row.
        with engine.connect() as connection:
            remapped = connection.execute(
                text(
                    "SELECT id FROM checkpoints "
                    "WHERE migration_origin = 'legacy-comment' AND legacy_record_id = :id"
                ),
                {"id": handoff_id},
            ).scalar_one()
            assert isinstance(remapped, UUID)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()
