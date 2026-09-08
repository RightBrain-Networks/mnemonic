"""Failed intents do not block unrelated reads, recovery progress, or safe cleanup."""

import os
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from mnemonic_api.application import artifact_maintenance
from mnemonic_api.models import Artifact, ArtifactAudit, ArtifactOperation, ArtifactRevision
from mnemonic_api.services.artifacts import recover_project_artifacts

from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


def _unavailable(*args):
    raise OSError("Simulated unavailable artifact storage")


def _pending_upload(api, project, filename):
    request_headers = headers({"filename": filename})
    response = api.post(collection(project), content=b"pending", headers=request_headers)
    assert response.status_code == 503, response.text
    return UUID(request_headers["X-Client-Operation-ID"]), request_headers


@pytest.mark.parametrize("kind", ["replace", "delete"])
@pytest.mark.parametrize("after_filesystem_change", [False, True])
def test_failed_intent_does_not_block_unrelated_artifact_reads(
    api, project, artifact_storage, monkeypatch, kind, after_filesystem_change,
):
    failed = upload(api, project, filename="A-failed.txt")
    healthy = upload(api, project, filename="M-healthy.txt")
    last = upload(api, project, filename="Z-healthy.txt")
    failed_path = collection(project) + "/" + failed["id"]
    healthy_path = collection(project) + "/" + healthy["id"]
    method = "publish" if kind == "replace" else "delete"
    original = getattr(artifact_storage, method)
    attempts = []

    def fail_one(argument):
        attempts.append(argument)
        if after_filesystem_change:
            original(argument)
        _unavailable()

    monkeypatch.setattr(artifact_storage, method, fail_one)
    if kind == "replace":
        response = api.put(
            failed_path + "/content", content=b"replacement",
            headers=headers({"filename": failed["filename"]}, revision=1),
        )
    else:
        response = api.delete(failed_path, headers=headers(revision=1))
    assert response.status_code == 503, response.text
    attempts.clear()
    assert api.get(healthy_path).status_code == 200
    assert api.get(healthy_path + "/history").status_code == 200
    assert api.get(healthy_path + "/content").content == b"%PDF-original"
    assert attempts == []  # Targeted reads never retry a sibling's broken intent.
    for suffix in ("", "/history", "/content"):
        response = api.get(failed_path + suffix)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "artifact_storage_unavailable"
    for include_deleted in (False, True):
        for offset, expected in enumerate((healthy, last)):
            response = api.get(collection(project), params={
                "limit": 1, "offset": offset, "include_deleted": include_deleted,
            })
            assert response.status_code == 200, response.text
            assert response.json()["total"] == 2
            assert [item["id"] for item in response.json()["items"]] == [expected["id"]]
    response = api.get(collection(project), params={"q": "A-failed", "include_deleted": True})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0
    assert response.json()["items"] == []


def test_recovery_commits_each_operation_and_continues_after_failure(
    api, project, artifact_storage, postgres_engine, monkeypatch,
):
    original = artifact_storage.publish
    monkeypatch.setattr(artifact_storage, "publish", _unavailable)
    queued = [_pending_upload(api, project, name) for name in ("before", "failed", "after")]
    observed_states = []

    def fail_middle(staged):
        if staged.relative_path.endswith("/failed"):
            # Observe durability while the next operation is still in progress.
            with postgres_engine.connect() as connection:
                observed_states.append(connection.scalar(
                    select(ArtifactOperation.state).where(
                        ArtifactOperation.client_operation_id == queued[0][0]
                    )
                ))
            _unavailable()
        original(staged)

    monkeypatch.setattr(artifact_storage, "publish", fail_middle)
    for _ in range(2):
        with api.app.state.session_factory() as database:
            recover_project_artifacts(database, artifact_storage, UUID(project["id"]))
        with postgres_engine.connect() as connection:
            rows = connection.execute(
                select(Artifact.filename, Artifact.revision, ArtifactOperation.state)
                .join(ArtifactOperation, ArtifactOperation.artifact_id == Artifact.id)
            ).all()
            assert set(rows) == {
                ("before", 1, "completed"), ("failed", 0, "pending"), ("after", 1, "completed"),
            }
            assert connection.scalar(select(func.count()).select_from(ArtifactRevision)) == 2
            assert connection.scalar(select(func.count()).select_from(ArtifactAudit)) == 2
    assert observed_states == ["completed", "completed"]
    for _, request_headers in (queued[0], queued[2]):
        replay = api.post(collection(project), content=b"pending", headers=request_headers)
        assert replay.status_code == 201, replay.text
        assert replay.headers["x-artifact-operation-replayed"] == "true"
    with postgres_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ArtifactRevision)) == 2
        assert connection.scalar(select(func.count()).select_from(ArtifactAudit)) == 2


def test_maintenance_recovers_other_projects_and_cleans_unreferenced_stages(
    api, project, artifact_storage, postgres_engine, monkeypatch,
):
    other_project = api.post("/api/v1/projects", json={"name": "Other recovery project"}).json()
    original = artifact_storage.publish
    monkeypatch.setattr(artifact_storage, "publish", _unavailable)
    failed_ids = []
    healthy_ids = []
    for target in (project, other_project):
        failed_ids.append(_pending_upload(api, target, "failed")[0])
        healthy_ids.append(_pending_upload(api, target, "healthy")[0])
    with postgres_engine.connect() as connection:
        pending_paths = [
            artifact_storage.root / intent["staged"]["temporary_path"]
            for intent in connection.scalars(select(ArtifactOperation.intent).where(
                ArtifactOperation.client_operation_id.in_(failed_ids)
            ))
        ]
    orphan = artifact_storage.stage(uuid4(), uuid4(), "orphan", [b"unreferenced"])
    orphan_path = artifact_storage.root / orphan.temporary_path
    old = time.time() - 86401
    for path in [*pending_paths, orphan_path]:
        os.utime(path, (old, old))

    def fail_one_per_project(staged):
        if staged.relative_path.endswith("/failed"):
            _unavailable()
        original(staged)

    monkeypatch.setattr(artifact_storage, "publish", fail_one_per_project)
    for _ in range(2):
        artifact_maintenance.maintain_artifacts(api.app.state.session_factory, artifact_storage)
        assert not orphan_path.exists()
        assert all(path.exists() for path in pending_paths)
        with postgres_engine.connect() as connection:
            states = dict(connection.execute(select(
                ArtifactOperation.client_operation_id, ArtifactOperation.state,
            )).all())
            assert all(states[operation_id] == "completed" for operation_id in healthy_ids)
            assert all(states[operation_id] == "pending" for operation_id in failed_ids)
            assert connection.scalar(select(func.count()).select_from(ArtifactRevision)) == 2
        published = list(artifact_storage.root.rglob("healthy"))
        assert len(published) == 2
        assert all(path.read_bytes() == b"pending" for path in published)


def test_cleanup_still_runs_when_recovery_cannot_start(
    api, project, artifact_storage, monkeypatch,
):
    monkeypatch.setattr(artifact_storage, "publish", _unavailable)
    _pending_upload(api, project, "protected")
    orphan = artifact_storage.stage(uuid4(), uuid4(), "orphan", [b"unreferenced"])
    orphan_path = artifact_storage.root / orphan.temporary_path
    old = time.time() - 86401
    pending_paths = list(artifact_storage.root.rglob(".pending-*"))
    for path in pending_paths:
        os.utime(path, (old, old))
    monkeypatch.setattr(artifact_maintenance, "recover_all_artifacts", _unavailable)
    artifact_maintenance.maintain_artifacts(api.app.state.session_factory, artifact_storage)
    assert not orphan_path.exists()
    assert all(path.exists() for path in pending_paths if path != orphan_path)
