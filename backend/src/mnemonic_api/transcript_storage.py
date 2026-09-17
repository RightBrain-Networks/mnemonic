"""Read exact regular files beneath operator-approved roots without following links."""

import os
import stat
from pathlib import Path

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.transcript_access import TranscriptAccessError, access_error


def canonical_source_path(source: str) -> str:
    # The local POSIX filesystem treats repeated separators and '.' components
    # identically, including Python's otherwise distinct two-slash anchor.
    path = Path(source)
    return "/" + str(path).lstrip("/") if path.is_absolute() else str(path)


def _relative_source(source: str, roots: list[Path], *, directory: bool = False,
) -> tuple[Path, tuple[str, ...]]:
    path = Path(source)
    if not path.is_absolute() or ".." in path.parts or "\x00" in source:
        raise TranscriptAccessError("transcript_path_not_allowed", source)
    path = Path(canonical_source_path(source))
    for root in roots:
        root = Path(canonical_source_path(str(root)))
        if path.is_relative_to(root) and (directory or path != root):
            return root, path.relative_to(root).parts
    raise TranscriptAccessError("transcript_path_not_allowed", source)


def _open_component(name: str, flags: int, parent: int, path: str, operation: str) -> int:
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as error:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            info = None
        if info is not None and stat.S_ISLNK(info.st_mode):
            raise TranscriptAccessError("transcript_symlink_rejected", path,
                                        operation=operation, info=info) from None
        raise access_error(error, path, operation=operation, info=info) from None


def _open_source(source: str, roots: list[Path], *, directory: bool = False) -> int:
    root, parts = _relative_source(source, roots, directory=directory)
    # Walk from /, including the operator root: every component must be a real
    # directory, so a concurrent symlink swap can never redirect this descriptor.
    components = (*root.parts[1:], *parts)
    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)
    # O_PATH requires only search permission on ancestors, not directory listing.
    # O_DIRECTORY with O_NOFOLLOW still refuses links, including approved roots.
    walk_flags = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", walk_flags)
    current = Path("/")
    try:
        if not components:
            return os.dup(descriptor)
        for component in components[:-1]:
            child = _open_component(component, walk_flags, descriptor,
                                    str(current / component), "traverse")
            os.close(descriptor)
            descriptor = child
            current /= component
            # O_PATH can open an unsearchable directory; name that blocker.
            if not os.access(".", os.X_OK, dir_fd=descriptor, effective_ids=True):
                raise TranscriptAccessError("transcript_permission_denied", str(current),
                                            operation="traverse", info=os.fstat(descriptor))
        operation = "list_directory" if directory else "read_file"
        result = _open_component(components[-1], flags, descriptor,
                                 str(current / components[-1]), operation)
        info = os.fstat(result)
        if directory and not os.access(".", os.X_OK, dir_fd=result, effective_ids=True):
            os.close(result)
            raise TranscriptAccessError("transcript_permission_denied", source,
                                        operation="list_directory", info=info)
        if not directory and not stat.S_ISREG(info.st_mode):
            os.close(result)
            raise TranscriptAccessError("transcript_not_regular_file", source, info=info)
        return result
    finally:
        os.close(descriptor)


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
        raise access_error(error, source) from None
    if len(data) > maximum_bytes:
        raise ExtractionError("transcript_too_large")
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ) or len(data) != before.st_size:
        raise ExtractionError("transcript_content_changed", retryable=True)
    return data


def check_transcript_source(source: Path | None, roots: list[Path]) -> None:
    """Reject a configured but unavailable source before starting any index jobs."""
    if source is None:
        return
    try:
        descriptor = _open_source(str(source), roots, directory=True)
        os.close(descriptor)
    except (OSError, ExtractionError):
        raise RuntimeError(
            f"Configured transcript source is unavailable: {source}. Check its source directory "
            "setting, the read-only bind mount, and the API UID/GID permissions."
        ) from None


def validate_transcript_assertion(source: str, roots: list[Path]) -> dict | None:
    """Fresh assertions must identify readable native files; receipt replays skip this."""
    from mnemonic_api.errors import ApplicationError
    from mnemonic_api.transcript_access import access_instruction, require_native_path
    from mnemonic_api.transcript_source_identity import capture_identity

    try:
        require_native_path(source)
        descriptor = _open_source(source, roots)
        try:
            return capture_identity(descriptor, source)
        finally:
            os.close(descriptor)
    except TranscriptAccessError as error:
        raise ApplicationError(422, error.code,
            f"Transcript source unavailable at {error.details['path']}. "
            + access_instruction(error.code, error.details),
            context={"attempt_not_committed": True, "instructions":
                "Correct this verified path, or use explicit null on a fresh request if it "
                "cannot be established. Preserve exact arguments after an uncertain outcome."},
        ) from None
