"""Recovery approvals stay owned, immutable and byte-pinned across project restore."""

import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from mnemonic_api.models import Transcript
from mnemonic_api.services.transcript_recoveries import (
    TranscriptRecoveryRequest,
    apply_transcript_recovery,
    describe_recovery_source,
    inspect_recovery_target,
)
from mnemonic_api.transcript_copying import claim_transcript_copy, copy_next_transcript
from mnemonic_api.transcript_recovery_db import RECOVERY_FINDINGS
from mnemonic_backup.archive import restore_project
from mnemonic_backup.archive_integrity import validate_integrity
from mnemonic_backup.archive_schema import BackupError, validate_foreign_keys

from .test_leases_postgres import expire_lease, item_path
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_transcript_indexing_postgres import register, run
from .test_work_item_moves_postgres import _move_payload

pytestmark = pytest.mark.postgres


def recovered(api, project, work_payload, tmp_path, postgres_engine):
    work, _, record, original = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    replacement = original.with_name("recovered.jsonl")
    original.rename(replacement)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    target = inspect_recovery_target(factory, settings, UUID(record["id"]), UUID(project["id"]))
    pin = describe_recovery_source(settings, str(replacement))
    request = TranscriptRecoveryRequest(
        operation_id=uuid4(), transcript_id=target.transcript_id, project_id=target.project_id,
        original_source_path=target.original_source_path, replacement_path=str(replacement),
        expected_generation=target.generation, expected_snapshot_id=target.snapshot_id,
        expected_sha256=pin.sha256, expected_size_bytes=pin.size_bytes,
        reason="Recover a relocated synthetic fixture", evidence="Exact synthetic session match",
    )
    result = apply_transcript_recovery(factory, settings, request)
    return work, request, result, replacement


@pytest.mark.parametrize("ready", [False, True])
def test_roundtrip_preserves_approval_and_fences_restored_snapshot(
    api, project, work_payload, tmp_path, postgres_engine, ready,
):
    _, request, result, replacement = recovered(
        api, project, work_payload, tmp_path, postgres_engine)
    factory, settings = api.app.state.session_factory, api.app.state.settings
    if ready:
        assert run(api)
        replacement.unlink()
    else:
        old_claim = claim_transcript_copy(factory, settings)
        assert old_claim is not None
    original = _snapshot(postgres_engine, project)
    content = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    restored = _snapshot(postgres_engine, project)
    assert restored["transcript_recoveries"] == original["transcript_recoveries"]
    row = restored["transcripts"][0]
    assert row["source_path"] == request.original_source_path
    assert row["recovery_operation_id"] == str(request.operation_id)
    assert row["generation"] > result.resulting_generation
    assert (row["snapshot_id"] == str(result.resulting_snapshot_id)) == ready
    if ready:
        assert row["copy_sha256"] == request.expected_sha256
        assert row["normalized_text"] == original["transcripts"][0]["normalized_text"]
    else:
        assert row["copy_status"] == "pending" and row["copy_lease_token"] is None
        assert run(api)
        with factory() as database:
            copied = database.get(Transcript, request.transcript_id)
            assert copied.copy_status == "ready"
            assert copied.copy_sha256 == request.expected_sha256
            assert copied.copy_size_bytes == request.expected_size_bytes
    assert apply_transcript_recovery(factory, settings, request) == result


@pytest.mark.parametrize("damage", [
    "missing_journal", "foreign_owner", "original_source", "future_generation", "older_pointer",
    "approved_hash", "approved_size", "untyped_pointer", "untyped_operation",
])
def test_tampered_recovery_archive_is_atomic(
    api, project, work_payload, tmp_path, postgres_engine, damage,
):
    recovered(api, project, work_payload, tmp_path, postgres_engine)
    assert run(api)
    content = _export(postgres_engine, project)

    def mutate(_header, rows):
        receipt = rows["transcript_recoveries"][0]
        fields = {
            "foreign_owner": ("transcript_id", str(uuid4())),
            "original_source": ("original_source_path", "/another/asserted-source.jsonl"),
            "approved_hash": ("expected_sha256", "0" * 64),
            "approved_size": ("expected_size_bytes", receipt["expected_size_bytes"] + 1),
            "untyped_operation": ("operation_id", []),
        }
        if damage == "missing_journal":
            rows["transcript_recoveries"].clear()
        elif damage in fields:
            field, value = fields[damage]
            receipt[field] = value
        elif damage == "future_generation":
            receipt["expected_generation"] += 1
            receipt["resulting_generation"] += 1
        elif damage == "older_pointer":
            newer = dict(receipt, operation_id=str(uuid4()),
                         expected_generation=receipt["expected_generation"] + 1,
                         resulting_generation=receipt["resulting_generation"] + 1)
            rows["transcripts"][0]["generation"] += 1
            rows["transcript_recoveries"].append(newer)
        elif damage == "untyped_pointer":
            rows["transcripts"][0]["recovery_operation_id"] = {}

    before = _snapshot(postgres_engine, project)
    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]),
                        io.BytesIO(_rewrite(content, mutate)))
    assert _snapshot(postgres_engine, project) == before


def test_recovery_journal_follows_work_current_owner_not_original_audit_project(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, request, _, _ = recovered(api, project, work_payload, tmp_path, postgres_engine)
    target = api.post("/api/v1/projects", json={"name": "Recovery destination"}).json()
    path = item_path(project, work)
    current = api.get(path).json()["work_item"]
    moved = api.post(path + "/move", json=_move_payload(target, current["version"]))
    assert moved.status_code == 200, moved.text
    assert _snapshot(postgres_engine, project)["transcript_recoveries"] == []
    destination = _snapshot(postgres_engine, target)
    assert destination["transcript_recoveries"][0]["project_id"] == project["id"]
    assert destination["transcript_recoveries"][0]["operation_id"] == str(request.operation_id)
    content = _export(postgres_engine, target)
    restore_project(postgres_engine, UUID(target["id"]), io.BytesIO(content))
    assert _snapshot(postgres_engine, target)["transcript_recoveries"] \
        == destination["transcript_recoveries"]


@pytest.mark.parametrize("same_project", [False, True])
def test_archive_cannot_borrow_an_existing_other_transcript_approval(
    api, project, work_payload, tmp_path, postgres_engine, same_project,
):
    recovered(api, project, work_payload, tmp_path, postgres_engine)
    owner = project if same_project else api.post(
        "/api/v1/projects", json={"name": "Unrelated approval owner"}).json()
    second = tmp_path / "second"
    second.mkdir()
    _, other_request, _, _ = recovered(api, owner, work_payload, second, postgres_engine)
    content = _export(postgres_engine, project)

    def borrow(_header, rows):
        first = next(row for row in rows["transcripts"]
                     if row["id"] != str(other_request.transcript_id))
        first["recovery_operation_id"] = str(other_request.operation_id)

    before, untouched = _snapshot(postgres_engine, project), _snapshot(postgres_engine, owner)
    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]),
                        io.BytesIO(_rewrite(content, borrow)))
    assert _snapshot(postgres_engine, project) == before
    assert _snapshot(postgres_engine, owner) == untouched


@pytest.mark.parametrize("statement", [
    "UPDATE transcript_recoveries SET reason='rewritten'",
    "DELETE FROM transcript_recoveries", "TRUNCATE transcript_recoveries CASCADE",
])
def test_recovery_history_rejects_direct_mutation(
    api, project, work_payload, tmp_path, postgres_engine, statement,
):
    recovered(api, project, work_payload, tmp_path, postgres_engine)
    with pytest.raises(DBAPIError, match="transcript recovery history is immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(text(statement))


def test_privileged_load_still_requires_recovery_fk_and_domain_witness(
    api, project, work_payload, tmp_path, postgres_engine,
):
    recovered(api, project, work_payload, tmp_path, postgres_engine)
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text("UPDATE transcripts SET recovery_operation_id=:id"),
                           {"id": uuid4()})
        with pytest.raises(BackupError):
            validate_foreign_keys(connection)
        query = RECOVERY_FINDINGS["transcript_recovery_witness_invalid"]
        assert connection.scalar(text(query)) == 1
        with pytest.raises(BackupError):
            validate_integrity(connection)
        connection.rollback()


@pytest.mark.parametrize("assignment", [
    "original_source_path='/rewritten/assertion.jsonl'",
    "expected_generation=expected_generation+1,resulting_generation=resulting_generation+1",
    "expected_sha256=repeat('0',64)", "expected_size_bytes=expected_size_bytes+1",
])
def test_privileged_load_valid_fk_does_not_bypass_recovery_domain_witness(
    api, project, work_payload, tmp_path, postgres_engine, assignment,
):
    recovered(api, project, work_payload, tmp_path, postgres_engine)
    assert run(api)
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text(f"UPDATE transcript_recoveries SET {assignment}"))
        validate_foreign_keys(connection)
        query = RECOVERY_FINDINGS["transcript_recovery_witness_invalid"]
        assert connection.scalar(text(query)) == 1
        with pytest.raises(BackupError):
            validate_integrity(connection)
        connection.rollback()


def test_pending_restored_approval_rejects_changed_replacement_bytes(
    api, project, work_payload, tmp_path, postgres_engine,
):
    _, request, _, replacement = recovered(api, project, work_payload, tmp_path, postgres_engine)
    content = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    replacement.write_text('{"type":"user","message":{"role":"user","content":"changed"}}\n')
    assert copy_next_transcript(api.app.state.session_factory, api.app.state.settings)
    with api.app.state.session_factory() as database:
        row = database.get(Transcript, request.transcript_id)
        assert row.copy_status == "failed"
        assert row.copy_error_code == "transcript_recovery_content_changed"
