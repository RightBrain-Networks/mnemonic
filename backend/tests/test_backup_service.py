"""Private backup transport and filesystem failures never need a real database."""

import asyncio
import bz2
import errno
import io
import logging
import os
import time
from collections.abc import Iterator
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
from mnemonic_backup.service import BackupService, _receive_upload, create_app
from mnemonic_backup.store import BackupStore

TEST_TOKEN = "backup-service-unit-test-credential-32-characters"
AUTHORIZATION = {"Authorization": "Bearer " + TEST_TOKEN}


def test_retention_keeps_new_publication_after_clock_moves_backwards(tmp_path: Path):
    project = uuid4()
    store = BackupStore(tmp_path / "backups", retention_count=1)
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"previous archive"))
        previous = store.publish(project, output, directory, partial)
    prior_path = store.root / str(project) / previous["filename"]
    future = time.time() + 3600
    os.utime(prior_path, (future, future))
    with store.operation(), store.staging(project) as (output, directory, partial):
        output.write(bz2.compress(b"latest archive"))
        latest = store.publish(project, output, directory, partial)
    assert store.list_archives(project) == [latest]
    assert not prior_path.exists()
    with store.open(project, latest["filename"]) as content:
        assert bz2.decompress(content.read()) == b"latest archive"


@pytest.fixture
def backup_app(tmp_path: Path) -> Iterator[FastAPI]:
    settings = BackupSettings(DATABASE_URL="sqlite://", token=TEST_TOKEN, root=tmp_path / "backup")
    engine = create_engine("sqlite://")
    try:
        yield create_app(settings, engine=engine, scheduled=False)
    finally:
        engine.dispose()


def test_retention_defaults_to_seven_and_reads_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MNEMONIC_BACKUP_RETENTION_COUNT", raising=False)
    options = {"DATABASE_URL": "sqlite://", "token": TEST_TOKEN}
    assert BackupSettings(**options).retention_count == 7
    monkeypatch.setenv("MNEMONIC_BACKUP_RETENTION_COUNT", "3")
    assert BackupSettings(**options).retention_count == 3


@pytest.mark.parametrize("value", ["0", "-1", "10001", "seven", "1.5"])
def test_invalid_retention_fails_startup(value: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMONIC_BACKUP_RETENTION_COUNT", value)
    with pytest.raises(ValidationError):
        BackupSettings(DATABASE_URL="sqlite://", token=TEST_TOKEN)


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
        assert client.get("/healthz").status_code == 503
        backup_app.state.backup_service.last_success = time.monotonic()
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


def test_scheduler_retries_failed_cycle_without_logging_sensitive_details(
    backup_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    service: BackupService = backup_app.state.backup_service
    # Migration fixtures configure logging with disable_existing_loggers=True.
    # Restore this test's logger explicitly so collection order cannot mute it.
    monkeypatch.setattr(logging.getLogger("mnemonic_backup.service"), "disabled", False)
    caplog.set_level(logging.WARNING, logger="mnemonic_backup.service")
    attempts, sleeps = [], []

    def cycle():
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError(errno.ENOSPC, "secret-project-name-and-database-password")

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(service, "cycle", cycle)
    monkeypatch.setattr("mnemonic_backup.service.asyncio.sleep", sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.schedule())
    assert sleeps == [60, service.settings.interval_seconds]
    assert len(attempts) == 2
    assert "Scheduled backups unavailable" in caplog.text
    assert "secret-project" not in caplog.text


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
