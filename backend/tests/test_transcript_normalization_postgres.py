"""Normalized revisions survive rebuilds and fence capture races independently of jobs."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_normalized_storage import NORMALIZATIONS, SEGMENTS

from .test_leases_postgres import expire_lease
from .test_project_backup_archive import _export, _snapshot
from .test_transcript_indexing_postgres import collection, read, register, run

pytestmark = pytest.mark.postgres


def _messages(source):
    source.write_text("\n".join(json.dumps(row) for row in [
        {"role": "user", "content": "Human prose about authentication"},
        {"role": "user", "content": [
            {"type": "tool_result", "content": "lease_token_mismatch in tool output"}]},
        {"role": "assistant", "content": "We encountered an expired lease ourselves"},
    ]))


def test_content_kind_filter_excludes_user_role_tool_output_and_locates_match(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    _messages(source)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    assert ready["normalization_status"] == "ready" and ready["segment_count"] == 3
    assert ready["normalized_sha256"] != ready["sha256"] != ready["text_sha256"]
    path = collection(project)
    search = {"query": "lease_token_mismatch", "fulltext": True}
    result = api.post(path + "/search-content", json=search).json()
    hit = result["items"][0]
    assert hit["content_kind"] == "tool_result"
    assert "lease_token_mismatch" in hit["snippet"]
    assert api.post(path + "/search-content", json={
        **search, "content_kinds": ["human_text"]}).json()["total"] == 0
    assert api.post(path + "/search-content", json={
        **search, "content_kinds": ["tool_result"]}).json()["total"] == 1
    params = {"segment_id": hit["segment_id"],
              "expected_normalized_revision": hit["normalized_revision"], "before": 1, "after": 1}
    response = api.get(path + "/" + hit["id"] + "/text", params=params)
    assert response.status_code == 200, response.text
    segments = response.json()["segments"]
    assert [item["content_kind"] for item in segments] == [
        "human_text", "tool_result", "assistant_text"]
    assert api.get(path + "/" + hit["id"] + "/text", params={
        **params, "expected_normalized_revision": "0" * 64}).status_code == 409


def test_rebuild_reuses_normalized_revision_without_native_parser_or_source(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    source.unlink()

    def forbidden(*_args):
        raise AssertionError("Index rebuild must consume normalized records")

    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript", forbidden)
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    assert run(api)
    rebuilt = read(api, project, record)
    for field in ("normalized_revision", "normalized_sha256", "sha256", "text_sha256"):
        assert ready[field] == rebuilt[field]
    with api.app.state.session_factory() as database:
        assert len(database.execute(select(NORMALIZATIONS)).all()) == 1
        assert len(database.execute(select(SEGMENTS)).all()) == 1


def test_index_failure_preserves_normalized_stage_and_retry_uses_it(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api, stage_error=ExtractionError("extraction_unavailable", True))
    failed = read(api, project, record)
    assert failed["normalization_status"] == "ready" and failed["status"] == "pending"
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript",
                        lambda *_: pytest.fail("Native adapter repeated after normalized commit"))
    assert run(api)
    assert read(api, project, record)["normalized_revision"] == failed["normalized_revision"]


def test_artifact_text_budget_does_not_truncate_transcripts_or_backup(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"role": "user", "content": "x" * 5000 + " late evidence"}))
    api.app.state.settings.artifact_extraction_max_chars = 100
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    assert not ready["truncated"] and not ready["normalization_incomplete"]
    snapshot = _snapshot(postgres_engine, project)
    segment = snapshot["transcript_segments"][0]
    assert segment["text"].endswith("late evidence")
    assert snapshot["transcripts"][0]["normalized_text"].endswith(" late evidence")
    assert _export(postgres_engine, project)
    path = collection(project) + "/" + record["id"] + "/text"
    first = api.get(path, params={"segment_id": segment["segment_id"],
        "expected_normalized_revision": ready["normalized_revision"], "limit": 20}).json()
    assert first["next_segment_id"] == segment["segment_id"]
    assert first["next_segment_offset"] == 20
    last = api.get(path, params={"segment_id": segment["segment_id"],
        "expected_normalized_revision": ready["normalized_revision"], "offset": 5000}).json()
    assert last["text"] == " late evidence"


def test_failed_normalizer_upgrade_keeps_indexed_revision_and_locators_coherent(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from dataclasses import replace

    from mnemonic_api import transcript_normalization

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    normalize = transcript_normalization.normalize_transcript
    next_version = transcript_normalization.NORMALIZER_VERSION + 1
    monkeypatch.setattr(transcript_normalization, "NORMALIZER_VERSION", next_version)

    def revised(*args):
        result = normalize(*args)
        return replace(result, normalizer_version=next_version)

    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript", revised)
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    assert run(api, stage_error=ExtractionError("extraction_unavailable", True))
    failed = read(api, project, record)
    assert failed["normalized_revision"] == ready["normalized_revision"]
    assert failed["text_sha256"] == ready["text_sha256"]
    hit = api.get(collection(project), params={"query": "needle", "fulltext": True})\
        .json()["items"][0]
    assert hit["normalized_revision"] == ready["normalized_revision"]
    with api.app.state.session_factory.begin() as database:
        assert len(database.execute(select(NORMALIZATIONS)).all()) == 2
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert run(api)
    assert read(api, project, record)["normalized_revision"] != ready["normalized_revision"]


def test_context_window_continuation_reconstructs_large_preceding_segment(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text("\n".join(json.dumps({"role": "user", "content": value})
                                for value in ["prior " * 100, "anchor", "after"]))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    with api.app.state.session_factory() as database:
        segments = database.execute(select(SEGMENTS).order_by(SEGMENTS.c.ordinal)).mappings().all()
    path = collection(project) + "/" + record["id"] + "/text"
    params = {"segment_id": segments[1]["segment_id"],
              "expected_normalized_revision": ready["normalized_revision"],
              "before": 1, "after": 1, "limit": 50}
    collected = {row["segment_id"]: "" for row in segments}
    first = True
    for _ in range(20):
        response = api.get(path, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        if first:
            assert body["total_chars"] == sum(len(row["text"]) for row in segments) + 4
            first = False
        for item in body["segments"]:
            collected[item["segment_id"]] += item["text"]
        if body["next_segment_id"] is None:
            break
        params.update(segment_id=body["next_segment_id"], offset=body["next_segment_offset"],
                      before=0, after=body["next_segment_after"])
    else:
        pytest.fail("Continuation did not finish the selected context window")
    assert collected == {row["segment_id"]: row["text"] for row in segments}


def test_large_structured_payload_is_not_hydrated_by_bounded_read(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from sqlalchemy import event

    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "id": "call", "name": "read", "input": {"large": "x" * 50000}}]}))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    with api.app.state.session_factory() as database:
        segment_id = database.scalar(select(SEGMENTS.c.segment_id))
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _many):
        if "transcript_segments" in statement:
            statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", observed)
    try:
        response = api.get(collection(project) + "/" + record["id"] + "/text", params={
            "segment_id": segment_id, "expected_normalized_revision": ready["normalized_revision"],
            "limit": 20})
    finally:
        event.remove(postgres_engine, "before_cursor_execute", observed)
    assert response.status_code == 200, response.text
    assert response.json()["segments"][0]["payload"] is None
    assert "payload_omitted_for_budget" in response.json()["segments"][0]["dispositions"]
    assert any("substr(" in statement for statement in statements)
    assert not any(statement.startswith("SELECT transcript_segments.segment_data ->")
                   for statement in statements)


def test_normalizer_failure_retains_last_ready_text_and_active_revision(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    original = read(api, project, record)
    monkeypatch.setattr("mnemonic_api.transcript_normalization.NORMALIZER_VERSION",
                        original["normalizer_version"] + 1)

    def unsupported(*_args):
        raise ExtractionError("transcript_unsupported_format")

    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript", unsupported)
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    assert run(api)
    current = read(api, project, record)
    assert current["status"] == "ready"
    assert current["normalization_status"] == "failed"
    assert current["normalization_error_code"] == "transcript_unsupported_format"
    assert current["normalized_revision"] == original["normalized_revision"]
    assert current["text_sha256"] == original["text_sha256"]
    assert api.get(collection(project)).json()["indexing_incomplete"]


def test_tool_name_match_retains_locator_in_filtered_search(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "unique_tool_needle", "input": {"path": "file"}}]}))
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    result = api.post(collection(project) + "/search-content", json={
        "query": "unique_tool_needle", "fulltext": True, "content_kinds": ["tool_call"]}).json()
    assert result["total"] == 1
    hit = result["items"][0]
    assert "unique_tool_needle" in hit["snippet"]
    assert hit["segment_id"] and hit["content_kind"] == "tool_call"


def test_backup_checks_normalized_manifest_and_segment_integrity(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from mnemonic_backup.archive_normalization import validate_normalized_transcripts
    from mnemonic_backup.archive_schema import BackupError

    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    snapshot = _snapshot(postgres_engine, project)
    validate_normalized_transcripts(snapshot)
    snapshot["transcript_segments"][0]["segment_data"]["text"] = "tampered evidence"
    with pytest.raises(BackupError, match="inconsistent content"):
        validate_normalized_transcripts(snapshot)


def test_segment_reads_enforce_every_supplied_pin_and_require_an_anchor(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    with api.app.state.session_factory() as database:
        segment_id = database.scalar(select(SEGMENTS.c.segment_id))
    path = collection(project) + "/" + record["id"] + "/text"
    assert api.get(path, params={"before": 1}).json()["detail"]["code"] \
        == "transcript_segment_required"
    assert api.get(path, params={"segment_id": segment_id,
        "expected_normalized_revision": ready["normalized_revision"],
        "expected_sha256": "0" * 64}).json()["detail"]["code"] == "transcript_content_changed"


@pytest.mark.parametrize("field,value", [
    ("snapshot_id", "00000000-0000-0000-0000-000000000001"),
    ("copy_sha256", "0" * 64),
    ("normalized_sha256", "0" * 64),
    ("normalization_schema_version", 999),
    ("normalizer_version", 999),
    ("segment_count", 999),
    ("normalized_size_bytes", 999),
    ("normalization_incomplete", True),
])
def test_backup_rejects_active_normalization_projection_drift(
    api, project, work_payload, tmp_path, postgres_engine, field, value,
):
    from mnemonic_backup.archive_normalization import validate_normalized_transcripts
    from mnemonic_backup.archive_schema import BackupError

    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    snapshot = _snapshot(postgres_engine, project)
    validate_normalized_transcripts(snapshot)
    snapshot["transcripts"][0][field] = value
    with pytest.raises(BackupError, match="does not match its captured source"):
        validate_normalized_transcripts(snapshot)


def test_normalization_warnings_do_not_mean_search_text_was_truncated(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    with source.open("a") as output:
        output.write('\n{"type":"future-unknown-record","state":"synthetic bookkeeping"}\n')
    expire_lease(postgres_engine, work["id"])

    assert run(api)
    ready = read(api, project, record)
    assert ready["normalization_incomplete"] and not ready["truncated"]
    assert ready["metadata"].get("transcript:metadata_limited") is None
    assert api.get(collection(project)).json()["indexing_incomplete"]
    page = api.get(collection(project) + "/" + record["id"] + "/text", params={
        "expected_sha256": ready["text_sha256"],
    }).json()
    assert not page["truncated"] and "rare needle" in page["text"]


def test_migration_recomputes_old_truncation_flags_from_retained_segments(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from sqlalchemy import text

    from .test_artifact_extraction_migration_postgres import migrate

    work, _, record, source = register(api, project, work_payload, tmp_path)
    with source.open("a") as output:
        output.write('\n{"type":"future-unknown-record","state":"synthetic bookkeeping"}\n')
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    original = read(api, project, record)
    from .transcript_legacy import restore_legacy_native_layout

    restore_legacy_native_layout(api)
    migrate(postgres_engine, "0042_transcript_health", downgrade=True)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE transcripts SET truncated=true"))
    migrate(postgres_engine, "head")
    queued = read(api, project, record)
    assert queued["status"] == "ready" and queued["index_status"] == "pending"
    assert queued["copy_status"] == "ready" and queued["text_sha256"] == original["text_sha256"]
    source.unlink()
    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript",
                        lambda *_: pytest.fail("Persisted normalization should be reused"))
    assert run(api)
    ready = read(api, project, record)
    assert ready["normalization_incomplete"] and not ready["truncated"]
    assert ready["normalized_revision"] == original["normalized_revision"]
    assert ready["text_sha256"] == original["text_sha256"]


def test_coverage_refresh_does_not_alter_an_active_work_lease(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from sqlalchemy import text

    from .test_artifact_extraction_migration_postgres import migrate

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    from .transcript_legacy import restore_legacy_native_layout

    restore_legacy_native_layout(api)
    migrate(postgres_engine, "0042_transcript_health", downgrade=True)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE transcripts SET truncated=true"))
        connection.execute(text("UPDATE work_leases SET expires_at=clock_timestamp() "
                                "+ interval '10 minutes' WHERE work_item_id=:id"),
                           {"id": work["id"]})
        before = connection.execute(text("SELECT * FROM work_leases WHERE work_item_id=:id"),
                                    {"id": work["id"]}).mappings().one()
        generation = connection.scalar(text("SELECT generation FROM transcripts"))
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        after = connection.execute(text("SELECT * FROM work_leases WHERE work_item_id=:id"),
                                   {"id": work["id"]}).mappings().one()
        assert after == before
        assert connection.scalar(text("SELECT generation FROM transcripts")) == generation
    assert read(api, project, record)["index_status"] == "ready"


@pytest.mark.parametrize("spare_characters", [0, 1])
def test_rebuild_at_exact_text_budget_does_not_invent_truncation_from_trailing_records(
    api, project, work_payload, tmp_path, postgres_engine, spare_characters,
):
    from uuid import uuid4

    work, _, record, source = register(api, project, work_payload, tmp_path)
    with source.open("a") as output:
        output.write('\n{"type":"future-unknown-record","state":"synthetic bookkeeping"}\n')
    api.app.state.settings.artifact_extraction_max_chars = (
        len("user: rare needle in transcript") + spare_characters)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    before = read(api, project, record)
    assert not before["truncated"] and before["normalization_incomplete"]
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4()),
    }).status_code == 200
    source.unlink()
    assert run(api)
    rebuilt = read(api, project, record)
    assert not rebuilt["truncated"] and rebuilt["normalization_incomplete"]
    assert rebuilt["text_sha256"] == before["text_sha256"]
