"""Coherent pagination and coverage across work, artifacts and indexed transcripts."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import event

from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection as artifact_collection
from .test_artifacts_postgres import headers, upload
from .test_leases_postgres import expire_lease
from .test_semantic_postgres import DeterministicEmbedder, FailingEmbedder
from .test_transcript_indexing_postgres import register, run
from .test_work_items_postgres import create_work, item_path

pytestmark = pytest.mark.postgres


def path(project):
    return f"/api/v1/projects/{project['id']}/search"


def search(api, project, **payload):
    response = api.post(path(project), json=payload)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert sum(result["facet_totals"].values()) == result["total"]
    return result


def identities(page):
    return [hit["id"] for hit in page["items"]]


@pytest.fixture
def mixed(api, project, work_payload, tmp_path, postgres_engine, artifact_storage):
    first = create_work(api, project, work_payload, title="Needle first")["work_item"]
    artifact = upload(api, project, filename="needle.txt", body=b"rare needle document")
    extract(api, artifact_storage)
    work, _, transcript, _ = register(
        api, project, {**work_payload, "title": "Needle second"}, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    return first, artifact, work, transcript


def test_defaults_include_all_facets_and_work_statuses(api, project, mixed):
    result = search(api, project)
    assert result["limit"] == 50 and result["offset"] == 0
    assert result["facet_totals"] == {"work_items": 2, "artifacts": 1, "transcripts": 1}
    assert set(identities(result)) == {row["id"] for row in mixed}
    assert all(hit["score"] == 0 for hit in result["items"])


def test_content_is_opt_in_and_relevance_mixes_facets(api, project, mixed):
    assert search(api, project, q="needle")["facet_totals"] == {
        "work_items": 2, "artifacts": 1, "transcripts": 0,
    }
    full = search(api, project, q="needle", fulltext=True)
    assert full["facet_totals"] == {"work_items": 2, "artifacts": 1, "transcripts": 1}
    scores = [hit["score"] for hit in full["items"]]
    assert scores == sorted(scores, reverse=True)
    assert {hit["facet"] for hit in full["items"][:3]} == {
        "work_items", "artifacts", "transcripts",
    }
    assert not full["indexing_incomplete"]
    transcript = next(hit["transcript"] for hit in full["items"] if hit["facet"] == "transcripts")
    assert "needle" in transcript["snippet"]


def test_creation_sort_and_global_pagination(api, project, mixed):
    expected = [row["id"] for row in mixed]
    options = {"sort": {"by": "created_at", "direction": "asc"}, "limit": 2}
    pages = [search(api, project, offset=offset, **options) for offset in (0, 2, 99)]
    assert identities(pages[0]) + identities(pages[1]) == expected
    assert all(page["total"] == 4 for page in pages)
    assert pages[2]["items"] == []
    assert identities(search(api, project, sort={"by": "created_at"})) == expected[::-1]


def test_updated_date_is_primary_with_a_query(api, project, mixed):
    first, artifact, work, transcript = mixed
    response = api.patch(item_path(project, first), json={
        "expected_version": first["version"], "summary": "Updated needle explanation",
    })
    assert response.status_code == 200, response.text
    result = search(api, project, q="needle", fulltext=True, sort={"by": "updated_at"})
    assert identities(result) == [first["id"], transcript["id"], work["id"], artifact["id"]]


def test_facet_groups_have_independent_sorts_and_page_boundary(api, project, mixed):
    first, artifact, work, transcript = mixed
    payload = {
        "q": "needle", "fulltext": True,
        "facet_order": [
            {"facet": "artifacts", "sort": {"by": "relevance"}},
            {"facet": "work_items", "sort": {"by": "created_at", "direction": "asc"}},
        ],
    }
    assert identities(search(api, project, **payload)) == [
        artifact["id"], first["id"], work["id"], transcript["id"],
    ]
    page = search(api, project, **payload, offset=2, limit=2)
    assert identities(page) == [work["id"], transcript["id"]] and page["total"] == 4


def test_filters_are_independent_and_project_scoped(api, project, mixed):
    first, artifact, _, transcript = mixed
    result = search(api, project, filters={
        "work_items": {"status": "pending", "tag": "CACHE"},
        "artifacts": {"sensitive": False, "created_by_agent_session_id": "origin-session"},
        "transcripts": {"agent_session_id": "lease-session", "client": "claude-code",
                        "kind": "primary", "status": "ready"},
    })
    assert set(identities(result)) == {first["id"], artifact["id"], transcript["id"]}
    empty = search(api, project, filters={"transcripts": {"agent_session_id": "missing-session"}})
    assert empty["facet_totals"] == {"work_items": 2, "artifacts": 1, "transcripts": 0}
    other = api.post("/api/v1/projects", json={"name": "Other search project"}).json()
    assert search(api, other, q="needle", fulltext=True)["total"] == 0
    assert api.post(path({"id": str(uuid4())}), json={}).status_code == 404


def test_sensitive_filters_never_grant_body_or_property_access(api, project, artifact_storage):
    artifact = upload(api, project, filename="private.txt", body=b"classifiedneedle",
                      metadata={"sensitive": True})
    extract(api, artifact_storage)
    filters = {"artifacts": {"sensitive": True, "artifact_id": artifact["id"]}}
    content = search(api, project, q="classifiedneedle", fulltext=True, filters=filters)
    assert content["total"] == 0 and content["indexing_incomplete"]
    assert content["coverage"]["artifacts"]["sensitive_content_withheld"] == 1
    metadata = search(api, project, q="private", fulltext=True, filters=filters)
    assert metadata["total"] == 1
    hit = metadata["items"][0]["artifact"]
    assert hit["snippet"] is None and hit["matched_fields"] == ["metadata"]
    assert "Ada Example" not in json.dumps(metadata)
    public = search(api, project, q="private", filters={"artifacts": {"sensitive": False}})
    assert public["total"] == 0


def test_metadata_only_never_selects_extracted_bodies(api, project, mixed, postgres_engine):
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", record)
    try:
        assert search(api, project, q="needle")["total"] == 3
    finally:
        event.remove(postgres_engine, "before_cursor_execute", record)
    assert not any("normalized_text" in statement for statement in statements)


def test_replacement_deletion_erase_searchable_body(api, project, artifact_storage):
    artifact = upload(api, project, filename="report.txt", body=b"oldneedle")
    extract(api, artifact_storage)
    assert search(api, project, q="oldneedle", fulltext=True)["total"] == 1
    target = artifact_collection(project) + "/" + artifact["id"]
    response = api.put(target + "/content", content=b"newneedle",
                       headers=headers({"filename": "report.txt"}, revision=1))
    assert response.status_code == 200, response.text
    result = search(api, project, q="oldneedle", fulltext=True)
    assert result["total"] == 0 and result["indexing_incomplete"]
    extract(api, artifact_storage)
    assert search(api, project, q="newneedle", fulltext=True)["total"] == 1
    assert api.delete(target, headers=headers(revision=2)).status_code == 200
    assert search(api, project, q="newneedle", fulltext=True,
                  filters={"artifacts": {"include_deleted": True}})["total"] == 0


def test_disabled_artifacts_do_not_block_other_facets(api, project, mixed):
    api.app.state.settings.artifact_max_bytes = 0
    result = search(api, project)
    assert result["facet_totals"] == {"work_items": 2, "artifacts": 0, "transcripts": 1}
    assert result["coverage"]["artifacts"]["enabled"] is False
    assert result["indexing_incomplete"] is True


@pytest.mark.parametrize("payload", [
    {"facets": []}, {"facets": ["artifacts", "artifacts"]}, {"facets": ["unknown"]},
    {"limit": 101}, {"limit": 0}, {"offset": -1}, {"offset": 1_000_001},
    {"sort": {"by": "arbitrary"}}, {"sort": {"by": "priority"}},
    {"sort": {"direction": "sideways"}}, {"filters": {"artifacts": {"unknown": True}}},
    {"facet_order": [{"facet": "artifacts"}, {"facet": "artifacts"}]},
    {"facets": ["artifacts"], "facet_order": [{"facet": "work_items"}]},
    {"q": "a\ncontrol"}, {"q": "\ud800"}, {"q": "a" * 1001},
    {"filters": {"work_items": {"semantic": True}}},
])
def test_invalid_requests_are_rejected(api, project, payload):
    response = api.post(path(project), content=json.dumps(payload),
                        headers={"Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert response.headers["cache-control"] == "no-store"
    assert '"input"' not in response.text


@pytest.mark.parametrize("body,status", [
    (b'{"q":"first","q":"second"}', 422),
    (b'{"filters":{"work_items":{"semantic":true,"semantic":false}}}', 422),
    (b"{" + b" " * 16_384, 413), (b"[", 422), (b"[]", 422),
])
def test_json_wire_boundary_is_strict_and_bounded(api, project, body, status):
    response = api.post(path(project), content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"


def test_semantic_uses_shared_admission_and_reports_failure(api, project, work_payload):
    create_work(api, project, work_payload)
    api.app.state.semantic_embedder = DeterministicEmbedder()
    result = search(api, project, q="cache", filters={"work_items": {"semantic": True}})
    assert result["facet_totals"]["work_items"] == 1
    api.app.state.semantic_embedder = FailingEmbedder()
    payload = {"q": "cache", "filters": {"work_items": {"semantic": True}}}
    response = api.post(path(project), json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "semantic_unavailable"
    resources = api.app.state.duplicate_suggestion_resources
    resources.inference_slots = asyncio.Semaphore(0)
    resources.inference_wait_seconds = 0.001
    assert api.post(path(project), json=payload).status_code == 503
    assert search(api, project, q="cache")["total"] == 1
