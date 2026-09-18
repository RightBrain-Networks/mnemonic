"""Downgrade cannot erase durable transcript provenance, settings, or receipts."""

import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.transcript_job_queue import enqueue_transcript_jobs

from .test_artifact_extraction_migration_postgres import migrate
from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, register, run

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("populated", ["transcripts", "transcript_settings", "transcript_rebuilds"])
def test_populated_transcript_data_blocks_downgrade(
    api, project, work_payload, tmp_path, postgres_engine, populated,
):
    if populated == "transcripts":
        work, _, _, _ = register(api, project, work_payload, tmp_path)
        expire_lease(postgres_engine, work["id"])
        assert run(api)
    elif populated == "transcript_settings":
        response = api.patch(f"/api/v1/projects/{project['id']}/transcript-settings", json={
            "enabled": False, "max_file_size_bytes": 1024, "expected_revision": 1})
        assert response.status_code == 200, response.text
    else:
        response = api.post(collection(project) + "/rebuild",
                            json={"client_operation_id": str(uuid4())})
        assert response.status_code == 200, response.text
    with postgres_engine.connect() as connection:
        before = connection.scalar(text(f"SELECT count(*) FROM {populated}"))
    assert before == 1
    with pytest.raises(RuntimeError, match="cannot be safely downgraded"):
        migrate(postgres_engine, "0031_review_decisions", downgrade=True)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text(f"SELECT count(*) FROM {populated}")) == before
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) \
            == "0045_transcript_capacity"


def test_empty_transcript_schema_downgrades_and_upgrades(pristine_postgres_engine):
    migrate(pristine_postgres_engine, "0031_review_decisions", downgrade=True)
    with pristine_postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT to_regclass('transcripts')")) is None
    migrate(pristine_postgres_engine, "head")
    with pristine_postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transcripts")) == 0


@pytest.mark.parametrize("with_sources", [False, True])
def test_import_receipts_and_sources_prevent_downgrade(api, project, tmp_path,
                                                     postgres_engine, with_sources):
    from .test_transcript_imports_postgres import import_folder, source

    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    if with_sources:
        source(tmp_path)
    receipt = import_folder(api, project, tmp_path).json()
    with pytest.raises(RuntimeError, match="imports cannot be safely downgraded"):
        migrate(postgres_engine, "0032_agent_transcripts", downgrade=True)
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transcript_imports")) == 1
        assert connection.scalar(text("SELECT count(*) FROM transcripts")) == int(with_sources)
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) \
            == "0045_transcript_capacity"
    assert import_folder(api, project, tmp_path, receipt["client_operation_id"]).json() == receipt


def test_copy_migration_blocks_rollback_without_changing_enrollment_or_receipts(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from .test_transcript_indexing_postgres import read

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    original = read(api, project, record)
    rebuild = {"client_operation_id": str(uuid4())}
    response = api.post(collection(project) + "/rebuild", json=rebuild).json()
    assert run(api)
    before = read(api, project, record)
    message = "(transcript copies|normalized conversations) cannot be safely downgraded"
    with pytest.raises(RuntimeError, match=message):
        migrate(postgres_engine, "0032_agent_transcripts", downgrade=True)
    assert read(api, project, record) == before
    assert before["sha256"] == original["sha256"]
    assert api.post(collection(project) + "/rebuild", json=rebuild).json() == response


def test_populated_0035_upgrade_queues_every_source_and_preserves_ready_evidence(
    api, project, postgres_engine, tmp_path,
):
    migrate(postgres_engine, "0035_prompt_library", downgrade=True)
    identities = {status: uuid4() for status in ("ready", "failed", "processing")}
    retained_text = "user: Legacy evidence must survive the storage migration."
    retained_hash = hashlib.sha256(retained_text.encode()).hexdigest()
    with postgres_engine.begin() as connection:
        for status, identity in identities.items():
            connection.execute(text("""
                INSERT INTO transcripts(
                    id, import_project_id, kind, client, source_path, status, generation, attempts,
                    indexing_started_at, indexing_completed_at, error_code, size_bytes,
                    mime_type, format, sha256, text_sha256, normalized_text, extracted_metadata,
                    lease_token, lease_expires_at
                ) VALUES (
                    :id, :project, 'imported', 'claude_code', :path, CAST(:status AS text), 4, 2,
                    clock_timestamp() - interval '5 minutes',
                    CASE WHEN :status = 'processing' THEN NULL ELSE clock_timestamp() END,
                    CASE WHEN :status = 'failed' THEN 'transcript_io_error' ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN 128 ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN 'application/x-ndjson' ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN 'claude-code-jsonl' ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN repeat('a', 64) ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN :hash ELSE NULL END,
                    CASE WHEN :status = 'ready' THEN :body ELSE NULL END,
                    '{"legacy":["retained property"]}'::jsonb,
                    CASE WHEN :status = 'processing' THEN :token ELSE NULL END,
                    CASE WHEN :status = 'processing'
                         THEN clock_timestamp() + interval '1 hour' ELSE NULL END
                )
            """), {"id": identity, "project": project["id"], "status": status,
                   "path": str(tmp_path / f"legacy-{status}.jsonl"), "hash": retained_hash,
                   "body": retained_text, "token": uuid4()})
        before = {row["id"]: dict(row) for row in connection.execute(
            text("SELECT * FROM transcripts")).mappings()}
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) \
            == "0035_prompt_library"
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        after = {row["id"]: dict(row) for row in connection.execute(
            text("SELECT * FROM transcripts")).mappings()}
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) \
            == "0045_transcript_capacity"
        assert connection.scalar(text("SELECT count(*) FROM background_jobs")) == 0
    for status, identity in identities.items():
        row = after[identity]
        expected = before[identity]
        expected = expected | {"generation": expected["generation"] + 1, "attempts": 0,
                               "next_attempt_at": row["next_attempt_at"]}
        if status in {"processing", "failed"}:
            expected = expected | {"status": "pending", "lease_token": None,
                                   "lease_expires_at": None}
        if status == "ready":
            expected = expected | {"extracted_metadata": expected["extracted_metadata"] | {
                "transcript:index_created_at": [
                    expected["indexing_completed_at"].isoformat().replace("+00:00", "Z")],
            }}
        assert {name: row[name] for name in expected} == expected
        assert isinstance(row["snapshot_id"], UUID)
        assert row["copy_status"] == "pending" and row["copy_attempts"] == 0
        assert row["copy_next_attempt_at"] is not None
        assert all(row[name] is None for name in (
            "storage_key", "copy_sha256", "copy_size_bytes", "copied_at", "copy_error_code",
            "copy_lease_token", "copy_lease_expires_at", "reindex_error_code",
        ))
        assert row["reindex_status"] == ("pending" if status == "ready" else None)
    assert len({row["snapshot_id"] for row in after.values()}) == 3
    response = api.get(collection(project) + f"/{identities['ready']}/content")
    assert response.status_code == 200 and response.text == retained_text
    assert api.get(collection(project), params={"detail": "full"}).json()["indexing_incomplete"]
    with api.app.state.session_factory.begin() as database:
        assert enqueue_transcript_jobs(database, api.app.state.settings) == 3
        jobs = database.execute(text("SELECT kind, payload FROM background_jobs")).all()
    assert all(row.kind == "transcript_copy" for row in jobs)
    assert {row.payload["transcript_id"] for row in jobs} == {
        str(identity) for identity in identities.values()
    }
