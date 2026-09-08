"""Disabling artifact access preserves content, history, and uncertain-operation recovery."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mnemonic_api.application import create_app
from mnemonic_api.config import Settings
from mnemonic_api.models import (
    Artifact,
    ArtifactAudit,
    ArtifactOperation,
    ArtifactRevision,
    ArtifactWorkLink,
)
from mnemonic_api.services import work_context

from .conftest import TEST_API_KEY
from .test_artifacts_postgres import actual_files, collection, headers, upload
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_human_gates_postgres import gate_request
from .test_leases_postgres import claim_payload
from .test_work_items_postgres import create_work, item_path

pytestmark = pytest.mark.postgres


def _snapshot(engine, storage):
    with engine.connect() as connection:
        records = {
            model.__tablename__: list(connection.execute(select(model.__table__)))
            for model in (
                Artifact, ArtifactAudit, ArtifactOperation, ArtifactRevision, ArtifactWorkLink
            )
        }
    files = {str(path): path.read_bytes() for path in actual_files(storage)}
    return records, files


def _disabled_client(engine, storage):
    settings = Settings(
        database_url=engine.url.render_as_string(hide_password=False),
        api_key=TEST_API_KEY,
        artifact_root=storage.root,
        artifact_max_bytes=0,
    )
    client = TestClient(create_app(settings, engine=engine))
    client.headers["Authorization"] = f"Bearer {TEST_API_KEY}"
    return client


@pytest.mark.parametrize("kind", ["upload", "replace", "delete"])
@pytest.mark.parametrize("pending", [False, True])
def test_disabled_preserves_all_history_bytes_and_exact_replay_after_reenable(
    api, project, artifact_storage, postgres_engine, monkeypatch, kind, pending,
):
    endpoint = collection(project)
    method = "POST"
    body = b"new bytes"
    request_headers = headers({"filename": "new.txt"})
    if kind != "upload":
        artifact = upload(api, project)
        endpoint += "/" + artifact["id"]
        method = "PUT" if kind == "replace" else "DELETE"
        request_headers = headers(
            {"filename": artifact["filename"]} if kind == "replace" else {}, revision=1,
        )
        endpoint += "/content" if kind == "replace" else ""
        body = body if kind == "replace" else b""
    operation = "delete" if kind == "delete" else "publish"
    original = getattr(artifact_storage, operation)

    def unavailable(*args):
        raise OSError("Interrupted before filesystem change")

    if pending:
        monkeypatch.setattr(artifact_storage, operation, unavailable)
    result = api.request(method, endpoint, content=body, headers=request_headers)
    assert result.status_code == (503 if pending else (201 if kind == "upload" else 200))
    before = _snapshot(postgres_engine, artifact_storage)
    with _disabled_client(postgres_engine, artifact_storage) as disabled:
        result = disabled.request(method, endpoint, content=body, headers=request_headers)
        assert result.status_code == 503
        assert result.json()["detail"]["code"] == "artifact_library_disabled"
        assert disabled.get(collection(project)).status_code == 503
        assert disabled.get("/api/v1/projects").status_code == 200
        assert disabled.get("/readyz").status_code == 200
    assert _snapshot(postgres_engine, artifact_storage) == before
    monkeypatch.setattr(artifact_storage, operation, original)
    replay = api.request(method, endpoint, content=body, headers=request_headers)
    assert replay.status_code == (201 if kind == "upload" else 200), replay.text
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    assert replay.json()["revision"] == (2 if kind == "replace" else 1)
    assert (replay.json()["deleted_at"] is not None) is (kind == "delete")
    with postgres_engine.connect() as connection:
        assert set(connection.scalars(select(ArtifactOperation.state))) == {"completed"}


def test_disabled_suppresses_all_context_discovery_without_artifact_queries(
    api, project, work_payload, artifact_storage, postgres_engine, monkeypatch,
):
    work = create_work(api, project, work_payload)["work_item"]
    upload(api, project, metadata={"work_item_id": work["id"]})
    endpoint = item_path(project, work)
    assert api.get(endpoint + "/context").json()["artifact_total"] == 1

    def forbidden(*args):
        raise AssertionError("Disabled context must not query artifact metadata")

    monkeypatch.setattr(work_context, "work_artifacts", forbidden)
    with _disabled_client(postgres_engine, artifact_storage) as disabled:
        recalled = disabled.get(endpoint + "/context")
        claimed = disabled.post(
            endpoint + "/claim-and-recall", json=claim_payload("disabled-artifacts")
        )
        gate = disabled.post(
            endpoint + "/gates", json=gate_request(operation_id=uuid4()),
        )
        assert gate.status_code == 201, gate.text
        focused = disabled.get(endpoint + "/gates/" + gate.json()["id"] + "/context")
        for result, nested in ((recalled, False), (claimed, True), (focused, False)):
            assert result.status_code == 200, result.text
            context = result.json()["context"] if nested else result.json()
            assert context["artifacts"] == []
            assert context["artifact_total"] == 0
            assert context["omitted_artifact_count"] == 0


@pytest.mark.parametrize("streamed", [False, True])
def test_oversize_response_explicitly_identifies_configured_limit(
    api, project, artifact_storage, streamed,
):
    artifact_storage.max_bytes = 8
    api.app.state.settings.artifact_max_bytes = 8
    body = iter([b"12345", b"6789"]) if streamed else b"123456789"
    result = api.post(collection(project), content=body, headers=headers({"filename": "large"}))
    assert result.status_code == 413, result.text
    detail = result.json()["detail"]
    assert detail["code"] == "artifact_too_large"
    assert detail["context"] == {"max_bytes": 8}
    assert "8 bytes" in detail["message"]
    assert "MNEMONIC_ARTIFACT_MAX_BYTES" in detail["message"]
    assert actual_files(artifact_storage) == []


def test_lowered_upload_limit_does_not_prevent_existing_downloads(api, project, artifact_storage):
    artifact = upload(api, project, body=b"existing bytes larger than new limit")
    artifact_storage.max_bytes = 1
    api.app.state.settings.artifact_max_bytes = 1
    downloaded = api.get(collection(project) + "/" + artifact["id"] + "/content")
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == b"existing bytes larger than new limit"
