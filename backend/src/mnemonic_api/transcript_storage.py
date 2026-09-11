"""Read exact regular files beneath operator-approved roots without following links."""

import os
import stat
from pathlib import Path

from mnemonic_api.artifact_tika import ExtractionError


def _relative_source(source: str, roots: list[Path]) -> tuple[Path, tuple[str, ...]]:
    path = Path(source)
    if not path.is_absolute() or ".." in path.parts or "\x00" in source:
        raise ExtractionError("transcript_path_not_allowed")
    for root in roots:
        if path.is_relative_to(root) and path != root:
            return root, path.relative_to(root).parts
    raise ExtractionError("transcript_path_not_allowed")


def _open_source(source: str, roots: list[Path]) -> int:
    root, parts = _relative_source(source, roots)
    # Walk from /, including the operator root: every component must be a real
    # directory, so a concurrent symlink swap can never redirect this descriptor.
    components = (*root.parts[1:], *parts)
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in components[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(components[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=directory)
    finally:
        os.close(directory)


def read_transcript(source: str, roots: list[Path], maximum_bytes: int) -> bytes:
    try:
        descriptor = _open_source(source, roots)
        with os.fdopen(descriptor, "rb") as content:
            before = os.fstat(content.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ExtractionError("transcript_not_regular_file")
            if before.st_size > maximum_bytes:
                raise ExtractionError("transcript_too_large")
            data = content.read(maximum_bytes + 1)
            after = os.fstat(content.fileno())
    except OSError as error:
        raise ExtractionError("transcript_io_error") from error
    if len(data) > maximum_bytes:
        raise ExtractionError("transcript_too_large")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ) or len(data) != before.st_size:
        raise ExtractionError("transcript_content_changed", retryable=True)
    return data
