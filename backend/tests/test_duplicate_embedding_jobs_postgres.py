"""Duplicate checks enqueue bounded work without invalidating semantic search vectors."""

from time import monotonic
from uuid import UUID

import pytest
from sqlalchemy import func, select

from mnemonic_api.duplicate_embedding_jobs import (
    REFRESHES,
    enqueue_duplicate_embedding_jobs,
    handle_duplicate_embedding,
)
from mnemonic_api.models import BackgroundJob, WorkItemEmbedding
from mnemonic_jobs.ledger import LostLease, RetryJob, claim_job, finish_job

from .test_duplicate_suggestions_postgres import DeterministicEmbedder, bulk_save, save, suggest

pytestmark = pytest.mark.postgres


def next_job(api):
    factory = api.app.state.session_factory
    with factory.begin() as database:
        enqueue_duplicate_embedding_jobs(database)
        identity = database.scalar(select(BackgroundJob.id).where(
            BackgroundJob.kind == "duplicate_embed", BackgroundJob.status == "pending")
            .order_by(BackgroundJob.created_at, BackgroundJob.id).limit(1))
        return claim_job(database, identity) if identity is not None else None


def step(api, embedder):
    context = next_job(api)
    if context is None:
        return None
    result = handle_duplicate_embedding(api.app.state.session_factory, embedder, context)
    with api.app.state.session_factory.begin() as database:
        assert finish_job(database, context, result=result)
    return result


def drain(api, embedder):
    for _ in range(1000):
        if step(api, embedder) is None:
            return
    pytest.fail("Duplicate refresh did not converge")


def count_vectors(api, purpose):
    with api.app.state.session_factory() as database:
        return database.scalar(select(func.count()).select_from(WorkItemEmbedding).where(
            WorkItemEmbedding.purpose == purpose))


def test_cold_check_returns_promptly_and_worker_covers_beyond_lexical_shortlist(
    api, project, postgres_engine,
):
    bulk_save(postgres_engine, UUID(project["id"]), count=205, title_prefix="cache repair")
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    start = monotonic()
    response = suggest(api, project)
    assert monotonic() - start < 2
    assert response.status_code == 200, response.text
    semantic = response.json()["semantic"]
    assert semantic["inference"] == {"status": "unavailable", "reason": "vectors_pending"}
    assert semantic["cache_refresh"] == {"status": "queued", "reason": None}
    assert semantic["retry"] is None
    assert embedder.document_batches == []
    with api.app.state.session_factory() as database:
        identity = database.scalar(select(REFRESHES.c.id))
    assert suggest(api, project).status_code == 200
    with api.app.state.session_factory() as database:
        assert database.scalar(select(REFRESHES.c.id)) == identity
    drain(api, embedder)
    assert count_vectors(api, "duplicate_suggestions") == 205
    assert all(len(batch) <= 16 for batch in embedder.document_batches)
    warm = suggest(api, project).json()
    assert warm["semantic"]["inference"]["status"] == "completed"
    assert warm["semantic"]["candidate_scope"] == "full_scope"
    assert warm["semantic"]["cache_refresh"]["status"] == "not_needed"


def test_alternating_search_and_suggestions_preserves_both_cache_populations(
    api, project, work_payload,
):
    save(api, project, work_payload, title="cache repair", tags=["cache"])
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    path = f"/api/v1/projects/{project['id']}/work-items"
    query = {"q": "cache repair", "semantic": "true"}
    assert api.get(path, params=query).status_code == 200
    assert suggest(api, project).json()["semantic"]["cache_refresh"]["status"] == "queued"
    drain(api, embedder)
    before = len(embedder.document_batches)
    for _ in range(2):
        search = api.get(path, params=query)
        assert search.status_code == 200, search.text
        assert search.json()["semantic"]["cache_refresh"]["status"] == "not_needed"
        assert suggest(api, project).json()["semantic"]["cache_refresh"]["status"] == "not_needed"
    assert len(embedder.document_batches) == before
    assert count_vectors(api, "work_search") == count_vectors(api, "duplicate_suggestions") == 1


def test_later_native_failure_keeps_completed_batch_and_resumes_only_missing(
    api, project, postgres_engine,
):
    bulk_save(postgres_engine, UUID(project["id"]), count=18, title_prefix="cache")
    suggest(api, project)
    embedder = DeterministicEmbedder()
    assert step(api, embedder)["embedded"] == 16
    context = next_job(api)
    assert context is not None

    class Broken(DeterministicEmbedder):
        def embed_documents(self, _texts):
            raise RuntimeError("PRIVATE PROVIDER DETAILS")

    with pytest.raises(RetryJob, match="duplicate_embedding_unavailable") as error:
        handle_duplicate_embedding(api.app.state.session_factory, Broken(), context)
    assert "PRIVATE" not in str(error.value)
    assert count_vectors(api, "duplicate_suggestions") == 16
    # An unchanged owned delivery can safely retry its remaining batch.
    result = handle_duplicate_embedding(api.app.state.session_factory, embedder, context)
    assert result == {"disposition": "completed", "embedded": 2}
    assert [len(batch) for batch in embedder.document_batches] == [16, 2]
    assert count_vectors(api, "duplicate_suggestions") == 18


def test_source_change_during_inference_cannot_publish_obsolete_vector(
    api, project, work_payload,
):
    work = save(api, project, work_payload, title="cache repair")
    suggest(api, project)
    context = next_job(api)
    assert context is not None

    class Changing(DeterministicEmbedder):
        def embed_documents(self, texts):
            endpoint = f"/api/v1/projects/{project['id']}/work-items/{work['id']}/checkpoints"
            changed = api.post(endpoint,
                json={"kind": "progress", "prompt": "Changed current embedding input",
                      "source_client": "test", "source_session_id": "source-change"})
            assert changed.status_code == 201, changed.text
            return super().embed_documents(texts)

    with pytest.raises(RetryJob, match="duplicate_embedding_changed"):
        handle_duplicate_embedding(api.app.state.session_factory, Changing(), context)
    assert count_vectors(api, "duplicate_suggestions") == 0
    assert handle_duplicate_embedding(api.app.state.session_factory,
                                      DeterministicEmbedder(), context)["embedded"] == 1


def test_lost_job_ownership_fences_vector_publication(api, project, work_payload):
    save(api, project, work_payload, title="cache repair")
    suggest(api, project)
    context = next_job(api)
    assert context is not None

    class Losing(DeterministicEmbedder):
        def embed_documents(self, texts):
            context.lost.set()
            return super().embed_documents(texts)

    with pytest.raises(LostLease):
        handle_duplicate_embedding(api.app.state.session_factory, Losing(), context)
    assert count_vectors(api, "duplicate_suggestions") == 0


def test_valid_but_wrong_dimension_cache_is_queued_and_replaced(api, project, work_payload):
    from mnemonic_api.services import duplicate_suggestions as suggestions

    work = save(api, project, work_payload, title="cache repair")
    identity = UUID(work["id"])
    with api.app.state.session_factory.begin() as database:
        value = suggestions._bounded_compositions(database, [identity])[identity]
        database.add(WorkItemEmbedding(work_item_id=identity, purpose="duplicate_suggestions",
            model=suggestions._cache_version(1), digest=suggestions._digest(value), vector=[1.0]))
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    first = suggest(api, project)
    assert first.status_code == 200, first.text
    assert first.json()["semantic"]["cache_refresh"]["status"] == "queued"
    assert first.json()["semantic"]["comparison_incomplete"]
    drain(api, embedder)
    with api.app.state.session_factory() as database:
        row = database.get(WorkItemEmbedding, (identity, "duplicate_suggestions"))
        assert row is not None and len(row.vector) == 2
    assert suggest(api, project).json()["semantic"]["candidate_scope"] == "full_scope"


def test_partial_success_does_not_reset_exhausted_generation_on_unchanged_read(
    api, project, postgres_engine,
):
    from mnemonic_jobs.ledger import PermanentJobError

    bulk_save(postgres_engine, UUID(project["id"]), count=18, title_prefix="cache")
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    assert suggest(api, project).status_code == 200
    assert step(api, embedder)["embedded"] == 16
    context = next_job(api)
    assert context is not None
    with api.app.state.session_factory.begin() as database:
        assert finish_job(database, context, error=PermanentJobError("attempts_exhausted"))
        enqueue_duplicate_embedding_jobs(database)
        before = database.execute(select(REFRESHES)).mappings().one()
        assert before["status"] == "failed"
    response = suggest(api, project)
    assert response.status_code == 200, response.text
    assert response.json()["semantic"]["cache_refresh"]["status"] == "failed"
    with api.app.state.session_factory() as database:
        after = database.execute(select(REFRESHES)).mappings().one()
        assert after["id"] == before["id"] and after["status"] == "failed"
    assert count_vectors(api, "duplicate_suggestions") == 16


def test_cold_internal_cache_keeps_external_semantics_independent(api, project, work_payload):
    save(api, project, work_payload, title="cache repair")
    embedder = DeterministicEmbedder()
    api.app.state.semantic_embedder = embedder
    response = suggest(api, project, external_candidates=[{
        "url": "https://github.com/example/repo/issues/1", "title": "Related external repair",
        "body": "[dense-target] external cache repair details", "state": "open",
    }])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["semantic"]["inference"]["reason"] == "vectors_pending"
    assert body["external_scope"] == "hybrid"
    assert len(embedder.document_batches) == 1
    assert "external cache repair" in embedder.document_batches[0][0]
    assert count_vectors(api, "duplicate_suggestions") == 0


def test_backed_off_projects_do_not_starve_new_refresh_enrollment(api):
    from datetime import timedelta
    from uuid import uuid4

    from sqlalchemy import insert, update

    from mnemonic_api.duplicate_embedding_jobs import configuration
    from mnemonic_api.models import Project

    projects = [uuid4() for _ in range(101)]
    with api.app.state.session_factory.begin() as database:
        database.execute(insert(Project), [{"id": identity, "name": "Refresh fairness",
            "slug": f"refresh-{identity}"} for identity in projects])
        database.execute(insert(REFRESHES), [{"id": uuid4(), "project_id": identity,
            "config": configuration(), "fingerprint": "a" * 64,
            "target_ids": [uuid4()]} for identity in projects])
        assert enqueue_duplicate_embedding_jobs(database) == 100
        database.execute(update(BackgroundJob).where(BackgroundJob.kind == "duplicate_embed")
            .values(due_at=func.clock_timestamp() + timedelta(hours=1)))
        assert enqueue_duplicate_embedding_jobs(database) == 1
        assert database.scalar(select(func.count()).select_from(BackgroundJob).where(
            BackgroundJob.kind == "duplicate_embed")) == 101
