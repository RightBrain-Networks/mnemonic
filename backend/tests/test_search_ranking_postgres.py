"""Ranking, inference and disposable cache health are separate observable facts."""

import logging

import pytest

from .test_duplicate_suggestions_postgres import DeterministicEmbedder, save, suggest
from .test_search_compact_postgres import work_page
from .test_transcript_query_intent_postgres import query
from .test_transcript_search_postgres import seed_transcripts

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def enable_timing_logger(monkeypatch):
    # Alembic's fixture logging configuration disables already imported loggers.
    monkeypatch.setattr(logging.getLogger("mnemonic_api.search_timing"), "disabled", False)


@pytest.mark.parametrize("detail", ["compact", "full"])
def test_work_scores_and_totals_label_lexical_semantic_and_browse(
    api, project, work_payload, detail,
):
    save(api, project, work_payload, title="Needle exact candidate", prompt="needle [dense-target]")
    save(api, project, work_payload, title="Unrelated corpus member", prompt="other material")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    lexical = work_page(api, project, q="needle", detail=detail)
    assert lexical["total"] == 1 and lexical["total_kind"] == "lexical_matches"
    assert lexical["score_type"] == "postgresql_lexical"
    assert lexical["items"][0]["score"] > 0
    semantic = work_page(api, project, q="needle", semantic=True, detail=detail)
    assert semantic["total"] == 2 and semantic["total_kind"] == "ranked_candidates"
    assert semantic["score_type"] == "hybrid_reciprocal_rank"
    assert semantic["semantic"]["inference"] == {"status": "completed", "reason": None}
    assert semantic["semantic"]["candidate_scope"] == "full_scope"
    assert all(item["score_type"] == "hybrid_reciprocal_rank" and item["score"] > 0
               for item in semantic["items"])
    assert [item["rank"] for item in semantic["items"]] == [1, 2]
    browse = work_page(api, project, detail=detail)
    assert browse["total_kind"] == "browsed_records" and browse["score_type"] == "none"
    assert all(item["score"] == 0 and item["score_type"] == "none" for item in browse["items"])


def test_unified_total_and_score_types_distinguish_ranked_and_unsearched_facets(
    api, project, work_payload,
):
    save(api, project, work_payload, title="Needle candidate")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    response = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "needle", "facets": ["work_items", "artifacts"],
        "filters": {"work_items": {"semantic": True}}})
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total_kind"] == "mixed" and page["score_type"] == "unified_reciprocal_rank"
    assert page["facet_total_kinds"] == {
        "work_items": "ranked_candidates", "artifacts": "lexical_matches", "transcripts": None}
    assert page["facet_score_types"] == {
        "work_items": "hybrid_reciprocal_rank", "artifacts": "tantivy_relevance",
        "transcripts": None}
    assert page["items"][0]["rank"] == 1
    assert page["items"][0]["score_type"] == "unified_reciprocal_rank"
    assert page["items"][0]["work_item"]["score_type"] == "hybrid_reciprocal_rank"


def test_transcript_ranking_separates_native_tantivy_and_literal_presence(api, project):
    seed_transcripts(api, project, "ordinary body")
    term = query(api, project, "synthetic", fulltext=False)
    literal = query(api, project, "/synthetic/archive/", fulltext=False, query_mode="literal")
    assert term["score_type"] == term["items"][0]["score_type"] == "tantivy_relevance"
    assert literal["score_type"] == literal["items"][0]["score_type"] == "literal_presence"
    assert literal["total_kind"] == "lexical_matches" and literal["items"][0]["rank"] == 1


def test_saturated_inference_reports_same_safe_reason_for_search_and_duplicate_check(
    api, project, work_payload,
):
    save(api, project, work_payload, title="Cache repair candidate")
    resources = api.app.state.duplicate_suggestion_resources
    resources.inference.slots = 0
    resources.inference.wait_seconds = 0.001
    page = suggest(api, project).json()
    failure = api.get(f"/api/v1/projects/{project['id']}/work-items",
                      params={"q": "cache", "semantic": True})
    assert failure.status_code == 503
    assert page["semantic"]["inference"] == (
        failure.json()["detail"]["context"]["semantic"]["inference"])
    assert page["semantic"]["inference"]["reason"] == "capacity_exhausted"
    assert page["semantic"]["comparison_incomplete"] is True
    assert page["semantic"]["retry"] == {"max_attempts": 1, "after_seconds": 1}
    assert failure.headers["retry-after"] == "1"
    # Advisory unavailability never acquires authority over subsequent creation.
    assert save(api, project, work_payload, title="A subsequent independent item")


@pytest.mark.parametrize("exception,reason", [(RuntimeError, "model_failure"),
                                               (TimeoutError, "deadline_exceeded")])
def test_duplicate_inference_failures_report_incomplete_comparison_without_content_logs(
    api, project, work_payload, caplog, exception, reason,
):
    class Broken:
        def embed_query(self, text):
            raise exception("private query or provider response must never be logged")

    save(api, project, work_payload, title="Cache repair candidate")
    api.app.state.semantic_embedder = Broken()
    with caplog.at_level(logging.INFO, logger="mnemonic_api.search_timing"):
        response = suggest(api, project)
    assert response.status_code == 200, response.text
    semantic = response.json()["semantic"]
    assert semantic["inference"] == {"status": "unavailable", "reason": reason}
    assert semantic["comparison_incomplete"] is True
    assert "private query or provider response" not in caplog.text
    timings = [record for record in caplog.records if hasattr(record, "search_phase")]
    assert any(record.search_phase == "query_embedding" and record.search_outcome == "failed"
               for record in timings)


def test_cold_warm_duplicate_cache_and_timings_are_observable(api, project, work_payload, caplog):
    for title in ["Cache repair candidate", "Cache repair companion"]:
        save(api, project, work_payload, title=title, prompt="cache [dense-target]")
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    with caplog.at_level(logging.INFO, logger="mnemonic_api.search_timing"):
        cold = suggest(api, project).json()
        warm = suggest(api, project).json()
    assert cold["semantic"]["candidate_scope"] == "lexical_shortlist"
    assert cold["semantic"]["cache_refresh"]["status"] == "completed"
    assert warm["semantic"]["candidate_scope"] == "full_scope"
    assert warm["semantic"]["cache_refresh"]["status"] == "not_needed"
    assert warm["semantic"]["comparison_incomplete"] is False
    assert len(embedder.document_batches) == 1
    timings = [record for record in caplog.records if hasattr(record, "search_phase")]
    assert {record.search_phase for record in timings} >= {
        "request_queue", "inference_queue", "query_embedding", "candidate_selection",
        "document_inference", "cache_refresh", "total"}
    assert all(record.duration_ms >= 0 for record in timings)


@pytest.mark.parametrize("endpoint", ["work", "unified", "duplicates"])
def test_cache_write_failure_preserves_completed_ranking(
    api, project, work_payload, monkeypatch, endpoint,
):
    for title in ["Cache repair target", "Cache repair companion"]:
        save(api, project, work_payload, title=title, prompt="cache [dense-target]")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    module = {"work": "mnemonic_api.application.routes.work_search.persist_embedding_updates",
              "unified": "mnemonic_api.services.search.persist_embedding_updates",
              "duplicates": "mnemonic_api.services.duplicate_suggestions._persist_cache_updates"}

    def broken(*args, **kwargs):
        raise RuntimeError("disposable cache failure")

    def read_page():
        if endpoint == "work":
            return work_page(api, project, q="cache", semantic=True)
        if endpoint == "unified":
            response = api.post(f"/api/v1/projects/{project['id']}/search", json={
                "q": "cache", "facets": ["work_items"],
                "filters": {"work_items": {"semantic": True}}})
        else:
            response = suggest(api, project)
        assert response.status_code == 200, response.text
        return response.json()

    with monkeypatch.context() as patch:
        patch.setattr(module[endpoint], broken)
        first = read_page()
    second = read_page()
    assert second["semantic"]["cache_refresh"]["status"] == "completed"
    assert first["items"] == second["items"]
    assert first["semantic"]["inference"]["status"] == "completed"
    assert first["semantic"]["cache_refresh"] == {
        "status": "failed", "reason": "cache_refresh_failed"}
    assert len(first["items"]) == 2
    assert [item["rank"] for item in first["items"]] == [1, 2]


def test_partial_vectors_are_distinct_from_inference_failure(api, project, work_payload):
    for title in ["Cache repair target", "Cache repair companion"]:
        save(api, project, work_payload, title=title, prompt="cache [dense-target]")
    api.app.state.settings.duplicate_suggestion_missing_vector_limit = 1
    api.app.state.semantic_embedder = DeterministicEmbedder()
    response = suggest(api, project)
    assert response.status_code == 200, response.text
    semantic = response.json()["semantic"]
    assert semantic["inference"] == {"status": "completed", "reason": None}
    assert semantic["candidate_scope"] == "lexical_shortlist"
    assert semantic["partial_vectors"] is True and semantic["comparison_incomplete"] is True
    assert semantic["retry"] is None
