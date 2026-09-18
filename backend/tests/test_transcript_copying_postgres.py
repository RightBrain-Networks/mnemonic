"""Copy migration, lifecycle and RabbitMQ jobs preserve bytes across failures."""

import io
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import BackgroundJob, Transcript
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_copying import (
    claim_transcript_copy,
    complete_transcript_copy,
    copy_next_transcript,
)
from mnemonic_api.transcript_indexing import claim_transcript_job, index_next_transcript
from mnemonic_api.transcript_job_queue import (
    enqueue_transcript_jobs,
    handle_transcript_copy,
    handle_transcript_index,
)
from mnemonic_api.transcript_snapshots import new_transcript_copy
from mnemonic_backup.archive import restore_project
from mnemonic_jobs.ledger import PermanentJobError, RetryJob, claim_job, finish_job

from .test_leases_postgres import create_work, expire_lease, item_path
from .test_project_backup_archive import _export
from .test_transcript_imports_postgres import import_folder, source
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def test_crash_after_publish_recovers_copy_after_source_disappears(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    assert claim_transcript_copy(factory, settings) is None
    expire_lease(postgres_engine, work["id"])
    abandoned = claim_transcript_copy(factory, settings)
    assert abandoned is not None
    store = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    copied = store.capture(abandoned.transcript_id, abandoned.snapshot_id,
                           abandoned.source_path, settings.transcript_allowed_roots)
    source_path.unlink()
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.copy_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert copy_next_transcript(factory, settings)
    complete_transcript_copy(factory, abandoned, None, ExtractionError("obsolete_failure"))
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        assert row.storage_key == copied.storage_key
        assert row.copy_sha256 == copied.sha256
        assert row.copy_status == "ready" and row.copy_attempts == 2
    assert index_next_transcript(factory, settings)
    assert read(api, project, record)["status"] == "ready"


@pytest.mark.parametrize("source_change", ["unchanged", "changed", "missing"])
def test_legacy_ready_backfill_retains_text_until_copy_success(
    api, project, work_payload, tmp_path, postgres_engine, source_change,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    # These are exactly the migration defaults added to old ready rows.
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        for name, value in new_transcript_copy().items():
            setattr(row, name, value)
        if source_change == "missing":
            row.copy_attempts = 7
    if source_change == "changed":
        source_path.write_text('{"role":"user","content":"newer copied transcript"}\n')
    elif source_change == "missing":
        source_path.unlink()
    assert api.get(collection(project), params={"detail": "full"}).json()["indexing_incomplete"]
    assert copy_next_transcript(factory, settings)
    current = read(api, project, record)
    if source_change == "unchanged":
        assert index_next_transcript(factory, settings)
        assert read(api, project, record)["normalized_revision"] is not None
        assert not api.get(collection(project)).json()["indexing_incomplete"]
    elif source_change == "changed":
        assert current["status"] == "ready" and current["index_status"] == "pending"
        assert current["text_sha256"] == ready["text_sha256"]
        assert index_next_transcript(factory, settings)
        assert "newer copied" in api.get(collection(project) + "/" + record["id"]
                                         + "/content").text
    else:
        assert current["status"] == "ready"
        assert current["text_sha256"] == ready["text_sha256"]
        assert current["copy_status"] == ("failed" if source_change == "missing" else "ready")
        assert not index_next_transcript(factory, settings)
        assert api.get(collection(project), params={"detail": "full"}).json()[
            "indexing_incomplete"
        ] == (source_change == "missing")


def test_rebuild_fences_copy_claim_and_recovers_published_snapshot(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    old = claim_transcript_copy(factory, settings)
    assert old is not None
    copied = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes).capture(
        old.transcript_id, old.snapshot_id, old.source_path, settings.transcript_allowed_roots)
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    source_path.unlink()
    complete_transcript_copy(factory, old, copied, None)
    assert read(api, project, record)["copy_status"] == "pending"
    assert run(api)
    assert read(api, project, record)["status"] == "ready"


def test_enrolling_import_invalidates_its_copy_before_new_active_session(
    api, project, work_payload, tmp_path, postgres_engine,
):
    source_path = source(tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    settings.transcript_allowed_roots = [tmp_path]
    assert import_folder(api, project, tmp_path).json()["imported"] == 1
    old = claim_transcript_copy(factory, settings)
    assert old is not None
    copied = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes).capture(
        old.transcript_id, old.snapshot_id, old.source_path, settings.transcript_allowed_roots)
    work = create_work(api, project, work_payload)["work_item"]
    claim(api, item_path(project, work), source={"client": "claude_code", "path": str(source_path)})
    complete_transcript_copy(factory, old, copied, None)
    assert not copy_next_transcript(factory, settings)
    source_path.write_text('{"role":"user","content":"new session bytes"}\n')
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    with factory() as database:
        record = database.get(Transcript, old.transcript_id)
        assert record.snapshot_id != old.snapshot_id
        assert record.storage_key != copied.storage_key
        assert "new session bytes" in record.normalized_text


def _queued_context(factory, kind):
    with factory.begin() as database:
        row = database.scalar(select(BackgroundJob).where(BackgroundJob.kind == kind,
                                                         BackgroundJob.status == "pending"))
        assert row is not None
        assert set(row.payload) == {"transcript_id", "generation"}
        context = claim_job(database, row.id)
        assert context is not None
        return context


def test_queue_copy_then_index_and_rebuild_only_index_the_retained_file(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    with factory.begin() as database:
        assert enqueue_transcript_jobs(database, settings) == 0
    expire_lease(postgres_engine, work["id"])
    with factory.begin() as database:
        assert enqueue_transcript_jobs(database, settings) == 1
    context = _queued_context(factory, "transcript_copy")
    result = handle_transcript_copy(factory, settings, context)
    with factory.begin() as database:
        assert finish_job(database, context, result=result)
        assert enqueue_transcript_jobs(database, settings) == 1
    source_path.unlink()
    context = _queued_context(factory, "transcript_index")
    result = handle_transcript_index(factory, settings, context)
    with factory.begin() as database:
        assert finish_job(database, context, result=result)
    assert read(api, project, record)["status"] == "ready"
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    with factory.begin() as database:
        assert enqueue_transcript_jobs(database, settings) == 1
        assert len(database.scalars(select(BackgroundJob).where(
            BackgroundJob.kind == "transcript_copy")).all()) == 1
    context = _queued_context(factory, "transcript_index")
    assert handle_transcript_index(factory, settings, context)["disposition"] == "ready"


def test_queue_exhaustion_becomes_a_visible_copy_failure(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    with factory.begin() as database:
        enqueue_transcript_jobs(database, settings)
        job = database.scalar(select(BackgroundJob))
        job.status, job.error_code = "failed", "attempts_exhausted"
        job.completed_at = datetime.now(UTC)
    with factory.begin() as database:
        assert enqueue_transcript_jobs(database, settings) == 0
    failed = read(api, project, record)
    assert failed["copy_status"] == "failed"
    assert failed["copy_error_code"] == "transcript_job_exhausted"


def test_allowlist_recovery_then_transient_retry_gets_a_fresh_queue_identity(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    settings.transcript_allowed_roots = []
    with factory.begin() as database:
        enqueue_transcript_jobs(database, settings)
    first = _queued_context(factory, "transcript_copy")
    with pytest.raises(PermanentJobError) as failure:
        handle_transcript_copy(factory, settings, first)
    with factory.begin() as database:
        assert finish_job(database, first, error=failure.value)
    settings.transcript_allowed_roots = [tmp_path]
    with factory.begin() as database:
        enqueue_transcript_jobs(database, settings)
    recovery = _queued_context(factory, "transcript_copy")

    def unavailable(*_args, **_kwargs):
        raise ExtractionError("transcript_io_error", retryable=True)

    with monkeypatch.context() as patch:
        patch.setattr(TranscriptStorage, "capture", unavailable)
        result = handle_transcript_copy(factory, settings, recovery)
    assert result == {"disposition": "pending"}
    with factory.begin() as database:
        assert finish_job(database, recovery, result=result)
        row = database.get(Transcript, UUID(record["id"]))
        assert row.copy_attempts == 1
        row.copy_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    with factory.begin() as database:
        assert enqueue_transcript_jobs(database, settings) == 1
    retried = _queued_context(factory, "transcript_copy")
    assert retried.job_id not in {first.job_id, recovery.job_id}
    assert handle_transcript_copy(factory, settings, retried) == {"disposition": "ready"}


@pytest.mark.parametrize("failure", ["malformed", "projection"])
def test_changed_legacy_copy_failure_retains_complete_readable_extraction(
    api, project, work_payload, tmp_path, postgres_engine, failure,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    before = read(api, project, record)
    endpoint = collection(project) + "/" + record["id"] + "/content"
    body = api.get(endpoint).content
    factory, settings = api.app.state.session_factory, api.app.state.settings
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        for name, value in new_transcript_copy().items():
            setattr(row, name, value)
    source_path.write_bytes(b'{"unfinished":' if failure == "malformed" else
                            b'{"role":"user","content":"changed source"}\n')
    assert copy_next_transcript(factory, settings)
    error = ExtractionError("transcript_projection_failed") if failure == "projection" else None
    assert run(api, stage_error=error)
    after = read(api, project, record)
    assert after["index_status"] == "failed"
    for field in ("status", "sha256", "size_bytes", "text_sha256", "metadata", "format",
                  "mime_type", "indexing_started_at", "indexing_completed_at"):
        assert after[field] == before[field]
    assert api.get(endpoint).content == body
    page = api.get(
        collection(project), params={"detail": "full", "query": "needle", "fulltext": True}
    ).json()
    assert page["total"] == 1 and page["indexing_incomplete"]
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    assert api.get(endpoint).content == body


def test_rebuild_of_uncopied_legacy_failure_retains_ready_text(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    factory = api.app.state.session_factory
    endpoint = collection(project) + "/" + record["id"] + "/content"
    body = api.get(endpoint).content
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        for name, value in new_transcript_copy().items():
            setattr(row, name, value)
        row.copy_status, row.copy_error_code = "failed", "transcript_io_error"
    source_path.unlink()
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    assert api.get(endpoint).content == body
    retained = read(api, project, record)
    assert retained["status"] == "ready" and retained["index_status"] == "pending"


def test_restore_active_work_rotates_uncopied_snapshot_and_rejects_stale_file(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source_path = register(api, project, work_payload, tmp_path)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    archive = _export(postgres_engine, project)
    expire_lease(postgres_engine, work["id"])
    old = claim_transcript_copy(factory, settings)
    assert old is not None
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    # A worker which claimed before restore finishes its file while the restored
    # generation is Active. Its file identity must not be adopted by a new job.
    copied = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes).capture(
        old.transcript_id, old.snapshot_id, old.source_path, settings.transcript_allowed_roots)
    complete_transcript_copy(factory, old, copied, None)
    assert not copy_next_transcript(factory, settings)
    source_path.write_text('{"role":"user","content":"bytes authored after restore"}\n')
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    with factory() as database:
        restored = database.get(Transcript, UUID(record["id"]))
        assert restored.snapshot_id != old.snapshot_id
        assert restored.storage_key != copied.storage_key
        assert "bytes authored after restore" in restored.normalized_text


def test_queued_retained_reindex_defers_pause_without_losing_its_job(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    with factory.begin() as database:
        enqueue_transcript_jobs(database, settings)
    context = _queued_context(factory, "transcript_index")
    endpoint = f"/api/v1/projects/{project['id']}/transcript-settings"
    assert api.patch(endpoint, json={"enabled": False, "expected_revision": 1,
                                    "max_file_size_bytes": 1024}).status_code == 200
    with pytest.raises(RetryJob) as deferred:
        handle_transcript_index(factory, settings, context)
    assert not deferred.value.consume_attempt
    with factory.begin() as database:
        assert finish_job(database, context, error=deferred.value)
        job = database.get(BackgroundJob, context.job_id)
        assert job.status == "pending" and job.attempts == 0
        job.due_at = datetime.now(UTC) - timedelta(seconds=1)
    assert read(api, project, record)["status"] == "ready"
    assert api.patch(endpoint, json={"enabled": True, "expected_revision": 2,
                                    "max_file_size_bytes": 1024}).status_code == 200
    resumed = _queued_context(factory, "transcript_index")
    assert resumed.job_id == context.job_id
    assert handle_transcript_index(factory, settings, resumed) == {"disposition": "ready"}


@pytest.mark.parametrize("phase", ["copy", "index"])
def test_rebuild_between_target_scan_and_claim_cannot_claim_newer_generation(
    api, project, work_payload, tmp_path, postgres_engine, phase,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    if phase == "index":
        assert run(api)
        assert api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())}).status_code == 200
    with factory() as database:
        generation = database.get(Transcript, UUID(record["id"])).generation
    changed = []

    def rebuild_after_candidate(_connection, _cursor, statement, *_args):
        if changed or not statement.startswith("SELECT transcripts.id, transcripts.work_item_id"):
            return
        changed.append(True)
        assert api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())}).status_code == 200

    event.listen(postgres_engine, "after_cursor_execute", rebuild_after_candidate)
    try:
        claim_handler = claim_transcript_copy if phase == "copy" else claim_transcript_job
        assert claim_handler(factory, settings, UUID(record["id"]), generation) is None
    finally:
        event.remove(postgres_engine, "after_cursor_execute", rebuild_after_candidate)
    assert changed
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        assert row.generation == generation + 1
        assert row.lease_token is None and row.copy_lease_token is None
