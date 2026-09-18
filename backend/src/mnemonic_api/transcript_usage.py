"""Filesystem accounting measures retained files, including obsolete snapshots."""

import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_storage import _open_source

_MAX_ENTRIES = 200_000
_MAX_SECONDS = 5
_CACHE: dict[str, tuple[float, DirectoryUsage]] = {}


class DirectoryUsage(BaseModel):
    checked_at: datetime
    bytes: int | None = None
    logical_bytes: int | None = None
    free_bytes: int | None = None
    total_bytes: int | None = None
    complete: bool = False
    error_code: str | None = None


class TranscriptUsage(BaseModel):
    scope: str = "all_projects"
    transcripts: DirectoryUsage | None = None
    index: DirectoryUsage | None = None
    database_bytes: int


def _walk_usage(root: Path, result: DirectoryUsage) -> None:
    deadline = monotonic() + _MAX_SECONDS
    entries = 0
    seen: set[tuple[int, int]] = set()
    result.bytes = result.logical_bytes = 0
    def fail(error: OSError) -> None:
        raise error

    for _, directories, files, descriptor in os.fwalk(
        root, follow_symlinks=False, onerror=fail,
    ):
        # Directory blocks are real allocation too, including an empty index.
        result.bytes += os.fstat(descriptor).st_blocks * 512
        for name in [*directories, *files]:
            entries += 1
            if entries > _MAX_ENTRIES or monotonic() > deadline:
                result.error_code = "transcript_storage_scan_limited"
                return
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            identity = (info.st_dev, info.st_ino)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)) or identity in seen:
                continue
            seen.add(identity)
            result.bytes += info.st_blocks * 512
            if stat.S_ISREG(info.st_mode):
                result.logical_bytes += info.st_size
    result.complete = True


def directory_usage(root: Path) -> DirectoryUsage:
    key = str(root)
    cached = _CACHE.get(key)
    if cached is not None and monotonic() - cached[0] < 30:
        return cached[1]
    result = DirectoryUsage(checked_at=datetime.now(UTC))
    try:
        descriptor = _open_source(key, [root], directory=True)
        try:
            volume = os.fstatvfs(descriptor)
            result.free_bytes = volume.f_bavail * volume.f_frsize
            result.total_bytes = volume.f_blocks * volume.f_frsize
        finally:
            os.close(descriptor)
        _walk_usage(root, result)
    except (OSError, ExtractionError):
        result.error_code = "transcript_storage_usage_unavailable"
    if len(_CACHE) >= 20:
        _CACHE.clear()
    _CACHE[key] = monotonic(), result
    return result


def database_usage(database: Session) -> int:
    return int(database.scalar(text("""
        SELECT coalesce(sum(pg_total_relation_size(c.oid)), 0)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relkind='r' AND c.relname IN (
            'transcripts', 'transcript_segments', 'transcript_normalizations',
            'transcript_settings', 'transcript_rebuilds', 'transcript_imports',
            'transcript_recoveries', 'transcript_worker_health')
    """)) or 0)
