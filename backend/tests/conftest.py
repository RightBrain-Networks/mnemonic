"""Integration tests use a disposable schema, never the application's tables."""

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from mnemonic_api.config import Settings
from mnemonic_api.main import create_app

TEST_API_KEY = "mnemonic-integration-test-key-32-characters"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration tests")
    settings = Settings(database_url=raw_url, api_key=TEST_API_KEY)
    url = make_url(settings.database_url.get_secret_value())
    admin = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    schema = "mnemonic_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    # Exclude public from search_path so a real application's alembic_version
    # or tables can never be discovered or mutated by these tests.
    options = f"-c search_path={schema} -c timezone=UTC"
    test_url = url.update_query_dict({"options": options})
    engine = create_engine(
        test_url, pool_pre_ping=True, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        config = Config(str(BACKEND_DIR / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        # Only the schema created above is removed; the database is retained.
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@pytest.fixture
def api(postgres_engine: Engine) -> Iterator[TestClient]:
    with postgres_engine.begin() as connection:
        # Row-level immutability/read-only triggers intentionally reject DELETE.
        # This exact test schema reset is scoped to the disposable random schema.
        connection.execute(
            text(
                "TRUNCATE work_relationships, work_leases, work_item_embeddings, checkpoints, "
                "work_items, projects "
                "RESTART IDENTITY CASCADE"
            )
        )
    settings = Settings(
        database_url=postgres_engine.url.render_as_string(hide_password=False), api_key=TEST_API_KEY
    )
    with TestClient(create_app(settings, engine=postgres_engine)) as client:
        client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
        yield client


@pytest.fixture
def project(api: TestClient) -> dict:
    response = api.post("/api/v1/projects", json={"name": "First project"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def checkpoint_fields() -> dict:
    return {
        "prompt": (
            "  Agent-authored proposal; recheck the current tree before acting.\r\n\r\n"
            "Context: investigate cache state in src/cache.py.\n"
            "Outcome: invalidated entries must stop appearing after a branch switch.\n"
            "References: src/cache.py; verified commit abc1234.\n"
            "Hazard: preserve entries belonging to other projects.\n"
            "Verify: reproduce, add a regression test, and run the cache test suite.\n  "
        ),
        "source_client": "claude-code",
        "source_session_id": "3d46fe7a-session:opaque_001",
        "source_model": "origin-model",
        "source_session_url": "https://example.com/sessions/3d46fe7a",
        "repository_branch": "feature/cache",
        "verified_against": "abc1234",
        "tags": ["cache", "correctness"],
        "source_metadata": {"reference": "src/cache.py:42", "author_notes": ["recheck", 2, True]},
    }


@pytest.fixture
def work_payload(checkpoint_fields: dict) -> dict:
    return {
        "title": "Investigate stale cache entries",
        "summary": "Cached state survives invalidation after a branch switch.",
        "priority": 30,
        "initial_checkpoint": dict(checkpoint_fields),
    }
