"""Growing archives remain searchable beyond every retired corpus admission limit."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from mnemonic_api.models import Transcript

from .test_transcript_search_postgres import body_reads, search, seed_transcripts

pytestmark = pytest.mark.postgres


def test_complete_corpus_above_512_mib_remains_searchable(api, project, postgres_engine):
    # Long tokens keep this capacity regression quick without shrinking the real
    # 512 MiB former threshold. Markers on both ends detect partial indexing.
    body = "needle " + "x" * (8 * 1024 * 1024) + " tailmarker"
    identities = seed_transcripts(api, project, body, count=65)
    assert len(body.encode()) * len(identities) > 536_870_912
    with body_reads(postgres_engine) as reads:
        cold = search(api, project, query="tailmarker", limit=1)
        assert cold.status_code == 200, cold.text
        assert cold.json()["total"] == 65
        assert "tailmarker" in cold.json()["items"][0]["snippet"]
        assert reads == [1, None]
        reads.clear()
        for endpoint in ("list", "content", "unified"):
            page = search(api, project, endpoint, query="needle tailmarker", limit=1, offset=64)
            assert page.status_code == 200, page.text
            assert page.json()["total"] == 65 and len(page.json()["items"]) == 1
        assert reads == [None, None, None]


def test_metadata_above_record_and_byte_limits_streams_without_retaining_archive(
    api, project, postgres_engine,
):
    count = 10_001
    metadata = {"padding": ["x" * 3300]}
    assert count * len(metadata["padding"][0]) > 32_000_000
    with api.app.state.session_factory.begin() as database:
        database.execute(insert(Transcript), [{
            "id": uuid4(), "import_project_id": UUID(project["id"]), "kind": "imported",
            "client": "codex", "source_path": f"/synthetic/archive/{number}.jsonl",
            "extracted_metadata": metadata,
        } for number in range(count)])
    retained = []

    def loaded(database, record):
        if isinstance(record, Transcript):
            retained.append(sum(isinstance(value, Transcript)
                                for value in database.identity_map.values()))

    event.listen(Session, "loaded_as_persistent", loaded)
    try:
        with body_reads(postgres_engine) as reads:
            for endpoint in ("content", "unified"):
                page = search(api, project, endpoint, query="archive", fulltext=False,
                              limit=2, offset=9999)
                assert page.status_code == 200, page.text
                assert page.json()["total"] == count
                assert len(page.json()["items"]) == 2
            assert reads == []
    finally:
        event.remove(Session, "loaded_as_persistent", loaded)
    assert retained and max(retained) <= 3
