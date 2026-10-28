"""Container initialization and private backup failure boundaries."""

import os
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from mnemonic_api import container_backup as backup
from mnemonic_api import container_runtime as runtime
from mnemonic_api import container_storage as storage

DATABASE_URL = "postgresql+psycopg://example:private-password@database:5432/example"


@pytest.fixture
def serving_runtime(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL)
    engine = MagicMock()
    monkeypatch.setattr(runtime, "create_engine", Mock(return_value=engine))
    guard = Mock()
    monkeypatch.setattr(runtime, "require_schema_head", guard)
    launch = Mock()
    monkeypatch.setattr(runtime.os, "execvp", launch)
    return engine, guard, launch


@pytest.mark.parametrize(("action", "target", "port"), [
    ("serve-api", "mnemonic_api.main:create_app", "8000"),
    ("serve-worker", "mnemonic_api.job_worker:create_app", "8002"),
])
def test_production_server_guards_before_launch_and_disables_access_logging(
    monkeypatch, serving_runtime, action, target, port,
):
    engine, guard, launch = serving_runtime
    monkeypatch.setattr(runtime.sys, "argv", ["container_runtime", action])

    def check_launch(command, arguments):
        guard.assert_called_once()
        engine.dispose.assert_called_once()
        assert command == "uvicorn"
        assert arguments[1] == target
        assert arguments[arguments.index("--port") + 1] == port
        assert "--no-access-log" in arguments
        assert arguments[arguments.index("--log-config") + 1] == "logging.json"
        if action == "serve-api":
            assert arguments[arguments.index("--h11-max-incomplete-event-size") + 1] == "65536"

    launch.side_effect = check_launch
    runtime.main()
    launch.assert_called_once()


@pytest.mark.parametrize("action", ["serve-api", "serve-worker"])
def test_production_guard_failure_never_launches_server(monkeypatch, serving_runtime, action):
    engine, guard, launch = serving_runtime
    monkeypatch.setattr(runtime.sys, "argv", ["container_runtime", action])
    guard.side_effect = RuntimeError("Database schema differs from this image")
    with pytest.raises(RuntimeError, match="Database schema differs"):
        runtime.main()
    engine.dispose.assert_called_once()
    launch.assert_not_called()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    paths = {name: tmp_path / name for name in runtime.STORES}
    monkeypatch.setattr(runtime, "STORES", paths)
    return paths


@pytest.mark.parametrize("mode", [None, "", "legacy", "automatic"])
def test_init_requires_explicit_storage_mode(stores, monkeypatch, mode):
    monkeypatch.delenv("MNEMONIC_STORAGE_MODE", raising=False)
    if mode is not None:
        monkeypatch.setenv("MNEMONIC_STORAGE_MODE", mode)
    with pytest.raises(RuntimeError, match="Existing installation"):
        runtime.initialize_storage()
    assert not any(path.exists() for path in stores.values())


@pytest.mark.parametrize("uid,gid", [("0", "10001"), ("10001", "0"), ("-1", "10001")])
def test_init_rejects_root_service_identity(stores, monkeypatch, uid, gid):
    monkeypatch.setenv("MNEMONIC_STORAGE_MODE", "volumes")
    monkeypatch.setenv("MNEMONIC_API_UID", uid)
    monkeypatch.setenv("MNEMONIC_API_GID", gid)
    with pytest.raises(RuntimeError, match="positive non-root"):
        runtime.initialize_storage()
    assert not any(path.exists() for path in stores.values())


def test_init_changes_mount_roots_only(stores, monkeypatch):
    monkeypatch.setenv("MNEMONIC_STORAGE_MODE", "bind")
    monkeypatch.setenv("MNEMONIC_API_UID", "1026")
    monkeypatch.setenv("MNEMONIC_API_GID", "1000")
    child = stores["artifacts"] / "existing" / "content"
    child.parent.mkdir(parents=True)
    child.write_bytes(b"preserved")
    child.chmod(0o640)
    change_owner = Mock()
    monkeypatch.setattr(runtime.os, "chown", change_owner)
    runtime.initialize_storage()
    assert change_owner.call_count == len(stores)
    assert {call.args for call in change_owner.call_args_list} == {
        (path, 1026, 1000) for path in stores.values()}
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in stores.values())
    assert stat.S_IMODE(child.stat().st_mode) == 0o640
    assert child.read_bytes() == b"preserved"


def test_init_refuses_symlink_ancestor(stores, monkeypatch, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(runtime, "STORES", {"artifacts": link / "artifacts"})
    monkeypatch.setenv("MNEMONIC_STORAGE_MODE", "volumes")
    with pytest.raises(RuntimeError, match="symlink"):
        runtime.initialize_storage()
    assert not (real / "artifacts").exists()


@pytest.mark.parametrize("relative", ["../outside", "/absolute", "linked", "directory"])
def test_store_matches_only_readable_regular_files_without_symlinks(tmp_path, relative):
    (tmp_path / "content").write_text("retained")
    (tmp_path / "linked").symlink_to(tmp_path / "content")
    (tmp_path / "directory").mkdir()
    assert storage._readable_file(tmp_path, "content")
    assert not storage._readable_file(tmp_path, relative)


def test_dump_uses_exact_connection_identity_and_no_inherited_pg_overrides(monkeypatch):
    monkeypatch.setenv("PGSERVICE", "unrelated-database")
    monkeypatch.setenv("PGHOST", "other-host")
    env = backup.dump_environment(DATABASE_URL + "?sslmode=require&options=-c%20search_path%3Dapp")
    assert env["PGHOST"] == "database"
    assert env["PGDATABASE"] == "example"
    assert env["PGPASSWORD"] == "private-password"
    assert env["PGSSLMODE"] == "require"
    assert env["PGOPTIONS"] == "-c search_path=app"
    assert "PGSERVICE" not in env


def test_dump_rejects_unknown_connection_options():
    with pytest.raises(RuntimeError, match="Unsupported.*connection option"):
        backup.dump_environment(DATABASE_URL + "?unknown=true")


def test_no_free_space_prevents_pg_dump(tmp_path, monkeypatch):
    connection = Mock()
    connection.scalar.return_value = 100
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=99))
    run = Mock()
    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="Insufficient free space"):
        backup.backup_database(connection, DATABASE_URL, tmp_path / "backups")
    run.assert_not_called()
    assert list((tmp_path / "backups").iterdir()) == []


@pytest.mark.parametrize("failure", ["exit", "empty", "launch"])
def test_failed_dump_removes_private_incomplete_file(tmp_path, monkeypatch, failure):
    connection = Mock()
    connection.scalar.return_value = 100
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    root = tmp_path / "backups"

    def run(argv, **kwargs):
        assert argv == ["pg_dump", "--format=custom", "--no-password"]
        output = kwargs["stdout"]
        assert stat.S_IMODE(os.fstat(output.fileno()).st_mode) == 0o600
        assert kwargs["env"]["PGPASSWORD"] == "private-password"
        assert len(list(root.glob(".incomplete-*"))) == 1
        if failure == "launch":
            raise OSError("pg_dump is unavailable")
        if failure == "exit":
            output.write(b"unfinished dump")
        return subprocess.CompletedProcess(argv, 1 if failure == "exit" else 0)

    monkeypatch.setattr(backup.subprocess, "run", run)
    with pytest.raises((RuntimeError, OSError)):
        backup.backup_database(connection, DATABASE_URL, root)
    assert list(root.iterdir()) == []


def test_successful_dump_publishes_private_file_after_completion(tmp_path, monkeypatch):
    connection = Mock()
    connection.scalar.return_value = 100
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    root = tmp_path / "backups"

    def run(argv, **kwargs):
        assert not list(root.glob("*.dump"))
        kwargs["stdout"].write(b"PGDMP-retained recovery bytes")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(backup.subprocess, "run", run)
    path = backup.backup_database(connection, DATABASE_URL, root)
    assert path.read_bytes() == b"PGDMP-retained recovery bytes"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert list(root.iterdir()) == [path]


def test_actual_failing_dump_process_cannot_leave_a_published_backup(tmp_path, monkeypatch):
    executable = tmp_path / "pg_dump"
    executable.write_text("#!/bin/sh\nprintf 'partial dump bytes'\nexit 1\n")
    executable.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=10**12))
    connection = Mock()
    connection.scalar.return_value = 100
    root = tmp_path / "backups"
    with pytest.raises(RuntimeError, match="pg_dump failed"):
        backup.backup_database(connection, DATABASE_URL, root)
    assert list(root.iterdir()) == []
