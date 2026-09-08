"""Artifact bytes, receipt replay, crash recovery, and permanent metadata on PostgreSQL."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.artifact_storage import ArtifactStorage

from .test_project_activity_audit_postgres import _audit
from .test_work_items_postgres import create_work, item_path

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def artifact_storage(api, tmp_path):
    storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
    api.app.state.artifact_storage = storage
    api.app.state.settings.artifact_max_bytes = 1024
    return storage


def collection(project):
    return f"/api/v1/projects/{project['id']}/artifacts"


def headers(metadata=None, *, operation_id=None, revision=None):
    result = {
        "X-Artifact-Metadata": json.dumps(metadata or {}),
        "X-Client-Operation-ID": str(operation_id or uuid4()),
    }
    if revision is not None:
        result["X-Artifact-Expected-Revision"] = str(revision)
    return result


def upload(api, project, *, filename="confidential.pdf", body=b"%PDF-original", metadata=None):
    response = api.post(
        collection(project),
        content=body,
        headers=headers(
            {
                "filename": filename,
                "agent_session_id": "origin-session",
                "actor_client": "pytest",
                **(metadata or {}),
            }
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def actual_files(storage: ArtifactStorage) -> list[Path]:
    return [path for path in storage.root.rglob("*") if path.is_file()]


def test_lifecycle_retains_metadata_only_and_discovers_linked_work(
    api, project, work_payload, artifact_storage
):
    work = create_work(api, project, work_payload)["work_item"]
    artifact = upload(
        api,
        project,
        metadata={
            "description": "Sensitive payroll",
            "work_item_id": work["id"],
        },
    )
    path = collection(project) + "/" + artifact["id"]
    assert artifact["revision"] == 1
    assert artifact["mime_type"] == "application/pdf"
    assert api.get(collection(project), params={"q": "application/pdf"}).json()["total"] == 1
    assert artifact["created_by_agent_session_id"] == "origin-session"
    assert artifact["related_work_item_ids"] == []
    assert [file.name for file in actual_files(artifact_storage)] == ["confidential.pdf"]
    context = api.get(item_path(project, work) + "/context").json()
    assert context["artifacts"][0]["id"] == artifact["id"]
    assert context["artifact_total"] == 1
    assert context["omitted_artifact_count"] == 0
    downloaded = api.get(path + "/content", params={"expected_revision": 1})
    assert downloaded.content == b"%PDF-original"
    assert downloaded.headers["content-type"] == "application/octet-stream"
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "no-store"
    assert downloaded.headers["x-artifact-revision"] == "1"
    assert api.get(path + "/content", params={"expected_revision": 2}).status_code == 409

    replacement_headers = headers(
        {
            "filename": artifact["filename"],
            "agent_session_id": "replacing-session",
        },
        revision=1,
    )
    replaced = api.put(path + "/content", content=b"New bytes", headers=replacement_headers)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["revision"] == 2
    assert replaced.json()["description"] == "Sensitive payroll"
    assert replaced.json()["related_work_item_ids"] == []
    assert replaced.json()["created_by_agent_session_id"] == "origin-session"
    assert len(actual_files(artifact_storage)) == 1
    assert actual_files(artifact_storage)[0].read_bytes() == b"New bytes"
    assert (
        api.put(path + "/content", content=b"New bytes", headers=replacement_headers).json()
        == replaced.json()
    )
    assert len(actual_files(artifact_storage)) == 1
    history = api.get(path + "/history").json()
    assert [row["revision"] for row in history["revisions"]["items"]] == [2, 1]
    assert [row["action"] for row in history["audit"]["items"]] == [
        "replaced",
        "downloaded",
        "uploaded",
    ]
    assert api.get(collection(project), params={"q": "origin-session"}).json()["total"] == 1
    assert (
        api.get(path + "/history", params={"q": "replacing-session"}).json()["audit"]["total"] == 1
    )

    deletion_headers = headers({"agent_session_id": "deleting-session"}, revision=2)
    deleted = api.delete(path, headers=deletion_headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["revision"] == 2
    assert deleted.json()["deleted_at"] is not None
    assert deleted.json()["content_available"] is False
    assert actual_files(artifact_storage) == []
    assert api.delete(path, headers=deletion_headers).json() == deleted.json()
    assert api.get(path + "/content").status_code == 410
    assert api.get(collection(project)).json()["total"] == 0
    assert api.get(collection(project), params={"include_deleted": True}).json()["total"] == 1
    assert api.get(path + "/history").json()["audit"]["total"] == 4
    assert api.get(item_path(project, work) + "/context").json()["artifacts"] == []


def test_operation_replay_conflict_and_revision_compare_and_set(api, project, artifact_storage):
    request_headers = headers({"filename": "unique.txt"})
    first = api.post(collection(project), content=b"first", headers=request_headers)
    assert first.status_code == 201, first.text
    second = api.post(collection(project), content=b"first", headers=request_headers)
    assert second.json() == first.json()
    assert second.headers["x-artifact-operation-replayed"] == "true"
    assert second.headers["x-client-operation-id"] == request_headers["X-Client-Operation-ID"]
    conflict = api.post(collection(project), content=b"changed", headers=request_headers)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "client_operation_conflict"
    path = collection(project) + "/" + first.json()["id"]
    stale = api.put(
        path + "/content",
        content=b"stale",
        headers=headers(
            {
                "filename": "unique.txt",
            },
            revision=2,
        ),
    )
    assert stale.status_code == 409
    assert api.get(path + "/content").content == b"first"
    renamed = api.put(
        path + "/content",
        content=b"new",
        headers=headers(
            {
                "filename": "renamed.txt",
            },
            revision=1,
        ),
    )
    assert renamed.status_code == 409
    assert len(actual_files(artifact_storage)) == 1


def test_replacement_adds_related_work_and_searches_historical_metadata(api, project, work_payload):
    origin = create_work(api, project, work_payload)["work_item"]
    related = create_work(api, project, {**work_payload, "title": "Related analysis"})["work_item"]
    artifact = upload(
        api,
        project,
        metadata={
            "description": "Historical sensitivity note",
            "work_item_id": origin["id"],
        },
    )
    path = collection(project) + "/" + artifact["id"]
    replacement = api.put(
        path + "/content",
        content=b"updated",
        headers=headers(
            {
                "filename": artifact["filename"],
                "description": "Current note",
                "related_work_item_ids": [related["id"]],
            },
            revision=1,
        ),
    )
    assert replacement.status_code == 200, replacement.text
    assert replacement.json()["related_work_item_ids"] == [related["id"]]
    assert replacement.json()["originating_work_item_id"] == origin["id"]
    assert api.get(item_path(project, related) + "/context").json()["artifact_total"] == 1
    assert api.get(collection(project), params={"q": "Historical sensitivity"}).json()["total"] == 1
    revisions = api.get(path + "/history").json()["revisions"]["items"]
    assert revisions[0]["related_work_item_ids"] == [related["id"]]
    assert revisions[1]["related_work_item_ids"] == []


@pytest.mark.parametrize("after_publish", [False, True])
def test_crash_recovery_publishes_once_and_finishes_receipt(
    api, project, artifact_storage, monkeypatch, after_publish
):
    request_headers = headers({"filename": "recover.txt"})
    original_publish = artifact_storage.publish

    def interrupt(staged):
        if after_publish:
            original_publish(staged)
        raise OSError("simulated process failure")

    monkeypatch.setattr(artifact_storage, "publish", interrupt)
    failed = api.post(collection(project), content=b"recoverable", headers=request_headers)
    assert failed.status_code == 503, failed.text
    monkeypatch.setattr(artifact_storage, "publish", original_publish)
    recovered = api.get(collection(project))
    assert recovered.status_code == 200, recovered.text
    artifact = recovered.json()["items"][0]
    replay = api.post(collection(project), content=b"recoverable", headers=request_headers)
    assert replay.status_code == 201, replay.text
    assert replay.json() == artifact
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    history = api.get(collection(project) + "/" + artifact["id"] + "/history").json()
    assert history["audit"]["total"] == 1
    assert history["revisions"]["total"] == 1
    assert len(actual_files(artifact_storage)) == 1


def test_replacement_and_delete_recover_after_filesystem_change(
    api, project, artifact_storage, monkeypatch
):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"]
    publish = artifact_storage.publish

    def interrupted_publish(staged):
        publish(staged)
        raise OSError("crash after atomic replace")

    monkeypatch.setattr(artifact_storage, "publish", interrupted_publish)
    response = api.put(
        path + "/content",
        content=b"replacement",
        headers=headers(
            {
                "filename": artifact["filename"],
            },
            revision=1,
        ),
    )
    assert response.status_code == 503
    assert [file.read_bytes() for file in actual_files(artifact_storage)] == [b"replacement"]
    monkeypatch.setattr(artifact_storage, "publish", publish)
    assert api.get(path).json()["revision"] == 2
    delete = artifact_storage.delete

    def interrupted_delete(relative_path):
        delete(relative_path)
        raise OSError("crash after unlink")

    monkeypatch.setattr(artifact_storage, "delete", interrupted_delete)
    assert api.delete(path, headers=headers(revision=2)).status_code == 503
    assert actual_files(artifact_storage) == []
    monkeypatch.setattr(artifact_storage, "delete", delete)
    assert api.get(path).json()["deleted_at"] is not None
    assert api.get(path + "/history").json()["audit"]["total"] == 3


def test_project_scope_link_validation_search_and_sorting(api, project, work_payload):
    other_project = api.post("/api/v1/projects", json={"name": "Other"}).json()
    foreign_work = create_work(api, other_project, work_payload)["work_item"]
    invalid = api.post(
        collection(project),
        content=b"scoped",
        headers=headers(
            {
                "filename": "foreign.txt",
                "work_item_id": foreign_work["id"],
            }
        ),
    )
    assert invalid.status_code == 404
    assert api.get(collection(project)).json()["total"] == 0
    first = upload(api, project, filename="A.txt", body=b"a")
    upload(api, project, filename="Z.txt", body=b"longer")
    response = api.get(
        collection(project), params={"sort": "size_bytes", "order": "desc", "limit": 1}
    )
    assert response.json()["items"][0]["filename"] == "Z.txt"
    assert response.json()["total"] == 2
    assert api.get(collection(project), params={"q": "%"}).json()["total"] == 0
    assert api.get(collection(other_project) + "/" + first["id"]).status_code == 404
    assert api.get(collection(project), params={"limit": 101}).status_code == 422
    assert (
        api.post(collection(project) + "/search-content", json={"q": "private"}).status_code == 200
    )


@pytest.mark.parametrize("table", ["artifact_audit", "artifact_revisions", "artifact_operations"])
def test_metadata_audit_and_receipts_are_database_immutable(api, project, postgres_engine, table):
    upload(api, project)
    with postgres_engine.connect() as connection:
        for statement in (f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
            with pytest.raises(DBAPIError):
                connection.execute(text(statement))
            connection.rollback()
        column = "state" if table == "artifact_operations" else "filename"
        with pytest.raises(DBAPIError):
            connection.execute(text(f"UPDATE {table} SET {column} = {column}"))
        connection.rollback()


def test_stream_bounds_metadata_validation_and_auth_before_bytes(api, project, artifact_storage):
    oversized = api.post(
        collection(project),
        content=iter([b"a" * 600, b"b" * 600]),
        headers=headers({"filename": "oversized.bin"}),
    )
    assert oversized.status_code == 413
    assert actual_files(artifact_storage) == []
    for metadata in ({"filename": "../../escape"}, {"filename": "safe", "extra": "value"}):
        response = api.post(collection(project), content=b"x", headers=headers(metadata))
        assert response.status_code == 422, response.text
    duplicate = headers({"filename": "safe"})
    duplicate["X-Artifact-Metadata"] = '{"filename":"safe","filename":"other"}'
    assert api.post(collection(project), content=b"x", headers=duplicate).status_code == 422
    no_auth = headers({"filename": "safe"})
    no_auth["Authorization"] = "Bearer wrong"
    assert api.post(collection(project), content=b"x" * 5000, headers=no_auth).status_code == 401
    assert actual_files(artifact_storage) == []


def test_concurrent_replacement_has_one_winner(api, project, artifact_storage):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"] + "/content"

    def replace(body):
        return api.put(
            path,
            content=body,
            headers=headers(
                {
                    "filename": artifact["filename"],
                },
                revision=1,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(replace, [b"winner one", b"winner two"]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert api.get(path.removesuffix("/content")).json()["revision"] == 2
    assert len(actual_files(artifact_storage)) == 1


def test_historic_work_links_survive_project_move(api, project, work_payload):
    # Work IDs are durable global identities; the artifact retains its original project.
    work = create_work(api, project, work_payload)["work_item"]
    artifact = upload(api, project, metadata={"work_item_id": work["id"]})
    assert UUID(artifact["originating_work_item_id"]) == UUID(work["id"])
    assert api.get(collection(project), params={"work_item_id": work["id"]}).json()["total"] == 1
    target = api.post("/api/v1/projects", json={"name": "Moved work project"}).json()
    moved = api.post(
        item_path(project, work) + "/move",
        json={
            "target_project_id": target["id"],
            "expected_version": work["version"],
            "actor": {"actor_client": "pytest", "actor_session_id": "artifact-move"},
            "client_operation_id": str(uuid4()),
        },
    )
    assert moved.status_code == 200, moved.text
    context = api.get(item_path(target, work) + "/context").json()
    assert context["artifacts"] == []
    assert context["artifact_total"] == 0
    assert api.get(collection(project) + "/" + artifact["id"]).json()["project_id"] == project["id"]


def test_artifact_library_passes_integrity_audit(api, project, postgres_engine):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"]
    replacement = api.put(
        path + "/content",
        content=b"latest",
        headers=headers(
            {
                "filename": artifact["filename"],
            },
            revision=1,
        ),
    )
    assert replacement.status_code == 200, replacement.text
    assert api.get(path + "/content").status_code == 200
    assert api.delete(path, headers=headers(revision=2)).status_code == 200
    audit = _audit(postgres_engine)
    assert audit["result"] == "pass", audit["blocking_findings"]


@pytest.mark.parametrize("field", ["filename", "description", "agent_session_id", "actor_client"])
def test_known_credentials_cannot_be_copied_into_artifact_metadata(
    api, project, artifact_storage, field
):
    key = str(uuid4())
    api.app.state.settings.api_key = SecretStr(key)
    api.headers["Authorization"] = f"Bearer {key}"
    metadata = {"filename": "credentials.txt", field: key}
    response = api.post(collection(project), content=b"untrusted", headers=headers(metadata))
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "client_operation_secret_echo"
    assert key not in response.text
    reused = api.post(
        collection(project),
        content=b"untrusted",
        headers=headers({"filename": "safe.txt"}, operation_id=key),
    )
    assert reused.status_code == 422
    assert actual_files(artifact_storage) == []
    assert api.get(collection(project)).json()["total"] == 0
    # Arbitrary authorized private content remains supported; only metadata and
    # operation controls are subject to the existing known-credential guard.
    artifact = upload(api, project, filename="private.txt", body=key.encode())
    path = collection(project) + "/" + artifact["id"] + "/content"
    assert api.get(path).content == key.encode()
    echo = api.get(path, headers={"X-Artifact-Metadata": json.dumps({"agent_session_id": key})})
    assert echo.status_code == 422


def test_permanent_upload_receipts_replay_after_limit_reduction_without_storage_writes(
    api, project, artifact_storage, monkeypatch
):
    upload_headers = headers({"filename": "replay.txt"})
    original = api.post(collection(project), content=b"original bytes", headers=upload_headers)
    assert original.status_code == 201, original.text
    path = collection(project) + "/" + original.json()["id"]
    replace_headers = headers({"filename": "replay.txt"}, revision=1)
    replacement = api.put(path + "/content", content=b"replacement bytes", headers=replace_headers)
    assert replacement.status_code == 200, replacement.text
    assert api.delete(path, headers=headers(revision=2)).status_code == 200
    api.app.state.settings.artifact_max_bytes = 4
    artifact_storage.max_bytes = 4

    def forbid_storage(*args, **kwargs):
        raise AssertionError("Completed receipt replay cannot require content storage")

    for method in ("stage_async", "publish", "discard", "open"):
        monkeypatch.setattr(artifact_storage, method, forbid_storage)
    replay = api.post(collection(project), content=b"original bytes", headers=upload_headers)
    assert replay.status_code == 201, replay.text
    assert replay.json() == original.json()
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    replay = api.put(path + "/content", content=b"replacement bytes", headers=replace_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == replacement.json()
    conflict = api.post(collection(project), content=b"different bytes", headers=upload_headers)
    assert conflict.status_code == 409
    fresh = api.post(
        collection(project),
        content=b"fresh oversized",
        headers=headers(
            {
                "filename": "fresh.txt",
            }
        ),
    )
    assert fresh.status_code == 413


@pytest.mark.parametrize("kind", ["delete", "replace"])
def test_recall_omits_artifacts_with_unfinished_filesystem_intents(
    api, project, work_payload, artifact_storage, monkeypatch, kind
):
    work = create_work(api, project, work_payload)["work_item"]
    artifact = upload(api, project, metadata={"work_item_id": work["id"]})
    path = collection(project) + "/" + artifact["id"]
    method = "delete" if kind == "delete" else "publish"
    original = getattr(artifact_storage, method)

    def interrupted(argument):
        original(argument)
        raise OSError("failure after filesystem mutation")

    monkeypatch.setattr(artifact_storage, method, interrupted)
    if kind == "delete":
        response = api.delete(path, headers=headers(revision=1))
    else:
        response = api.put(
            path + "/content",
            content=b"new revision",
            headers=headers(
                {
                    "filename": artifact["filename"],
                },
                revision=1,
            ),
        )
    assert response.status_code == 503, response.text
    recalled = api.get(item_path(project, work) + "/context")
    assert recalled.status_code == 200, recalled.text
    assert recalled.json()["artifacts"] == []
    assert recalled.json()["artifact_total"] == 0
    claimed = api.post(
        item_path(project, work) + "/claim-and-recall",
        json={
            "holder_client": "pytest",
            "holder_session_id": "pending-artifact-recall",
            "claim_request_id": str(uuid4()),
        },
    )
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["context"]["artifacts"] == []
    monkeypatch.setattr(artifact_storage, method, original)
    assert api.get(path).status_code == 200
    expected = 0 if kind == "delete" else 1
    assert api.get(item_path(project, work) + "/context").json()["artifact_total"] == expected
