"""The shipped aggregate audit checks real retained bytes without publishing bodies."""

import runpy
from pathlib import Path

import pytest

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import register, run

pytestmark = pytest.mark.postgres


def test_native_and_normalized_integrity_audit_is_read_only_and_body_free(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, _, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    source.unlink()
    path = Path(__file__).resolve().parents[2] / "scripts/audit_transcript_normalization.py"
    audit = runpy.run_path(str(path))["audit"]
    monkeypatch.setitem(audit.__globals__, "build_engine", lambda _: postgres_engine)
    report = audit(api.app.state.settings, verify_native=True)
    assert report["counts"] == {"native_copies_verified": 1, "normalized_manifests_verified": 1,
                                "stored_normalizations_verified": 1, "transcripts": 1}
    assert "rare needle" not in str(report)
    assert str(source) not in str(report)


def test_audit_detects_a_corrupted_stored_segment_hash(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from sqlalchemy import text

    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    # This only corrupts a disposable test schema's projection, never a native copy.
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE transcripts SET normalized_sha256=:digest"),
                           {"digest": "0" * 64})
    path = Path(__file__).resolve().parents[2] / "scripts/audit_transcript_normalization.py"
    audit = runpy.run_path(str(path))["audit"]
    monkeypatch.setitem(audit.__globals__, "build_engine", lambda _: postgres_engine)
    report = audit(api.app.state.settings, verify_native=True)
    assert report["counts"]["normalized_hash_mismatch"] == 1
    assert report["counts"]["stored_segments_hash_mismatch"] == 1
    assert report["counts"]["native_copies_verified"] == 1
