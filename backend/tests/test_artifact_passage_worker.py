"""Artifact-disabled workers neither enroll new bodies nor execute old deliveries."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mnemonic_api.job_worker import _artifact_embedding_job, schedule_jobs
from mnemonic_jobs.ledger import RetryJob

from .artifact_passage_fixtures import passage_policy


@pytest.mark.parametrize("maximum", [0, 1024])
def test_scheduler_obeys_artifact_availability_without_pausing_other_job_kinds(
    monkeypatch, maximum,
):
    calls = {}
    for name in ("refresh_outdated_normalizations", "enqueue_transcript_jobs",
                 "enqueue_artifact_passage_jobs", "enqueue_duplicate_embedding_jobs",
                 "schedule_backups"):
        calls[name] = Mock()
        monkeypatch.setattr(f"mnemonic_api.job_worker.{name}", calls[name])
    schedule_jobs(None, SimpleNamespace(artifact_max_bytes=maximum), None,
                  embedder=SimpleNamespace(passage_tokenizer=passage_policy))
    assert calls["enqueue_artifact_passage_jobs"].call_count == int(maximum > 0)
    calls["enqueue_transcript_jobs"].assert_called_once()
    calls["schedule_backups"].assert_called_once()


def test_disabled_existing_delivery_defers_without_inference_or_attempt_exhaustion(monkeypatch):
    handle = Mock()
    monkeypatch.setattr("mnemonic_api.job_worker.handle_artifact_embedding", handle)
    with pytest.raises(RetryJob) as error:
        _artifact_embedding_job(SimpleNamespace(artifact_max_bytes=0), None, None, None)
    assert error.value.code == "artifact_library_disabled"
    assert error.value.consume_attempt is False
    handle.assert_not_called()


def test_tokenizer_preparation_precedes_sql_and_failure_keeps_other_jobs(monkeypatch):
    order = []
    for name in ("refresh_outdated_normalizations", "enqueue_transcript_jobs",
                 "enqueue_artifact_passage_jobs", "enqueue_duplicate_embedding_jobs",
                 "schedule_backups"):
        monkeypatch.setattr(f"mnemonic_api.job_worker.{name}",
                            lambda *_args, name=name: order.append(name))

    def tokenizer():
        order.append("prepare_tokenizer")
        return passage_policy()

    settings = SimpleNamespace(artifact_max_bytes=1024)
    schedule_jobs(None, settings, None, embedder=SimpleNamespace(passage_tokenizer=tokenizer))
    assert order == ["prepare_tokenizer", "refresh_outdated_normalizations",
                     "enqueue_transcript_jobs",
                     "enqueue_artifact_passage_jobs", "enqueue_duplicate_embedding_jobs",
                     "schedule_backups"]
    order.clear()

    def unavailable():
        raise RuntimeError("private model failure")

    schedule_jobs(None, settings, None, embedder=SimpleNamespace(passage_tokenizer=unavailable))
    assert order == ["refresh_outdated_normalizations", "enqueue_transcript_jobs",
                     "enqueue_duplicate_embedding_jobs", "schedule_backups"]


@pytest.mark.parametrize("threads", [1, 4])
def test_worker_uses_configured_native_threads_with_one_model(monkeypatch, threads):
    from mnemonic_api import job_worker
    from mnemonic_api.config import Settings

    monkeypatch.setenv("MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_THREADS", str(threads))
    settings = Settings(
        api_key="synthetic-worker-test-key-32-characters",
        database_url="postgresql+psycopg://unused:unused@127.0.0.1/unused",
    )
    monkeypatch.setattr(job_worker, "Settings", lambda: settings)
    for name in ("BackupSettings", "WorkerSettings", "build_engine", "build_session_factory",
                 "create_backup_app"):
        monkeypatch.setattr(job_worker, name, Mock())
    embedder = Mock()
    monkeypatch.setattr(job_worker, "FastembedEmbedder", embedder)
    job_worker.create_app()
    embedder.assert_called_once_with(threads=threads)
