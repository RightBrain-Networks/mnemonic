"""Compact discovery preserves selected identities, evidence and bounded hydration."""

import json

import pytest

from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_semantic_postgres import DeterministicEmbedder
from .test_transcript_search_postgres import seed_transcripts
from .test_work_items_postgres import collection, create_work

pytestmark = pytest.mark.postgres


def work_page(api, project, **params):
    response = api.get(collection(project), params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("view", ["full", "roots"])
def test_compact_work_preserves_page_identity_order_and_hierarchy(api, project, work_payload, view):
    for title in ["First compact work", "Second compact work"]:
        create_work(api, project, work_payload, title=title)
    compact = work_page(api, project, view=view)
    full = work_page(api, project, view=view, detail="full")
    assert compact["detail"] == "compact" and full["detail"] == "full"
    assert compact["limit"] == full["limit"] == 20
    assert compact["total"] == full["total"] == 2
    assert [row["id"] for row in compact["items"]] == [
        row["summary"]["work_item"]["id"] for row in full["items"]
    ]
    for rank, (pointer, detailed) in enumerate(
        zip(compact["items"], full["items"], strict=True), 1
    ):
        assert pointer["rank"] == rank
        assert pointer["canonical_work_item_id"] == pointer["id"]
        assert pointer["display_state"] == detailed["summary"]["readiness"]["display_state"]
        assert pointer["search_status"] == "pending"
        assert not {"summary", "current_context", "readiness", "matched_member"} & pointer.keys()
        if view == "roots":
            assert pointer["presentation"] == detailed["presentation"]
            assert pointer["self_matches_filter"] == detailed["self_matches_filter"]
            assert pointer["has_matching_descendants"] == detailed["has_matching_descendants"]
    later = work_page(api, project, view=view, offset=1, limit=1)
    assert later["items"][0]["id"] == compact["items"][1]["id"]
    assert later["items"][0]["rank"] == 2


def test_compact_work_does_not_hydrate_full_summaries(api, project, work_payload, monkeypatch):
    create_work(api, project, work_payload)

    def forbidden(*args, **kwargs):
        pytest.fail("Compact discovery must not hydrate a full work summary")

    monkeypatch.setattr("mnemonic_api.services.work_search._summaries_with_ancestry", forbidden)
    monkeypatch.setattr("mnemonic_api.services.search_work._summaries_with_ancestry", forbidden)
    assert work_page(api, project)["total"] == 1
    unified = api.post(f"/api/v1/projects/{project['id']}/search", json={"q": "cache"})
    assert unified.status_code == 200, unified.text
    assert unified.json()["items"][0]["work_item"]["title"] == work_payload["title"]


def test_semantic_hydration_reads_only_the_selected_page(api, project, work_payload, monkeypatch):
    for index in range(5):
        create_work(api, project, work_payload, title=f"Candidate {index}")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    from mnemonic_api.services import work_search
    observed = []
    original = work_search._summaries_with_ancestry

    def record(database, project_id, work_items, **kwargs):
        observed.append(len(work_items))
        return original(database, project_id, work_items, **kwargs)

    monkeypatch.setattr(work_search, "_summaries_with_ancestry", record)
    page = work_page(api, project, q="paraphrase", semantic=True, limit=1, detail="full")
    assert page["total"] == 5 and len(page["items"]) == 1
    assert observed == [1]
    page = work_page(api, project, q="paraphrase", semantic=True, limit=1)
    assert page["total"] == 5 and len(page["items"]) == 1
    assert observed == [1]


def test_twenty_compact_work_hits_save_three_quarters_of_response_bytes(
    api, project, work_payload, monkeypatch,
):
    # Measure compaction independently of the separate result page budget.
    monkeypatch.setattr("mnemonic_api.search_pagination.SEARCH_PAGE_MAX_BYTES", 1024 * 1024)
    for index in range(20):
        create_work(api, project, work_payload, title=f"Search result {index}",
                    summary="Search context supporting a concrete decision. " * 25)
    compact = work_page(api, project)
    full = work_page(api, project, detail="full")
    assert len(compact["items"]) == len(full["items"]) == 20
    assert len(json.dumps(compact).encode()) <= len(json.dumps(full).encode()) * 0.25


def test_compact_artifacts_preserve_snippet_and_coverage(api, project, artifact_storage):
    upload(api, project, filename="report.txt", body=b"A precise needle inside this document")
    extract(api, artifact_storage)
    url = f"/api/v1/projects/{project['id']}/artifacts/search-content"
    payload = {"q": "needle", "fulltext": True}
    compact = api.post(url, json=payload).json()
    full = api.post(url, json={**payload, "detail": "full"}).json()
    assert compact["detail"] == "compact" and full["detail"] == "full"
    assert compact["total"] == full["total"] == 1
    assert compact["indexing"] == full["indexing"]
    assert compact["items"][0]["snippet"] == full["items"][0]["snippet"]
    assert compact["items"][0]["matched_fields"] == full["items"][0]["matched_fields"]
    assert compact["items"][0]["artifact"]["id"] == full["items"][0]["artifact"]["id"]
    assert "sha256" not in compact["items"][0]["artifact"]
    assert "metadata" not in compact["items"][0]["artifact"]["extraction"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_compact_transcripts_preserve_snippet_and_coverage(api, project, method):
    seed_transcripts(api, project, "A precise needle in the transcript")
    url = f"/api/v1/projects/{project['id']}/transcripts"
    payload = {"query": "needle", "fulltext": True}
    if method == "post":
        url += "/search-content"
    request = getattr(api, method)
    argument = "params" if method == "get" else "json"
    compact = request(url, **{argument: payload}).json()
    full = request(url, **{argument: {**payload, "detail": "full"}}).json()
    assert compact["detail"] == "compact" and full["detail"] == "full"
    assert compact["total"] == full["total"] == 1
    assert compact["indexing_incomplete"] == full["indexing_incomplete"]
    assert compact["items"][0]["id"] == full["items"][0]["id"]
    assert compact["items"][0]["snippet"] == full["items"][0]["snippet"]
    assert "sha256" not in compact["items"][0]
    assert "source_path" not in compact["items"][0]



def test_compact_alias_matches_retain_identity_and_canonical_scope(api, project, work_payload):
    from .test_duplicate_handling_postgres import merge_work

    alias = create_work(api, project, work_payload, title="Quokka unique evidence")["work_item"]
    root = create_work(api, project, work_payload, title="Canonical objective")["work_item"]
    merge_work(api, project, alias, root)
    compact = work_page(api, project, q="quokka")
    full = work_page(api, project, q="quokka", detail="full")
    assert compact["total"] == full["total"] == 1
    pointer = compact["items"][0]
    assert pointer["id"] == pointer["canonical_work_item_id"] == root["id"]
    assert pointer["matched_member"] == full["items"][0]["matched_member"]
    assert pointer["matched_member"]["id"] == alias["id"]
    aliases = work_page(api, project, duplicate_scope="aliases", canonical_work_item_id=root["id"])
    assert aliases["total"] == 1
    assert aliases["items"][0]["id"] == alias["id"]
    assert aliases["items"][0]["canonical_work_item_id"] == root["id"]
    assert aliases["items"][0]["display_state"] == "duplicate"
    assert "matched_member" not in aliases["items"][0]


def test_semantic_page_hydration_keeps_snapshot_without_blocking_writers(
    api, project, work_payload,
):
    work = create_work(api, project, work_payload, title="Original title")["work_item"]
    calls = []

    class ConcurrentWriter(DeterministicEmbedder):
        def embed_documents(self, texts):
            # This write takes Mnemonic's normal project lock while inference has
            # an open read-only snapshot. It must finish without waiting on search.
            response = api.patch(collection(project) + "/" + work["id"], json={
                "expected_version": work["version"], "title": "Committed concurrent title",
            })
            assert response.status_code == 200, response.text
            calls.append(response.json())
            return super().embed_documents(texts)

    api.app.state.semantic_embedder = ConcurrentWriter()
    page = work_page(api, project, q="durability", semantic=True)
    assert len(calls) == 1
    assert page["items"][0]["title"] == "Original title"
    assert work_page(api, project)["items"][0]["title"] == "Committed concurrent title"


def test_unified_compact_full_projection_preserves_global_ranking_and_coverage(
    api, project, work_payload, artifact_storage,
):
    create_work(api, project, work_payload, title="Needle work")
    upload(api, project, filename="needle.txt", body=b"Needle document")
    extract(api, artifact_storage)
    seed_transcripts(api, project, "Needle transcript")
    url = f"/api/v1/projects/{project['id']}/search"
    payload = {"q": "needle", "fulltext": True, "limit": 2, "offset": 1}
    compact = api.post(url, json=payload).json()
    full = api.post(url, json={**payload, "detail": "full"}).json()
    assert compact["detail"] == "compact" and full["detail"] == "full"
    for key in ("total", "limit", "offset", "facet_totals", "coverage", "indexing_incomplete",
                "applied_filters", "query_interpretation", "warnings", "search_scope"):
        assert compact[key] == full[key]
    assert [(hit["facet"], hit["id"], hit["score"]) for hit in compact["items"]] == [
        (hit["facet"], hit["id"], hit["score"]) for hit in full["items"]
    ]



def test_unified_work_rank_follows_effective_sort_before_global_pagination(
    api, project, work_payload,
):
    works = [create_work(api, project, work_payload, title=f"Needle work {index}")["work_item"]
             for index in range(3)]
    response = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "needle", "sort": {"by": "created_at", "direction": "asc"},
        "limit": 1, "offset": 1,
    })
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["work_rank_scope"] == "work_items"
    assert page["items"][0]["id"] == works[1]["id"]
    assert page["items"][0]["work_item"]["rank"] == 2
