"""Bounded current extracted-text reads preserve revision and storage boundaries."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from mnemonic_api.artifact_extraction import _claim_job
from mnemonic_api.artifact_schemas import ArtifactTextQuery
from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Artifact, ArtifactAudit, ArtifactExtraction, ArtifactOperation
from mnemonic_api.services.artifacts import read_artifact_text

from .test_artifact_extraction_postgres import Parser, run_job
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


def text_path(project, artifact):
    return collection(project) + "/" + artifact["id"] + "/text"


class FixedParser:
    def __init__(self, text, *, truncated=False):
        self.text = text
        self.truncated = truncated

    def extract(self, content, *, filename, size_bytes):
        return ExtractedArtifact(self.text, {"untrusted-property": ["x" * 4000]}, self.truncated)


def test_unicode_pages_reassemble_all_text_without_metadata_or_audit(
    api, project, artifact_storage,
):
    normalized = "é雪\U0001f603e\u0301\n" * 4001
    artifact = upload(api, project, filename="unicode.txt", body=b"source")
    assert run_job(api, artifact_storage, FixedParser(normalized))
    path = text_path(project, artifact)
    params = {"expected_revision": 1}
    pages = []
    while True:
        response = api.get(path, params=params)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        page = response.json()
        assert set(page) == {
            "project_id", "artifact_id", "revision", "sha256", "extraction", "text",
            "offset", "limit", "total_chars", "next_offset",
        }
        assert page["project_id"] == project["id"]
        assert page["artifact_id"] == artifact["id"]
        assert page["sha256"] == artifact["sha256"]
        assert page["revision"] == 1
        assert page["limit"] == 20_000
        assert page["total_chars"] == len(normalized)
        assert len(page["text"]) <= 20_000
        assert page["text"] == normalized[page["offset"]:page["offset"] + page["limit"]]
        assert set(page["extraction"]) == {"status", "truncated", "error_code", "extracted_at"}
        assert page["extraction"]["status"] == "ready"
        assert page["extraction"]["extracted_at"] is not None
        assert "untrusted-property" not in response.text
        pages.append(page["text"])
        if page["next_offset"] is None:
            break
        params["offset"] = page["next_offset"]
    assert len(pages) == 2
    assert "".join(pages) == normalized
    for offset in (len(normalized), len(normalized) + 1, 8_000_000):
        page = api.get(path, params={"expected_revision": 1, "offset": offset}).json()
        assert page["text"] == ""
        assert page["offset"] == offset
        assert page["total_chars"] == len(normalized)
        assert page["next_offset"] is None
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(ArtifactAudit)) == 1
        assert database.scalar(select(func.count()).select_from(ArtifactOperation)) == 1


def test_ready_empty_and_truncated_text_have_explicit_counts(api, project, artifact_storage):
    for normalized, truncated in (("", False), ("partial", True)):
        artifact = upload(api, project, filename="ready.txt", body=b"source")
        assert run_job(api, artifact_storage, FixedParser(normalized, truncated=truncated))
        response = api.get(
            text_path(project, artifact), params={"expected_revision": 1, "limit": 1},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["text"] == normalized[:1]
        assert page["total_chars"] == len(normalized)
        assert page["next_offset"] == (1 if normalized else None)
        assert page["extraction"]["truncated"] is truncated


@pytest.mark.parametrize("status", ["pending", "processing", "failed"])
def test_unready_extraction_distinguishes_status_from_empty_text(
    api, project, artifact_storage, status,
):
    artifact = upload(api, project, filename="state.txt", body=b"source")
    if status == "processing":
        assert _claim_job(api.app.state.session_factory, 60) is not None
    elif status == "failed":
        parser = Parser(error=ExtractionError("extraction_unsupported"))
        assert run_job(api, artifact_storage, parser)
    response = api.get(text_path(project, artifact), params={"expected_revision": 1})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["extraction"]["status"] == status
    assert page["extraction"]["error_code"] == (
        "extraction_unsupported" if status == "failed" else None
    )
    assert page["text"] is None
    assert page["total_chars"] is None
    assert page["next_offset"] is None


def test_replace_invalidates_pages_and_delete_blocks_text(api, project, artifact_storage):
    artifact = upload(api, project, filename="replace.txt", body=b"old confidential content")
    assert run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    first = api.get(path + "/text", params={"expected_revision": 1, "limit": 3}).json()
    assert first["text"] == "old"
    replaced = api.put(path + "/content", content=b"new contents", headers=headers(
        {"filename": artifact["filename"]}, revision=1,
    ))
    assert replaced.status_code == 200, replaced.text
    obsolete = api.get(path + "/text", params={"expected_revision": 1, "offset": 3})
    assert obsolete.status_code == 409, obsolete.text
    assert obsolete.json()["detail"]["code"] == "artifact_revision_conflict"
    pending = api.get(path + "/text", params={"expected_revision": 2}).json()
    assert pending["text"] is None
    assert pending["extraction"]["status"] == "pending"
    assert pending["sha256"] == replaced.json()["sha256"]
    assert run_job(api, artifact_storage, Parser())
    assert api.get(path + "/text", params={"expected_revision": 2}).json()["text"] == "new contents"
    deleted = api.delete(path, headers=headers(revision=2))
    assert deleted.status_code == 200, deleted.text
    for expected_revision in (1, 2):
        response = api.get(path + "/text", params={"expected_revision": expected_revision})
        assert response.status_code == 410, response.text
        assert response.json()["detail"]["code"] == "artifact_deleted"


@pytest.mark.parametrize("kind", ["replace", "delete"])
def test_read_recovers_pending_mutation_before_exposing_text(
    api, project, artifact_storage, monkeypatch, kind,
):
    artifact = upload(api, project, filename="recovery.txt", body=b"old secret")
    assert run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    operation = "publish" if kind == "replace" else "delete"
    original = getattr(artifact_storage, operation)

    def unavailable(*_args):
        raise OSError("Interrupted before filesystem mutation")

    monkeypatch.setattr(artifact_storage, operation, unavailable)
    if kind == "replace":
        failed = api.put(path + "/content", content=b"new", headers=headers(
            {"filename": artifact["filename"]}, revision=1,
        ))
    else:
        failed = api.delete(path, headers=headers(revision=1))
    assert failed.status_code == 503, failed.text
    blocked = api.get(path + "/text", params={"expected_revision": 1})
    assert blocked.status_code == 503, blocked.text
    assert "old secret" not in blocked.text
    monkeypatch.setattr(artifact_storage, operation, original)
    recovered = api.get(path + "/text", params={"expected_revision": 1})
    assert recovered.status_code == (409 if kind == "replace" else 410), recovered.text
    with api.app.state.session_factory() as database:
        assert set(database.scalars(select(ArtifactOperation.state))) == {"completed"}
        old = database.get(ArtifactExtraction, (UUID(artifact["id"]), 1))
        assert old.normalized_text is None


def test_missing_current_bytes_and_disabled_library_do_not_expose_text(
    api, project, artifact_storage, monkeypatch,
):
    artifact = upload(api, project, filename="missing.txt", body=b"confidential")
    assert run_job(api, artifact_storage, Parser())
    (artifact_storage.root / project["id"] / artifact["id"] / artifact["filename"]).unlink()
    response = api.get(text_path(project, artifact), params={"expected_revision": 1})
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "artifact_storage_unavailable"
    assert "confidential" not in response.text
    api.app.state.settings.artifact_max_bytes = 0

    def forbidden(*_args):
        raise AssertionError("Disabled library must not begin journal recovery")

    monkeypatch.setattr("mnemonic_api.services.artifacts.recover_artifact", forbidden)
    response = api.get(text_path(project, artifact), params={"expected_revision": 1})
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "artifact_library_disabled"


@pytest.mark.parametrize("params", [
    {}, {"expected_revision": 0}, {"expected_revision": -1}, {"expected_revision": "1.5"},
    {"expected_revision": 1, "offset": -1}, {"expected_revision": 1, "offset": 8_000_001},
    {"expected_revision": 1, "limit": 0}, {"expected_revision": 1, "limit": 20_001},
    {"expected_revision": 1, "unknown": "unsupported"},
])
def test_invalid_text_pagination_is_rejected(api, project, params):
    artifact = upload(api, project)
    response = api.get(text_path(project, artifact), params=params)
    assert response.status_code == 422, response.text


def test_text_reads_require_auth_and_exact_project_membership(api, project, artifact_storage):
    artifact = upload(api, project)
    params = {"expected_revision": 1}
    unauthenticated = api.get(text_path(project, artifact), params=params, headers={
        "Authorization": "Bearer invalid",
    })
    assert unauthenticated.status_code == 401
    missing = api.get(collection(project) + f"/{uuid4()}/text", params=params)
    assert missing.status_code == 404
    other = api.post("/api/v1/projects", json={"name": "Other text project"}).json()
    wrong_project = api.get(text_path(other, artifact), params=params)
    assert wrong_project.status_code == 404
    assert wrong_project.json()["detail"]["code"] == "artifact_not_found"


def test_text_read_refreshes_cached_revision_before_selecting_text(api, project, artifact_storage):
    artifact = upload(api, project, filename="cached.txt", body=b"obsolete")
    assert run_job(api, artifact_storage, Parser())
    with api.app.state.session_factory() as database:
        cached = database.get(Artifact, UUID(artifact["id"]))
        database.commit()
        replacement = api.put(
            collection(project) + "/" + artifact["id"] + "/content",
            content=b"current", headers=headers({"filename": artifact["filename"]}, revision=1),
        )
        assert replacement.status_code == 200, replacement.text
        assert cached.revision == 1
        with pytest.raises(ApplicationError) as error:
            read_artifact_text(
                database, artifact_storage, UUID(project["id"]), UUID(artifact["id"]),
                ArtifactTextQuery(expected_revision=1),
            )
        assert error.value.detail["code"] == "artifact_revision_conflict"
        assert cached.revision == 2
