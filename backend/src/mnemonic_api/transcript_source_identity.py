"""Recognize append-only native files without retaining enrollment-time bodies."""

import hashlib
import os
from pathlib import Path

from mnemonic_api.transcript_access import TranscriptAccessError, access_error
from mnemonic_api.transcript_storage import _open_source

PREFIX_BYTES = 65536
MINIMUM_PREFIX_BYTES = 256


def capture_identity(descriptor: int, source: str) -> dict | None:
    """Called only while verifying a fresh assertion, never to infer old evidence."""
    try:
        prefix = os.pread(descriptor, PREFIX_BYTES, 0)
    except OSError as error:
        raise access_error(error, source) from None
    if len(prefix) < MINIMUM_PREFIX_BYTES:
        return None
    return {"version": 1, "filename": Path(source).name, "prefix_size": len(prefix),
            "prefix_sha256": hashlib.sha256(prefix).hexdigest()}


def require_identity(descriptor: int, source: str, identity: dict | None) -> None:
    if identity is None:
        return
    prefix = os.pread(descriptor, identity["prefix_size"], 0)
    if (Path(source).name != identity["filename"] or len(prefix) != identity["prefix_size"]
            or hashlib.sha256(prefix).hexdigest() != identity["prefix_sha256"]):
        raise TranscriptAccessError("transcript_source_identity_changed", source)


def verify_identity(source: str, roots: list[Path], identity: dict) -> None:
    try:
        descriptor = _open_source(source, roots)
        try:
            require_identity(descriptor, source, identity)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise access_error(error, source) from None
