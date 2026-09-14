"""Uncertain recovery outcomes retain one frozen batch for exact receipt replay."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import BackgroundJob, TranscriptRecovery
from mnemonic_api.services.transcript_recoveries import TranscriptRecoveryRequest

from .test_transcript_recovery_cli import recovery_cli as recovery_cli
from .test_transcript_recovery_postgres import _request_fixture


def _write_batch(cli, tmp_path, requests):
    manifest = cli.RecoveryManifest(version=1, requests=requests, deferred=[])
    prepared = tmp_path / "prepared.json"
    cli.write_manifest(prepared, manifest)
    return prepared


@pytest.mark.postgres
@pytest.mark.parametrize("lost_reply_number", [1, 2])
def test_lost_commit_reply_stops_batch_and_replays_every_committed_receipt(
    recovery_cli, api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
    lost_reply_number,
):
    requests = []
    for number in range(3):
        directory = tmp_path / str(number)
        directory.mkdir()
        _, _, _, request = _request_fixture(
            api, project, work_payload, directory, postgres_engine)
        requests.append(request)
    settings = api.app.state.settings
    settings.transcript_allowed_roots = [tmp_path]
    prepared = _write_batch(recovery_cli, tmp_path, requests)
    original_bytes = prepared.read_bytes()
    committed, attempted = [], []
    original_apply = recovery_cli.apply_transcript_recovery

    class LostCommitReply(Session):
        def commit(self):
            super().commit()
            committed.append(True)
            if len(committed) == lost_reply_number:
                raise OperationalError(
                    "synthetic lost commit reply", None, Exception("connection lost"),
                    connection_invalidated=True,
                )

    def tracked_apply(factory, settings, request):
        attempted.append(request.operation_id)
        return original_apply(factory, settings, request)

    monkeypatch.setattr(recovery_cli, "apply_transcript_recovery", tracked_apply)
    failing_factory = sessionmaker(postgres_engine, class_=LostCommitReply)
    with pytest.raises(recovery_cli.RecoveryOutcomeUnknown):
        recovery_cli.apply(failing_factory, settings, prepared)
    assert attempted == [request.operation_id for request in requests[:lost_reply_number]]
    factory = api.app.state.session_factory
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) \
            == lost_reply_number
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == lost_reply_number
    assert prepared.read_bytes() == original_bytes

    resumed = recovery_cli.apply(factory, settings, prepared)
    assert resumed["applied_or_replayed"] == 3 and resumed["refused"] == 0
    assert recovery_cli.apply(factory, settings, prepared) == resumed
    assert prepared.read_bytes() == original_bytes
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(TranscriptRecovery)) == 3
        assert database.scalar(select(func.count()).select_from(BackgroundJob)) == 3


def test_precommit_busy_stops_batch_and_preserves_exact_intent(
    recovery_cli, tmp_path, monkeypatch,
):
    request = TranscriptRecoveryRequest(
        operation_id=uuid4(), transcript_id=uuid4(), project_id=uuid4(),
        original_source_path="/allowed/wrong.jsonl",
        replacement_path="/allowed/verified/session.jsonl",
        expected_generation=1, expected_snapshot_id=uuid4(),
        expected_sha256="a" * 64, expected_size_bytes=100,
        reason="Verified historical path correction", evidence="Synthetic exact native identity",
    )
    second = request.model_copy(update={"operation_id": uuid4(), "transcript_id": uuid4()})
    prepared = _write_batch(recovery_cli, tmp_path, [request, second])
    original_bytes, attempted = prepared.read_bytes(), []

    def busy(_factory, _settings, request):
        attempted.append(request)
        raise ApplicationError(503, "transcript_recovery_busy", "Retry the frozen request.")

    monkeypatch.setattr(recovery_cli, "apply_transcript_recovery", busy)
    for _ in range(2):
        with pytest.raises(recovery_cli.RecoveryOutcomeUnknown):
            recovery_cli.apply(None, None, prepared)
        assert prepared.read_bytes() == original_bytes
    assert attempted == [request, request]
