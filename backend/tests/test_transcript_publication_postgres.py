"""Bulk canonical storage cannot inherit the interactive project deadline or locks."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update

from mnemonic_api import transcript_indexing, transcript_publication
from mnemonic_api.models import BackgroundJob, Project, Transcript
from mnemonic_api.services import project_mutations
from mnemonic_api.transcript_copying import copy_next_transcript
from mnemonic_api.transcript_normalized_storage import NORMALIZATIONS, SEGMENTS
from mnemonic_jobs.ledger import LostLease, claim_job, enqueue_job, heartbeat_job

from .test_leases_postgres import expire_lease
from .test_transcript_imports_postgres import codex_source, import_folder
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def _counts(api):
    with api.app.state.session_factory() as database:
        return tuple(database.scalar(select(func.count()).select_from(table))
                     for table in (NORMALIZATIONS, SEGMENTS))


def _expire_index(api, record):
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record['id'])).values(
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))


def _context(api, record):
    with api.app.state.session_factory.begin() as database:
        identity = UUID(record['id'])
        generation = database.get(Transcript, identity).generation
        identifier = enqueue_job(database, 'transcript_index', 'publication:' + str(uuid4()),
            {'transcript_id': str(identity), 'generation': generation})
        context = claim_job(database, identifier, lease_seconds=900)
        assert context is not None
        return context


def test_slow_stage_allows_project_edits_and_job_heartbeats(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    assert copy_next_transcript(api.app.state.session_factory, api.app.state.settings)
    context = _context(api, record)
    persist = transcript_publication.persist_normalization

    def slow(database, identity, normalized):
        with api.app.state.session_factory.begin() as other:
            other.scalar(select(Project.id).where(Project.id == UUID(project['id']))
                         .with_for_update(nowait=True))
            other.execute(update(Project).where(Project.id == UUID(project['id'])).values(
                name='Project edits remain available'))
            assert heartbeat_job(other, context, lease_seconds=900)
        # The previous implementation times out here while holding both locks.
        database.execute(text('SELECT pg_sleep(1.2)'))
        persist(database, identity, normalized)

    monkeypatch.setattr(project_mutations, 'DOMAIN_SECONDS', 1.0)
    monkeypatch.setattr(transcript_publication, 'persist_normalization', slow)
    assert transcript_indexing.index_next_transcript(
        api.app.state.session_factory, api.app.state.settings, context=context)
    assert read(api, project, record)['status'] == 'ready'
    assert _counts(api) == (1, 1)


def test_crash_during_staging_leaves_no_partial_manifest(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    persist = transcript_publication.persist_normalization

    def interrupted(*args):
        persist(*args)
        raise RuntimeError('crash before stage commit')

    with monkeypatch.context() as failing:
        failing.setattr(transcript_publication, 'persist_normalization', interrupted)
        with pytest.raises(RuntimeError, match='before stage commit'):
            run(api)
    assert _counts(api) == (0, 0)
    assert read(api, project, record)['normalized_revision'] is None
    _expire_index(api, record)
    assert run(api)
    assert read(api, project, record)['status'] == 'ready'


def test_activation_failure_reuses_committed_stage_without_reparsing(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])

    def interrupted(*_args):
        raise RuntimeError('crash after stage commit')

    with monkeypatch.context() as failing:
        failing.setattr(transcript_indexing, 'publish_complete_text', interrupted)
        with pytest.raises(RuntimeError, match='after stage commit'):
            run(api)
    assert _counts(api) == (1, 1)
    assert read(api, project, record)['normalized_revision'] is None
    source.unlink()
    _expire_index(api, record)
    monkeypatch.setattr(transcript_indexing, 'normalize_transcript',
                        lambda *_: pytest.fail('Committed canonical stage must be reused'))
    assert run(api)
    assert read(api, project, record)['status'] == 'ready'
    assert _counts(api) == (1, 1)


def test_rebuild_between_stage_and_activation_fences_old_generation(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    stage = transcript_indexing.stage_normalization

    def superseded(*args):
        result = stage(*args)
        response = api.post(collection(project) + '/rebuild',
                            json={'client_operation_id': str(uuid4())})
        assert response.status_code == 200
        return result

    with monkeypatch.context() as racing:
        racing.setattr(transcript_indexing, 'stage_normalization', superseded)
        assert run(api)
    assert _counts(api) == (1, 1)
    assert read(api, project, record)['normalized_revision'] is None
    assert read(api, project, record)['status'] == 'waiting'
    monkeypatch.setattr(transcript_indexing, 'normalize_transcript',
                        lambda *_: pytest.fail('Same retained snapshot must reuse its stage'))
    assert run(api)
    assert read(api, project, record)['status'] == 'ready'


def test_lost_ledger_ownership_rolls_back_staging(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work['id'])
    assert copy_next_transcript(api.app.state.session_factory, api.app.state.settings)
    context = _context(api, record)
    persist = transcript_publication.persist_normalization

    def stolen(*args):
        persist(*args)
        with api.app.state.session_factory.begin() as other:
            other.execute(update(BackgroundJob).where(BackgroundJob.id == context.job_id)
                          .values(lease_token=uuid4()))

    monkeypatch.setattr(transcript_publication, 'persist_normalization', stolen)
    with pytest.raises(LostLease):
        transcript_indexing.index_next_transcript(
            api.app.state.session_factory, api.app.state.settings, context=context)
    assert _counts(api) == (0, 0)
    assert read(api, project, record)['normalized_revision'] is None


def test_imported_codex_identity_survives_activation_crash(
    api, project, tmp_path, monkeypatch,
):
    from mnemonic_api import transcript_discovery

    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    source = codex_source(tmp_path)
    monkeypatch.setattr(transcript_discovery, "_source_client", lambda *_: "claude_code")
    assert import_folder(api, project, tmp_path).status_code == 200
    record = api.get(collection(project)).json()["items"][0]
    assert record["client"] == "claude_code"

    def interrupted(*_args):
        raise RuntimeError("crash after Codex stage commit")

    with monkeypatch.context() as failing:
        failing.setattr(transcript_indexing, "publish_complete_text", interrupted)
        with pytest.raises(RuntimeError, match="after Codex stage commit"):
            run(api)
    source.unlink()
    _expire_index(api, record)
    monkeypatch.setattr(transcript_indexing, "normalize_transcript",
                        lambda *_: pytest.fail("Committed Codex stage must be reused"))
    assert run(api)
    ready = read(api, project, record)
    assert ready["status"] == "ready"
    assert ready["client"] == "codex" and ready["format"] == "codex-jsonl"
