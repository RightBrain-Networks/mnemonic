"""Operator recovery preserves exact enrollment and pins explicit replacement bytes."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import BackgroundJob, Transcript, TranscriptRecovery, TranscriptSettings
from mnemonic_api.services import transcript_recoveries as recovery_service
from mnemonic_api.services.transcript_recoveries import (
    TranscriptRecoveryRequest,
    apply_transcript_recovery,
    describe_recovery_source,
    inspect_recovery_target,
)
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_copying import (
    claim_transcript_copy,
    complete_transcript_copy,
    copy_next_transcript,
)
from mnemonic_api.transcript_indexing import index_next_transcript
from mnemonic_api.transcript_job_queue import enqueue_transcript_jobs, handle_transcript_copy
from mnemonic_api.transcript_snapshots import new_transcript_copy
from mnemonic_jobs.ledger import PermanentJobError, RetryJob, claim_job, finish_job

from .test_artifact_extraction_migration_postgres import migrate
from .test_leases_postgres import create_work, expire_lease, item_path
from .test_transcript_imports_postgres import import_folder, source
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_lifecycle_postgres import claim
from .test_work_item_moves_postgres import _move_payload, _project

pytestmark = pytest.mark.postgres


def recovery_request(api, project, record, replacement):
    factory, settings = api.app.state.session_factory, api.app.state.settings
    fingerprint = describe_recovery_source(settings, str(replacement))
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        return TranscriptRecoveryRequest(operation_id=uuid4(), transcript_id=row.id,
            project_id=UUID(project["id"]), original_source_path=row.source_path,
            replacement_path=str(replacement), expected_generation=row.generation,
            expected_snapshot_id=row.snapshot_id, expected_sha256=fingerprint.sha256,
            expected_size_bytes=fingerprint.size_bytes, reason="Recover verified misplaced source",
            evidence="Synthetic session metadata and exact file identity verified by operator.")


def _apply(api, request):
    return apply_transcript_recovery(api.app.state.session_factory,
                                     api.app.state.settings, request)


def _stored(api, request, name):
    with api.app.state.session_factory() as database:
        return getattr(database.get(Transcript, request.transcript_id), name)


def _request_fixture(api, project, work_payload, tmp_path, postgres_engine):
    work, _, record, original = register(api, project, work_payload, tmp_path)
    replacement = source(tmp_path / "actual-worktree", "session.jsonl")
    original.unlink()
    expire_lease(postgres_engine, work["id"])
    return work, record, replacement, recovery_request(api, project, record, replacement)


def _copy_context(api):
    with api.app.state.session_factory.begin() as database:
        job = database.scalar(select(BackgroundJob).where(BackgroundJob.kind == "transcript_copy")
                               .order_by(BackgroundJob.created_at.desc()))
        context = claim_job(database, job.id)
        assert context is not None
        return context


def _pause(api, project, enabled):
    with api.app.state.session_factory.begin() as database:
        row = database.get(TranscriptSettings, UUID(project["id"]))
        if row is None:
            database.add(TranscriptSettings(project_id=UUID(project["id"]), enabled=enabled))
        else:
            row.enabled = enabled


def test_recovery_atomically_queues_only_identifiers_and_replays_after_guards_change(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, record, replacement, request = _request_fixture(
        api, project, work_payload, tmp_path, postgres_engine)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    result = _apply(api, request)
    with factory.begin() as database:
        row = database.get(Transcript, request.transcript_id)
        journal = database.get(TranscriptRecovery, request.operation_id)
        job = database.scalar(select(BackgroundJob))
        assert row.source_path == request.original_source_path
        assert row.work_item_id == UUID(work["id"])
        assert row.recovery_operation_id == request.operation_id
        assert row.snapshot_id == result.resulting_snapshot_id != request.expected_snapshot_id
        assert row.generation == result.resulting_generation == request.expected_generation + 1
        assert row.copy_status == "pending" and row.copy_attempts == 0
        assert job.payload == {"transcript_id": record["id"], "generation": row.generation}
        assert journal.replacement_path == str(replacement)
        assert enqueue_transcript_jobs(database, settings) == 1
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 1
    context = _copy_context(api)
    disposition = handle_transcript_copy(factory, settings, context)
    with factory.begin() as database:
        assert finish_job(database, context, result=disposition)
    assert index_next_transcript(factory, settings)
    ready = read(api, project, record)
    assert ready["source_path"] == request.original_source_path
    assert ready["sha256"] == request.expected_sha256 and ready["status"] == "ready"
    _pause(api, project, False)
    settings.transcript_allowed_roots = []
    replacement.unlink()
    assert _apply(api, request) == result
    assert read(api, project, record) == ready


@pytest.mark.parametrize("field,value", [
    ("replacement_path", "/different/source.jsonl"), ("reason", "Changed justification"),
    ("evidence", "Changed verification"), ("expected_generation", 99),
    ("expected_sha256", "b" * 64), ("expected_size_bytes", 2),
    ("expected_snapshot_id", uuid4()), ("project_id", uuid4()), ("transcript_id", uuid4()),
    ("original_source_path", "/different/original.jsonl"),
])
def test_recovery_receipt_rejects_every_changed_argument(
    api, project, work_payload, tmp_path, postgres_engine, field, value,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    changed = TranscriptRecoveryRequest.model_validate(request.model_dump() | {field: value})
    with pytest.raises(ApplicationError) as failure:
        _apply(api, changed)
    assert failure.value.detail["code"] == "transcript_recovery_conflict"


@pytest.mark.parametrize("state,code", [
    ("active", "transcript_recovery_active"), ("paused", "transcript_recovery_paused"),
    ("ready", "transcript_recovery_already_copied"),
])
def test_prepare_and_fresh_apply_reject_active_paused_or_already_copied(
    api, project, work_payload, tmp_path, postgres_engine, state, code,
):
    work, _, record, original = register(api, project, work_payload, tmp_path)
    request = recovery_request(api, project, record, original)
    if state != "active":
        expire_lease(postgres_engine, work["id"])
    if state == "paused":
        _pause(api, project, False)
    elif state == "ready":
        assert run(api)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    for action in (lambda: inspect_recovery_target(factory, settings, request.transcript_id,
                                                   request.project_id),
                   lambda: _apply(api, request)):
        with pytest.raises(ApplicationError) as failure:
            action()
        assert failure.value.detail["code"] == code
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 0
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 0


@pytest.mark.parametrize("field,value", [
    ("expected_generation", 42), ("expected_snapshot_id", uuid4()),
    ("original_source_path", "/wrong/assertion.jsonl"),
])
def test_fresh_recovery_rejects_stale_identity_without_side_effects(
    api, project, work_payload, tmp_path, postgres_engine, field, value,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    changed = TranscriptRecoveryRequest.model_validate(request.model_dump() | {field: value})
    with pytest.raises(ApplicationError) as failure:
        _apply(api, changed)
    assert failure.value.detail["code"] == "transcript_recovery_stale"
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 0
        row = database.get(Transcript, request.transcript_id)
        assert row.generation == request.expected_generation


def test_changed_bytes_are_terminal_before_publication_and_keep_legacy_ready_text(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, original = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    retained = read(api, project, record)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        for name, value in new_transcript_copy().items():
            setattr(row, name, value)
    replacement = source(tmp_path / "verified")
    request = recovery_request(api, project, record, replacement)
    result = _apply(api, request)
    replacement.write_bytes(b"changed, no longer approved")
    context = _copy_context(api)
    with pytest.raises(PermanentJobError, match="transcript_recovery_content_changed"):
        handle_transcript_copy(factory, settings, context)
    current = read(api, project, record)
    assert current["copy_error_code"] == "transcript_recovery_content_changed"
    assert current["copy_status"] == "failed" and _stored(api, request, "copy_attempts") == 1
    for name in ("status", "sha256", "text_sha256", "metadata", "source_path"):
        assert current[name] == retained[name]
    assert api.get(collection(project) + "/" + record["id"] + "/content").status_code == 200
    key = TranscriptStorage.key(request.transcript_id, result.resulting_snapshot_id)
    assert not (settings.transcript_root / key).exists()
    assert not list(settings.transcript_root.rglob(".pending-*"))
    original.unlink()


def test_recovery_rejects_corrupt_retained_snapshot_instead_of_adopting_it(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, record, replacement, request = _request_fixture(
        api, project, work_payload, tmp_path, postgres_engine)
    result = _apply(api, request)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    store = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    unapproved = tmp_path / "unapproved.jsonl"
    unapproved.write_bytes(b"wrong bytes left in private storage")
    copied = store.capture(request.transcript_id, result.resulting_snapshot_id,
                            str(unapproved), settings.transcript_allowed_roots)
    replacement.unlink()
    assert copy_next_transcript(factory, settings)
    current = read(api, project, record)
    assert current["copy_error_code"] == "transcript_recovery_content_changed"
    assert current["copy_status"] == "failed" and _stored(api, request, "copy_sha256") is None
    assert store.read_copy(copied) == unapproved.read_bytes()


def test_recovery_crash_adopts_only_pinned_copy_after_source_disappears(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, record, replacement, request = _request_fixture(
        api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    abandoned = claim_transcript_copy(factory, settings)
    assert abandoned is not None and abandoned.expected is not None
    store = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    copied = store.capture(abandoned.transcript_id, abandoned.snapshot_id,
        abandoned.source_path, settings.transcript_allowed_roots, expected=abandoned.expected)
    replacement.unlink()
    with factory.begin() as database:
        row = database.get(Transcript, request.transcript_id)
        row.copy_lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert run(api)
    complete_transcript_copy(factory, abandoned, None, ExtractionError("obsolete_failure"))
    ready = read(api, project, record)
    assert ready["sha256"] == copied.sha256 == request.expected_sha256
    assert ready["status"] == "ready"
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4())}).status_code == 200
    assert run(api)
    with factory() as database:
        assert database.get(Transcript, request.transcript_id).recovery_operation_id \
            == request.operation_id


def test_operator_recovery_fences_preexisting_copy_claim(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, original = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    stale = claim_transcript_copy(factory, settings)
    replacement = source(tmp_path / "correct")
    request = recovery_request(api, project, record, replacement)
    result = _apply(api, request)
    copied = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes).capture(
        stale.transcript_id, stale.snapshot_id, stale.source_path,
        settings.transcript_allowed_roots)
    complete_transcript_copy(factory, stale, copied, None)
    assert read(api, project, record)["copy_status"] == "pending"
    assert run(api)
    with factory() as database:
        row = database.get(Transcript, request.transcript_id)
        assert row.snapshot_id == result.resulting_snapshot_id
        assert row.copy_sha256 == request.expected_sha256 != copied.sha256
        assert row.source_path == str(original)


def test_recovery_queue_rechecks_pause_and_current_allowlist_without_losing_approval(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, record, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    settings.transcript_allowed_roots = []
    with pytest.raises(ApplicationError) as failure:
        _apply(api, request)
    assert failure.value.detail["code"] == "transcript_path_not_allowed"
    settings.transcript_allowed_roots = [tmp_path]
    _apply(api, request)
    context = _copy_context(api)
    _pause(api, project, False)
    with pytest.raises(RetryJob) as failure:
        handle_transcript_copy(factory, settings, context)
    assert not failure.value.consume_attempt
    _pause(api, project, True)
    settings.transcript_allowed_roots = []
    with pytest.raises(PermanentJobError, match="transcript_path_not_allowed"):
        handle_transcript_copy(factory, settings, context)
    settings.transcript_allowed_roots = [tmp_path / "actual-worktree"]
    assert copy_next_transcript(factory, settings)
    assert read(api, project, record)["copy_status"] == "ready"


def test_fresh_enrollment_clears_recovery_pointer_but_preserves_journal(
    api, project, work_payload, tmp_path, postgres_engine,
):
    original = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    assert import_folder(api, project, tmp_path).json()["imported"] == 1
    record = api.get(collection(project), params={"detail": "full"}).json()["items"][0]
    replacement = source(tmp_path / "recovered")
    request = recovery_request(api, project, record, replacement)
    _apply(api, request)
    work = create_work(api, project, work_payload)["work_item"]
    claim(api, item_path(project, work), source={"client": "claude_code", "path": str(original)})
    with api.app.state.session_factory() as database:
        row = database.get(Transcript, request.transcript_id)
        assert row.recovery_operation_id is None
        assert database.get(TranscriptRecovery, request.operation_id) is not None
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    current = read(api, project, record)
    assert current["sha256"] == describe_recovery_source(
        api.app.state.settings, str(original)).sha256


def test_recovery_receipt_follows_work_move_but_fresh_stale_project_is_rejected(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, record, _, request = _request_fixture(
        api, project, work_payload, tmp_path, postgres_engine)
    result = _apply(api, request)
    target = _project(api, "Recovery destination")
    moved = api.post(item_path(project, work) + "/move",
                     json=_move_payload(target, work["version"]))
    assert moved.status_code == 200, moved.text
    assert _apply(api, request) == result
    fresh = TranscriptRecoveryRequest.model_validate(request.model_dump() | {
        "operation_id": uuid4(), "expected_generation": result.resulting_generation,
        "expected_snapshot_id": result.resulting_snapshot_id})
    with pytest.raises(ApplicationError) as failure:
        _apply(api, fresh)
    assert failure.value.detail["code"] == "transcript_recovery_stale"
    assert run(api)
    assert read(api, target, record)["sha256"] == request.expected_sha256


@pytest.mark.parametrize("mutation", ["UPDATE", "DELETE", "TRUNCATE"])
def test_database_recovery_history_is_append_only_and_downgrade_is_guarded(
    api, project, work_payload, tmp_path, postgres_engine, mutation,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    statement = {"UPDATE": "UPDATE transcript_recoveries SET reason='rewritten'",
                 "DELETE": "DELETE FROM transcript_recoveries",
                 "TRUNCATE": "TRUNCATE transcript_recoveries CASCADE"}[mutation]
    with pytest.raises(IntegrityError, match="transcript recovery history is immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text(statement))
    with pytest.raises(RuntimeError, match="recovery history cannot be safely downgraded"):
        migrate(postgres_engine, "0037_background_jobs", downgrade=True)


def test_database_pointer_rejects_another_transcripts_approval(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    another = source(tmp_path / "separate")
    assert import_folder(api, project, another.parent).json()["imported"] == 1
    with pytest.raises(IntegrityError):
        with api.app.state.session_factory.begin() as database:
            record = database.scalar(select(Transcript).where(
                Transcript.source_path == str(another)))
            record.recovery_operation_id = request.operation_id


def test_concurrent_exact_operation_is_busy_then_replays_without_duplicate_jobs(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    entered, release = Event(), Event()
    original = recovery_service._record_recovery

    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(recovery_service, "_record_recovery", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(_apply, api, request)
        try:
            assert entered.wait(5)
            with pytest.raises(ApplicationError) as failure:
                _apply(api, request)
            assert failure.value.detail["code"] == "transcript_recovery_busy"
        finally:
            release.set()
        result = first.result(timeout=5)
    assert _apply(api, request) == result
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 1
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 1


def test_concurrent_new_operations_cannot_both_change_one_expected_generation(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    second = TranscriptRecoveryRequest.model_validate(
        request.model_dump() | {"operation_id": uuid4()})

    def attempt(payload):
        try:
            return _apply(api, payload)
        except ApplicationError as error:
            return error.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (request, second)))
    assert results.count("transcript_recovery_stale") == 1
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 1
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 1


def test_superseded_recovery_claim_cannot_publish_its_previous_approved_source(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, record, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)
    _apply(api, request)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    stale = claim_transcript_copy(factory, settings)
    replacement = source(tmp_path / "second-approved")
    replacement.write_text('{"role":"user","content":"new explicit recovery approval"}\n')
    newer = recovery_request(api, project, record, replacement)
    result = _apply(api, newer)
    copied = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes).capture(
        stale.transcript_id, stale.snapshot_id, stale.source_path,
        settings.transcript_allowed_roots, expected=stale.expected)
    complete_transcript_copy(factory, stale, copied, None)
    assert _stored(api, request, "snapshot_id") == result.resulting_snapshot_id
    assert read(api, project, record)["copy_status"] == "pending"
    assert run(api)
    assert read(api, project, record)["sha256"] == newer.expected_sha256 != request.expected_sha256
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 2


def test_failed_job_enqueue_rolls_back_recovery_and_generation(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    _, _, _, request = _request_fixture(api, project, work_payload, tmp_path, postgres_engine)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic transaction interruption")

    monkeypatch.setattr(recovery_service, "enqueue_job", unavailable)
    with pytest.raises(RuntimeError, match="synthetic transaction interruption"):
        _apply(api, request)
    assert _stored(api, request, "generation") == request.expected_generation
    assert _stored(api, request, "recovery_operation_id") is None
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 0
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 0


def test_work_move_preserves_redundant_import_with_immutable_recovery_history(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, enrolled, original = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    target = _project(api, "Recovered import destination")
    assert import_folder(api, target, tmp_path).json()["imported"] == 1
    imported = api.get(collection(target), params={"detail": "full"}).json()["items"][0]
    replacement = source(tmp_path / "recovery-copy")
    request = recovery_request(api, target, imported, replacement)
    _apply(api, request)
    moved = api.post(item_path(project, work) + "/move",
                     json=_move_payload(target, work["version"]))
    assert moved.status_code == 200, moved.text
    with api.app.state.session_factory() as database:
        assert database.get(TranscriptRecovery, request.operation_id) is not None
        retained = database.get(Transcript, request.transcript_id)
        assert retained.import_project_id == UUID(target["id"])
    page = api.get(collection(target), params={"detail": "full"}).json()
    assert {item["id"] for item in page["items"]} == {enrolled["id"], imported["id"]}
    assert all(item["source_path"] == str(original) for item in page["items"])
