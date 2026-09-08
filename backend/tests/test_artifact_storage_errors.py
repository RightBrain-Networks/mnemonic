"""Storage failures expose controlled causes without guessing receipt outcomes."""

import errno
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from starlette.requests import Request

from mnemonic_api import artifact_storage as storage_module
from mnemonic_api.application.routes.artifacts import storage_errors
from mnemonic_api.artifact_storage import (
    ArtifactContentUnavailable,
    ArtifactStorage,
    UnsafeArtifactPath,
)
from mnemonic_api.errors import ApplicationError

CAUSES = (
    "storage_owner_mismatch", "storage_permission_denied", "storage_full",
    "storage_read_only", "storage_integrity", "storage_unavailable",
)


@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        (PermissionError(errno.EACCES, "private detail", "/private/content"),
         "storage_permission_denied"),
        (OSError(errno.EPERM, "private detail", "/private/content"),
         "storage_permission_denied"),
        (OSError(errno.ENOSPC, "private detail", "/private/content"), "storage_full"),
        (OSError(errno.EDQUOT, "private detail", "/private/content"), "storage_full"),
        (OSError(errno.EROFS, "private detail", "/private/content"), "storage_read_only"),
        (ArtifactContentUnavailable("private detail"), "storage_integrity"),
        (UnsafeArtifactPath("/private/content"), "storage_integrity"),
        (FileNotFoundError(errno.ENOENT, "private detail", "/private/content"),
         "storage_unavailable"),
        (OSError(errno.EIO, "private detail", "/private/content"), "storage_unavailable"),
        (OSError("Artifact storage has an unexpected owner. Permission denied. No space left."),
         "storage_unavailable"),
    ],
)
def test_storage_errors_classify_only_types_and_errno(failure, cause):
    with pytest.raises(ApplicationError) as caught, storage_errors(Request({"type": "http"})):
        raise failure
    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "code": "artifact_storage_unavailable",
        "message": "Artifact content storage is unavailable.",
        "context": {"cause": cause, "attempt_not_committed": False},
    }


@pytest.mark.parametrize("cause", CAUSES)
@pytest.mark.parametrize("attempt_not_committed", [False, True])
def test_safe_error_context_preserves_controlled_storage_values(cause, attempt_not_committed):
    context = {"cause": cause, "attempt_not_committed": attempt_not_committed}
    error = ApplicationError(503, "artifact_storage_unavailable", "Safe message", context=context)
    assert error.detail["context"] == context


@pytest.mark.parametrize("cause", [None, 0, True, [], {}, "private/path", "STORAGE_FULL"])
@pytest.mark.parametrize("attempt_not_committed", [None, 0, 1, "true", "false", [], {}])
def test_safe_error_context_rejects_uncontrolled_storage_values(cause, attempt_not_committed):
    error = ApplicationError(503, "artifact_storage_unavailable", "Safe message", context={
        "cause": cause, "attempt_not_committed": attempt_not_committed,
        "path": "/private/content", "exception": "private detail",
    })
    assert error.detail["context"] == {}


@pytest.mark.parametrize("check", ["directory", "regular_file"])
def test_owner_mismatch_is_typed_without_changing_security_checks(tmp_path, monkeypatch, check):
    storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=100)
    staged = storage.stage(uuid4(), uuid4(), "private.txt", [b"private bytes"])
    storage.publish(staged)
    target = storage.root if check == "directory" else storage.root / staged.relative_path
    original_mode = target.stat().st_mode
    owner = os.geteuid()
    monkeypatch.setattr(storage_module.os, "geteuid", lambda: owner + 1)
    verify = (
        storage_module._verify_regular if check == "regular_file" else storage._secure_directory
    )
    with _descriptor(target) as descriptor:
        with pytest.raises(ArtifactContentUnavailable) as caught:
            verify(descriptor)
    assert type(caught.value).__name__ == "ArtifactStorageOwnerMismatch"
    assert target.stat().st_mode == original_mode
    with pytest.raises(ApplicationError) as public, storage_errors(Request({"type": "http"})):
        raise caught.value
    assert public.value.detail["context"] == {
        "cause": "storage_owner_mismatch", "attempt_not_committed": False,
    }


@contextmanager
def _descriptor(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)
