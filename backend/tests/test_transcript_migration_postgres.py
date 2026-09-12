"""Downgrade cannot erase durable transcript provenance, settings, or receipts."""

from uuid import uuid4

import pytest
from sqlalchemy import text

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
            == "0034_variable_work_leases"


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
            == "0034_variable_work_leases"
    assert import_folder(api, project, tmp_path, receipt["client_operation_id"]).json() == receipt


def test_import_migration_preserves_existing_enrollment_bytes_and_receipts(
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
    migrate(postgres_engine, "0032_agent_transcripts", downgrade=True)
    migrate(postgres_engine, "head")
    assert read(api, project, record) == before
    assert before["sha256"] == original["sha256"]
    assert api.post(collection(project) + "/rebuild", json=rebuild).json() == response
