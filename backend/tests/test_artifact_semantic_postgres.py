"""Semantic evidence, incomplete coverage, sensitive exclusion and revision-pinned reads."""

import hashlib
from uuid import UUID

import pytest
from sqlalchemy import delete, event, select, update

from mnemonic_api import artifact_passages as passages
from mnemonic_api.models import ArtifactExtraction

from .test_artifact_passage_jobs_postgres import PassageEmbedder, drain, step
from .test_artifact_search_postgres import extract, search
from .test_artifacts_postgres import collection, headers, upload

pytestmark = pytest.mark.postgres


@pytest.fixture
def artifact_storage(api, tmp_path):
    from mnemonic_api.artifact_storage import ArtifactStorage

    storage = ArtifactStorage(tmp_path / "semantic-artifacts", max_bytes=2_000_000)
    api.app.state.artifact_storage = storage
    api.app.state.settings.artifact_max_bytes = storage.max_bytes
    api.app.state.semantic_embedder = PassageEmbedder()
    return storage


def semantic(api, project, **options):
    return search(api, project, "how should the remote endpoint validate its session",
                  semantic=True,
                  fulltext=True, **options)


def unified(api, project, **options):
    result = api.post(f"/api/v1/projects/{project['id']}/search", json={
        "q": "how should the remote endpoint validate its session", "fulltext": True,
        "facets": ["artifacts"], "filters": {"artifacts": {"semantic": True}}, **options})
    assert result.status_code == 200, result.text
    return result.json()


def test_paraphrase_finds_a_passage_near_end_and_pin_recovers_exact_evidence(
    api, project, artifact_storage,
):
    body = "Unrelated background. " * 30_000 + " authorize listener cookies"
    artifact = upload(api, project, body=body.encode(), filename="large-design.txt")
    unrelated = upload(api, project, body=b"Daily lunch menu", filename="menu.txt")
    extract(api, artifact_storage)
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    result = semantic(api, project, detail="compact")
    assert result["total"] == 2 and result["total_kind"] == "ranked_candidates"
    hit = result["items"][0]
    assert hit["artifact"]["id"] == artifact["id"]
    assert result["items"][1]["artifact"]["id"] == unrelated["id"]
    assert result["match_mode"] == "semantic_passages"
    assert hit["evidence"] == "semantic" and hit["matched_fields"] == ["content"]
    locator = hit["passage"]
    assert locator["start_offset"] > 600_000 and locator["cosine_similarity"] == 1.0
    assert locator["score_is_probability"] is False
    text = api.get(collection(project) + "/" + artifact["id"] + "/text", params={
        "expected_revision": locator["artifact_revision"],
        "expected_text_sha256": locator["text_sha256"], "offset": locator["start_offset"],
        "limit": locator["end_offset"] - locator["start_offset"]})
    assert text.status_code == 200, text.text
    assert text.json()["text"] == body[locator["start_offset"]:locator["end_offset"]]
    assert "authorize listener cookies" in text.json()["text"]
    assert text.json()["text_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    mixed = unified(api, project)
    assert mixed["items"][0]["id"] == artifact["id"]
    assert mixed["coverage"]["artifacts"]["embedding"]["state"] == "ready"


def test_pending_passages_report_partial_coverage_without_exposing_partial_rankings(
    api, project, artifact_storage,
):
    upload(api, project, body=b"background " * 5_000 + b" authorize listener cookies")
    extract(api, artifact_storage)
    before = semantic(api, project)
    assert before["total"] == 0 and before["embedding"]["pending"] == 1
    assert before["embedding"]["state"] == "incomplete"
    assert before["total_kind"] == "ranked_candidates"
    step(api, PassageEmbedder())
    partial = unified(api, project)
    assert partial["total"] == 0 and partial["indexing_incomplete"] is True
    assert partial["coverage"]["artifacts"]["embedding"]["pending"] == 1
    drain(api, PassageEmbedder())
    assert semantic(api, project)["total"] == 1


def test_metadata_only_never_selects_passage_vectors_or_extracted_bodies(
    api, project, artifact_storage,
):
    upload(api, project, filename="needle.txt", body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    statements = []
    engine = api.app.state.session_factory.kw["bind"]

    def capture(_connection, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        result = search(api, project, "needle")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result["total"] == 1 and result["embedding"] is None
    assert not any("artifact_passages" in sql or "normalized_text" in sql for sql in statements)


def test_sensitive_body_vectors_and_terms_do_not_contribute_to_broad_search(
    api, project, artifact_storage,
):
    artifact = upload(api, project, body=b"authorize listener cookies secretbody",
                      metadata={"sensitive": True})
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    result = semantic(api, project)
    assert result["total"] == 0 and result["sensitive_content_withheld"] == 1
    assert result["embedding"]["withheld"] == 1 and result["embedding"]["ready"] == 0
    result = unified(api, project, q="secretbody", filters={"artifacts": {
        "semantic": True, "artifact_id": artifact["id"]}})
    assert result["total"] == 0 and result["indexing_incomplete"] is True
    assert result["term_diagnostics"][0]["matches"]["artifacts"] == 0


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_old_passages_cannot_survive_replacement_or_deletion(
    api, project, artifact_storage, mutation,
):
    artifact = upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    assert semantic(api, project)["total"] == 1
    path = collection(project) + "/" + artifact["id"]
    if mutation == "replace":
        changed = api.put(path + "/content", content=b"unrelated replacement",
                          headers=headers({"filename": artifact["filename"]}, revision=1))
    else:
        changed = api.delete(path, headers=headers(revision=1))
    assert changed.status_code == 200, changed.text
    with api.app.state.session_factory() as database:
        assert database.execute(select(passages.INDEXES)).first() is None
        assert database.execute(select(passages.PASSAGES)).first() is None
    assert semantic(api, project, include_deleted=True)["total"] == 0
    if mutation == "replace":
        extract(api, artifact_storage)
        drain(api, PassageEmbedder())
        passage = semantic(api, project)["items"][0]["passage"]
        assert passage["artifact_revision"] == 2 and passage["cosine_similarity"] == 0


def test_text_hash_change_invalidates_existing_locator_and_passage_cache(
    api, project, artifact_storage,
):
    artifact = upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    locator = semantic(api, project)["items"][0]["passage"]
    with api.app.state.session_factory.begin() as database:
        database.execute(update(ArtifactExtraction).where(
            ArtifactExtraction.artifact_id == UUID(artifact["id"])).values(
                normalized_text="changed extraction"))
    result = api.get(collection(project) + "/" + artifact["id"] + "/text", params={
        "expected_revision": 1, "expected_text_sha256": locator["text_sha256"]})
    assert result.status_code == 409 and result.json()["detail"]["code"] == "artifact_text_changed"
    assert semantic(api, project)["embedding"]["pending"] == 1


def test_cache_rebuild_uses_new_delivery_generation_after_completed_jobs(
    api, project, artifact_storage,
):
    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    first = semantic(api, project)["items"][0]["passage"]
    with api.app.state.session_factory.begin() as database:
        identity = database.scalar(select(passages.INDEXES.c.id))
        database.execute(delete(passages.INDEXES))
    drain(api, PassageEmbedder())
    with api.app.state.session_factory() as database:
        assert database.scalar(select(passages.INDEXES.c.id)) != identity
    assert semantic(api, project)["items"][0]["passage"] == first


def semantic_request(project, route):
    payload = {"q": "request", "fulltext": True}
    if route == "dedicated":
        payload["semantic"] = True
        return collection(project) + "/search-content", payload
    payload.update(facets=["artifacts"], filters={"artifacts": {"semantic": True}})
    if route == "workspace":
        payload["project_ids"] = [project["id"]]
        return "/api/v1/search", payload
    return f"/api/v1/projects/{project['id']}/search", payload


@pytest.mark.parametrize("route", ["dedicated", "project", "workspace"])
@pytest.mark.parametrize("exception,reason", [(RuntimeError, "model_failure"),
                                              (TimeoutError, "deadline_exceeded")])
def test_query_inference_failure_is_unavailable_instead_of_empty_result(
    api, project, artifact_storage, caplog, route, exception, reason,
):
    class FailingEmbedder(PassageEmbedder):
        def embed_query(self, _text):
            raise exception("private native provider message")

    api.app.state.semantic_embedder = FailingEmbedder()
    path, payload = semantic_request(project, route)
    result = api.post(path, json=payload)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "semantic_unavailable"
    semantic = result.json()["detail"]["context"]["semantic"]
    assert semantic["inference"] == {"status": "unavailable", "reason": reason}
    assert semantic["comparison_incomplete"] is True
    assert semantic["retry"] == {"max_attempts": 1, "after_seconds": 1}
    assert result.headers["retry-after"] == "1"
    assert "private native" not in result.text and "private native" not in caplog.text


def test_restore_excludes_vectors_and_rebuilds_despite_completed_delivery_receipts(
    api, project, artifact_storage, postgres_engine,
):
    import io

    from mnemonic_backup.archive import export_project, restore_project
    from mnemonic_backup.archive_format import read_archive

    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    before = semantic(api, project)["items"][0]["passage"]
    backup = io.BytesIO()
    export_project(postgres_engine, UUID(project["id"]), backup)
    _, rows = read_archive(io.BytesIO(backup.getvalue()), 10_000_000)
    assert "artifact_passages" not in rows and "artifact_passage_indexes" not in rows
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(backup.getvalue()))
    with api.app.state.session_factory() as database:
        assert database.execute(select(passages.PASSAGES)).first() is None
    pending = semantic(api, project)
    assert pending["total"] == 0 and pending["embedding"]["pending"] == 1
    drain(api, PassageEmbedder())
    assert semantic(api, project)["items"][0]["passage"] == before


def test_model_or_chunk_configuration_change_hides_old_vectors_and_rebuilds(
    api, project, artifact_storage, monkeypatch,
):
    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    first = semantic(api, project)["items"][0]["passage"]
    monkeypatch.setattr(passages, "CHUNK_CONFIG", "characters-test-v2")
    assert semantic(api, project)["embedding"]["pending"] == 1
    drain(api, PassageEmbedder())
    second = semantic(api, project)["items"][0]["passage"]
    assert second["chunk_config"].startswith("characters-test-v2:")
    assert second["passage_id"] != first["passage_id"]


def test_vector_search_capacity_is_an_explicit_error(api, project, artifact_storage, monkeypatch):
    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    monkeypatch.setattr("mnemonic_api.services.artifact_semantic.MAX_SEARCH_PASSAGES", 0)
    result = api.post(collection(project) + "/search-content", json={
        "q": "paraphrase", "semantic": True, "fulltext": True})
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "artifact_semantic_capacity"


@pytest.mark.parametrize(("path", "payload", "code", "field"), [
    ("/artifacts/search-content", {"q": "text", "semantic": True},
     "artifact_semantic_requires_fulltext", "fulltext"),
    ("/search", {"q": "text", "filters": {"artifacts": {"semantic": True}}},
     "artifact_semantic_requires_fulltext", "fulltext"),
    ("/search", {"q": "", "fulltext": True, "filters": {"artifacts": {"semantic": True}}},
     "artifact_semantic_requires_query_and_facet", "semantic"),
    ("/search", {"q": "text", "fulltext": True, "facets": ["work_items"],
                 "filters": {"artifacts": {"semantic": True}}},
     "artifact_semantic_requires_query_and_facet", "semantic"),
])
def test_semantic_preconditions_report_reviewed_retryable_field_rules(
    api, project, artifact_storage, path, payload, code, field,
):
    result = api.post(f"/api/v1/projects/{project['id']}" + path, json=payload)
    assert result.status_code == 422, result.text
    error = result.json()["detail"][0]
    assert error["type"] == code and error["loc"] == ["body", field]


def test_operator_rebuild_is_project_scoped_and_invalidates_delivery_generations(
    api, project, artifact_storage,
):
    from mnemonic_api.artifact_passage_rebuild import reset_artifact_passages

    other = api.post("/api/v1/projects", json={"name": "Other project"}).json()
    upload(api, project, body=b"authorize listener cookies")
    upload(api, other, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    with api.app.state.session_factory.begin() as database:
        assert reset_artifact_passages(database, UUID(project["id"])) == 1
    assert semantic(api, project)["embedding"]["pending"] == 1
    assert semantic(api, other)["embedding"]["ready"] == 1
    drain(api, PassageEmbedder())
    assert semantic(api, project)["embedding"]["ready"] == 1


@pytest.mark.parametrize("route", ["dedicated", "project", "workspace"])
def test_artifact_semantic_requests_share_bounded_inference_admission(
    api, project, artifact_storage, route,
):
    import asyncio

    class NeverEmbedder:
        def embed_query(self, _text):
            raise AssertionError("Inference ran without capacity")

    resources = api.app.state.duplicate_suggestion_resources
    resources.inference_slots = asyncio.Semaphore(0)
    resources.inference_wait_seconds = 0.001
    api.app.state.semantic_embedder = NeverEmbedder()
    path, payload = semantic_request(project, route)
    response = api.post(path, json=payload)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "semantic_unavailable"
    semantic = response.json()["detail"]["context"]["semantic"]
    assert semantic["inference"] == {"status": "unavailable", "reason": "capacity_exhausted"}
    assert semantic["comparison_incomplete"] is True
    assert semantic["retry"] == {"max_attempts": 1, "after_seconds": 1}
    assert response.headers["retry-after"] == "1"
    payload["fulltext"] = False
    assert api.post(path, json=payload).status_code == 422


def test_dense_unicode_tail_is_embedded_and_retrievable_without_silent_token_truncation(
    api, project, artifact_storage,
):
    from .artifact_passage_fixtures import document_tokenizer

    body = "雪" * 1400 + " needle " + "界e\u0301😃" * 1500
    native = document_tokenizer()
    # The previous 1,500-character chunk would silently lose the target tail.
    assert "needle" not in native.encode(body[:1500]).tokens

    class DenseEmbedder(PassageEmbedder):
        def embed_documents(self, texts):
            encoded = native.encode_batch(texts)
            assert all(not item.overflowing for item in encoded)
            return [[1.0, 0.0] if "needle" in item.tokens else [0.0, 1.0] for item in encoded]

    artifact = upload(api, project, filename="unicode.txt", body=body.encode())
    extract(api, artifact_storage)
    embedder = DenseEmbedder()
    api.app.state.semantic_embedder = embedder
    drain(api, embedder)
    hit = semantic(api, project)["items"][0]
    assert hit["artifact"]["id"] == artifact["id"] and hit["passage"]["cosine_similarity"] == 1
    passage = hit["passage"]
    assert "needle" in body[passage["start_offset"]:passage["end_offset"]]
    assert passage["token_count"] <= passage["token_limit"] == 512
    with api.app.state.session_factory() as database:
        rows = database.execute(select(passages.PASSAGES).order_by(passages.PASSAGES.c.ordinal))
        covered = 0
        for row in rows:
            assert row.start_offset <= covered
            assert row.token_count <= 512
            covered = row.end_offset
        assert covered == len(body)


def test_changed_model_token_window_rebuilds_a_previously_ready_generation(
    api, project, artifact_storage,
):
    from mnemonic_api.artifact_passage_jobs import enqueue_artifact_passage_jobs

    from .artifact_passage_fixtures import passage_policy

    upload(api, project, body=("雪" * 1000 + " needle").encode())
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    previous = semantic(api, project)["items"][0]["passage"]["chunk_config"]

    class SmallerWindowEmbedder(PassageEmbedder):
        def passage_tokenizer(self):
            return passage_policy(64)

    api.app.state.semantic_embedder = SmallerWindowEmbedder()
    assert semantic(api, project)["embedding"]["pending"] == 1
    # Enroll using the worker's new effective tokenizer, not the old test default.
    with api.app.state.session_factory.begin() as database:
        enqueue_artifact_passage_jobs(database, passage_policy(64))
        assert database.scalar(select(passages.INDEXES.c.chunk_config)) != previous
        assert database.scalar(select(passages.INDEXES.c.token_limit)) == 64
    drain(api, SmallerWindowEmbedder())
    rebuilt = semantic(api, project)["items"][0]["passage"]
    assert rebuilt["chunk_config"] != previous
    assert rebuilt["token_count"] <= rebuilt["token_limit"] == 64


def test_multi_project_passages_share_ranking_and_retain_embedding_coverage(
    api, project, artifact_storage,
):
    other = api.post("/api/v1/projects", json={"name": "Other semantic project"}).json()
    unrelated = upload(api, project, filename="menu.txt", body=b"Daily lunch menu")
    target = upload(api, other, filename="listener.txt", body=b"authorize listener cookies")
    upload(api, other, filename="secret.txt", body=b"private unrelated text",
           metadata={"sensitive": True})
    for _ in range(3):
        extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    payload = {"project_ids": [project["id"], other["id"]],
               "q": "how should the remote endpoint validate its session", "fulltext": True,
               "facets": ["artifacts"], "filters": {"artifacts": {"semantic": True}}}
    response = api.post("/api/v1/search", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [hit["id"] for hit in result["items"]] == [target["id"], unrelated["id"]]
    assert result["items"][0]["project_id"] == other["id"]
    rows = {row["project_id"]: row for row in result["project_coverage"]}
    assert rows[project["id"]]["coverage"]["artifacts"]["embedding"]["ready"] == 1
    assert rows[project["id"]]["indexing_incomplete"] is False
    assert rows[other["id"]]["coverage"]["artifacts"]["embedding"]["withheld"] == 1
    assert rows[other["id"]]["indexing_incomplete"] is True
    assert result["coverage"]["artifacts"]["embedding"]["ready"] == 2
    first = api.post("/api/v1/search", json={**payload, "limit": 1}).json()
    second = api.post("/api/v1/search", json={**payload, "limit": 1, "offset": 1}).json()
    assert first["items"] + second["items"] == result["items"]


@pytest.mark.parametrize("endpoint", ["dedicated", "unified", "multi"])
@pytest.mark.parametrize(("q", "query_mode"), [
    ('"listener cookies"', "terms"), ("listener cookies", "phrase"),
    ("listener cookies", "literal"),
])
def test_semantic_search_rejects_exact_intent_instead_of_ignoring_it(
    api, project, artifact_storage, endpoint, q, query_mode,
):
    payload = {"q": q, "query_mode": query_mode, "fulltext": True}
    if endpoint == "dedicated":
        path = collection(project) + "/search-content"
        payload["semantic"] = True
    else:
        path = f"/api/v1/projects/{project['id']}/search"
        payload.update(facets=["artifacts"], filters={"artifacts": {"semantic": True}})
        if endpoint == "multi":
            path = "/api/v1/search"
            payload["project_ids"] = [project["id"]]
    response = api.post(path, json=payload)
    assert response.status_code == 422, response.text
    error = response.json()["detail"][0]
    assert error["type"] == "semantic_requires_unconstrained_artifact_query"
    assert error["loc"] == ["body", "q"]


@pytest.mark.parametrize("endpoint", ["dedicated", "unified", "multi"])
def test_semantic_date_scope_diagnostics_and_ranking_use_the_same_corpus(
    api, project, artifact_storage, endpoint,
):
    artifact = upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    drain(api, PassageEmbedder())
    filters = {"semantic": True, "created_after": artifact["created_at"]}
    payload = {"q": "listener", "fulltext": True, "diagnostics": "always"}
    if endpoint == "dedicated":
        path = collection(project) + "/search-content"
        payload.update(filters)
    else:
        path = f"/api/v1/projects/{project['id']}/search"
        payload.update(facets=["artifacts"], filters={"artifacts": filters})
        if endpoint == "multi":
            path = "/api/v1/search"
            payload["project_ids"] = [project["id"]]
    response = api.post(path, json=payload)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1 and page["total_kind"] == "ranked_candidates"
    assert page["semantic"]["inference"]["status"] == "completed"
    assert page["semantic"]["comparison_incomplete"] is False
    assert page["term_diagnostics"] == [{"term": "listener", "matches": {
        "work_items": None, "artifacts": 1, "transcripts": None}}]
    if endpoint != "dedicated":
        assert page["facet_score_types"]["artifacts"] == "semantic_reciprocal_rank"
        assert page["items"][0]["artifact"]["rank"] == 1
        assert page["project_coverage"][0]["facet_totals"]["artifacts"] == 1
    scoped = payload if endpoint == "dedicated" else filters
    del scoped["created_after"]
    scoped["created_before"] = artifact["created_at"]
    payload["diagnostics"] = "off"
    response = api.post(path, json=payload)
    assert response.status_code == 200, response.text
    empty = response.json()
    assert empty["total"] == 0 and empty["term_diagnostics"] == []
    assert empty["applied_filters"]["artifacts"]["created_before"] == artifact["created_at"]


@pytest.mark.parametrize("route", ["dedicated", "project", "workspace"])
@pytest.mark.parametrize("fault", ["capacity", "model"])
def test_disabled_artifact_semantics_does_not_request_inference_or_hide_coverage(
    api, project, artifact_storage, monkeypatch, route, fault,
):
    attempts = []

    async def admission(_resources):
        attempts.append("admission")
        return fault != "capacity"

    class BrokenEmbedder:
        def embed_query(self, _text):
            attempts.append("query")
            raise RuntimeError("A disabled artifact library must not load the model")

    resources = api.app.state.duplicate_suggestion_resources
    monkeypatch.setattr(type(resources), "acquire_inference", admission)
    api.app.state.semantic_embedder = BrokenEmbedder()
    api.app.state.settings.artifact_max_bytes = 0
    path, payload = semantic_request(project, route)
    response = api.post(path, json=payload)
    if route == "dedicated":
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "artifact_library_disabled"
    else:
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["total"] == 0 and page["items"] == []
        assert page["search_scope"]["searched_facets"] == []
        assert page["coverage"]["artifacts"]["enabled"] is False
        assert page["indexing_incomplete"] is True
        assert page["applied_filters"]["artifacts"] is None
        assert page["query_interpretation"]["artifacts"] is None
        assert page["facet_score_types"]["artifacts"] is None
        assert page["facet_total_kinds"]["artifacts"] is None
        assert page["semantic"]["inference"] == {"status": "not_requested", "reason": None}
        assert page["semantic"]["candidate_scope"] == "none"
        assert page["semantic"]["comparison_incomplete"] is False
        assert all(term["matches"]["artifacts"] is None for term in page["term_diagnostics"])
        assert page["project_coverage"][0]["coverage"]["artifacts"]["enabled"] is False
        assert page["project_coverage"][0]["indexing_incomplete"] is True
    assert attempts == []


@pytest.mark.parametrize("route", ["project", "workspace"])
@pytest.mark.parametrize("work_semantic", [False, True])
def test_disabled_artifact_semantics_preserves_other_selected_work_search(
    api, project, artifact_storage, work_payload, monkeypatch, route, work_semantic,
):
    from mnemonic_api.application.routes import search as routes

    from .test_duplicate_suggestions_postgres import DeterministicEmbedder, save

    def forbidden_tokenizer(_embedder):
        raise AssertionError("Disabled artifact search must not prepare its tokenizer")

    monkeypatch.setattr(routes, "passage_tokenizer", forbidden_tokenizer)
    save(api, project, work_payload, title="Request work candidate")
    api.app.state.semantic_embedder = DeterministicEmbedder()
    api.app.state.settings.artifact_max_bytes = 0
    path, payload = semantic_request(project, route)
    payload["facets"].append("work_items")
    payload["filters"]["work_items"] = {"semantic": work_semantic}
    response = api.post(path, json=payload)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 1 and page["items"][0]["facet"] == "work_items"
    assert page["search_scope"]["searched_facets"] == ["work_items"]
    assert page["semantic"]["inference"] == {
        "status": "completed" if work_semantic else "not_requested", "reason": None}
    assert page["coverage"]["artifacts"]["enabled"] is False
    assert page["indexing_incomplete"] is True
