"""Private compressed archive publication, locking, and per-project retention."""

import fcntl
import json
import logging
import os
import re
import stat
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from mnemonic_backup.archive import BackupError

ARCHIVE_NAME = re.compile(r"project-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json\.bz2\Z")
RESTORE_DIRECTORY = re.compile(r"\.mnemonic-restore-[a-z0-9_]{8}\Z")
PARTIAL_NAME = re.compile(r"\.compressed-[0-9a-f]{32}\.partial\.bz2\Z")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK


def _check_file(descriptor: int) -> os.stat_result:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
        raise BackupError(503, "storage_unavailable", "Backup storage is unavailable.")
    return info


def metadata(name: str, info: os.stat_result) -> dict:
    return {
        "filename": name,
        "created_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
        "size_bytes": info.st_size,
    }


class BackupStore:
    def __init__(self, root: Path, retention_days: int = 7):
        self.root = root
        self.retention_days = retention_days

    @contextmanager
    def directory(self, project_id: UUID | None = None) -> Generator[int]:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.root, DIRECTORY_FLAGS)
        try:
            if os.fstat(descriptor).st_uid != os.geteuid():
                raise BackupError(503, "storage_unavailable", "Backup storage owner is invalid.")
            if os.fstat(descriptor).st_mode & 0o077:
                raise BackupError(503, "storage_unavailable", "Backup storage must be private.")
            if project_id is None:
                yield descriptor
                return
            project = str(project_id)
            try:
                os.mkdir(project, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
            project_fd = os.open(project, DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                if os.fstat(project_fd).st_uid != os.geteuid():
                    raise BackupError(503, "storage_unavailable",
                                      "Backup storage owner is invalid.")
                if os.fstat(project_fd).st_mode & 0o077:
                    raise BackupError(503, "storage_unavailable", "Backup storage must be private.")
                yield project_fd
            finally:
                os.close(project_fd)
        finally:
            os.close(descriptor)

    @contextmanager
    def operation(self) -> Generator[None]:
        # One lock also coordinates CLI commands and multiple service processes.
        with self.directory() as directory:
            descriptor = os.open(".operation-lock", os.O_RDWR | os.O_CREAT | FILE_FLAGS,
                                 0o600, dir_fd=directory)
            try:
                _check_file(descriptor)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise BackupError(409, "backup_busy",
                                      "Another backup or restore is running.") from None
                self.cleanup_staging(directory)
                yield
            finally:
                os.close(descriptor)

    def cleanup_staging(self, root: int) -> None:
        # The global operation lock proves no live upload/export owns these files.
        for name in os.listdir(root):
            if RESTORE_DIRECTORY.fullmatch(name):
                self._remove_restore_spool(root, name)
                continue
            try:
                project = UUID(name)
            except ValueError:
                continue
            if str(project) != name:
                continue
            info = os.stat(name, dir_fd=root, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                continue
            with self.directory(project) as directory:
                self._remove_partials(directory)

    @staticmethod
    def _remove_restore_spool(root: int, name: str) -> None:
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077):
            return
        directory = os.open(name, DIRECTORY_FLAGS, dir_fd=root)
        try:
            entries = os.listdir(directory)
            if set(entries) - {"rows.sqlite"}:
                return
            if entries:
                payload = os.stat("rows.sqlite", dir_fd=directory, follow_symlinks=False)
                if not stat.S_ISREG(payload.st_mode) or payload.st_uid != os.geteuid():
                    return
                os.unlink("rows.sqlite", dir_fd=directory)
                os.fsync(directory)
        finally:
            os.close(directory)
        os.rmdir(name, dir_fd=root)
        os.fsync(root)

    @staticmethod
    def _remove_partials(directory: int) -> None:
        for name in os.listdir(directory):
            if not PARTIAL_NAME.fullmatch(name):
                continue
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                os.unlink(name, dir_fd=directory)
        os.fsync(directory)

    def list_archives(self, project_id: UUID) -> list[dict]:
        with self.directory(project_id) as directory:
            entries = []
            for name in os.listdir(directory):
                if not ARCHIVE_NAME.fullmatch(name):
                    continue
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    # Catalog polling can overlap deletion or retention. An
                    # archive removed after listdir is simply no longer listed.
                    continue
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                    entries.append(metadata(name, info))
            return sorted(entries, key=lambda item: (item["created_at"], item["filename"]),
                          reverse=True)

    @contextmanager
    def staging(self, project_id: UUID) -> Generator[tuple[BinaryIO, int, str]]:
        with self.directory(project_id) as directory:
            name = ".compressed-" + uuid4().hex + ".partial.bz2"
            descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | FILE_FLAGS,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "w+b") as output:
                    yield output, directory, name
            finally:
                try:
                    os.unlink(name, dir_fd=directory)
                    os.fsync(directory)
                except FileNotFoundError:
                    pass

    def job_result(self, project_id: UUID, job_id: UUID) -> dict | None:
        """Recover publication after a worker died before recording its database result."""
        with self.directory(project_id) as directory:
            name = f".job-{job_id.hex}.json"
            try:
                descriptor = os.open(name, os.O_RDONLY | FILE_FLAGS, dir_fd=directory)
            except FileNotFoundError:
                # Publication precedes the receipt. A crash in that narrow window
                # leaves a recognizable archive that must never be exported again.
                suffix = f"-{job_id.hex}.json.bz2"
                matches = [item for item in self.list_archives(project_id)
                           if item["filename"].endswith(suffix)]
                if not matches:
                    return None
                result = matches[0]
                self._record_job(directory, job_id, result)
                return result
            with os.fdopen(descriptor, "rb") as receipt:
                info = _check_file(receipt.fileno())
                if info.st_size > 1024:
                    raise BackupError(503, "storage_unavailable", "Backup receipt is invalid.")
                try:
                    result = json.load(receipt)
                except (ValueError, UnicodeError):
                    raise BackupError(503, "storage_unavailable",
                                      "Backup receipt is invalid.") from None
            if (not isinstance(result, dict)
                    or not isinstance(result.get("filename"), str)
                    or not ARCHIVE_NAME.fullmatch(result["filename"])
                    or not result["filename"].endswith(f"-{job_id.hex}.json.bz2")
                    or not isinstance(result.get("created_at"), str)
                    or type(result.get("size_bytes")) is not int
                    or result["size_bytes"] < 1):
                raise BackupError(503, "storage_unavailable", "Backup receipt is invalid.")
            return result

    @staticmethod
    def _record_job(directory: int, job_id: UUID, result: dict) -> None:
        # Small durable receipts outlive archive retention. Replaying an old
        # delivery must not create another archive or prune another backup.
        temporary = f".receipt-{job_id.hex}.partial"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | FILE_FLAGS,
                             0o600, dir_fd=directory)
        with os.fdopen(descriptor, "wb") as receipt:
            _check_file(receipt.fileno())
            receipt.write(json.dumps(result, separators=(",", ":")).encode())
            receipt.flush()
            os.fsync(receipt.fileno())
        os.replace(temporary, f".job-{job_id.hex}.json",
                   src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)

    def publish(self, project_id: UUID, output: BinaryIO, directory: int, partial: str,
                *, job_id: UUID | None = None, retention_days: int | None = None) -> dict:
        output.flush()
        os.fsync(output.fileno())
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"project-{stamp}-{(job_id or uuid4()).hex}.json.bz2"
        # Hard-link publication never overwrites a previous successful backup.
        os.link(partial, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
        result = metadata(name, os.fstat(output.fileno()))
        if job_id is not None:
            self._record_job(directory, job_id, result)
        # Publication is already durable. A pruning error must not turn a valid
        # archive into a failed job or prompt creation of another copy.
        try:
            self.prune(project_id, keep=name, retention_days=retention_days)
        except (BackupError, OSError):
            logging.getLogger(__name__).warning("Backup published; retention cleanup failed")
        return result

    def prune(self, project_id: UUID, *, keep: str, retention_days: int | None = None) -> None:
        days = self.retention_days if retention_days is None else retention_days
        if days == 0:
            return
        cutoff = datetime.now(UTC) - timedelta(days=days)
        with self.directory(project_id) as directory:
            for entry in self.list_archives(project_id):
                if (entry["filename"] == keep
                        or datetime.fromisoformat(entry["created_at"]) >= cutoff):
                    continue
                self._remove_archive(project_id, directory, entry["filename"])
            os.fsync(directory)

    def _remove_archive(self, project_id: UUID, directory: int, filename: str) -> None:
        # Retain a publication receipt so late job redelivery cannot resurrect
        # a manually deleted or pruned backup.
        previous_job = UUID(hex=filename.removesuffix(".json.bz2").rsplit("-", 1)[1])
        self.job_result(project_id, previous_job)
        os.unlink(filename, dir_fd=directory)

    def delete(self, project_id: UUID, filename: str) -> None:
        if not ARCHIVE_NAME.fullmatch(filename):
            raise BackupError(404, "backup_not_found", "Backup not found.")
        with self.operation(), self.directory(project_id) as directory:
            try:
                with self.open(project_id, filename):
                    pass
            except BackupError as error:
                if error.code == "backup_not_found":
                    return  # A repeated delete has the same final state.
                raise
            self._remove_archive(project_id, directory, filename)
            os.fsync(directory)

    def capacity(self, path: str | None = None) -> dict:
        result = {"path": path or str(self.root), "total_bytes": None,
                  "free_bytes": None, "error": None}
        try:
            with self.directory() as directory:
                info = os.fstatvfs(directory)
                result.update(total_bytes=info.f_blocks * info.f_frsize,
                              free_bytes=info.f_bavail * info.f_frsize)
        except (OSError, BackupError):
            result["error"] = "Backup storage is unavailable. Check its mount and permissions."
        return result

    def open(self, project_id: UUID, filename: str) -> BinaryIO:
        if not ARCHIVE_NAME.fullmatch(filename):
            raise BackupError(404, "backup_not_found", "Backup not found.")
        with self.directory(project_id) as directory:
            try:
                descriptor = os.open(filename, os.O_RDONLY | FILE_FLAGS, dir_fd=directory)
            except FileNotFoundError:
                raise BackupError(404, "backup_not_found", "Backup not found.") from None
            try:
                _check_file(descriptor)
                return os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
