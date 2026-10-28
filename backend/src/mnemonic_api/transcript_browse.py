"""Bounded server directory browsing within the operator's approved source roots."""

import os
from pathlib import Path
from time import monotonic

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.errors import ApplicationError
from mnemonic_api.transcript_schemas import TranscriptBrowseRead, TranscriptDirectory
from mnemonic_api.transcript_storage import _open_source, canonical_source_path

MAX_BROWSE_ENTRIES = 1000
MAX_BROWSE_SCAN = 10000


def _safe_name(value: str) -> bool:
    return not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value)


def browse_directories(directory: str | None, roots: list[Path],
                       excluded_roots: tuple[Path, ...] = (),
                       query: str | None = None) -> TranscriptBrowseRead:
    if directory is None:
        return TranscriptBrowseRead(directory=None, parent=None, entries=[
            TranscriptDirectory(name=str(root), path=str(root)) for root in roots[:100]
            if not query or query.casefold() in str(root).casefold()])
    path = Path(canonical_source_path(directory))
    if any(path.is_relative_to(root) for root in excluded_roots):
        raise ApplicationError(422, "transcript_path_not_allowed",
                               "Retained transcript storage is not a source folder.")
    try:
        descriptor = _open_source(str(path), roots, directory=True)
        try:
            entries, truncated = _children(descriptor, path, excluded_roots, query)
        finally:
            os.close(descriptor)
    except (OSError, ExtractionError) as error:
        code = getattr(error, "code", "transcript_import_scan_failed")
        raise ApplicationError(422, code,
                               "This folder is unavailable. Check its path and access.") from None
    parent = str(path.parent) if path.parent != path and any(
        path.parent.is_relative_to(root) for root in roots) else None
    return TranscriptBrowseRead(directory=str(path), parent=parent,
                                 entries=entries, truncated=truncated)


def _children(descriptor: int, path: Path, excluded: tuple[Path, ...], query: str | None):
    children = []
    truncated = False
    deadline = monotonic() + 2
    with os.scandir(descriptor) as entries:
        for position, entry in enumerate(entries):
            if position >= MAX_BROWSE_SCAN or monotonic() > deadline:
                truncated = True
                break
            if query and query.casefold() not in entry.name.casefold():
                continue
            child = path / entry.name
            if (not _safe_name(str(child)) or len(str(child)) > 4096
                    or any(child.is_relative_to(root) for root in excluded)):
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if len(children) >= MAX_BROWSE_ENTRIES:
                truncated = True
                break
            children.append(TranscriptDirectory(name=entry.name, path=str(child)))
    return sorted(children, key=lambda item: item.name.casefold()), truncated
