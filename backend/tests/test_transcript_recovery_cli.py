"""Private frozen operator intents cannot turn retries into new recoveries."""

import importlib.util
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import BackgroundJob, Transcript, TranscriptRecovery
from mnemonic_api.services.transcript_recoveries import TranscriptRecoveryResult

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import register


@pytest.fixture
def recovery_cli():
    path = Path(__file__).parents[2] / "scripts/recover_transcript_paths.py"
    spec = importlib.util.spec_from_file_location("transcript_recovery_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_json(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    return path


def mapping(**changes):
    return {"transcript_id": str(uuid4()), "project_id": str(uuid4()),
            "original_source_path": "/allowed/wrong.jsonl",
            "replacement_path": "/allowed/worktree/session.jsonl",
            "reason": "Correct verified historical path",
            "evidence": "Operator verified exact unique native session identity", **changes}


@pytest.mark.parametrize("kind", ["public", "symlink", "parent_symlink", "fifo", "hardlink"])
def test_recovery_manifest_rejects_unsafe_files(recovery_cli, tmp_path, kind):
    source = private_json(tmp_path / "source.json", {})
    candidate = source
    if kind == "public":
        source.chmod(0o644)
    elif kind == "symlink":
        candidate = tmp_path / "alias.json"
        candidate.symlink_to(source)
    elif kind == "parent_symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        candidate = alias / "source.json"
    elif kind == "fifo":
        candidate = tmp_path / "fifo"
        os.mkfifo(candidate, 0o600)
    else:
        os.link(source, tmp_path / "hardlink")
    with pytest.raises((OSError, ValueError)):
        recovery_cli.read_private_json(candidate)


@pytest.mark.parametrize("payload", [
    '{"version":1,"version":2}', '{"value":NaN}', '[',
])
def test_recovery_json_rejects_ambiguous_or_invalid_input(recovery_cli, tmp_path, payload):
    source = tmp_path / "intent.json"
    source.write_text(payload)
    source.chmod(0o600)
    with pytest.raises(ValueError):
        recovery_cli.read_private_json(source)


def test_prepare_defers_active_without_reading_source(recovery_cli, tmp_path, monkeypatch):
    entry = mapping()

    def active(*_args):
        raise ApplicationError(409, "transcript_recovery_active", "Wait for the lease.")

    def must_not_read(*_args):
        pytest.fail("An active transcript must be deferred before hashing its file")

    monkeypatch.setattr(recovery_cli, "inspect_recovery_target", active)
    monkeypatch.setattr(recovery_cli, "describe_recovery_source", must_not_read)
    output = tmp_path / "prepared.json"
    summary = recovery_cli.prepare(None, None, private_json(tmp_path / "mappings", [entry]), output)
    assert summary["prepared"] == 0 and summary["deferred"] == 1
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    frozen = output.read_bytes()
    with pytest.raises(FileExistsError):
        recovery_cli.prepare(None, None, tmp_path / "mappings", output)
    assert output.read_bytes() == frozen


def test_duplicate_mappings_rejected_before_any_inspection(recovery_cli, tmp_path, monkeypatch):
    entry = mapping()
    source = private_json(tmp_path / "mappings", [entry, entry])
    monkeypatch.setattr(recovery_cli, "inspect_recovery_target",
                        lambda *_: pytest.fail("inspected"))
    with pytest.raises(ValueError, match="Duplicate"):
        recovery_cli.prepare(None, None, source, tmp_path / "prepared")
    assert not (tmp_path / "prepared").exists()


def test_apply_uses_frozen_requests_without_rehashing_or_repreparing(
    recovery_cli, tmp_path, monkeypatch,
):
    entry = mapping()
    parsed = recovery_cli.RecoveryMapping.model_validate_json(json.dumps(entry))
    fingerprint = SimpleNamespace(sha256="a" * 64, size_bytes=100)
    target = SimpleNamespace(original_source_path=entry["original_source_path"],
                             generation=3, snapshot_id=uuid4(), maximum_bytes=1024)
    monkeypatch.setattr(recovery_cli, "inspect_recovery_target", lambda *_: target)
    monkeypatch.setattr(recovery_cli, "describe_recovery_source", lambda *_: fingerprint)
    frozen_request = recovery_cli.prepare_mapping(None, None, parsed)
    manifest = recovery_cli.RecoveryManifest(version=1, requests=[frozen_request], deferred=[])
    output = tmp_path / "request.json"
    recovery_cli.write_manifest(output, manifest)
    original = output.read_bytes()
    attempts = []

    def apply(_factory, _settings, request):
        attempts.append(request)
        return TranscriptRecoveryResult(operation_id=request.operation_id,
            transcript_id=request.transcript_id, resulting_generation=4,
            resulting_snapshot_id=target.snapshot_id)

    monkeypatch.setattr(recovery_cli, "apply_transcript_recovery", apply)
    monkeypatch.setattr(recovery_cli, "describe_recovery_source",
                        lambda *_: pytest.fail("rehashed"))
    for _ in range(2):
        assert recovery_cli.apply(None, None, output)["applied_or_replayed"] == 1
    assert attempts == [frozen_request, frozen_request]
    assert output.read_bytes() == original


@pytest.mark.postgres
def test_operator_prepare_apply_and_exact_replay_enqueue_once(
    recovery_cli, api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    factory = api.app.state.session_factory
    settings = api.app.state.settings
    expire_lease(postgres_engine, work["id"])
    replacement = tmp_path / "verified-worktree.jsonl"
    source.rename(replacement)
    with factory() as database:
        record = database.scalar(select(Transcript))
        entry = mapping(transcript_id=str(record.id), project_id=project["id"],
                        original_source_path=record.source_path, replacement_path=str(replacement))
    prepared = tmp_path / "request.json"
    assert recovery_cli.prepare(factory, settings, private_json(tmp_path / "mappings", [entry]),
                                prepared)["prepared"] == 1
    before = prepared.read_bytes()
    with factory() as database:
        assert not list(database.scalars(select(TranscriptRecovery)))
    first = recovery_cli.apply(factory, settings, prepared)
    replacement.unlink()
    assert recovery_cli.apply(factory, settings, prepared) == first
    assert prepared.read_bytes() == before
    with factory() as database:
        record = database.scalar(select(Transcript))
        assert record.source_path == entry["original_source_path"]
        assert len(list(database.scalars(select(TranscriptRecovery)))) == 1
        jobs = list(database.scalars(select(BackgroundJob)))
        assert len(jobs) == 1
        assert jobs[0].payload == {"transcript_id": entry["transcript_id"],
                                   "generation": record.generation}


def test_manifest_rejects_repeated_operations_and_unknown_fields(recovery_cli, tmp_path):
    entry = {**mapping(), "operation_id": str(uuid4()), "expected_generation": 1,
             "expected_snapshot_id": str(uuid4()), "expected_sha256": "a" * 64,
             "expected_size_bytes": 10}
    duplicate = {**entry, "transcript_id": str(uuid4())}
    for data in ({"version": 1, "requests": [entry, duplicate], "deferred": []},
                 {"version": 1, "requests": [entry], "deferred": [], "force": True},
                 {"version": True, "requests": [entry], "deferred": []}):
        with pytest.raises(ValueError):
            recovery_cli.RecoveryManifest.model_validate_json(json.dumps(data))
