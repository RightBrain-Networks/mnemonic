"""Portable mixed exports include exact current bytes and never silently skip assets."""

import json
import tarfile
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from mnemonic_api.errors import ApplicationError
from mnemonic_backup.archive import BackupError
from mnemonic_backup.exports import ExportSelection, export_file

from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload
from .test_decision_search_postgres import domain, put

pytestmark = pytest.mark.postgres


def test_mixed_export_contains_current_decisions_and_exact_sensitive_artifact(
    api, project, postgres_engine, artifact_storage,
):
    selected = domain(api, project)
    decision = put(api, project, selected)
    put(api, project, selected, decision_id=decision["decision_id"], expected_revision=0,
        content="Current café decision")
    artifact = upload(api, project, filename="metadata.json", body=b'{"exact":true}',
                      metadata={"sensitive": True})
    selection = ExportSelection(domain_ids=[selected["id"]], artifact_ids=[artifact["id"]])
    with export_file(postgres_engine, artifact_storage, UUID(project["id"]), selection) as output:
        assert output.read(3) == b"BZh"
        output.seek(0)
        with tarfile.open(fileobj=output, mode="r:bz2") as archive:
            names = archive.getnames()
            assert len(names) == len(set(names))
            assert archive.extractfile(
                f"domains/{selected['id']}/{decision['decision_id']}.md"
            ).read().decode() == "Current café decision"
            assert archive.extractfile(
                f"artifacts/{artifact['id']}/files/metadata.json"
            ).read() == b'{"exact":true}'
            manifest = json.load(archive.extractfile("manifest.json"))
            assert manifest["selection"] == selection.model_dump(mode="json")


def test_export_rejects_missing_selection_without_partial_download(
    api, project, postgres_engine, artifact_storage,
):
    artifact = upload(api, project)
    selection = ExportSelection(artifact_ids=[artifact["id"], uuid4()])
    with pytest.raises(ApplicationError):
        export_file(postgres_engine, artifact_storage, UUID(project["id"]), selection)


@pytest.mark.parametrize("all_artifacts", [False, True])
def test_export_rejects_corrupt_artifact(api, project, postgres_engine, artifact_storage,
                                         all_artifacts):
    artifact = upload(api, project)
    path = next(artifact_storage.root.rglob("confidential.pdf"))
    path.write_bytes(b"corrupted")
    with pytest.raises(BackupError, match="changed or is damaged"):
        export_file(postgres_engine, artifact_storage, UUID(project["id"]),
                    ExportSelection(include_artifacts=True) if all_artifacts
                    else ExportSelection(artifact_ids=[artifact["id"]]))


def test_export_cannot_select_domain_from_another_project(api, project, postgres_engine,
                                                        artifact_storage):
    selected = domain(api, project)
    with pytest.raises(ApplicationError):
        export_file(postgres_engine, artifact_storage, uuid4(),
                    ExportSelection(domain_ids=[selected["id"]]))


@pytest.mark.parametrize("selection", [{}, {"domain_ids": []}, {"artifact_ids": ["bad"]},
                                       {"domain_ids": [str(UUID(int=1))] * 2}])
def test_selection_requires_nonempty_unique_identifiers(selection):
    with pytest.raises(ValidationError):
        ExportSelection.model_validate(selection)



def test_export_finishes_pending_artifact_replacement_before_reading_current_bytes(
    api, project, postgres_engine, artifact_storage, monkeypatch,
):
    artifact = upload(api, project, filename="recover.txt", body=b"before replacement")
    original_publish = artifact_storage.publish

    def interrupted_publish(_staged):
        raise OSError("simulated worker interruption before publication")

    monkeypatch.setattr(artifact_storage, "publish", interrupted_publish)
    replaced = api.put(
        f"{collection(project)}/{artifact['id']}/content", content=b"recovered replacement",
        headers=headers({"filename": "recover.txt"}, revision=1),
    )
    assert replaced.status_code == 503, replaced.text
    assert replaced.json()["detail"]["context"]["attempt_not_committed"] is False
    monkeypatch.setattr(artifact_storage, "publish", original_publish)
    with export_file(postgres_engine, artifact_storage, UUID(project["id"]),
                     ExportSelection(artifact_ids=[artifact["id"]])) as output:
        with tarfile.open(fileobj=output, mode="r:bz2") as archive:
            assert archive.extractfile(
                f"artifacts/{artifact['id']}/files/recover.txt"
            ).read() == b"recovered replacement"
            metadata = json.load(archive.extractfile(f"artifacts/{artifact['id']}/metadata.json"))
            assert metadata["revision"] == 2
    current = api.get(f"{collection(project)}/{artifact['id']}")
    assert current.status_code == 200 and current.json()["revision"] == 2


def test_all_artifacts_exports_live_project_files_including_sensitive(
    api, project, postgres_engine, artifact_storage,
):
    first = upload(api, project, filename="first.txt", body=b"first file")
    second = upload(api, project, filename="second.txt", body=b"second file",
                    metadata={"sensitive": True})
    deleted = upload(api, project, filename="deleted.txt")
    response = api.delete(f"{collection(project)}/{deleted['id']}", headers=headers(revision=1))
    assert response.status_code == 200, response.text
    other = api.post("/api/v1/projects", json={"name": "Other export project"}).json()
    foreign = upload(api, other, filename="foreign.txt")
    with export_file(postgres_engine, artifact_storage, UUID(project["id"]),
                     ExportSelection(include_artifacts=True)) as output:
        with tarfile.open(fileobj=output, mode="r:bz2") as archive:
            names = archive.getnames()
            assert archive.extractfile(
                f"artifacts/{first['id']}/files/first.txt"
            ).read() == b"first file"
            assert archive.extractfile(
                f"artifacts/{second['id']}/files/second.txt"
            ).read() == b"second file"
            assert not any(deleted["id"] in name or foreign["id"] in name for name in names)


@pytest.mark.parametrize("selection", [
    {"include_artifacts": True, "artifact_ids": [str(uuid4())]},
    {"include_artifacts": "true"},
])
def test_all_artifacts_choice_requires_an_unambiguous_boolean(selection):
    with pytest.raises(ValidationError):
        ExportSelection.model_validate(selection)
