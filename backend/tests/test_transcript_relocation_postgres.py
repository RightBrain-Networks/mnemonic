"""Real enrollment, durable jobs, receipts and backups retain relocation evidence."""

import io
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copying import copy_next_transcript
from mnemonic_api.transcript_indexing import index_next_transcript
from mnemonic_backup.archive import restore_project

from .test_leases_postgres import create_work, expire_lease, item_path
from .test_project_backup_archive import _export, _snapshot
from .test_transcript_imports_postgres import import_folder
from .test_transcript_indexing_postgres import Parser, collection, read
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def enroll(api, project, work_payload, tmp_path):
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "user", "sessionId": "relocation-test",
        "message": {"role": "user", "content": "verified transcript " * 60}}) + "\n")
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    receipt, payload = claim(api, item_path(project, work),
                             source={"client": "claude_code", "path": str(source)})
    record = api.get(collection(project), params={"detail": "full"}).json()["items"][0]
    return source, work, receipt, payload, record


def test_move_after_claim_preserves_receipt_and_indexes_only_after_lease_ends(
    api, project, work_payload, tmp_path, postgres_engine,
):
    source, work, receipt, payload, record = enroll(api, project, work_payload, tmp_path)
    target = tmp_path / "worktree" / source.name
    target.parent.mkdir()
    source.rename(target)
    with target.open("a") as output:
        output.write('{"type":"assistant","message":{"role":"assistant",'
                     '"content":"finished after moving"}}\n')
    # New session factory models a worker restart; no in-memory inode/path cache.
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    settings = api.app.state.settings
    assert not copy_next_transcript(factory, settings)
    assert api.post(item_path(project, work) + "/claim", json=payload).json() == receipt
    expire_lease(postgres_engine, work["id"])
    assert copy_next_transcript(factory, settings)
    assert index_next_transcript(factory, settings, Parser())
    assert read(api, project, record)["status"] == "ready"
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        assert row.source_path == str(source) and row.copy_source_path == str(target)
        assert row.source_identity["prefix_size"] >= 256
        assert row.normalized_text.endswith("finished after moving")
    warnings = api.get(collection(project) + "/health").json()["warnings"]
    assert not any(item["path"] == str(source) for item in warnings)


def test_ambiguous_move_warns_then_automatically_recovers_after_operator_fix(
    api, project, work_payload, tmp_path, postgres_engine,
):
    source, work, _, _, record = enroll(api, project, work_payload, tmp_path)
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    target = one / source.name
    duplicate = two / source.name
    duplicate.write_bytes(source.read_bytes())
    source.rename(target)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    assert copy_next_transcript(factory, settings)
    assert read(api, project, record)["copy_error_code"] == "transcript_relocation_ambiguous"
    warnings = api.get(collection(project) + "/health").json()["warnings"]
    warning = next(item for item in warnings if item["code"] == "transcript_relocation_ambiguous")
    assert warning["path"] == str(source) and "Several files" in warning["action"]
    assert not copy_next_transcript(factory, settings)
    duplicate.unlink()
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.copy_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert copy_next_transcript(factory, settings)
    assert index_next_transcript(factory, settings, Parser())
    assert read(api, project, record)["copy_status"] == "ready"


def test_enrolled_identity_is_immutable_and_retained_in_project_backup(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, _, _, _, record = enroll(api, project, work_payload, tmp_path)
    with pytest.raises(IntegrityError, match="source identity is immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE transcripts SET source_identity=NULL WHERE id=:id"),
                                {"id": record["id"]})
    original = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = _snapshot(postgres_engine, project)["transcripts"][0]
    assert restored["source_identity"] == original["transcripts"][0]["source_identity"]
    assert restored["source_path"] == original["transcripts"][0]["source_path"]
    assert restored["generation"] > original["transcripts"][0]["generation"]


def test_enrolling_import_establishes_identity_without_reusing_old_copy(
    api, project, work_payload, tmp_path,
):
    source = tmp_path / "session.jsonl"
    source.write_text('{"role":"user","content":"' + "test content " * 60 + '"}\n')
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    assert import_folder(api, project, tmp_path).json()["imported"] == 1
    factory = api.app.state.session_factory
    with factory() as database:
        before = database.scalar(select(Transcript))
        imported_id, snapshot = before.id, before.snapshot_id
        assert before.source_identity is None
    work = create_work(api, project, work_payload)["work_item"]
    claim(api, item_path(project, work), source={"client": "claude_code", "path": str(source)})
    with factory() as database:
        row = database.get(Transcript, imported_id)
        assert row.source_identity is not None
        assert row.snapshot_id != snapshot and row.copy_source_path is None


def test_legacy_enrollment_cannot_gain_retroactive_identity(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from mnemonic_api.transcript_storage import validate_transcript_assertion

    from .test_transcript_indexing_postgres import register

    _, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_bytes(b"later unrelated bytes" * 40)
    proof = validate_transcript_assertion(str(source), [tmp_path])
    assert proof is not None
    with pytest.raises(IntegrityError, match="source identity is immutable"):
        with api.app.state.session_factory.begin() as database:
            row = database.get(Transcript, UUID(record["id"]))
            assert row.source_identity is None
            row.source_identity = proof


def test_new_approved_root_retries_only_with_enrollment_evidence(
    api, project, work_payload, tmp_path, postgres_engine,
):
    source, work, _, _, record = enroll(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    settings.transcript_allowed_roots = []
    assert copy_next_transcript(factory, settings)
    assert read(api, project, record)["copy_error_code"] == "transcript_path_not_allowed"
    assert not copy_next_transcript(factory, settings)
    target = tmp_path / "archive" / source.name
    target.parent.mkdir()
    source.rename(target)
    settings.transcript_allowed_roots = [target.parent]
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.copy_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert copy_next_transcript(factory, settings)
    assert index_next_transcript(factory, settings, Parser())
    assert read(api, project, record)["copy_status"] == "ready"
