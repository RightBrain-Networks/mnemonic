"""Native reclamation preserves public reads, receipts, and concurrent readers."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from mnemonic_api import transcript_reclaim
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copies import TranscriptCopy, TranscriptStorage
from mnemonic_api.transcript_copying import claim_transcript_copy, complete_transcript_copy
from mnemonic_api.transcript_indexing import index_next_transcript
from mnemonic_api.transcript_objects import object_key
from mnemonic_api.transcript_reclaim import apply_reclaim, plan_reclaim, snapshots

from .conftest import BACKEND_DIR
from .test_leases_postgres import expire_lease, item_path
from .test_transcript_indexing_postgres import collection, register
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def test_empty_historical_capture_keeps_its_zero_byte_legacy_reader(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, _ = register(api, project, work_payload, tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    storage = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    expire_lease(postgres_engine, work['id'])
    job = claim_transcript_copy(factory, settings)
    assert job is not None
    writer = ArtifactStorage(storage.root, settings.transcript_max_bytes)
    staged = writer.stage(job.transcript_id, job.snapshot_id, 'transcript.jsonl', [])
    writer.publish(staged)
    complete_transcript_copy(factory, job,
        TranscriptCopy(staged.relative_path, staged.sha256, staged.size_bytes), None)
    rows = snapshots(factory)
    report = plan_reclaim(storage, rows)
    assert report['replacement_reference_bytes'] == 0
    with storage.maintenance_lock(exclusive=True):
        assert apply_reclaim(factory, storage, rows) == {
            'rows_repointed': 1, 'legacy_files_reclaimed': 0}
    assert (storage.root / rows[0].legacy_key).stat().st_size == 0
    with storage.open_copy(rows[0].copy) as original:
        assert original.read() == b''
    assert plan_reclaim(storage, snapshots(factory))['rows_to_reclaim'] == 0


def test_shared_migration_preserves_legacy_snapshots_and_refuses_unsafe_downgrade(
    api, project, work_payload, tmp_path, postgres_engine,
):
    storage, factory = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    config = Config(str(BACKEND_DIR / 'alembic.ini'))
    before = snapshots(factory)
    with postgres_engine.begin() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, '0045_transcript_capacity')
        command.upgrade(config, 'head')
    assert snapshots(factory) == before
    with storage.maintenance_lock(exclusive=True):
        apply_reclaim(factory, storage, before)
    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE transcripts SET copy_sha256=repeat('a', 64) "
                                    "WHERE id=:id"), {'id': before[0].id})
    with pytest.raises(RuntimeError, match='cannot be safely downgraded'):
        with postgres_engine.begin() as connection:
            config.attributes['connection'] = connection
            command.downgrade(config, '0045_transcript_capacity')
    with postgres_engine.connect() as connection:
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == (
            '0049_duplicate_embeddings')
    for row in snapshots(factory):
        with storage.open_copy(row.copy):
            pass


def legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    storage = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    data = source.read_bytes()
    for index in range(4):
        if index:
            if index > 1:
                data += (json.dumps({'role': 'assistant', 'content': f'appended {index}'
                                     + ' unchanged prefix' * 2000}) + '\n').encode()
                source.write_bytes(data)
            claim(api, item_path(project, work), request_id=f'reclaim-generation-{index}',
                  source={'client': 'claude_code', 'path': str(source)})
        expire_lease(postgres_engine, work['id'])
        job = claim_transcript_copy(factory, settings)
        assert job is not None
        # Construct the exact pre-0046 on-disk and database state without invoking
        # the new shared writer; then exercise the real canonical indexer.
        writer = ArtifactStorage(storage.root, settings.transcript_max_bytes)
        stage = writer.stage(job.transcript_id, job.snapshot_id, 'transcript.jsonl', [data])
        writer.publish(stage)
        complete_transcript_copy(factory, job,
            TranscriptCopy(stage.relative_path, stage.sha256, stage.size_bytes), None)
        assert index_next_transcript(factory, settings)
    source.unlink()
    return storage, factory


def file_inventory(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns,
                                      hashlib.sha256(p.read_bytes()).hexdigest())
            for p in root.rglob('*') if p.is_file()}


def test_dry_run_and_apply_preserve_all_public_reads_and_reclaim_both_duplicate_types(
    api, project, work_payload, tmp_path, postgres_engine,
):
    storage, factory = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    rows = snapshots(factory)
    before_files = file_inventory(storage.root)
    before = {str(r.id): api.get(collection(project) + '/' + str(r.id)).json() for r in rows}
    bodies = {str(r.id): api.get(collection(project) + '/' + str(r.id) + '/content').content
              for r in rows}
    report = plan_reclaim(storage, rows)
    assert report['verified_snapshots'] == report['rows_to_reclaim'] == 4
    assert report['distinct_contents'] == 3
    assert report['identical_payload_bytes'] > 0 and report['shared_prefix_bytes'] > 0
    assert report['estimated_reclaimable_bytes'] > 0
    assert file_inventory(storage.root) == before_files
    with storage.maintenance_lock(exclusive=True):
        result = apply_reclaim(factory, storage, rows)
    assert result == {'rows_repointed': 4, 'legacy_files_reclaimed': 4}
    assert len(list(storage.root.rglob('snapshot.bin'))) == 3
    assert sum(info[0] for info in before_files.values()) - sum(
        info[0] for info in file_inventory(storage.root).values()
    ) == report['estimated_reclaimable_bytes']
    for old in rows:
        # Old DB snapshots and pinned recovery readers retain their exact bytes.
        with storage.open_copy(old.copy) as native:
            assert hashlib.sha256(native.read()).hexdigest() == old.sha256
        endpoint = collection(project) + '/' + str(old.id)
        assert api.get(endpoint).json() == before[str(old.id)]
        assert api.get(endpoint + '/content').content == bodies[str(old.id)]
    response = api.post(collection(project) + '/search-content',
                        json={'query': 'rare needle', 'fulltext': True})
    assert response.status_code == 200 and response.json()['total'] == 4
    after = snapshots(factory)
    assert len({r.storage_key for r in after}) == 3
    assert plan_reclaim(storage, after)['rows_to_reclaim'] == 0
    with storage.maintenance_lock(exclusive=True):
        assert apply_reclaim(factory, storage, after) == {
            'rows_repointed': 0, 'legacy_files_reclaimed': 0}


@pytest.mark.parametrize('after_commit', [False, True])
def test_reclaim_resumes_after_interruption_without_original_sources(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch, after_commit,
):
    storage, factory = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    rows = snapshots(factory)
    original = transcript_reclaim._repoint

    def interrupted(*args):
        if after_commit:
            original(*args)
        raise RuntimeError('simulated process interruption')

    monkeypatch.setattr(transcript_reclaim, '_repoint', interrupted)
    with pytest.raises(RuntimeError, match='simulated process interruption'):
        with storage.maintenance_lock(exclusive=True):
            apply_reclaim(factory, storage, rows)
    for row in rows:
        with storage.open_copy(row.copy) as source:
            assert hashlib.sha256(source.read()).hexdigest() == row.sha256
    monkeypatch.setattr(transcript_reclaim, '_repoint', original)
    with storage.maintenance_lock(exclusive=True):
        apply_reclaim(factory, storage, snapshots(factory))
    assert plan_reclaim(storage, snapshots(factory))['rows_to_reclaim'] == 0


def test_reclamation_waits_for_an_open_native_reader_and_old_pointer_stays_readable(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    storage, factory = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    rows = snapshots(factory)
    entered = Event()
    original = transcript_reclaim._replace_legacy

    def observed(*args):
        entered.set()
        return original(*args)

    monkeypatch.setattr(transcript_reclaim, '_replace_legacy', observed)

    def reclaim():
        with storage.maintenance_lock(exclusive=True):
            return apply_reclaim(factory, storage, rows)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with storage.open_copy(rows[0].copy) as reader:
            future = pool.submit(reclaim)
            assert entered.wait(10)
            assert not future.done()
            assert hashlib.sha256(reader.read()).hexdigest() == rows[0].sha256
        assert future.result(timeout=15)['legacy_files_reclaimed'] == 4
    with storage.open_copy(rows[0].copy) as reader:
        assert hashlib.sha256(reader.read()).hexdigest() == rows[0].sha256


def test_reclaim_refuses_a_changed_row_and_preserves_its_raw_file(
    api, project, work_payload, tmp_path, postgres_engine,
):
    storage, factory = legacy_snapshots(api, project, work_payload, tmp_path, postgres_engine)
    rows = snapshots(factory)
    before = (storage.root / rows[0].legacy_key).read_bytes()
    from mnemonic_api.transcript_snapshots import new_transcript_copy

    with factory.begin() as database:
        row = database.get(Transcript, rows[0].id)
        for name, value in new_transcript_copy().items():
            setattr(row, name, value)
    with pytest.raises(ExtractionError, match='transcript_reclaim_conflict'):
        with storage.maintenance_lock(exclusive=True):
            apply_reclaim(factory, storage, rows)
    assert (storage.root / rows[0].legacy_key).read_bytes() == before


def test_fresh_repeated_claims_keep_rows_but_share_content(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from .test_transcript_indexing_postgres import run

    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    assert run(api)
    claim(api, item_path(project, work), request_id='next-copy-generation',
          source={'client': 'claude_code', 'path': str(source)})
    # Active enrollment cannot copy or reuse a ready snapshot early.
    assert not run(api)
    expire_lease(postgres_engine, work['id'])
    assert run(api)
    factory = api.app.state.session_factory
    with factory() as database:
        rows = database.scalars(select(Transcript).order_by(Transcript.created_at)).all()
        assert len(rows) == 2 and rows[0].id == UUID(record['id'])
        assert rows[0].snapshot_id != rows[1].snapshot_id
        assert rows[0].lease_generation_id != rows[1].lease_generation_id
        assert rows[0].storage_key == rows[1].storage_key == object_key(rows[0].copy_sha256)
        assert all(row.copy_status == row.status == 'ready' for row in rows)
