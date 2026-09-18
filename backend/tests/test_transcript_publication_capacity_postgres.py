"""Production-sized canonical publication, beyond parser-only capacity coverage."""

import hashlib
import json
from uuid import UUID

import pytest
from sqlalchemy import func, select

from mnemonic_api.models import Transcript

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def test_two_hundred_megabyte_session_persists_and_downloads_complete_text(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    tail = 'publicationtailneedle 🦊 complete final evidence'
    with source.open('w') as output:
        for number in range(60_000):
            output.write(json.dumps({'role': 'user',
                'content': f'part {number} ' + 'ordinary ' * 165,
                'bookkeeping': 'x' * 2100}) + '\n')
        output.write(json.dumps({'role': 'assistant', 'content': tail}) + '\n')
    assert source.stat().st_size > 200 * 1024 * 1024
    expire_lease(postgres_engine, work['id'])
    try:
        assert run(api)
        ready = read(api, project, record)
        assert ready['status'] == 'ready' and not ready['truncated']
        assert ready['segment_count'] == 60_001
        with api.app.state.session_factory() as database:
            chars = database.scalar(select(func.length(Transcript.normalized_text)).where(
                Transcript.id == UUID(record['id'])))
        assert chars > 80_000_000
        endpoint = collection(project) + '/' + record['id']
        page = api.get(endpoint + '/text', params={'offset': chars - len(tail),
                                                  'limit': len(tail)}).json()
        assert page['text'] == tail and page['next_offset'] is None
        downloaded = api.get(endpoint + '/content')
        assert downloaded.status_code == 200
        assert downloaded.content.endswith(tail.encode())
        assert hashlib.sha256(downloaded.content).hexdigest() == ready['text_sha256']
        assert int(downloaded.headers['content-length']) == len(downloaded.content)
        found = api.get(collection(project), params={
            'query': 'publicationtailneedle', 'fulltext': True}).json()
        assert found['total'] == 1
    finally:
        source.unlink(missing_ok=True)
        with api.app.state.session_factory() as database:
            copy = database.get(Transcript, UUID(record['id']))
            if copy.storage_key:
                (api.app.state.settings.transcript_root / copy.storage_key).unlink(missing_ok=True)
