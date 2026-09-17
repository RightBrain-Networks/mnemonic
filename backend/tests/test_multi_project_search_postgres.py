"""Explicit project unions rank and paginate one corpus with bounded index reuse."""

import json
from time import monotonic
from uuid import uuid4

import pytest
from sqlalchemy import event

from mnemonic_api.artifact_extraction import extract_next_artifact
from mnemonic_api.artifact_tika import ExtractedArtifact

from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_leases_postgres import expire_lease
from .test_semantic_postgres import DeterministicEmbedder, save
from .test_transcript_indexing_postgres import register, run
from .test_unified_search_postgres import mixed as mixed
from .test_unified_search_postgres import search
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def across(api, projects, **payload):
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"] for project in projects], **payload,
    })
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


@pytest.fixture
def other(api):
    response = api.post("/api/v1/projects", json={"name": "Another search project"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("detail", ["compact", "full"])
def test_combined_search_matches_union_and_has_one_global_page(
    api, project, other, mixed, artifact_storage, work_payload, detail,
):
    work = create_work(api, other, work_payload, title="Needle from other project")["work_item"]
    artifact = upload(api, other, filename="needle-other.txt", body=b"needle elsewhere")
    extract(api, artifact_storage)
    payload = {"q": "needle", "fulltext": True, "detail": detail}
    single = [search(api, owner, **payload) for owner in (project, other)]
    expected = {hit["id"] for page in single for hit in page["items"]}
    combined = across(api, [project, other], **payload)
    assert {hit["id"] for hit in combined["items"]} == expected
    assert {work["id"], artifact["id"]} <= expected
    assert combined["total"] == sum(page["total"] for page in single)
    assert combined["applied_filters"]["project_id"] is None
    assert combined["applied_filters"]["project_ids"] == sorted([project["id"], other["id"]])
    assert {row["project_id"] for row in combined["project_coverage"]} == {
        project["id"], other["id"],
    }
    for hit in combined["items"]:
        assert hit["project_id"] == (other["id"] if hit["id"] in {work["id"], artifact["id"]}
                                      else project["id"])
    paged = [across(api, [other, project], **payload, limit=2, offset=offset)
             for offset in range(0, len(expected), 2)]
    assert [hit["id"] for page in paged for hit in page["items"]] == [
        hit["id"] for hit in combined["items"]
    ]
    assert across(api, [project, other], **payload, offset=999)["items"] == []


def test_transcript_current_project_is_the_enrolled_work_owner(
    api, project, other, work_payload, tmp_path, postgres_engine,
):
    work, _, transcript, _ = register(api, other, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    page = across(api, [project, other], q="needle", fulltext=True, facets=["transcripts"])
    assert page["total"] == 1
    hit = page["items"][0]
    assert hit["id"] == transcript["id"]
    assert hit["project_id"] == hit["transcript"]["project_id"] == other["id"]


def test_combined_index_builds_once_and_reuses_reordered_selection(
    api, project, other, mixed, artifact_storage, monkeypatch, record_property,
    tmp_path, work_payload, postgres_engine,
):
    upload(api, other, filename="needle-other.txt", body=b"needle elsewhere")
    extract(api, artifact_storage)
    other_directory = tmp_path / "other"
    other_directory.mkdir()
    work, _, _, _ = register(api, other, work_payload, other_directory)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    builds = {"artifacts": 0, "transcripts": 0}
    for name in builds:
        index = getattr(api.app.state, "artifact_search_index" if name == "artifacts"
                        else "transcript_search_index")
        original = index._build

        def tracked(documents, original=original, name=name):
            builds[name] += 1
            return original(documents)

        monkeypatch.setattr(index, "_build", tracked)
    started = monotonic()
    first = across(api, [project, other], q="needle", fulltext=True)
    record_property("cold_combined_seconds", monotonic() - started)
    started = monotonic()
    second = across(api, [other, project], q="needle", fulltext=True)
    record_property("warm_combined_seconds", monotonic() - started)
    assert first == second
    assert builds == {"artifacts": 1, "transcripts": 1}
    search(api, project, q="needle", fulltext=True)
    assert builds == {"artifacts": 2, "transcripts": 2}
    across(api, [project, other], q="needle", fulltext=True)
    assert builds == {"artifacts": 3, "transcripts": 3}


def test_selected_projects_keep_sensitive_coverage_separate(api, project, other, artifact_storage):
    upload(api, other, filename="private.txt", body=b"classifiedneedle",
           metadata={"sensitive": True})
    extract(api, artifact_storage)
    page = across(api, [project, other], q="classifiedneedle", fulltext=True)
    assert page["total"] == 0 and page["indexing_incomplete"]
    rows = {row["project_id"]: row for row in page["project_coverage"]}
    assert rows[other["id"]]["coverage"]["artifacts"]["sensitive_content_withheld"] == 1
    assert rows[project["id"]]["indexing_incomplete"] is False
    assert rows[other["id"]]["indexing_incomplete"] is True


def test_metadata_only_combined_search_does_not_load_bodies(
    api, project, other, mixed, postgres_engine,
):
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", observe)
    try:
        assert across(api, [project, other], q="needle")["total"] == 3
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observe)
    assert not any("normalized_text" in statement for statement in statements)


@pytest.mark.parametrize("projects", [[], [str(uuid4())] * 2, [str(uuid4()) for _ in range(11)]])
def test_project_selection_is_explicit_unique_and_bounded(api, projects):
    response = api.post("/api/v1/search", json={"project_ids": projects})
    assert response.status_code == 422


def test_missing_project_does_not_become_a_partial_absence_result(api, project):
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"], str(uuid4())], "facets": ["work_items"],
    })
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"
    assert api.post("/api/v1/search", json={}).status_code == 422
    assert api.post(f"/api/v1/projects/{project['id']}/search", json={
        "project_ids": [project["id"]],
    }).status_code == 422


def test_combined_content_budget_is_not_reset_per_project(
    api, project, other, artifact_storage, monkeypatch,
):
    for owner in (project, other):
        upload(api, owner, filename="note.txt", body=b"needle")
        extract(api, artifact_storage)
    monkeypatch.setattr("mnemonic_api.services.multi_search_limits.MAX_ARTIFACT_CONTENT_BYTES", 10)
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"], other["id"]], "q": "needle", "fulltext": True,
        "facets": ["artifacts"],
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "multi_project_search_capacity"
    assert across(api, [project], q="needle", fulltext=True, facets=["artifacts"])["total"] == 1


def test_semantic_search_ranks_one_combined_candidate_pool(api, project, other, work_payload):
    save(api, project, work_payload, title="Other topic", prompt="No semantic marker")
    target = save(api, other, work_payload, title="Durable records",
                  prompt="[dense-target] Preserve facts through restart")
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    payload = {"q": "protect facts after reboot", "facets": ["work_items"],
               "filters": {"work_items": {"semantic": True}}}
    result = across(api, [project, other], **payload)
    assert result["total"] == 2
    assert result["items"][0]["id"] == target["id"]
    assert result["items"][0]["project_id"] == other["id"]
    assert [len(batch) for batch in embedder.document_batches] == [2]
    assert across(api, [other, project], **payload, limit=1)["items"] == result["items"][:1]
    assert [len(batch) for batch in embedder.document_batches] == [2]


def test_work_capacity_is_checked_across_selected_projects_before_hydration(
    api, project, other, work_payload, monkeypatch,
):
    for owner in (project, other):
        create_work(api, owner, work_payload)
    monkeypatch.setattr("mnemonic_api.services.multi_search_limits.MAX_DOCUMENTS", 1)
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"], other["id"]], "facets": ["work_items"],
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "multi_project_search_capacity"
    assert across(api, [project], facets=["work_items"])["total"] == 1


def test_global_facet_groups_do_not_restart_pagination_for_each_project(
    api, project, other, mixed, artifact_storage, work_payload,
):
    create_work(api, other, work_payload, title="Needle other work")
    upload(api, other, filename="needle-other.txt", body=b"needle other body")
    extract(api, artifact_storage)
    payload = {"q": "needle", "fulltext": True, "facet_order": [{"facet": "artifacts"}]}
    complete = across(api, [project, other], **payload)
    assert [hit["facet"] for hit in complete["items"][:2]] == ["artifacts", "artifacts"]
    pages = [across(api, [other, project], **payload, offset=offset, limit=2)
             for offset in range(0, complete["total"], 2)]
    assert [hit["id"] for page in pages for hit in page["items"]] == [
        hit["id"] for hit in complete["items"]
    ]


def test_duplicate_projects_report_the_actual_field_and_rule(api, project):
    response = api.post("/api/v1/search", json={"project_ids": [project["id"]] * 2})
    assert response.status_code == 422
    assert response.json()["detail"] == [{
        "type": "search_projects_must_be_unique", "loc": ["body", "project_ids"],
        "msg": "project_ids must contain unique projects.",
    }]


@pytest.mark.parametrize("facet", ["work_items", "artifacts"])
def test_metadata_budget_counts_all_hydrated_rows_including_sensitive_properties(
    api, project, other, work_payload, artifact_storage, monkeypatch, facet,
):
    for owner in (project, other):
        if facet == "work_items":
            create_work(api, owner, work_payload)
        else:
            upload(api, owner, filename="private.txt", body=b"needle",
                   metadata={"sensitive": True})
            extract(api, artifact_storage)
    monkeypatch.setattr("mnemonic_api.services.multi_search_limits.MAX_METADATA_BYTES", 1)
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"], other["id"]], "facets": [facet],
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "multi_project_search_capacity"


def test_sensitive_metadata_bytes_still_count_toward_hydration_budget(
    api, project, other, artifact_storage, monkeypatch,
):
    upload(api, project, filename="private.txt", body=b"needle",
                      metadata={"sensitive": True})
    class PropertyExtractor:
        def extract(self, content, *, filename, size_bytes):
            return ExtractedArtifact(content.read().decode(),
                                     {"large_property": ["n" * 2000]}, False)

    assert extract_next_artifact(api.app.state.session_factory, artifact_storage,
                                 PropertyExtractor())
    monkeypatch.setattr("mnemonic_api.services.multi_search_limits.MAX_METADATA_BYTES", 1000)
    response = api.post("/api/v1/search", json={
        "project_ids": [project["id"], other["id"]], "facets": ["artifacts"],
    })
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "multi_project_search_capacity"


@pytest.mark.parametrize("query", ["", "needle"])
def test_normalization_coverage_stays_with_owner_when_kind_filter_omits_record(
    api, project, other, work_payload, tmp_path, postgres_engine, query,
):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"type": "future-event", "payload": "needle"}) + "\n")
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    result = across(api, [project, other], q=query, fulltext=True, facets=["transcripts"],
                    filters={"transcripts": {"content_kinds": ["human_text"]}})
    assert result["total"] == 0 and result["indexing_incomplete"]
    coverage = {row["project_id"]: row for row in result["project_coverage"]}
    assert coverage[project["id"]]["indexing_incomplete"] is True
    assert coverage[other["id"]]["indexing_incomplete"] is False
