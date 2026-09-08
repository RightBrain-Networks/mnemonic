"""Private compressed archive publication, locking, and per-project retention."""

import fcntl
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from mnemonic_backup.archive import BackupError

ARCHIVE_NAME = re.compile(r"project-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}\.json\.bz2\Z")
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
    def __init__(self, root: Path, retention_count: int):
        self.root = root
        self.retention_count = retention_count

    @contextmanager
    def directory(self, project_id: UUID | None = None) -> Iterator[int]:
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
    def operation(self) -> Iterator[None]:
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
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                    entries.append(metadata(name, info))
            return sorted(entries, key=lambda item: (item["created_at"], item["filename"]),
                          reverse=True)

    @contextmanager
    def staging(self, project_id: UUID) -> Iterator[tuple[BinaryIO, int, str]]:
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

    def publish(self, project_id: UUID, output: BinaryIO, directory: int, partial: str) -> dict:
        output.flush()
        os.fsync(output.fileno())
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        name = f"project-{stamp}-{uuid4().hex}.json.bz2"
        # Hard-link publication never overwrites a previous successful backup.
        os.link(partial, name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.fsync(directory)
        result = metadata(name, os.fstat(output.fileno()))
        # A backward clock adjustment must never evict the backup just created.
        previous = [entry for entry in self.list_archives(project_id)
                    if entry["filename"] != name]
        for entry in previous[self.retention_count - 1:]:
            os.unlink(entry["filename"], dir_fd=directory)
        os.fsync(directory)
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
