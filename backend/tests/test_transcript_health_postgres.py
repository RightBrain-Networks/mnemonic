"""Fresh assertions, worker observations, and automatic recovery preserve domain guards."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.models import Transcript, TranscriptWorkerHealth
from mnemonic_api.transcript_access import RECHECK_SECONDS
from mnemonic_api.transcript_copying import copy_next_transcript
from mnemonic_api.transcript_health import TranscriptHealthReporter

from .report_fixtures import reported
from .test_leases_postgres import claim_payload, create_work, expire_lease, item_path
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def health(api, project):
    result = api.get(collection(project) + "/health")
    assert result.status_code == 200, result.text
    return result.json()


def worker_report(api):
    with api.app.state.session_factory() as database:
        TranscriptHealthReporter().publish(database, api.app.state.settings)
        database.commit()


@pytest.mark.parametrize("kind,code", [
    ("missing", "transcript_source_missing"), ("outside", "transcript_path_not_allowed"),
    ("directory", "transcript_not_regular_file"), ("output", "transcript_native_path_required"),
    ("symlink", "transcript_symlink_rejected"),
])
def test_fresh_invalid_assertions_are_rejected_before_claim_commits(
    api, project, work_payload, tmp_path, kind, code,
):
    file = tmp_path / "session.jsonl"
    if kind == "directory":
        file.mkdir()
    elif kind == "symlink":
        target = tmp_path / "real.jsonl"
        target.write_text("fixture")
        file.symlink_to(target)
    if kind == "output":
        file = tmp_path / "task.output"
    api.app.state.settings.transcript_allowed_roots = [] if kind == "outside" else [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    path = item_path(project, work)
    payload = {**claim_payload("invalid-source"), "session_transcript":
               {"client": "claude_code", "path": str(file)}}
    result = api.post(path + "/claim", json=payload)
    assert result.status_code == 422, result.text
    assert result.json()["detail"]["code"] == code
    assert result.json()["detail"]["context"]["attempt_not_committed"] is True
    assert api.get(collection(project)).json()["total"] == 0
    assert not api.get(path + "/context").json()["readiness"]["has_active_lease"]
    payload["session_transcript"] = None
    assert api.post(path + "/claim", json=payload).status_code == 200


def test_claim_and_closeout_receipts_replay_after_files_disappear(
    api, project, work_payload, tmp_path,
):
    work, receipt, _, source = register(api, project, work_payload, tmp_path)
    path = item_path(project, work)
    child = tmp_path / "child.jsonl"
    child.write_bytes(source.read_bytes())
    payload = reported({"expected_version": work["version"],
        "lease_token": receipt["lease_token"], "checkpoint": {"prompt": "verified",
            "source_client": "test", "source_session_id": "test"},
        "subagent_transcripts": [{"client": "claude_code", "path": str(child)}]})
    completed = api.post(path + "/complete", json=payload)
    assert completed.status_code == 200, completed.text
    child.unlink()
    source.unlink()
    api.app.state.settings.transcript_allowed_roots = []
    assert api.post(path + "/complete", json=payload).json() == completed.json()


def test_bad_child_rolls_back_all_closeout_transcripts_and_work(
    api, project, work_payload, tmp_path,
):
    work, receipt, _, source = register(api, project, work_payload, tmp_path)
    path = item_path(project, work)
    payload = reported({"expected_version": work["version"],
        "lease_token": receipt["lease_token"], "checkpoint": {"prompt": "verified",
            "source_client": "test", "source_session_id": "test"},
        "subagent_transcripts": [{"client": "claude_code", "path": str(source)},
                                 {"client": "claude_code",
                                  "path": str(tmp_path / "missing.jsonl")}]})
    result = api.post(path + "/complete", json=payload)
    assert result.status_code == 422, result.text
    assert api.get(collection(project)).json()["total"] == 1
    assert api.get(path).json()["work_item"]["status"] == work["status"]
    assert api.get(path + "/context").json()["readiness"]["has_active_lease"]


def test_missing_source_reports_and_recovers_without_rebuild(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    data = source.read_bytes()
    source.unlink()
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert read(api, project, record)["copy_error_code"] == "transcript_source_missing"
    worker_report(api)
    result = health(api, project)
    assert result["affected_transcripts"] == 1
    warning = next(row for row in result["warnings"] if row["code"] == "transcript_source_missing")
    assert warning["path"] == str(source)
    assert warning["retry_at"] is not None
    assert result["recheck_seconds"] == RECHECK_SECONDS
    assert not run(api)  # No hot loop while an operator investigates.
    source.write_bytes(data)
    with api.app.state.session_factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.copy_next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        database.commit()
    assert run(api)
    assert read(api, project, record)["copy_status"] == "ready"
    assert health(api, project)["warnings"] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses DAC")
def test_permission_diagnostics_include_exact_path_identity_and_needed_permissions(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    source.chmod(0)
    expire_lease(postgres_engine, work["id"])
    try:
        assert copy_next_transcript(api.app.state.session_factory, api.app.state.settings)
        warning = next(row for row in health(api, project)["warnings"]
                       if row["code"] == "transcript_permission_denied")
        assert warning["path"] == str(source)
        assert warning["uid"] == os.geteuid() and warning["mode"] == "0000"
        assert "read (r)" in warning["action"] and "search/execute (x)" in warning["action"]
    finally:
        source.chmod(0o600)


def test_health_reports_worker_outage_mismatch_and_project_isolation(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    assert any(row["code"] == "transcript_worker_unavailable"
               for row in health(api, project)["warnings"])
    worker_report(api)
    with api.app.state.session_factory() as database:
        worker = database.scalar(select(TranscriptWorkerHealth))
        worker.report = {**worker.report, "roots": ["/different/source"]}
        database.commit()
    assert any(row["code"] == "transcript_worker_configuration_mismatch"
               for row in health(api, project)["warnings"])
    source.unlink()
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    other = api.post("/api/v1/projects", json={"name": "isolated health"}).json()
    assert health(api, other)["affected_transcripts"] == 0
    assert api.get(f"/api/v1/projects/{uuid4()}/transcripts/health").status_code == 404
    with api.app.state.session_factory() as database:
        worker = database.scalar(select(TranscriptWorkerHealth))
        worker.checked_at = datetime.now(UTC) - timedelta(minutes=3)
        database.commit()
    assert any(row["code"] == "transcript_worker_unavailable"
               for row in health(api, project)["warnings"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses DAC")
def test_shared_blocked_parent_reports_one_warning_with_all_affected_transcripts(
    api, project, work_payload, tmp_path, postgres_engine,
):
    folders = [tmp_path / "first", tmp_path / "second"]
    for folder in folders:
        folder.mkdir()
        work, _, _, _ = register(api, project, work_payload, folder)
        expire_lease(postgres_engine, work["id"])
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    tmp_path.chmod(0)
    try:
        assert run(api)
        assert run(api)
        result = health(api, project)
        copies = [row for row in result["warnings"] if row["affected"]]
        assert result["affected_transcripts"] == 2
        assert len(copies) == 1
        assert copies[0]["affected"] == 2
        assert copies[0]["path"] == str(tmp_path)
        assert copies[0]["code"] == "transcript_permission_denied"
        assert result["warnings_omitted"] == 0
    finally:
        tmp_path.chmod(0o700)
