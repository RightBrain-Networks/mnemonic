"""Configured disk indexes retain the existing API scope, budget, and rebuild contracts."""

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from mnemonic_api.application import create_app

from .test_transcript_indexing_postgres import collection
from .test_transcript_search_postgres import body_reads, search, seed_transcripts
from .test_transcript_search_postgres import (
    test_large_library_search_reuses_streamed_index_and_fetches_only_page_bodies as check_large,
)

pytestmark = pytest.mark.postgres


@contextmanager
def disk_api(api, engine, directory, *, limit=536_870_912):
    config = api.app.state.settings.model_copy(update={
        "transcript_index_dir": directory, "transcript_search_max_bytes": limit,
    })
    with TestClient(create_app(config, engine=engine)) as client:
        client.headers["Authorization"] = api.headers["Authorization"]
        yield client


def test_large_library_uses_configured_disk_directory(api, project, postgres_engine, tmp_path):
    with disk_api(api, postgres_engine, tmp_path) as client:
        check_large(client, project, postgres_engine)
        assert (tmp_path / "snapshot" / "meta.json").is_file()


def test_restart_reuses_validated_corpus_but_still_enforces_configured_budget(
    api, project, postgres_engine, tmp_path,
):
    body = "needle " + "🦊" * 2000
    seed_transcripts(api, project, body)
    with disk_api(api, postgres_engine, tmp_path) as client:
        assert search(client, project).status_code == 200
    with disk_api(api, postgres_engine, tmp_path) as client, body_reads(postgres_engine) as reads:
        response = search(client, project, "unified")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert reads == [None]  # Reopen on disk; fetch only the page's snippet body.
    with disk_api(api, postgres_engine, tmp_path, limit=4096) as client:
        with body_reads(postgres_engine) as reads:
            response = search(client, project, "unified")
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["code"] == "transcript_search_capacity"
            assert reads == []
            assert search(client, project, query="archive", fulltext=False).json()["total"] == 1
            assert reads == []


def test_rebuild_clears_disk_snapshot_and_keeps_configured_storage(
    api, project, postgres_engine, tmp_path,
):
    seed_transcripts(api, project, "needle")
    with disk_api(api, postgres_engine, tmp_path) as client:
        assert search(client, project).status_code == 200
        index = client.app.state.transcript_search_index
        assert (tmp_path / "snapshot" / "meta.json").exists()
        response = client.post(collection(project) + "/rebuild",
                               json={"client_operation_id": str(uuid4())})
        assert response.status_code == 200, response.text
        assert not (tmp_path / "snapshot").exists()
        assert client.app.state.transcript_search_index is index
        assert search(client, project).json()["total"] == 0
        assert (tmp_path / "snapshot" / "meta.json").exists()
