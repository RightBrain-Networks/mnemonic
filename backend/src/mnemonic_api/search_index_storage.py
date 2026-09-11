"""Private, exclusively owned disk storage for one rebuildable search snapshot."""

import fcntl
import os
import shutil
import stat
from pathlib import Path

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_FORMAT = b"mnemonic-transcript-search-v1\n"
_ENTRIES = {".lock", ".format", ".key", ".key.pending", "snapshot"}


def _private(descriptor: int, *, directory: bool = False) -> None:
    info = os.fstat(descriptor)
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError("Index storage must belong to the API user")
    if info.st_mode & 0o077 or (not directory and info.st_nlink != 1):
        raise OSError("Index storage must be private and must not contain hard links")


def _stable_ancestors(descriptor: int) -> None:
    # Tantivy canonicalizes its directory path. Unlike our fd-relative writes,
    # subsequent engine opens therefore require ancestors other users cannot move.
    directory = Path(f"/proc/self/fd/{descriptor}").resolve(strict=True)
    for parent in directory.parents:
        info = parent.stat()
        if (info.st_uid not in {0, os.geteuid()}
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)):
            raise OSError("Index directory ancestors must be protected from other users")


class SearchIndexStorage:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._root: int | None = None
        self._lock: int | None = None

    def open(self) -> None:
        if self._root is not None:
            return
        root = os.open(self.directory, _DIRECTORY)
        lock = None
        try:
            _private(root, directory=True)
            _stable_ancestors(root)
            lock = os.open(".lock", os.O_RDWR | os.O_CREAT | _FILE, 0o600, dir_fd=root)
            _private(lock)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._root, self._lock = root, lock
            self._identify()
        except BaseException:
            self._root = self._lock = None
            if lock is not None:
                os.close(lock)
            os.close(root)
            raise

    def _identify(self) -> None:
        assert self._root is not None
        entries = set(os.listdir(self._root))
        if ".format" not in entries:
            if entries != {".lock"}:
                raise OSError("Index storage must be an empty dedicated directory")
            self._write(".format", _FORMAT, exclusive=True)
        if entries - _ENTRIES or self._read(".format") != _FORMAT:
            raise OSError("Index storage is not a recognized transcript cache")

    def _read(self, name: str) -> bytes | None:
        assert self._root is not None
        try:
            descriptor = os.open(name, os.O_RDONLY | _FILE, dir_fd=self._root)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            _private(stream.fileno())
            return stream.read(1024)

    def _write(self, name: str, value: bytes, *, exclusive: bool = False) -> None:
        assert self._root is not None
        flags = os.O_WRONLY | os.O_CREAT | _FILE | (os.O_EXCL if exclusive else 0)
        descriptor = os.open(name, flags, 0o600, dir_fd=self._root)
        with os.fdopen(descriptor, "wb") as stream:
            _private(stream.fileno())
            stream.truncate(0)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())

    @property
    def path(self) -> str:
        assert self._root is not None
        # Start at the checked inode; the engine canonicalizes this path, so
        # open() also validates its ancestors before any indexed text is written.
        return f"/proc/self/fd/{self._root}/snapshot"

    def reusable(self, key: str) -> bool:
        self.open()
        if self._read(".key") != key.encode():
            return False
        try:
            self._check_snapshot()
        except FileNotFoundError:
            # A retained key cannot make an incomplete cache authoritative.
            # Keep ownership, permissions and symlink failures explicit.
            return False
        return True

    def _check_snapshot(self) -> None:
        descriptor = os.open("snapshot", _DIRECTORY, dir_fd=self._root)
        try:
            _private(descriptor, directory=True)
            for name in os.listdir(descriptor):
                child = os.open(name, os.O_RDONLY | _FILE, dir_fd=descriptor)
                try:
                    _private(child)
                finally:
                    os.close(child)
        finally:
            os.close(descriptor)

    def prepare(self) -> str:
        self.open()
        self.discard()
        os.mkdir("snapshot", 0o700, dir_fd=self._root)
        return self.path

    def publish(self, key: str) -> None:
        assert self._root is not None
        # Tantivy has finished all writer threads. Private parents protect the
        # files during creation; seal final modes before publishing the key.
        descriptor = os.open("snapshot", _DIRECTORY, dir_fd=self._root)
        try:
            for name in os.listdir(descriptor):
                child = os.open(name, os.O_RDONLY | _FILE, dir_fd=descriptor)
                try:
                    info = os.fstat(child)
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                        raise OSError("Invalid index file")
                    os.fchmod(child, 0o600)
                finally:
                    os.close(child)
        finally:
            os.close(descriptor)
        self._write(".key.pending", key.encode())
        os.replace(".key.pending", ".key", src_dir_fd=self._root, dst_dir_fd=self._root)
        os.fsync(self._root)

    def discard(self) -> None:
        if self._root is None:
            return
        for name in (".key", ".key.pending"):
            try:
                os.unlink(name, dir_fd=self._root)
            except FileNotFoundError:
                pass
        try:
            # fd-relative rmtree does not follow a replaced directory/symlink.
            shutil.rmtree("snapshot", dir_fd=self._root)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        if self._root is not None:
            os.close(self._root)
            self._root = None
