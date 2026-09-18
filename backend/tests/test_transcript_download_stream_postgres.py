"""Complete downloads stay linear, bounded, and faithful to the published snapshot."""

import hashlib
import tracemalloc
from uuid import UUID

import pytest
from sqlalchemy import event, update

from mnemonic_api import transcript_publication
from mnemonic_api.database import begin_coherent_read
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_fulltext import TEXT_PROJECTION_KEY, download_text_chunks

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def test_large_unicode_download_uses_bounded_chunks_and_one_snapshot(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    import json

    work, _, record, source = register(api, project, work_payload, tmp_path)
    text = 'naïve 🦊 ' * 200_000
    source.write_text('\n'.join(json.dumps({'role': 'user', 'content': text})
                               for _ in range(12)) + '\n')
    expire_lease(postgres_engine, work['id'])
    persist = transcript_publication.persist_normalization
    persistence_peaks = []

    def measured_persistence(*args):
        tracemalloc.start()
        try:
            persist(*args)
        finally:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            persistence_peaks.append(peak)

    monkeypatch.setattr(transcript_publication, 'persist_normalization', measured_persistence)
    assert run(api)
    assert max(persistence_peaks) < 64 * 1024 * 1024, 'Publication must bound segment batch bytes'
    ready = read(api, project, record)
    queries = []

    def count(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)

    engine = api.app.state.session_factory.kw['bind']
    event.listen(engine, 'before_cursor_execute', count)
    digest, count_chunks, maximum = hashlib.sha256(), 0, 0
    tracemalloc.start()
    try:
        with api.app.state.session_factory() as database:
            begin_coherent_read(database)
            stored = database.get(Transcript, UUID(record['id']))
            chunks = download_text_chunks(database, stored)
            first = next(chunks)
            with api.app.state.session_factory.begin() as other:
                other.execute(update(Transcript).where(Transcript.id == stored.id).values(
                    normalized_text='a later projection', text_sha256='a' * 64))
            digest.update(first)
            for chunk in chunks:
                digest.update(chunk)
                maximum = max(maximum, len(chunk))
                count_chunks += 1
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        event.remove(engine, 'before_cursor_execute', count)
    assert count_chunks > 50 and maximum <= 65_536
    assert digest.hexdigest() == ready['text_sha256']
    assert peak < 24 * 1024 * 1024, 'Download must not buffer a batch of large segments'
    assert len(queries) <= 4, 'A download must not re-read compressed text for every chunk'


def test_legacy_download_preserves_its_published_bytes_and_hash(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    assert run(api)
    legacy = 'Legacy extractor projection 🦊\n' * 3_000
    digest = hashlib.sha256(legacy.encode()).hexdigest()
    with api.app.state.session_factory.begin() as database:
        stored = database.get(Transcript, UUID(record['id']))
        stored.extracted_metadata = {key: value for key, value in stored.extracted_metadata.items()
                                     if key != TEXT_PROJECTION_KEY}
        stored.normalized_text, stored.text_sha256 = legacy, digest
    response = api.get(collection(project) + '/' + record['id'] + '/content',
                       params={'expected_sha256': digest})
    assert response.status_code == 200
    assert response.content == legacy.encode()
    assert response.headers['x-content-sha256'] == digest
    assert int(response.headers['content-length']) == len(response.content)
