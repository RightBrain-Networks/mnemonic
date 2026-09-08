"""Storage diagnostics distinguish this staging attempt from durable operation receipts."""

import asyncio
import errno
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import func, select

from mnemonic_api import artifact_storage as storage_module
from mnemonic_api.artifact_storage import ArtifactContentUnavailable, UnsafeArtifactPath
from mnemonic_api.models import Artifact, ArtifactAudit, ArtifactOperation, ArtifactRevision

from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


def _mutation_request(api, project, kind):
    path = collection(project)
    revision = None
    if kind == "replace":
        artifact = upload(api, project, filename="safe.txt", body=b"original")
        path += "/" + artifact["id"] + "/content"
        revision = 1
    request_headers = headers({"filename": "safe.txt"}, revision=revision)

    def send():
        return api.request(
            "PUT" if kind == "replace" else "POST", path,
            content=b"unchanged retry bytes", headers=request_headers,
        )

    return send, UUID(request_headers["X-Client-Operation-ID"])


def _counts(engine):
    with engine.connect() as connection:
        return tuple(
            connection.scalar(select(func.count()).select_from(model))
            for model in (Artifact, ArtifactRevision, ArtifactAudit, ArtifactOperation)
        )


def _operation_state(engine, operation_id):
    with engine.connect() as connection:
        return connection.scalar(select(ArtifactOperation.state).where(
            ArtifactOperation.client_operation_id == operation_id,
        ))


def _assert_storage_error(response, cause, *, attempt_not_committed):
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == {
        "code": "artifact_storage_unavailable",
        "message": "Artifact content storage is unavailable.",
        "context": {"cause": cause, "attempt_not_committed": attempt_not_committed},
    }


@pytest.mark.parametrize("kind", ["upload", "replace"])
@pytest.mark.parametrize(
    ("failure", "cause"),
    [
        ("owner", "storage_owner_mismatch"),
        (PermissionError(errno.EACCES, "private staging detail", "/private/content"),
         "storage_permission_denied"),
        (OSError(errno.EPERM, "private staging detail"), "storage_permission_denied"),
        (OSError(errno.ENOSPC, "private staging detail"), "storage_full"),
        (OSError(errno.EDQUOT, "private staging detail"), "storage_full"),
        (OSError(errno.EROFS, "private staging detail"), "storage_read_only"),
        (ArtifactContentUnavailable("private integrity detail"), "storage_integrity"),
        (UnsafeArtifactPath("/private/content"), "storage_integrity"),
        (OSError("private unavailable detail"), "storage_unavailable"),
    ],
)
def test_stage_failure_is_sanitized_and_same_request_retries_once(
    api, project, artifact_storage, postgres_engine, monkeypatch, kind, failure, cause,
):
    send, operation_id = _mutation_request(api, project, kind)
    before = _counts(postgres_engine)

    def fail_write(_writer, _chunk):
        raise failure

    with monkeypatch.context() as patch:
        if failure == "owner":
            owner = os.geteuid()
            patch.setattr(storage_module.os, "geteuid", lambda: owner + 1)
        else:
            patch.setattr(storage_module._StagingWriter, "write", fail_write)
        failed = send()
    _assert_storage_error(failed, cause, attempt_not_committed=True)
    assert _operation_state(postgres_engine, operation_id) is None
    assert _counts(postgres_engine) == before
    assert list(artifact_storage.root.rglob(".pending-*")) == []
    assert [path.read_bytes() for path in artifact_storage.root.rglob("safe.txt")] == (
        [b"original"] if kind == "replace" else []
    )
    successful = send()
    assert successful.status_code == (200 if kind == "replace" else 201), successful.text
    assert send().json() == successful.json()
    assert _operation_state(postgres_engine, operation_id) == "completed"
    assert _counts(postgres_engine) == tuple(
        count + extra for count, extra in zip(before, (int(kind == "upload"), 1, 1, 1), strict=True)
    )


@pytest.mark.parametrize("kind", ["upload", "replace"])
@pytest.mark.parametrize("failure_point", ["stage", "discard_duplicate"])
def test_same_operation_can_be_completed_when_this_attempt_reports_storage_failure(
    api, project, artifact_storage, postgres_engine, monkeypatch, kind, failure_point,
):
    send, operation_id = _mutation_request(api, project, kind)
    before = _counts(postgres_engine)
    entered, release = Event(), Event()
    original_stage = artifact_storage.stage_async
    duplicates = []

    async def paused_stage(*args):
        if entered.is_set():
            return await original_stage(*args)
        staged = await original_stage(*args) if failure_point == "discard_duplicate" else None
        if staged is not None:
            duplicates.append(staged)
        entered.set()
        assert await asyncio.to_thread(release.wait, 10), "Concurrent retry did not finish"
        if staged is None:
            raise PermissionError(errno.EACCES, "private staging path", "/private/content")
        return staged

    def failed_discard(staged):
        assert staged in duplicates
        assert _operation_state(postgres_engine, operation_id) == "completed"
        raise OSError(errno.ENOSPC, "private cleanup path", "/private/content")

    with monkeypatch.context() as patch:
        patch.setattr(artifact_storage, "stage_async", paused_stage)
        if failure_point == "discard_duplicate":
            patch.setattr(artifact_storage, "discard", failed_discard)
        with ThreadPoolExecutor(max_workers=1) as workers:
            waiting = workers.submit(send)
            try:
                assert entered.wait(10), "Initial attempt did not finish receipt preflight"
                completed = send()
                assert completed.status_code == (200 if kind == "replace" else 201), completed.text
                assert _operation_state(postgres_engine, operation_id) == "completed"
            finally:
                release.set()
            failed = waiting.result(timeout=10)
    _assert_storage_error(
        failed, "storage_permission_denied" if failure_point == "stage" else "storage_full",
        attempt_not_committed=failure_point == "stage",
    )
    replay = send()
    assert replay.json() == completed.json()
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    assert _counts(postgres_engine) == tuple(
        count + extra for count, extra in zip(before, (int(kind == "upload"), 1, 1, 1), strict=True)
    )
    for staged in duplicates:
        artifact_storage.discard(staged)
    assert [path.read_bytes() for path in artifact_storage.root.rglob("safe.txt")] == [
        b"unchanged retry bytes",
    ]


def test_download_owner_failure_does_not_claim_this_attempt_did_not_commit(
    api, project, artifact_storage, monkeypatch,
):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"] + "/content"
    owner = os.geteuid()
    with monkeypatch.context() as patch:
        patch.setattr(storage_module.os, "geteuid", lambda: owner + 1)
        failed = api.get(path)
    _assert_storage_error(failed, "storage_owner_mismatch", attempt_not_committed=False)
    assert api.get(path).content == b"%PDF-original"
