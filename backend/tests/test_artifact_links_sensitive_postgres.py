"""Metadata-only artifact links and sensitivity use durable, concurrent-safe revisions."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.models import ArtifactExtraction
from mnemonic_api.services import artifacts

from .test_artifact_extraction_postgres import Parser, run_job
from .test_artifacts_postgres import actual_files, collection, headers, upload
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_project_activity_audit_postgres import _audit
from .test_work_items_postgres import create_work, item_path

pytestmark = pytest.mark.postgres


def update(api, project, artifact, **changes):
    return api.patch(collection(project) + "/" + artifact["id"], json={
        "client_operation_id": str(uuid4()),
        "expected_revision": artifact["revision"],
        "agent_session_id": "metadata-session",
        "actor_client": "pytest",
        **changes,
    })


def test_metadata_update_adds_symmetric_artifact_and_work_links_without_file_io(
    api, project, work_payload, artifact_storage, monkeypatch, postgres_engine,
):
    work = create_work(api, project, work_payload)["work_item"]
    first = upload(api, project, filename="first.txt")
    second = upload(api, project, filename="second.txt")
    before = [(path, path.read_bytes()) for path in actual_files(artifact_storage)]

    def forbid_io(*_args, **_kwargs):
        raise AssertionError("Metadata changes must not read or rewrite content")

    for method in ("open", "publish", "stage", "delete"):
        monkeypatch.setattr(artifact_storage, method, forbid_io)
    response = update(api, project, first, related_artifact_ids=[second["id"]],
                      related_work_item_ids=[work["id"]], description="Linked metadata")
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["revision"] == 2
    assert changed["related_artifact_ids"] == [second["id"]]
    assert changed["related_work_item_ids"] == [work["id"]]
    assert changed["sha256"] == first["sha256"]
    peer_path = collection(project) + "/" + second["id"]
    assert api.get(peer_path).json()["related_artifact_ids"] == [first["id"]]
    assert api.get(peer_path + "/history").json()["audit"]["items"][0]["action"] == "linked"
    linked_work = api.get(item_path(project, work) + "/context").json()
    assert [item["id"] for item in linked_work["artifacts"]] == [first["id"]]
    assert api.get(collection(project), params={"work_item_id": work["id"]}).json()["total"] == 1
    assert [(path, path.read_bytes()) for path, _ in before] == before
    audit = _audit(postgres_engine)
    assert audit["result"] == "pass", audit


def test_upload_links_are_additive_durable_and_sensitive_replacement_preserves_flag(api, project):
    peer = upload(api, project, filename="peer.txt")
    artifact = upload(api, project, metadata={
        "sensitive": True, "related_artifact_ids": [peer["id"]],
    })
    assert artifact["sensitive"] is True
    path = collection(project) + "/" + artifact["id"]
    replaced = api.put(path + "/content", content=b"new bytes", headers=headers(
        {"filename": artifact["filename"]}, revision=1,
    ))
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["sensitive"] is True
    assert replaced.json()["related_artifact_ids"] == [peer["id"]]
    changed = update(api, project, replaced.json(), sensitive=False, related_artifact_ids=[])
    assert changed.status_code == 200
    assert changed.json()["sensitive"] is False
    assert changed.json()["related_artifact_ids"] == [peer["id"]]
    assert api.delete(path, headers=headers(revision=3)).status_code == 200
    assert api.get(collection(project) + "/" + peer["id"]).json()["related_artifact_ids"] == [
        artifact["id"],
    ]
    revisions = api.get(path + "/history").json()["revisions"]["items"]
    assert [row["sensitive"] for row in revisions] == [False, True, True]


def test_metadata_receipts_replay_exactly_after_later_updates_and_delete(api, project):
    artifact = upload(api, project)
    operation = str(uuid4())
    first = update(api, project, artifact, sensitive=True, client_operation_id=operation)
    assert first.status_code == 200, first.text
    assert update(api, project, first.json(), sensitive=False).status_code == 200
    path = collection(project) + "/" + artifact["id"]
    assert api.delete(path, headers=headers(revision=3)).status_code == 200
    replay = update(api, project, artifact, sensitive=True, client_operation_id=operation)
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["x-artifact-operation-replayed"] == "true"
    changed = update(api, project, artifact, sensitive=False, client_operation_id=operation)
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "client_operation_conflict"


def test_metadata_update_rejects_stale_revision_and_concurrent_changes(api, project):
    artifact = upload(api, project)
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda flag: update(api, project, artifact, sensitive=flag),
                                     [True, False]))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert api.get(collection(project) + "/" + artifact["id"]).json()["revision"] == 2


def test_related_artifacts_reject_self_deleted_unknown_and_foreign_project(api, project):
    artifact = upload(api, project)
    other = api.post("/api/v1/projects", json={"name": "Other"}).json()
    foreign = upload(api, other)
    deleted = upload(api, project, filename="deleted.txt")
    assert api.delete(collection(project) + "/" + deleted["id"], headers=headers(revision=1))
    for target, status, code in (
        (artifact["id"], 422, "artifact_self_link"),
        (foreign["id"], 404, "artifact_related_artifact_not_found"),
        (deleted["id"], 404, "artifact_related_artifact_not_found"),
        (str(uuid4()), 404, "artifact_related_artifact_not_found"),
    ):
        response = update(api, project, artifact, related_artifact_ids=[target])
        assert response.status_code == status, response.text
        assert response.json()["detail"]["code"] == code
    assert api.get(collection(project) + "/" + artifact["id"]).json()["revision"] == 1


def test_metadata_revision_invalidates_text_and_suppresses_sensitive_properties(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="metadata.txt")
    assert run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    assert api.get(path).json()["extraction"]["metadata"]
    response = update(api, project, artifact, sensitive=True)
    assert response.status_code == 200, response.text
    assert response.json()["extraction"]["status"] == "pending"
    with api.app.state.session_factory() as database:
        old = database.get(ArtifactExtraction, (UUID(artifact["id"]), 1))
        assert old.status == "superseded"
        assert old.normalized_text is None
    assert run_job(api, artifact_storage, Parser())
    assert api.get(path).json()["extraction"]["metadata"] == {}
    for row in api.get(path + "/history").json()["revisions"]["items"]:
        assert row["extraction"]["metadata"] == {}


def test_metadata_intent_recovers_without_reapplying_links(api, project, monkeypatch):
    first = upload(api, project)
    peer = upload(api, project, filename="peer.txt")
    original = artifacts._publish_metadata_revision

    def interrupted(*_args):
        raise OSError("Simulated interruption after metadata intent committed")

    monkeypatch.setattr(artifacts, "_publish_metadata_revision", interrupted)
    response = update(api, project, first, sensitive=True, related_artifact_ids=[peer["id"]])
    assert response.status_code == 503, response.text
    monkeypatch.setattr(artifacts, "_publish_metadata_revision", original)
    recovered = api.get(collection(project) + "/" + first["id"]).json()
    assert recovered["revision"] == 2
    assert recovered["sensitive"] is True
    assert recovered["related_artifact_ids"] == [peer["id"]]
    peer_history = api.get(collection(project) + "/" + peer["id"] + "/history").json()
    assert [row["action"] for row in peer_history["audit"]["items"]].count("linked") == 1


def test_artifact_link_database_guards_preserve_same_project_canonical_durable_pairs(
    api, project, postgres_engine,
):
    first = upload(api, project)
    second = upload(api, project, filename="second.txt")
    assert update(api, project, first, related_artifact_ids=[second["id"]]).status_code == 200
    with postgres_engine.connect() as connection:
        for statement in (
            "DELETE FROM artifact_links",
            "UPDATE artifact_links SET related_artifact_id=artifact_id",
            "TRUNCATE artifact_links",
        ):
            with pytest.raises(DBAPIError):
                connection.execute(text(statement))
            connection.rollback()
    with api.app.state.session_factory() as database:
        assert len(list(database.scalars(select(ArtifactExtraction)))) == 3



def test_symmetric_artifact_link_limit_applies_to_both_endpoints(api, project):
    hub = upload(api, project, filename="hub.txt")
    peers = [upload(api, project, filename=f"peer-{index}.txt") for index in range(51)]
    full = update(api, project, hub, related_artifact_ids=[peer["id"] for peer in peers[:50]])
    assert full.status_code == 200, full.text
    source_overflow = update(api, project, full.json(), related_artifact_ids=[peers[50]["id"]])
    peer_overflow = update(api, project, peers[50], related_artifact_ids=[hub["id"]])
    for response in (source_overflow, peer_overflow):
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "artifact_link_limit"
    repeated = update(api, project, full.json(), related_artifact_ids=[peers[0]["id"]])
    assert repeated.status_code == 200, repeated.text
    assert len(repeated.json()["related_artifact_ids"]) == 50



def test_metadata_patch_accepts_exact_mcp_json_wire_and_echoes_receipt_identity(api, project):
    artifact = upload(api, project)
    operation = str(uuid4())
    body = {
        "client_operation_id": operation, "expected_revision": artifact["revision"],
        "agent_session_id": "mcp-session", "actor_client": "mcp-client", "sensitive": True,
    }
    path = collection(project) + "/" + artifact["id"]
    response = api.patch(path, content=json.dumps(body, ensure_ascii=True).encode(), headers={
        "Content-Type": "application/json", "Accept-Encoding": "identity",
    })
    assert response.status_code == 200, response.text
    assert response.headers.get_list("X-Client-Operation-ID") == [operation]
    assert response.headers["X-Artifact-Operation-Replayed"] == "false"
    assert response.json()["sensitive"] is True
    assert response.json()["revision"] == artifact["revision"] + 1
    replay = api.patch(path, content=json.dumps(body, ensure_ascii=True).encode(), headers={
        "Content-Type": "application/json", "Accept-Encoding": "identity",
    })
    assert replay.status_code == 200, replay.text
    assert replay.headers["X-Artifact-Operation-Replayed"] == "true"
    assert replay.json() == response.json()
