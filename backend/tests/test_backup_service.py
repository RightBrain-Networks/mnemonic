"""Private backup transport and filesystem failures never need a real database."""

import asyncio
import bz2
import errno
import io
import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from starlette.requests import Request

from mnemonic_backup.archive import BackupError
from mnemonic_backup.config import BackupSettings
from mnemonic_backup.jobs import ERROR_MESSAGES, schedule_backups
from mnemonic_backup.schedules import BackupSchedule
from mnemonic_backup.service import BackupService, _receive_upload, create_app
from mnemonic_backup.store import BackupStore

TEST_TOKEN = "backup-service-unit-test-credential-32-characters"
AUTHORIZATION = {"Authorization": "Bearer " + TEST_TOKEN}


def test_retention_preserves_future_archives_after_clock_moves_backwards(tmp_path: Path):
    project = uuid4()
    store = BackupStore(tmp_path / "backups", retention_days=1)
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"previous archive"))
        previous = store.publish(project, output, directory, partial)
    prior_path = store.root / str(project) / previous["filename"]
    future = time.time() + 3600
    os.utime(prior_path, (future, future))
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"latest archive"))
        latest = store.publish(project, output, directory, partial)
    assert len(store.list_archives(project)) == 2
    assert prior_path.exists()
    with store.open(project, latest["filename"]) as content:
        assert bz2.decompress(content.read()) == b"latest archive"


@pytest.fixture
def backup_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    settings = BackupSettings(DATABASE_URL="sqlite://", token=TEST_TOKEN, root=tmp_path / "backup")
    engine = create_engine("sqlite://")
    monkeypatch.setattr("mnemonic_backup.service.read_schedule", lambda *_args: BackupSchedule())
    try:
        yield create_app(settings, engine=engine)
    finally:
        engine.dispose()


def test_retention_defaults_to_seven_days_and_zero_disables_pruning():
    assert BackupSchedule().retention_days == 7
    assert not BackupSchedule().enabled
    assert BackupSchedule(retention_days=0).retention_days == 0


@pytest.mark.parametrize("value", [-1, 36501, "seven", 1.5, True])
def test_invalid_retention_is_rejected(value):
    with pytest.raises(ValidationError):
        BackupSchedule(retention_days=value)


@pytest.mark.parametrize("values", [{"timezone": "../private"}, {"timezone": "Unknown/Zone"},
                                     {"time": "25:00"}, {"time": "2:00"}, {"extra": True}])
def test_invalid_schedule_is_rejected(values):
    with pytest.raises(ValidationError):
        BackupSchedule(**values)


@pytest.mark.parametrize("root", ["/", "relative/backups"])
def test_storage_requires_a_dedicated_absolute_root(root: str):
    with pytest.raises(ValidationError):
        BackupSettings(DATABASE_URL="sqlite://", token=TEST_TOKEN, root=root)


def test_authentication_and_no_discoverable_documentation(backup_app: FastAPI):
    with TestClient(backup_app) as client:
        path = f"/projects/{uuid4()}/backups"
        for headers in ({}, {"Authorization": "Bearer ordinary-api-credential"}):
            denied = client.post(path, headers=headers)
            assert denied.status_code == 401
            assert denied.headers["Cache-Control"] == "no-store"
            assert TEST_TOKEN not in denied.text
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert client.get(path, headers=AUTHORIZATION).status_code == 404
        assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("location", ["root", "project", "lock", "archive"])
def test_store_refuses_symlinks(tmp_path: Path, location: str):
    root, outside, project = tmp_path / "backups", tmp_path / "outside", uuid4()
    outside.mkdir(mode=0o700)
    root.mkdir(mode=0o700)
    store = BackupStore(root, 7)
    name = f"project-20260908T120000000000Z-{uuid4().hex}.json.bz2"
    if location == "root":
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
    elif location == "project":
        (root / str(project)).symlink_to(outside, target_is_directory=True)
    elif location == "lock":
        (root / ".operation-lock").symlink_to(outside / "secret")
    else:
        (root / str(project)).mkdir(mode=0o700)
        (root / str(project) / name).symlink_to(outside / "secret")
    with pytest.raises((OSError, BackupError)):
        if location == "lock":
            with store.operation():
                pytest.fail("A symlink lock must not be accepted")
        elif location == "archive":
            store.open(project, name)
        else:
            store.list_archives(project)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_nonregular_archives_are_not_listed_or_opened(tmp_path: Path, kind: str):
    project = uuid4()
    store = BackupStore(tmp_path / "backups", 7)
    store.list_archives(project)
    name = f"project-20260908T120000000000Z-{uuid4().hex}.json.bz2"
    path = store.root / str(project) / name
    if kind == "fifo":
        os.mkfifo(path, 0o600)
    else:
        path.mkdir(mode=0o700)
    assert store.list_archives(project) == []
    with pytest.raises(BackupError, match="storage"):
        store.open(project, name)


def test_world_readable_storage_is_rejected(tmp_path: Path):
    root = tmp_path / "backups"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(BackupError, match="private"):
        BackupStore(root, 7).list_archives(uuid4())


def test_two_store_instances_cannot_overlap_mutations(tmp_path: Path):
    first = BackupStore(tmp_path / "backups", 7)
    second = BackupStore(first.root, 7)
    with first.operation(), pytest.raises(BackupError) as error:
        with second.operation():
            pytest.fail("Concurrent mutations must be rejected")
    assert error.value.status == 409
    with second.operation():
        pass


@pytest.mark.parametrize("failure", ["link", "fsync"])
def test_disk_full_after_export_preserves_archives_and_cleans_partial(
    backup_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
):
    service: BackupService = backup_app.state.backup_service
    project = uuid4()
    compressed = bz2.compress(b"PostgreSQL-only fixture")
    exports = []

    def fake_export(_engine, _project, output, **_kwargs):
        exports.append(True)
        output.write(compressed)

    monkeypatch.setattr("mnemonic_backup.service.export_project", fake_export)
    initial = service.create(project)
    directory = service.settings.root / str(project)
    prior = {path.name: path.read_bytes() for path in directory.iterdir()}

    original = getattr(os, failure)

    def disk_full(*args, **kwargs):
        if len(exports) < 2:
            return original(*args, **kwargs)
        raise OSError(errno.ENOSPC, "sensitive-database-or-storage-path")

    monkeypatch.setattr(f"mnemonic_backup.store.os.{failure}", disk_full)
    monkeypatch.setattr(service, "enqueue", service.create)
    with TestClient(backup_app, headers=AUTHORIZATION) as client:
        response = client.post(f"/projects/{project}/backups")
    assert response.status_code == 507
    assert response.json()["error"]["code"] == "storage_full"
    assert "sensitive" not in response.text
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == prior
    assert prior == {initial["filename"]: compressed}
    assert len(exports) == 2


def test_operation_cleans_only_stale_owned_compressed_partials(tmp_path: Path):
    store = BackupStore(tmp_path / "backups", 7)
    project = uuid4()
    store.list_archives(project)
    directory = store.root / str(project)
    stale = directory / f".compressed-{uuid4().hex}.partial.bz2"
    stale.write_bytes(b"BZh9 interrupted upload")
    unknown = directory / ".compressed-unknown.partial.bz2"
    unknown.write_bytes(b"user-managed file")
    archive = directory / f"project-20260908T120000000000Z-{uuid4().hex}.json.bz2"
    archive.write_bytes(b"BZh9 published archive")
    outside = tmp_path / "outside"
    outside.write_bytes(b"must stay")
    symlink = directory / f".compressed-{uuid4().hex}.partial.bz2"
    symlink.symlink_to(outside)
    unrelated = store.root / "not-a-project"
    unrelated.mkdir(mode=0o700)
    unrelated_partial = unrelated / stale.name
    unrelated_partial.write_bytes(b"must also stay")
    with store.operation():
        assert not stale.exists()
    assert unknown.read_bytes() == b"user-managed file"
    assert archive.read_bytes() == b"BZh9 published archive"
    assert outside.read_bytes() == b"must stay"
    assert symlink.is_symlink()
    assert unrelated_partial.exists()


def test_cleanup_cannot_delete_another_running_operations_staging(tmp_path: Path):
    first = BackupStore(tmp_path / "backups", 7)
    second = BackupStore(first.root, 7)
    project = uuid4()
    with first.operation(), first.staging(project) as (output, _directory, name):
        output.write(b"BZh9 live compressed upload")
        output.flush()
        with pytest.raises(BackupError):
            with second.operation():
                pytest.fail("Must acquire the operation lock before cleanup")
        assert (first.root / str(project) / name).exists()


def upload_request(chunks: list[bytes]) -> Request:
    pending = iter(chunks)

    async def receive():
        body = next(pending, None)
        return {"type": "http.request", "body": body or b"", "more_body": body is not None}

    return Request({"type": "http", "method": "POST", "path": "/"}, receive)


@pytest.mark.parametrize("chunks", [[], [b"B"], [b"B", b"Z"], [b"plain text"], [b"B", b"ad"]])
def test_empty_partial_and_wrong_magic_never_reach_storage(chunks: list[bytes]):
    output = io.BytesIO()
    with pytest.raises(BackupError) as error:
        asyncio.run(_receive_upload(upload_request(chunks), output, 1024))
    assert error.value.status == 400
    assert output.getvalue() == b""


def test_split_magic_preserves_the_exact_compressed_upload():
    compressed = bz2.compress(b"Exact project archive bytes")
    output = io.BytesIO()
    asyncio.run(
        _receive_upload(
            upload_request([compressed[:1], compressed[1:2], compressed[2:]]), output, 1024
        )
    )
    assert output.read() == compressed


def test_chunked_upload_limit_does_not_depend_on_content_length():
    output = io.BytesIO()
    with pytest.raises(BackupError) as error:
        asyncio.run(_receive_upload(upload_request([b"BZh9", b"x" * 1024]), output, 1024))
    assert error.value.status == 413
    assert output.getvalue() == b"BZh9"


def test_scheduler_reuses_the_same_durable_slot_after_restart(
    backup_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
):
    service: BackupService = backup_app.state.backup_service
    project = str(uuid4())
    with service.engine.begin() as connection:
        connection.execute(text("CREATE TABLE backup_settings "
            "(project_id TEXT, enabled BOOLEAN, time TEXT, timezone TEXT, updated_at TEXT)"))
        connection.execute(text("INSERT INTO backup_settings VALUES "
            "(:id, true, '02:00', 'UTC', '2026-01-01T00:00:00+00:00')"), {"id": project})
    calls = []
    monkeypatch.setattr("mnemonic_backup.jobs.enqueue_job",
                        lambda _session, **values: calls.append(values))
    with service.engine.connect() as connection:
        schedule_backups(connection, service.settings, now=datetime(2026, 10, 28, 1, tzinfo=UTC))
        assert calls == []
        schedule_backups(connection, service.settings, now=datetime(2026, 10, 28, 2, tzinfo=UTC))
        schedule_backups(connection, service.settings, now=datetime(2026, 10, 28, 3, tzinfo=UTC))
    assert calls[0] == calls[1]
    assert calls[0]["kind"] == "backup_create"
    assert calls[0]["payload"] == {"project_id": project}


def test_job_replay_after_retention_does_not_export_or_prune_again(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
):
    service: BackupService = backup_app.state.backup_service
    service.store.retention_days = 1
    exports = []

    def export(_engine, _project, output, **_kwargs):
        exports.append(True)
        output.write(bz2.compress(b"exact project data"))

    monkeypatch.setattr("mnemonic_backup.service.export_project", export)
    project, first_job, second_job = uuid4(), uuid4(), uuid4()
    with service.store.operation():
        first = service.create_locked(project, job_id=first_job)
        old = time.time() - 8 * 86400
        os.utime(service.store.root / str(project) / first["filename"], (old, old))
        second = service.create_locked(project, job_id=second_job)
        assert service.create_locked(project, job_id=first_job) == first
    assert len(exports) == 2
    assert service.store.list_archives(project) == [second]


def test_job_recovery_between_archive_publication_and_receipt(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
):
    service: BackupService = backup_app.state.backup_service
    project, job = uuid4(), uuid4()
    exports = []

    def export(_engine, _project, output, **_kwargs):
        exports.append(True)
        output.write(bz2.compress(b"exact project data"))

    monkeypatch.setattr("mnemonic_backup.service.export_project", export)
    record = service.store._record_job
    monkeypatch.setattr(service.store, "_record_job", lambda *_args: (_ for _ in ()).throw(
        OSError(errno.EIO, "interrupted receipt")))
    with service.store.operation(), pytest.raises(OSError):
        service.create_locked(project, job_id=job)
    published = service.store.list_archives(project)
    assert len(published) == 1
    monkeypatch.setattr(service.store, "_record_job", record)
    with service.store.operation():
        assert service.create_locked(project, job_id=job) == published[0]
    assert len(exports) == 1


def test_retention_recovers_an_interrupted_older_jobs_receipt(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
):
    service: BackupService = backup_app.state.backup_service
    service.store.retention_days = 1
    project, first_job, next_job = uuid4(), uuid4(), uuid4()
    calls = []

    def export(_engine, _project, output, **_kwargs):
        calls.append(True)
        output.write(bz2.compress(b"synthetic archive"))

    monkeypatch.setattr("mnemonic_backup.service.export_project", export)
    record = service.store._record_job
    monkeypatch.setattr(service.store, "_record_job", lambda *_args: None)
    with service.store.operation():
        first = service.create_locked(project, job_id=first_job)
    monkeypatch.setattr(service.store, "_record_job", record)
    old = time.time() - 8 * 86400
    os.utime(service.store.root / str(project) / first["filename"], (old, old))
    # The recovered receipt records the actual published file metadata.
    first["created_at"] = datetime.fromtimestamp(old, UTC).isoformat()
    with service.store.operation():
        latest = service.create_locked(project, job_id=next_job)
        assert service.create_locked(project, job_id=first_job) == first
    assert len(calls) == 2
    assert service.store.list_archives(project) == [latest]


def test_queue_http_and_project_scoped_status(backup_app: FastAPI,
                                            monkeypatch: pytest.MonkeyPatch):
    service: BackupService = backup_app.state.backup_service
    project, job = uuid4(), uuid4()
    queued = {"project_id": str(project), "job_id": str(job), "state": "pending"}
    monkeypatch.setattr(service, "enqueue", lambda _project: queued)
    monkeypatch.setattr(service, "job", lambda _project, _job: queued)
    with TestClient(backup_app, headers=AUTHORIZATION) as client:
        response = client.post(f"/projects/{project}/backups")
        assert response.status_code == 202
        assert response.json() == queued
        response = client.get(f"/projects/{project}/backup-jobs/{job}")
        assert response.status_code == 200
        assert response.json() == queued


def test_failed_project_does_not_skip_others_or_mark_cycle_success(
    backup_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    service: BackupService = backup_app.state.backup_service
    first, second = sorted([str(uuid4()), str(uuid4())])
    with service.engine.begin() as connection:
        connection.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        connection.execute(
            text("INSERT INTO projects VALUES (:id)"), [{"id": first}, {"id": second}]
        )
    attempted = []

    def create(project):
        attempted.append(project)
        if project == first:
            raise BackupError(503, "test", "secret-project-detail")

    monkeypatch.setattr(service, "create_locked", create)
    with pytest.raises(BackupError) as error:
        service.cycle()
    assert error.value.code == "backup_incomplete"
    assert attempted == [first, second]
    assert service.last_success is None
    assert "secret-project-detail" not in caplog.text


def test_day_retention_and_zero_preserve_expected_files(tmp_path: Path):
    project = uuid4()
    store = BackupStore(tmp_path / "backups")
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"old"))
        old = store.publish(project, output, directory, partial)
    old_path = store.root / str(project) / old["filename"]
    stamp = time.time() - 8 * 86400
    os.utime(old_path, (stamp, stamp))
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"keep forever"))
        recent = store.publish(project, output, directory, partial, retention_days=0)
    assert old_path.exists()
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"new"))
        latest = store.publish(project, output, directory, partial, retention_days=7)
    assert not old_path.exists()
    assert {item["filename"] for item in store.list_archives(project)} == {
        recent["filename"], latest["filename"]}


def test_delete_is_idempotent_and_retains_job_receipt(tmp_path: Path):
    project, job = uuid4(), uuid4()
    store = BackupStore(tmp_path / "backups")
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"data"))
        result = store.publish(project, output, directory, partial, job_id=job)
    store.delete(project, result["filename"])
    store.delete(project, result["filename"])
    assert store.list_archives(project) == []
    assert store.job_result(project, job) == result
    with pytest.raises(BackupError):
        store.delete(project, "../outside.bz2")


def test_storage_capacity_reports_host_path_and_safe_unavailability(tmp_path: Path):
    store = BackupStore(tmp_path / "backups")
    status = store.capacity("/srv/mnemonic/backups")
    assert status["path"] == "/srv/mnemonic/backups"
    assert status["total_bytes"] >= status["free_bytes"] > 0
    assert status["error"] is None
    store.root.chmod(0o755)
    status = store.capacity()
    assert status["total_bytes"] is status["free_bytes"] is None
    assert status["error"]


def test_known_job_failures_explain_recovery_without_secrets():
    assert "limit" in ERROR_MESSAGES["backup_too_large"]
    assert "Delete old backups" in ERROR_MESSAGES["storage_full"]
    assert "permissions" in ERROR_MESSAGES["storage_unavailable"]


def test_server_creation_does_not_inherit_browser_upload_limit(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
):
    service = backup_app.state.backup_service
    service.settings.max_bytes = 1024
    service.settings.expanded_max_bytes = 1024
    content = os.urandom(4096)

    def export(_engine, _project, output, **kwargs):
        assert kwargs == {}
        output.write(bz2.compress(content))

    monkeypatch.setattr("mnemonic_backup.service.export_project", export)
    project = uuid4()
    result = service.create(project)
    assert result["size_bytes"] > 1024
    with service.store.open(project, result["filename"]) as archived:
        assert bz2.decompress(archived.read()) == content


def test_operation_cleans_abandoned_restore_spools_without_following_symlinks(tmp_path: Path):
    store = BackupStore(tmp_path / "backups")
    store.list_archives(uuid4())
    abandoned = store.root / ".mnemonic-restore-abcdefgh"
    abandoned.mkdir(mode=0o700)
    (abandoned / "rows.sqlite").write_bytes(b"large private restore spool")
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "rows.sqlite").write_bytes(b"preserve outside")
    linked = store.root / ".mnemonic-restore-abcdefgi"
    linked.symlink_to(outside, target_is_directory=True)
    mixed = store.root / ".mnemonic-restore-abcdefgj"
    mixed.mkdir(mode=0o700)
    (mixed / "rows.sqlite").write_bytes(b"retain unknown directory")
    (mixed / "unknown").write_bytes(b"user file")
    linked_file = store.root / ".mnemonic-restore-abcdefgk"
    linked_file.mkdir(mode=0o700)
    (linked_file / "rows.sqlite").symlink_to(outside / "rows.sqlite")
    with store.operation():
        assert not abandoned.exists()
    assert linked.is_symlink()
    assert (outside / "rows.sqlite").read_bytes() == b"preserve outside"
    assert (mixed / "unknown").exists()
    assert (linked_file / "rows.sqlite").is_symlink()


def test_second_operation_cannot_clean_active_restore_spool(tmp_path: Path):
    from mnemonic_backup.archive_spool import RestoreRows

    store = BackupStore(tmp_path / "backups")
    with store.operation(), RestoreRows(("projects",), directory=store.root) as rows:
        rows["projects"].append({"id": "fixture"})
        with pytest.raises(BackupError):
            with BackupStore(store.root).operation():
                pytest.fail("Concurrent operation must not acquire the cleanup lock")
        assert list(rows["projects"]) == [{"id": "fixture"}]


@pytest.mark.parametrize("error_number, retry", [(errno.ENOSPC, False), (errno.EACCES, True),
                                                 (errno.EIO, True)])
def test_worker_retries_recoverable_storage_failures_but_reports_disk_full(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch, error_number: int, retry: bool,
):
    from mnemonic_jobs import JobContext, PermanentJobError, RetryJob

    service = backup_app.state.backup_service
    project = uuid4()
    context = JobContext(uuid4(), "backup_create", {"project_id": str(project)}, uuid4(), 1)

    def unavailable():
        raise OSError(error_number, "private path must not escape")

    monkeypatch.setattr(service.store, "operation", unavailable)
    with pytest.raises(PermanentJobError) as raised:
        service.handle_job(context)
    assert isinstance(raised.value, RetryJob) is retry
    assert raised.value.code == ("storage_unavailable" if retry else "storage_full")


def test_catalog_skips_archive_deleted_between_listing_and_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    project = uuid4()
    store = BackupStore(tmp_path / "backups")
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"data"))
        result = store.publish(project, output, directory, partial)
    original = os.stat

    def disappeared(path, *args, **kwargs):
        if path == result["filename"]:
            raise FileNotFoundError(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr("mnemonic_backup.store.os.stat", disappeared)
    assert store.list_archives(project) == []


def test_job_losing_lease_during_export_cannot_publish_or_prune(
    backup_app: FastAPI, monkeypatch: pytest.MonkeyPatch,
):
    from mnemonic_jobs import JobContext, LostLease

    service = backup_app.state.backup_service
    project = uuid4()
    context = JobContext(uuid4(), "backup_create", {"project_id": str(project)}, uuid4(), 1)
    checks = []

    def owned(self, _database):
        checks.append(True)
        if self.lost.is_set():
            raise LostLease()

    def export(_engine, _project, output):
        output.write(bz2.compress(b"snapshot before a lost worker lease"))
        context.lost.set()

    monkeypatch.setattr(JobContext, "assert_owned", owned)
    monkeypatch.setattr("mnemonic_backup.service.export_project", export)
    with pytest.raises(LostLease):
        service.handle_job(context)
    assert len(checks) == 2
    assert service.store.list_archives(project) == []
    assert list((service.store.root / str(project)).iterdir()) == []


def test_download_closes_archive_when_response_aborts_before_first_byte(
    backup_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    archive = tmp_path / "download.bz2"
    archive.write_bytes(bz2.compress(b"retained backup"))
    content = archive.open("rb")
    monkeypatch.setattr(backup_app.state.backup_service.store, "open", lambda *_args: content)
    route = next(route for route in backup_app.routes
                 if getattr(route, "path", None) == "/projects/{project_id}/backups/{filename}"
                 and "GET" in route.methods)
    response = route.endpoint(uuid4(), archive.name)

    async def disconnected(_message):
        raise RuntimeError("response aborted before archive iteration")

    async def receive():
        return {"type": "http.disconnect"}

    async def scenario():
        with pytest.raises(RuntimeError, match="response aborted"):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}},
                           receive, disconnected)

    asyncio.run(scenario())
    assert content.closed
