"""A real normalized conversation beyond the historical 8 MiB row limit is recoverable."""

import io
import json
from uuid import UUID

import pytest
from sqlalchemy import text

from mnemonic_backup.archive import BackupError, export_project, restore_project
from mnemonic_backup.schedules import BackupSchedule, save_schedule

from .test_leases_postgres import expire_lease
from .test_project_backup_archive import _export, _rewrite, _snapshot
from .test_transcript_indexing_postgres import register, run


@pytest.mark.postgres
def test_large_transcript_backup_and_spooled_restore(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    with source.open("a") as output:
        for index in range(512):
            output.write(json.dumps({
                "type": "user", "sessionId": "synthetic-session",
                "uuid": f"large-message-{index}",
                "message": {"role": "user", "content": f"Message {index}: " + "x" * 20_000},
            }) + "\n")
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    query = text("SELECT normalized_sha256, octet_length(normalized_text), generation "
                 "FROM transcripts WHERE id = :id")
    with postgres_engine.connect() as connection:
        before = connection.execute(query, {"id": record["id"]}).one()
    assert before[1] > 8 * 1024 * 1024
    archive = io.BytesIO()
    export_project(postgres_engine, UUID(project["id"]), archive)
    archive.seek(0)
    spool_root = tmp_path / "spools"
    spool_root.mkdir(mode=0o700)
    restore_project(postgres_engine, UUID(project["id"]), archive,
                    max_bytes=64 * 1024 * 1024, spool_directory=spool_root)
    with postgres_engine.connect() as connection:
        after = connection.execute(query, {"id": record["id"]}).one()
    assert after[:2] == before[:2]
    assert after[2] > before[2]
    assert not list(spool_root.iterdir())


@pytest.mark.postgres
def test_restore_rejects_unknown_schedule_timezone_without_changing_project(
    project, postgres_engine,
):
    save_schedule(postgres_engine, UUID(project["id"]), BackupSchedule(enabled=True))
    source = _export(postgres_engine, project)
    before = _snapshot(postgres_engine, project)

    def tamper(_header, rows):
        rows["backup_settings"][0]["timezone"] = "Unknown/Not-A-Timezone"

    with pytest.raises(BackupError, match="invalid nightly"):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(_rewrite(source, tamper)))
    assert _snapshot(postgres_engine, project) == before


@pytest.mark.postgres
def test_backup_snapshot_does_not_wait_for_another_projects_writer(
    api, project, postgres_engine,
):
    other = api.post("/api/v1/projects", json={"name": "Other active project"}).json()
    with postgres_engine.connect() as writer, writer.begin():
        writer.execute(text("UPDATE projects SET name = 'Writer in progress' WHERE id = :id"),
                       {"id": other["id"]})
        # ROW EXCLUSIVE locks are compatible with the backup's ACCESS SHARE
        # snapshot; the former SHARE implementation times out here.
        output = io.BytesIO()
        result = export_project(postgres_engine, UUID(project["id"]), output)
        assert result["project_id"] == project["id"]
        assert output.getvalue().startswith(b"BZh")
