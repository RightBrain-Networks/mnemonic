"""Read-only transcript search must never acquire a project mutation lock."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import register, run

pytestmark = pytest.mark.postgres


def test_transcript_search_succeeds_while_project_writer_holds_lock(
    api,
    project,
    work_payload,
    tmp_path,
    postgres_engine,
):
    work, _, transcript, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    with postgres_engine.connect() as writer, writer.begin():
        writer.execute(
            text("SELECT id FROM projects WHERE id=:id FOR UPDATE"), {"id": UUID(project["id"])}
        )
        response = api.post(
            f"/api/v1/projects/{project['id']}/search",
            json={
                "q": "needle",
                "facets": ["transcripts"],
                "fulltext": True,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["id"] == transcript["id"]
        # A search that needs sensitive-artifact serialization still fails safely,
        # with a read-specific explanation rather than instructions about write IDs.
        blocked = api.post(
            f"/api/v1/projects/{project['id']}/search",
            json={
                "q": "needle",
                "facets": ["artifacts", "transcripts"],
                "fulltext": True,
            },
        )
        assert blocked.status_code == 503, blocked.text
        detail = blocked.json()["detail"]
        assert detail["code"] == "search_temporarily_unavailable"
        assert "search again" in detail["message"]
        assert "same ID" not in detail["message"]


def test_snapshot_search_rejects_missing_selected_project(api, project):
    response = api.post(
        "/api/v1/search",
        json={
            "project_ids": [project["id"], str(uuid4())],
            "facets": ["transcripts"],
        },
    )
    assert response.status_code == 404


def test_search_database_timeout_returns_a_read_error_and_releases_transaction(api, project):
    from mnemonic_api.errors import ApplicationError
    from mnemonic_api.search_transactions import search_transaction

    with api.app.state.session_factory() as database:
        with pytest.raises(ApplicationError) as failure:
            with search_transaction(database, [UUID(project["id"])], artifacts=False):
                database.execute(text("SET LOCAL statement_timeout = '1ms'"))
                database.execute(text("SELECT pg_sleep(0.05)"))
        assert failure.value.status_code == 503
        assert failure.value.detail["code"] == "search_temporarily_unavailable"
        assert database.scalar(text("SELECT 1")) == 1


def test_search_disconnect_returns_a_read_error_and_recovers_connection(
    api, project, postgres_engine
):
    from mnemonic_api.errors import ApplicationError
    from mnemonic_api.search_transactions import search_transaction

    with api.app.state.session_factory() as database:
        with pytest.raises(ApplicationError) as failure:
            with search_transaction(database, [UUID(project["id"])], artifacts=False):
                process = database.scalar(text("SELECT pg_backend_pid()"))
                with postgres_engine.connect() as terminator:
                    assert terminator.scalar(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": process}
                    )
                database.execute(text("SELECT 1"))
        assert failure.value.status_code == 503
        assert failure.value.detail["code"] == "search_temporarily_unavailable"
        assert database.scalar(text("SELECT 1")) == 1
