"""Artifact labels are current catalog assignments with durable metadata history."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.artifact_schemas import ArtifactUpdateMetadata, ArtifactUploadMetadata
from mnemonic_api.models import ArtifactOperation
from mnemonic_api.services import artifacts

from .test_artifact_extraction_postgres import Parser, run_job
from .test_artifact_links_sensitive_postgres import update
from .test_artifact_sensitive_postgres import ACTOR, _token
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload
from .test_project_tags import HEADERS, browser, catalog_names

pytestmark = pytest.mark.postgres


def tag(api, project, name="labels-test"):
    catalog_names(api, project, [name])
    rows = api.get(f"/api/v1/projects/{project['id']}/tags").json()["items"]
    return next(row for row in rows if row["name"] == name)


def test_assignment_upload_update_replace_clear_and_immutable_history(api, project):
    tag(api, project)
    first = upload(api, project, metadata={"tags": ["labels-test"]})
    path = collection(project) + "/" + first["id"]
    assert first["tags"] == ["labels-test"]
    changed = update(api, project, first, description="Label preserved")
    assert changed.status_code == 200, changed.text
    assert changed.json()["tags"] == first["tags"]
    replaced = api.put(path + "/content", content=b"new content", headers=headers(
        {"filename": first["filename"]}, revision=2,
    ))
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["tags"] == first["tags"]
    cleared = update(api, project, replaced.json(), tags=None)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["tags"] is None
    revisions = api.get(path + "/history").json()["revisions"]["items"]
    assert [row["tags"] for row in revisions] == [None, *[["labels-test"]] * 3]
    audit = api.get(path + "/history").json()["audit"]["items"]
    assert audit[0]["details"]["tags"] is None


@pytest.mark.parametrize("tags", [[], ["unknown"], ["not normalized"], ["dir", "dir"]])
def test_assignment_rejects_invalid_catalog_names_without_revision(api, project, tags):
    artifact = upload(api, project)
    response = update(api, project, artifact, tags=tags)
    assert response.status_code == 422, response.text
    assert api.get(collection(project) + "/" + artifact["id"]).json()["revision"] == 1


def test_catalog_rename_delete_advances_revision_reuses_extraction_and_replays(api, project,
        artifact_storage):
    definition = tag(api, project)
    artifact = upload(api, project, metadata={"tags": ["labels-test"]})
    assert run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    content = api.get(path + "/content").content
    renamed = browser(api, project, "PATCH", "/" + definition["id"], name="renamed-label")
    assert renamed.status_code == 200, renamed.text
    current = api.get(path).json()
    assert current["tags"] == ["renamed-label"]
    assert current["revision"] == 2
    assert current["extraction"]["status"] == "ready"
    assert api.get(path + "/content").content == content
    found = api.post(collection(project) + "/search-content", json={"q": "renamed-label"})
    assert found.json()["total"] == 1
    removed = browser(api, project, "DELETE", "/" + definition["id"])
    assert removed.status_code == 200, removed.text
    current = api.get(path).json()
    assert current["tags"] is None
    assert current["revision"] == 3
    assert [row["tags"] for row in api.get(path + "/history").json()["revisions"]["items"]] == [
        None, ["renamed-label"], ["labels-test"],
    ]


@pytest.mark.parametrize("mode, expected", [("or", 3), ("and", 1)])
def test_list_and_all_search_surfaces_filter_current_tags(api, project, mode, expected):
    catalog_names(api, project, ["labels-a", "labels-b"])
    for index, names in enumerate((["labels-a"], ["labels-b"], ["labels-a", "labels-b"], None)):
        upload(api, project, filename=f"document-{index}.txt", metadata={"tags": names})
    params = [("tags", "labels-a"), ("tags", "labels-b"), ("tag_mode", mode)]
    for prefix in ("/api/v1", "/api/artifacts"):
        path = f"{prefix}/projects/{project['id']}/artifacts"
        response = api.get(path, params=params, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
        response = api.post(path + "/search-content", headers=HEADERS, json={
            "q": "document", "tags": ["labels-a", "labels-b"], "tag_mode": mode,
        })
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
        assert all(row["artifact"]["tags"] for row in response.json()["items"])
        assert response.json()["applied_filters"]["artifacts"]["tags"] == ["labels-a", "labels-b"]
    response = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "facets": ["artifacts"], "filters": {"artifacts": {
            "tags": ["labels-a", "labels-b"], "tag_mode": mode,
        }},
    })
    assert response.status_code == 200, response.text
    assert response.json()["total"] == expected


def test_tag_names_are_searchable_metadata(api, project):
    tag(api, project)
    upload(api, project, metadata={"tags": ["labels-test"]})
    response = api.post(collection(project) + "/search-content", json={"q": "labels-test"})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1


@pytest.mark.parametrize("mutation", ["upload", "update"])
def test_catalog_changes_wait_for_pending_tagged_intents(api, project, monkeypatch, mutation):
    definition = tag(api, project)
    artifact = upload(api, project) if mutation == "update" else None
    method = "_publish_metadata_revision" if mutation == "update" else "_publish_revision"
    original = getattr(artifacts, method)

    def interrupted(*_args):
        raise OSError("Simulated interruption after committed tagged intent")

    monkeypatch.setattr(artifacts, method, interrupted)
    if artifact is not None:
        response = update(api, project, artifact, tags=["labels-test"])
    else:
        response = api.post(collection(project), content=b"content", headers=headers({
            "filename": "pending.txt", "tags": ["labels-test"],
        }))
    assert response.status_code == 503, response.text
    for http_method, changes in (("PATCH", {"name": "renamed-label"}), ("DELETE", {})):
        blocked = browser(api, project, http_method, "/" + definition["id"], **changes)
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "artifact_tags_busy"
    monkeypatch.setattr(artifacts, method, original)
    recovered = api.get(collection(project))
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["items"][0]["tags"] == ["labels-test"]
    assert browser(api, project, "DELETE", "/" + definition["id"]).status_code == 200


def test_exact_receipts_distinguish_omission_null_and_survive_catalog_deletion(api, project):
    definition = tag(api, project)
    artifact = upload(api, project)
    operation = str(uuid4())
    changed = update(api, project, artifact, tags=["labels-test"], client_operation_id=operation)
    assert changed.status_code == 200, changed.text
    assert browser(api, project, "DELETE", "/" + definition["id"]).status_code == 200
    replay = update(api, project, artifact, tags=["labels-test"], client_operation_id=operation)
    assert replay.status_code == 200, replay.text
    assert replay.json() == changed.json()
    assert replay.headers["X-Artifact-Operation-Replayed"] == "true"
    for payload in ({}, {"tags": None}):
        conflict = update(api, project, artifact, client_operation_id=operation, **payload)
        assert conflict.status_code == 409, conflict.text


def test_omitted_tags_preserve_historical_receipt_fingerprints(api, project):
    artifact = upload(api, project)
    with api.app.state.session_factory() as database:
        operation = database.scalar(select(ArtifactOperation).where(
            ArtifactOperation.artifact_id == UUID(artifact["id"])))
        metadata = ArtifactUploadMetadata.model_validate(operation.intent["metadata"])
        old_metadata = metadata.model_dump(mode="json", exclude={"tags", "sensitive",
                                                                  "related_artifact_ids"})
        staged = operation.intent["staged"]
        body = {"kind": "upload", "artifact_id": artifact["id"], "metadata": old_metadata,
                "expected_revision": None, "sha256": staged["sha256"],
                "size_bytes": staged["size_bytes"]}
        assert hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                              ).hexdigest() == operation.fingerprint
        assert "tags" not in operation.intent["metadata"]
    operation_id = uuid4()
    missing = ArtifactUpdateMetadata(client_operation_id=operation_id, expected_revision=1)
    cleared = ArtifactUpdateMetadata(client_operation_id=operation_id, expected_revision=1,
                                     tags=None)
    assert "tags" not in artifacts._intent_metadata(missing)
    assert artifacts._intent_metadata(cleared)["tags"] is None


def test_historical_receipt_replays_without_inventing_tags(api, project):
    artifact = upload(api, project)
    metadata = ArtifactUpdateMetadata(client_operation_id=uuid4(), expected_revision=1,
                                     actor_client="dashboard")
    mutation = artifacts.ArtifactMutation(project_id=UUID(project["id"]),
        artifact_id=UUID(artifact["id"]), client_operation_id=metadata.client_operation_id,
        kind="update", metadata=metadata, expected_revision=1)
    historical = {key: value for key, value in artifact.items() if key != "tags"}
    with api.app.state.session_factory() as database:
        database.add(ArtifactOperation(project_id=mutation.project_id,
            artifact_id=mutation.artifact_id, client_operation_id=metadata.client_operation_id,
            kind="update", state="completed", fingerprint=mutation.fingerprint(),
            intent={"metadata": artifacts._intent_metadata(metadata), "staged": None},
            response_body=historical, completed_at=datetime.now(UTC)))
        database.commit()
    for prefix in ("/api/v1", "/api/artifacts"):
        request_headers = dict(HEADERS)
        if prefix == "/api/artifacts":
            request_headers["X-Client-Operation-ID"] = str(metadata.client_operation_id)
        replay = api.patch(f"{prefix}/projects/{project['id']}/artifacts/{artifact['id']}",
            json=metadata.model_dump(mode="json", exclude_unset=True), headers=request_headers)
        assert replay.status_code == 200, replay.text
        assert replay.json() == historical
        assert replay.headers["X-Artifact-Operation-Replayed"] == "true"


def test_browser_upload_and_metadata_edit_tags(api, project):
    path = f"/api/artifacts/projects/{project['id']}/artifacts"
    response = api.post(path, content=b"browser content", headers={
        **HEADERS, **headers({"filename": "browser.txt", "tags": ["dir"],
                             "actor_client": "dashboard"}),
        "Content-Type": "application/octet-stream",
    })
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["tags"] == ["dir"]
    operation = str(uuid4())
    changed = api.patch(path + "/" + artifact["id"], json={
        "client_operation_id": operation, "expected_revision": 1, "tags": None,
        "actor_client": "dashboard",
    }, headers={**HEADERS, "X-Client-Operation-ID": operation})
    assert changed.status_code == 200, changed.text
    assert changed.json()["tags"] is None


def test_sensitive_search_approval_is_bound_to_tag_selection(api, project, artifact_storage):
    artifact = upload(api, project, filename="sensitive.txt", metadata={
        "sensitive": True, "tags": ["dir", "code-review"],
    })
    assert run_job(api, artifact_storage, Parser())
    path = collection(project) + "/search-content"
    payload = {"q": "confidentialneedle", "fulltext": True, "artifact_id": artifact["id"],
               "tags": ["dir"], **ACTOR}
    token = _token(api.post(path, json=payload))
    approved = {**payload, "approval_token": token, "human_approved": True}
    for change in ({"tags": ["code-review"]}, {"tag_mode": "and"}):
        assert _token(api.post(path, json={**approved, **change})) != token
    assert api.post(path, json=approved).status_code == 200
