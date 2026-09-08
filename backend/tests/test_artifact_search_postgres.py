"""End-to-end project scoping, lifecycle erasure and real Tantivy search."""

from uuid import uuid4

import pytest
from sqlalchemy import event

from mnemonic_api.artifact_extraction import extract_next_artifact
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.artifact_tika import ExtractedArtifact

from .test_artifacts_postgres import collection, headers, upload
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def artifact_storage(api, tmp_path):
    storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
    api.app.state.artifact_storage = storage
    api.app.state.settings.artifact_max_bytes = 1024
    return storage


class PlainExtractor:
    def extract(self, content, *, filename, size_bytes):
        return ExtractedArtifact(
            text=content.read().decode(), metadata={"dc:creator": ["Ada Example"]}, truncated=False
        )


def extract(api, storage):
    assert extract_next_artifact(api.app.state.session_factory, storage, PlainExtractor())


def search(api, project, query, **options):
    response = api.post(collection(project) + "/search-content", json={"q": query, **options})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_metadata_default_content_opt_in_and_extracted_metadata(api, project, artifact_storage):
    artifact = upload(api, project, filename="report.txt", body=b"Confidential payroll needle")
    pending = search(api, project, "needle", fulltext=True)
    assert pending["total"] == 0
    assert pending["indexing"]["pending"] == 1
    extract(api, artifact_storage)
    assert search(api, project, "needle")["total"] == 0
    result = search(api, project, "needle", fulltext=True)
    hit = result["items"][0]
    assert hit["artifact"]["id"] == artifact["id"]
    assert hit["artifact"]["extraction"]["metadata"] == {"dc:creator": ["Ada Example"]}
    assert hit["matched_fields"] == ["content"]
    assert "needle" in hit["snippet"]
    assert result["indexing"] == {"pending": 0, "failed": 0, "ready": 1, "truncated": 0}
    metadata = search(api, project, "Ada")
    assert metadata["items"][0]["snippet"] is None
    assert metadata["items"][0]["matched_fields"] == ["metadata"]
    assert search(api, project, "report needle", fulltext=True)["total"] == 1
    assert api.get(collection(project), params={"q": "Ada"}).json()["total"] == 1


def test_replacement_and_deletion_remove_searchable_body_but_keep_metadata(
    api,
    project,
    artifact_storage,
):
    artifact = upload(api, project, filename="report.txt", body=b"obsoletephrase")
    extract(api, artifact_storage)
    assert search(api, project, "obsoletephrase", fulltext=True)["total"] == 1
    path = collection(project) + "/" + artifact["id"]
    replacement = api.put(
        path + "/content",
        content=b"replacementphrase",
        headers=headers({"filename": "report.txt"}, revision=1),
    )
    assert replacement.status_code == 200, replacement.text
    assert search(api, project, "obsoletephrase", fulltext=True)["total"] == 0
    extract(api, artifact_storage)
    assert search(api, project, "replacementphrase", fulltext=True)["total"] == 1
    assert search(api, project, "obsoletephrase", fulltext=True)["total"] == 0
    assert api.delete(path, headers=headers(revision=2)).status_code == 200
    assert (
        search(api, project, "replacementphrase", fulltext=True, include_deleted=True)["total"] == 0
    )
    deleted = search(api, project, "Ada", include_deleted=True)
    assert deleted["total"] == 1
    assert deleted["items"][0]["artifact"]["content_available"] is False
    history = api.get(path + "/history").json()
    assert history["revisions"]["items"][1]["extraction"]["metadata"]["dc:creator"] == [
        "Ada Example"
    ]


def test_project_artifact_work_filters_and_pagination(api, project, artifact_storage, work_payload):
    work = create_work(api, project, work_payload)["work_item"]
    artifact = upload(api, project, filename="alpha.txt", metadata={"work_item_id": work["id"]})
    upload(api, project, filename="beta.txt")
    other = api.post("/api/v1/projects", json={"name": "Other", "slug": "other-search"}).json()
    assert search(api, other, "txt")["total"] == 0
    assert search(api, project, "txt", work_item_id=work["id"])["total"] == 1
    assert search(api, project, "txt", work_item_id=str(uuid4()))["total"] == 0
    assert search(api, project, "txt", artifact_id=artifact["id"])["total"] == 1
    page = search(api, project, "txt", limit=1)
    next_page = search(api, project, "txt", limit=1, offset=1)
    assert page["total"] == next_page["total"] == 2
    assert page["items"][0]["artifact"]["id"] != next_page["items"][0]["artifact"]["id"]
    assert search(api, project, "txt", offset=999)["items"] == []


def test_metadata_mode_does_not_select_normalized_text(api, project, artifact_storage):
    upload(api, project, filename="report.txt", body=b"secretbody")
    extract(api, artifact_storage)
    engine = api.app.state.session_factory.kw["bind"]
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert search(api, project, "report")["total"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any("normalized_text" in statement for statement in statements)


def test_unicode_metadata_search_uses_actual_text_not_json_escapes(api, project):
    artifact = upload(api, project, filename="café.txt", metadata={"description": "résumé"})
    assert search(api, project, "cafe")["items"][0]["artifact"]["id"] == artifact["id"]
    assert search(api, project, "resume")["total"] == 1


def test_search_indexes_metadata_values_without_schema_labels_or_nulls(
    api, project, artifact_storage,
):
    artifact = upload(
        api, project, filename="note.txt", body=b"description",
        metadata={"agent_session_id": None, "actor_client": None},
    )
    labels = ("filename", "description", "null", "extracted", "actor", "session")
    for query in labels:
        assert search(api, project, query)["total"] == 0
    extract(api, artifact_storage)
    for query in (*labels, "creator"):
        assert search(api, project, query)["total"] == 0
    metadata_hit = search(api, project, "Ada")["items"][0]
    assert metadata_hit["artifact"]["id"] == artifact["id"]
    assert metadata_hit["matched_fields"] == ["metadata"]
    body_hit = search(api, project, "description", fulltext=True)["items"][0]
    assert body_hit["artifact"]["id"] == artifact["id"]
    assert body_hit["matched_fields"] == ["content"]
    assert body_hit["snippet"] == "description"
    assert search(api, project, "filename description", fulltext=True)["total"] == 0


@pytest.mark.parametrize(
    "body",
    [
        b'{"q":"name","q":"other"}',
        b'{"q":"name","fulltext":true,"parser":"unsafe"}',
        b'{"q":""}',
        b'{"q":"\\ud800"}',
        b'{"q":"a","limit":101}',
    ],
)
def test_invalid_queries_are_safe_and_bounded(api, project, body):
    response = api.post(
        collection(project) + "/search-content",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "artifact_search_invalid"


def test_search_wire_limit_and_disabled_mode(api, project):
    path = collection(project) + "/search-content"
    assert (
        api.post(
            path, content=b" " * 4097, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    api.app.state.settings.artifact_max_bytes = 0
    result = api.post(path, content=b" " * 4097)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "artifact_library_disabled"
