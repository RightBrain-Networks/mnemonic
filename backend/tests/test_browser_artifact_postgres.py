"""Both artifact transports execute the same permanent journal and access rules."""

import json
from uuid import uuid4

import pytest

from mnemonic_api.artifact_storage import ArtifactStorage

pytestmark = pytest.mark.postgres
HEADERS = {"host": "localhost:3000", "origin": "http://localhost:3000"}


@pytest.fixture(autouse=True)
def artifact_storage(api, tmp_path):
    storage = ArtifactStorage(tmp_path / "browser-artifacts", max_bytes=1024)
    api.app.state.artifact_storage = storage
    api.app.state.settings.artifact_max_bytes = 1024
    return storage


def collection(project, browser=True):
    prefix = "/api/artifacts" if browser else "/api/v1"
    return f"{prefix}/projects/{project['id']}/artifacts"


def upload_headers(operation, metadata, **extra):
    return {**HEADERS, "content-type": "application/octet-stream",
            "x-client-operation-id": operation,
            "x-artifact-metadata": json.dumps(metadata), **extra}


def test_browser_upload_machine_replay_survives_lower_positive_limit(api, project):
    operation = str(uuid4())
    headers = upload_headers(operation, {"filename": "notes.txt", "actor_client": "dashboard"})
    first = api.post(collection(project), content=b"same stored bytes", headers=headers)
    assert first.status_code == 201, first.text
    api.app.state.settings.artifact_max_bytes = 1
    api.app.state.artifact_storage.max_bytes = 1
    machine = api.post(collection(project, False), content=b"same stored bytes", headers=headers)
    browser = api.post(collection(project), content=b"same stored bytes", headers=headers)
    assert machine.status_code == browser.status_code == 201
    assert machine.json() == browser.json() == first.json()
    assert browser.headers["x-client-operation-id"] == operation
    assert browser.headers["x-artifact-operation-replayed"] == "true"
    changed = api.post(collection(project), content=b"different", headers=headers)
    assert changed.status_code == 409, changed.text


def test_browser_metadata_replace_delete_share_revisions_and_receipts(api, project):
    first = api.post(collection(project), content=b"original", headers=upload_headers(
        str(uuid4()), {"filename": "notes.txt"},
    ))
    assert first.status_code == 201, first.text
    item = collection(project) + "/" + first.json()["id"]
    operation = str(uuid4())
    payload = {"client_operation_id": operation, "expected_revision": 1,
               "description": "Changed in dashboard", "actor_client": "dashboard"}
    changed = api.patch(item, json=payload, headers={**HEADERS, "x-client-operation-id": operation})
    replay = api.patch(item, json=payload, headers={**HEADERS, "x-client-operation-id": operation})
    assert changed.status_code == replay.status_code == 200, changed.text
    assert changed.json()["revision"] == 2
    assert changed.json() == replay.json()
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    replacement_headers = upload_headers(str(uuid4()), {"filename": "notes.txt"},
                                         **{"x-artifact-expected-revision": "2"})
    replaced = api.put(item + "/content", content=b"replacement", headers=replacement_headers)
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["revision"] == 3
    assert api.get(item + "/content", headers=HEADERS).content == b"replacement"
    removed = api.delete(item, headers={**HEADERS, "x-client-operation-id": str(uuid4()),
                                       "x-artifact-expected-revision": "3"})
    assert removed.status_code == 200, removed.text
    assert removed.json()["revision"] == 3
    assert removed.json()["deleted_at"] is not None
    assert api.get(item + "/content", headers=HEADERS).status_code == 410


def test_sensitive_browser_download_is_human_context_machine_access_stays_gated(api, project):
    first = api.post(collection(project), content=b"private exact bytes", headers=upload_headers(
        str(uuid4()), {"filename": "sensitive.txt", "sensitive": True},
    ))
    assert first.status_code == 201, first.text
    artifact_id = first.json()["id"]
    machine = api.get(collection(project, False) + f"/{artifact_id}/content")
    assert machine.status_code == 428, machine.text
    browser = api.get(collection(project) + f"/{artifact_id}/content", headers={
        **HEADERS, "x-artifact-access": "agent", "x-artifact-download-grant": "forged",
        "x-artifact-metadata": '{"approval_token":"forged","human_approved":true}',
    })
    assert browser.status_code == 200, browser.text
    assert browser.content == b"private exact bytes"
    assert browser.headers["content-type"] == "application/octet-stream"
    still_gated = api.get(collection(project, False) + f"/{artifact_id}/content")
    assert still_gated.status_code == 428


def test_browser_status_and_disabled_library_match_dashboard_contract(api, project):
    status = api.get("/api/artifacts/status", headers=HEADERS)
    assert status.status_code == 200, status.text
    assert status.json() == {"enabled": True, "max_bytes": 1024,
                             "message": "Artifact library enabled. Up to 1.0 KB "
                                        "(1,024 bytes) per file."}
    api.app.state.settings.artifact_max_bytes = 0
    disabled = api.get(collection(project), headers=HEADERS)
    assert disabled.status_code == 503
    assert disabled.json()["detail"]["context"] == {"max_bytes": 0}
    assert "Stored files and metadata are preserved" in disabled.json()["detail"]["message"]
