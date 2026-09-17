"""Passage workers publish only current bounded source batches and retain resumable cursors."""

import hashlib
from uuid import UUID

import pytest
from sqlalchemy import func, select

from mnemonic_api import artifact_passages as passages
from mnemonic_api.artifact_passage_jobs import (
    enqueue_artifact_passage_jobs,
    handle_artifact_embedding,
)
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.models import ArtifactExtraction, BackgroundJob
from mnemonic_jobs.ledger import claim_job, finish_job

from .artifact_passage_fixtures import passage_policy
from .test_artifact_search_postgres import extract
from .test_artifacts_postgres import upload

pytestmark = pytest.mark.postgres


@pytest.fixture
def artifact_storage(api, tmp_path):
    storage = ArtifactStorage(tmp_path / "semantic-artifacts", max_bytes=2_000_000)
    api.app.state.artifact_storage = storage
    api.app.state.settings.artifact_max_bytes = storage.max_bytes
    return storage


class PassageEmbedder:
    def __init__(self):
        self.batches = []

    def passage_tokenizer(self):
        return passage_policy()

    def embed_documents(self, texts):
        self.batches.append(list(texts))
        return [[1.0, 0.0] if "authorize listener cookies" in text else [0.0, 1.0]
                for text in texts]

    def embed_query(self, _text):
        return [1.0, 0.0]


def step(api, embedder):
    factory = api.app.state.session_factory
    with factory.begin() as database:
        enqueue_artifact_passage_jobs(database, embedder.passage_tokenizer())
        identity = database.scalar(select(BackgroundJob.id).where(
            BackgroundJob.kind == "artifact_embed", BackgroundJob.status == "pending",
        ).order_by(BackgroundJob.created_at, BackgroundJob.id).limit(1))
        if identity is None:
            return None
        context = claim_job(database, identity)
    assert context is not None
    result = handle_artifact_embedding(factory, embedder, context)
    with factory.begin() as database:
        assert finish_job(database, context, result=result)
    return result


def drain(api, embedder, *, maximum=100):
    for _ in range(maximum):
        if step(api, embedder) is None:
            return
    pytest.fail("Embedding work did not terminate within its expected bounded batches")


def test_passages_cover_the_tail_and_batches_never_hydrate_a_whole_long_document(
    api, project, artifact_storage,
):
    body = ("Unrelated background and details. " * 20_000 + " authorize listener cookies").encode()
    record = upload(api, project, body=body, filename="design.txt")
    extract(api, artifact_storage)
    embedder = PassageEmbedder()
    first = step(api, embedder)
    assert first["disposition"] == "pending"
    with api.app.state.session_factory() as database:
        index = database.execute(select(passages.INDEXES)).mappings().one()
        assert index["next_ordinal"] == 16
        assert database.scalar(select(func.count()).select_from(passages.PASSAGES)) == 16
        extraction = database.get(ArtifactExtraction, (UUID(record["id"]), 1))
        assert extraction.text_sha256 == hashlib.sha256(body).hexdigest()
    drain(api, embedder)
    assert all(len(batch) <= 16 and all(len(text) <= 1500 for text in batch)
               for batch in embedder.batches)
    assert "authorize listener cookies" in embedder.batches[-1][-1]
    with api.app.state.session_factory() as database:
        index = database.execute(select(passages.INDEXES)).mappings().one()
        assert index["status"] == "ready" and index["next_offset"] == len(body)
        last = database.execute(select(passages.PASSAGES)
            .order_by(passages.PASSAGES.c.ordinal.desc()).limit(1)).mappings().one()
        assert last["end_offset"] == len(body)


def claim_next(api):
    factory = api.app.state.session_factory
    with factory.begin() as database:
        enqueue_artifact_passage_jobs(database, passage_policy())
        identity = database.scalar(select(BackgroundJob.id).where(
            BackgroundJob.kind == "artifact_embed", BackgroundJob.status == "pending",
        ).order_by(BackgroundJob.created_at, BackgroundJob.id).limit(1))
        return claim_job(database, identity)


@pytest.mark.parametrize("failure", ["exception", "nan", "zero", "dimension"])
def test_failed_provider_preserves_completed_batches_and_exposes_safe_failure(
    api, project, artifact_storage, failure,
):
    from mnemonic_jobs.ledger import RetryJob

    upload(api, project, body=b"background " * 5_000)
    extract(api, artifact_storage)
    step(api, PassageEmbedder())
    context = claim_next(api)

    class BrokenEmbedder(PassageEmbedder):
        def embed_documents(self, texts):
            if failure == "exception":
                raise RuntimeError("PRIVATE SOURCE AND PROVIDER DETAILS")
            vector = {"nan": [float("nan"), 0], "zero": [0, 0], "dimension": [1]}[failure]
            return [vector for _ in texts]

    with pytest.raises(RetryJob, match="artifact_embedding_unavailable") as error:
        handle_artifact_embedding(api.app.state.session_factory, BrokenEmbedder(), context)
    assert "PRIVATE" not in str(error.value)
    with api.app.state.session_factory() as database:
        index = database.execute(select(passages.INDEXES)).mappings().one()
        assert index["status"] == "failed" and index["next_ordinal"] == 16
        assert index["error_code"] == "artifact_embedding_unavailable"
        assert database.scalar(select(func.count()).select_from(passages.PASSAGES)) == 16
    result = handle_artifact_embedding(api.app.state.session_factory, PassageEmbedder(), context)
    with api.app.state.session_factory.begin() as database:
        assert finish_job(database, context, result=result)
    drain(api, PassageEmbedder())
    with api.app.state.session_factory() as database:
        assert database.scalar(select(passages.INDEXES.c.status)) == "ready"


@pytest.mark.parametrize("change", ["source", "lease", "config"])
def test_inference_cannot_publish_after_its_source_or_ownership_changes(
    api, project, artifact_storage, monkeypatch, change,
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from mnemonic_jobs.ledger import LostLease

    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    context = claim_next(api)

    class ChangingEmbedder(PassageEmbedder):
        def embed_documents(self, texts):
            with api.app.state.session_factory.begin() as database:
                if change == "source":
                    database.execute(update(ArtifactExtraction).values(normalized_text="changed"))
                elif change == "lease":
                    database.execute(update(BackgroundJob).where(BackgroundJob.id == context.job_id)
                        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
                else:
                    monkeypatch.setattr(passages, "CHUNK_CONFIG", "new-test-config")
            return super().embed_documents(texts)

    if change == "lease":
        with pytest.raises(LostLease):
            handle_artifact_embedding(api.app.state.session_factory, ChangingEmbedder(), context)
    else:
        result = handle_artifact_embedding(
            api.app.state.session_factory, ChangingEmbedder(), context)
        assert result["disposition"] == "obsolete"
    with api.app.state.session_factory() as database:
        assert database.scalar(select(func.count()).select_from(passages.PASSAGES)) == 0


def test_large_finite_vectors_are_normalized_before_postgresql_real_storage(
    api, project, artifact_storage,
):
    import math

    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)

    class LargeEmbedder(PassageEmbedder):
        def embed_documents(self, texts):
            return [[1e200, -1e200] for _ in texts]

    drain(api, LargeEmbedder())
    with api.app.state.session_factory() as database:
        vector = database.scalar(select(passages.PASSAGES.c.vector))
        assert math.hypot(*vector) == pytest.approx(1, abs=1e-6)


def test_exhausted_delivery_reports_failed_cache_and_does_not_spin(
    api, project, artifact_storage,
):
    from sqlalchemy import update

    upload(api, project, body=b"authorize listener cookies")
    extract(api, artifact_storage)
    context = claim_next(api)
    with api.app.state.session_factory.begin() as database:
        database.execute(update(BackgroundJob).where(BackgroundJob.id == context.job_id).values(
            status="failed", lease_token=None, lease_expires_at=None,
            completed_at=func.clock_timestamp(), error_code="attempts_exhausted"))
        assert enqueue_artifact_passage_jobs(database, passage_policy()) == 0
    with api.app.state.session_factory() as database:
        index = database.execute(select(passages.INDEXES)).mappings().one()
        assert index["status"] == "failed" and index["error_code"] == "artifact_embedding_failed"
