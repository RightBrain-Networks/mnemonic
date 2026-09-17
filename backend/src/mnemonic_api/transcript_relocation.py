"""Bounded discovery requires a unique match to durable enrollment evidence."""

import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from mnemonic_api.transcript_access import TranscriptAccessError, access_error
from mnemonic_api.transcript_source_identity import verify_identity
from mnemonic_api.transcript_storage import _open_source, canonical_source_path

MAX_ENTRIES = 100_000
MAX_DIRECTORIES = 10_000
MAX_DEPTH = 64
MAX_SECONDS = 5
_RELOCATABLE = {"transcript_source_missing", "transcript_source_identity_changed",
                "transcript_path_not_allowed"}


@dataclass
class _ScanBudget:
    deadline: float = field(default_factory=lambda: time.monotonic() + MAX_SECONDS)
    entries: int = 0
    directories: int = 0

    def check(self, path: str, depth: int) -> None:
        if (self.entries > MAX_ENTRIES or self.directories > MAX_DIRECTORIES
                or depth > MAX_DEPTH or time.monotonic() > self.deadline):
            raise TranscriptAccessError("transcript_relocation_scan_limit", path,
                                        operation="list_directory")


def _entries(path: str, roots: list[Path]) -> Iterator[os.DirEntry]:
    try:
        descriptor = _open_source(path, roots, directory=True)
        try:
            with os.scandir(descriptor) as entries:
                yield from entries
        finally:
            os.close(descriptor)
    except OSError as error:
        raise access_error(error, path, operation="list_directory") from None


def _is_directory(entry: os.DirEntry, path: str) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError as error:
        raise access_error(error, path, operation="list_directory") from None


def _candidates(roots: list[Path], filename: str) -> Iterator[str]:
    budget = _ScanBudget()
    pending = [(canonical_source_path(str(root)), 0) for root in roots]
    visited: set[str] = set()
    while pending:
        path, depth = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        budget.directories += 1
        budget.check(path, depth)
        for entry in _entries(path, roots):
            budget.entries += 1
            budget.check(path, depth)
            child = str(Path(path) / entry.name)
            if _is_directory(entry, child):
                pending.append((child, depth + 1))
            elif entry.name == filename and not entry.is_symlink():
                yield child
        budget.check(path, depth)


def _matches(source: str, roots: list[Path], identity: dict) -> bool:
    try:
        verify_identity(source, roots, identity)
    except TranscriptAccessError as error:
        if error.code in {"transcript_source_identity_changed", "transcript_not_regular_file"}:
            return False
        # An unreadable or concurrently removed candidate leaves coverage uncertain.
        raise
    return True


def resolve_source(source: str, roots: list[Path], identity: dict | None) -> str:
    if identity is None:
        return source
    try:
        verify_identity(source, roots, identity)
        return source
    except TranscriptAccessError as error:
        if error.code not in _RELOCATABLE:
            raise
        original_error = error
    match = None
    for candidate in _candidates(roots, identity["filename"]):
        if not _matches(candidate, roots, identity):
            continue
        if match is not None:
            raise TranscriptAccessError("transcript_relocation_ambiguous", source)
        match = candidate
    if match is None:
        raise original_error
    return match
