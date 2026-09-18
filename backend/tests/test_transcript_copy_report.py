"""Migration reporting must not declare victory before extraction or verification."""

import hashlib
import runpy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, register, run

SCRIPT = runpy.run_path(str(Path(__file__).parents[2] / "scripts/migrate_transcript_copies.py"))


@pytest.mark.parametrize("change", ["none", "content", "size", "missing", "symlink", "traversal"])
def test_copy_verification_reports_tampering_without_reading_outside_root(tmp_path, change):
    source = tmp_path / str(uuid4()) / str(uuid4()) / "transcript.jsonl"
    source.parent.mkdir(parents=True, mode=0o700)
    source.parent.parent.chmod(0o700)
    source.write_bytes(b"private synthetic transcript")
    expected_size = source.stat().st_size
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    key = str(source.relative_to(tmp_path))
    if change == "content":
        source.write_bytes(b"X" * expected_size)
    elif change == "size":
        source.write_bytes(b"truncated")
    elif change in {"missing", "symlink"}:
        source.unlink()
        if change == "symlink":
            source.symlink_to("/etc/passwd")
    elif change == "traversal":
        key = "../outside.jsonl"
    assert SCRIPT["verify_file"](SimpleNamespace(transcript_root=tmp_path), key,
                                  expected_size, expected_hash) == (change == "none")


@pytest.mark.postgres
def test_report_counts_reindexing_while_last_good_text_stays_readable(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, _ = register(api, project, work_payload, tmp_path)
    factory = api.app.state.session_factory
    with factory() as database:
        initial = SCRIPT["report"](database)
    assert initial["pending"] == initial["deferred_active_or_paused"] == 1
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4()),
    }).status_code == 200
    with factory() as database:
        pending = SCRIPT["report"](database)
    assert pending["copied"] == pending["index_pending"] == 1
    assert pending["pending"] == pending["index_failed"] == 0
    assert run(api)
    with factory() as database:
        completed = SCRIPT["report"](database)
    assert completed["index_pending"] == completed["failed"] == 0
    assert completed["normalized"] == 1
    assert completed["normalization_failed"] == completed["normalization_pending"] == 0
