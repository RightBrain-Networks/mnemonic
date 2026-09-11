"""Bounded folder discovery through no-follow directory descriptors."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_storage import _open_source, canonical_source_path

MAX_IMPORT_FILES = 5000
MAX_IMPORT_ENTRIES = 50000
MAX_IMPORT_DEPTH = 64
SCAN_SECONDS = 10



@dataclass
class TranscriptDiscovery:
    paths: list[str] = field(default_factory=list)
    skipped: int = 0
    entries: int = 0
    deadline: float = field(default_factory=lambda: monotonic() + SCAN_SECONDS)

    def check(self, depth: int) -> None:
        if (len(self.paths) > MAX_IMPORT_FILES or self.entries > MAX_IMPORT_ENTRIES
                or depth > MAX_IMPORT_DEPTH or monotonic() > self.deadline):
            raise ApplicationError(422, "transcript_import_scan_limit",
                                   "This folder is too large to scan. Import a smaller subfolder.")


def _visit(descriptor: int, directory: Path, scan: TranscriptDiscovery, depth: int) -> None:
    scan.check(depth)
    with os.scandir(descriptor) as entries:
        for entry in entries:
            scan.entries += 1
            scan.check(depth)
            if entry.is_symlink():
                scan.skipped += 1
            elif entry.is_dir(follow_symlinks=False):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
                try:
                    _visit(child, directory / entry.name, scan, depth + 1)
                finally:
                    os.close(child)
            elif entry.name.endswith(".jsonl"):
                _candidate(entry, directory, scan)


def _candidate(entry: os.DirEntry, directory: Path, scan: TranscriptDiscovery) -> None:
    path = str(directory / entry.name)
    if (not entry.is_file(follow_symlinks=False) or len(path) > 4096
            or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in path)):
        scan.skipped += 1
        return
    scan.paths.append(path)
    scan.check(0)


def discover_transcripts(directory: str, roots: list[Path]) -> TranscriptDiscovery:
    scan = TranscriptDiscovery()
    try:
        descriptor = _open_source(directory, roots, directory=True)
        try:
            _visit(descriptor, Path(canonical_source_path(directory)), scan, 0)
        finally:
            os.close(descriptor)
    except ExtractionError as error:
        raise ApplicationError(422, "transcript_import_path_not_allowed",
                               "Choose a folder within the configured shared transcript folders.") \
            from error
    except OSError as error:
        raise ApplicationError(422, "transcript_import_scan_failed",
                               "The folder could not be read. Check its path, permissions and "
                               "shared filesystem mount. Symlink folders are not supported.") \
            from error
    scan.paths.sort()
    return scan
