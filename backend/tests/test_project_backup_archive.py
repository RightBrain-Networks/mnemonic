"""Project archive boundaries, PostgreSQL restore atomicity, and untrusted uploads."""

import bz2
import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_backup.archive import BackupError, export_project, restore_project
from mnemonic_backup.archive_format import read_archive, write_archive
from mnemonic_backup.archive_schema import (
    IDENTITY_COLUMNS,
    MAX_ARCHIVE_IDENTITY,
    advance_sequences,
    read_rows,
)

from .code_review_fixtures import claim_review, finding, mandatory, result_payload, result_url
from .conftest import _catalog_digest, _disposable_schema
from .test_artifacts_postgres import headers, upload
from .test_duplicate_handling_postgres import merge_work
from .test_human_gates_postgres import gate_path, gate_request
from .test_relationships_postgres import add_relationship, relationship_payload
from .test_work_item_moves_postgres import _move_payload
from .test_work_items_postgres import create_work

pytestmark = pytest.mark.postgres


def _export(engine, project) -> bytes:
    output = io.BytesIO()
    export_project(engine, UUID(project["id"]), output)
    assert output.getvalue().startswith(b"BZh")
    return output.getvalue()


def _rewrite(content: bytes, mutate) -> bytes:
    header, rows = read_archive(io.BytesIO(content), 10_000_000)
    mutate(header, rows)
    header["counts"] = {name: len(records) for name, records in rows.items()}
    output = io.BytesIO()
    write_archive(output, header, rows, 10_000_000)
    return output.getvalue()


def _snapshot(engine, project):
    with engine.begin() as connection:
        return read_rows(connection, project["id"])


def test_roundtrip_preserves_other_project_and_exact_receipts(api, project, work_payload,
                                                            postgres_engine):
    operation = str(uuid4())
    created = create_work(api, project, work_payload, client_operation_id=operation)
    original = _snapshot(postgres_engine, project)
    content = _export(postgres_engine, project)
    other = api.post("/api/v1/projects", json={"name": "Unaffected project"}).json()
    create_work(api, other, work_payload)
    untouched = _snapshot(postgres_engine, other)
    create_work(api, project, work_payload, title="Created after snapshot")
    with postgres_engine.connect() as connection:
        catalog = _catalog_digest(connection, _disposable_schema(postgres_engine))
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    restored = _snapshot(postgres_engine, project)
    assert restored["project_activity_heads"][0]["stream_id"] != \
        original["project_activity_heads"][0]["stream_id"]
    original["project_activity_heads"] = restored["project_activity_heads"]
    assert restored == original
    assert _snapshot(postgres_engine, other) == untouched
    with postgres_engine.connect() as connection:
        assert _catalog_digest(connection, _disposable_schema(postgres_engine)) == catalog
    replay = api.post(f"/api/v1/projects/{project['id']}/work-items",
                      json={**work_payload, "client_operation_id": operation})
    assert replay.status_code == 201, replay.text
    assert replay.json() == created
    create_work(api, project, work_payload, title="Global identity advances after restore")


def test_restore_missing_project_from_archive(api, project, postgres_engine):
    content = _export(postgres_engine, project)
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        for table in ("project_settings", "project_activity", "project_activity_heads",
                      "project_job_completion_report_counts"):
            connection.execute(text(f"DELETE FROM {table} WHERE project_id=CAST(:id AS uuid)"),
                               {"id": project["id"]})
        connection.execute(text("DELETE FROM projects WHERE id=CAST(:id AS uuid)"),
                           {"id": project["id"]})
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    assert api.get(f"/api/v1/projects/{project['id']}").status_code == 200


def test_artifact_bytes_excluded_and_files_unchanged(api, project, postgres_engine, tmp_path):
    storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
    api.app.state.artifact_storage = storage
    marker = b"ARTIFACT-CONTENT-MUST-NEVER-ENTER-THE-PROJECT-BACKUP"
    artifact = upload(api, project, filename="separate.txt", body=marker)
    content = _export(postgres_engine, project)
    assert marker not in bz2.decompress(content)
    path = storage.root / project["id"] / artifact["id"] / artifact["filename"]
    replace = api.put(f"/api/v1/projects/{project['id']}/artifacts/{artifact['id']}/content",
                      content=b"Managed by separate backup system",
                      headers=headers({"filename": "separate.txt"}, revision=1))
    assert replace.status_code == 200, replace.text
    before = path.stat()
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    assert path.read_bytes() == b"Managed by separate backup system"
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert _snapshot(postgres_engine, project)["artifacts"][0]["revision"] == 1


@pytest.mark.parametrize("damage", ["truncated", "garbage", "concatenated", "sql", "checksum"])
def test_malformed_upload_does_not_change_database(api, project, postgres_engine, damage):
    content = _export(postgres_engine, project)
    variants = {
        "truncated": content[:-8], "garbage": content + b"garbage",
        "concatenated": content + bz2.compress(b"{}\n"),
        "sql": bz2.compress(b"DROP DATABASE mnemonic;\n"),
        "checksum": bz2.compress(bz2.decompress(content).replace(b'"name":"First project"',
                                                                b'"name":"Hostile value"')),
    }
    before = _snapshot(postgres_engine, project)
    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(variants[damage]))
    assert error.value.status == 422
    assert _snapshot(postgres_engine, project) == before


@pytest.mark.parametrize("damage", ["foreign", "schema", "missing_column", "duplicate",
                                   "foreign_key", "witness", "pending", "sql_value"])
def test_semantically_invalid_archive_rolls_back(api, project, work_payload, postgres_engine,
                                                damage):
    create_work(api, project, work_payload, client_operation_id=str(uuid4()))
    content = _export(postgres_engine, project)

    def mutate(header, rows):
        match damage:
            case "foreign":
                rows["work_items"][0]["project_id"] = str(uuid4())
            case "schema":
                header["schema"] = "unsupported_schema"
            case "missing_column":
                del rows["work_items"][0]["priority"]
            case "duplicate":
                rows["work_items"].append(rows["work_items"][0].copy())
            case "foreign_key":
                rows["work_items"][0]["initial_checkpoint_id"] = str(uuid4())
            case "witness":
                rows["project_activity_heads"][0]["last_sequence"] += 1
            case "pending":
                rows["client_operations"][0]["state"] = "pending"
            case "sql_value":
                rows["work_items"][0]["id"] = "'); DROP TABLE projects; --"

    damaged = _rewrite(content, mutate)
    before = _snapshot(postgres_engine, project)
    with pytest.raises(BackupError):
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(damaged))
    assert _snapshot(postgres_engine, project) == before
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SHOW session_replication_role")) == "origin"


def test_wrong_project_and_expanded_size_limit(api, project, postgres_engine):
    content = _export(postgres_engine, project)
    with pytest.raises(BackupError, match="another project"):
        restore_project(postgres_engine, uuid4(), io.BytesIO(content))
    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content), max_bytes=64)
    assert error.value.status == 413
    with pytest.raises(BackupError) as error:
        export_project(postgres_engine, UUID(project["id"]), io.BytesIO(), max_bytes=64)
    assert error.value.status == 413


def test_unknown_project_cannot_be_exported(api, postgres_engine):
    with pytest.raises(BackupError) as error:
        export_project(postgres_engine, uuid4(), io.BytesIO())
    assert error.value.code == "project_not_found"


def test_restored_stream_rejects_old_cursor(api, project, postgres_engine):
    path = f"/api/v1/projects/{project['id']}/activity"
    cursor = api.get(path, params={"start": "now"}).json()["next_cursor"]
    content = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    stale = api.get(path, params={"after": cursor})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "activity_stream_changed"
    assert api.get(path, params={"start": "now"}).status_code == 200


@pytest.mark.parametrize("relationship_before_backup", [False, True])
def test_cross_project_relationship_preservation_or_conflict(api, project, work_payload,
                                                           postgres_engine,
                                                           relationship_before_backup):
    other = api.post("/api/v1/projects", json={"name": "Other endpoint"}).json()
    source = create_work(api, project, work_payload)["work_item"]
    target = create_work(api, other, work_payload)["work_item"]
    content = _export(postgres_engine, project)
    add_relationship(api, project, relationship_payload(source, target, "blocks"))
    if relationship_before_backup:
        content = _export(postgres_engine, project)
    before = _snapshot(postgres_engine, other)
    if relationship_before_backup:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    else:
        current = _snapshot(postgres_engine, project)
        with pytest.raises(BackupError) as error:
            restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
        assert error.value.code in {"backup_dependency_conflict", "backup_integrity_conflict"}
        assert _snapshot(postgres_engine, project) == current
    assert _snapshot(postgres_engine, other) == before


def test_moved_work_cannot_be_stolen_from_other_project(
    api, project, work_payload, postgres_engine,
):
    target = api.post("/api/v1/projects", json={"name": "Move target"}).json()
    work = create_work(api, project, work_payload)["work_item"]
    original = _export(postgres_engine, project)
    moved = api.post(f"/api/v1/projects/{project['id']}/work-items/{work['id']}/move",
                     json=_move_payload(target, work["version"]))
    assert moved.status_code == 200, moved.text
    target_before = _snapshot(postgres_engine, target)
    source_before = _snapshot(postgres_engine, project)
    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(original))
    assert error.value.code == "backup_integrity_conflict"
    assert _snapshot(postgres_engine, target) == target_before
    assert _snapshot(postgres_engine, project) == source_before
    # A backup after the move can restore the destination with historical source facts intact.
    current = _export(postgres_engine, target)
    restore_project(postgres_engine, UUID(target["id"]), io.BytesIO(current))
    assert _snapshot(postgres_engine, project) == source_before


def test_review_reports_remediation_and_embeddings_roundtrip(api, project, work_payload,
                                                           checkpoint_fields, postgres_engine):
    completion, _ = mandatory(api, project, work_payload, checkpoint_fields)
    lease = claim_review(api, project, completion, checkpoint_fields)
    result = api.post(result_url(project, completion),
                      json=result_payload(completion, lease, findings=[finding()]))
    assert result.status_code == 200, result.text
    with postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO work_item_embeddings(work_item_id,model,digest,vector)
            VALUES(CAST(:id AS uuid),'test-model',:digest,ARRAY[0.1,0.2,0.3]::real[])
        """), {"id": completion["work_item"]["id"], "digest": "a" * 64})
    before = _snapshot(postgres_engine, project)
    for name in ("code_reviews", "code_review_findings", "code_review_remediations",
                 "job_completion_reports", "work_completion_review_policies",
                 "work_item_embeddings"):
        assert before[name], name
    content = _export(postgres_engine, project)
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(content))
    restored = _snapshot(postgres_engine, project)
    before["project_activity_heads"] = restored["project_activity_heads"]
    assert restored == before


def _sequence_snapshot(engine):
    with engine.connect() as connection:
        return connection.execute(text("""
            SELECT sequencename,last_value FROM pg_sequences
            WHERE schemaname=current_schema() ORDER BY sequencename
        """)).all()


def _identity_archive(api, project, work_payload, postgres_engine, tmp_path, table):
    work = create_work(api, project, work_payload, client_operation_id=str(uuid4()))
    if table == "artifact_audit":
        api.app.state.artifact_storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
        upload(api, project)
    elif table == "work_gates":
        response = api.post(gate_path(project, work), json=gate_request(operation_id=uuid4()))
        assert response.status_code == 201, response.text
    elif table == "work_duplicate_merges":
        destination = create_work(api, project, work_payload, title="Canonical work")
        merge_work(api, project, work["work_item"], destination["work_item"])
    return _export(postgres_engine, project)


@pytest.mark.parametrize("table,column", IDENTITY_COLUMNS)
@pytest.mark.parametrize("value", [2**63 - 1, MAX_ARCHIVE_IDENTITY + 1, 0, -1, True, "7"])
def test_invalid_archive_identity_cannot_change_sequences_or_break_other_project(
    api, project, work_payload, postgres_engine, tmp_path, table, column, value,
):
    content = _identity_archive(api, project, work_payload, postgres_engine, tmp_path, table)
    other = api.post("/api/v1/projects", json={"name": "Unrelated protected writes"}).json()

    def tamper(_header, rows):
        assert rows[table]
        rows[table][0][column] = value

    damaged = _rewrite(content, tamper)
    sequences = _sequence_snapshot(postgres_engine)
    before = _snapshot(postgres_engine, project)
    untouched = _snapshot(postgres_engine, other)
    with pytest.raises(BackupError) as error:
        restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(damaged))
    assert error.value.status == 422
    assert "Identity values" in error.value.message
    assert _sequence_snapshot(postgres_engine) == sequences
    assert _snapshot(postgres_engine, project) == before
    assert _snapshot(postgres_engine, other) == untouched
    create_work(api, other, work_payload, client_operation_id=str(uuid4()))


def test_export_refuses_unsupported_identity_before_writing_archive(
    api, project, work_payload, postgres_engine,
):
    create_work(api, project, work_payload, client_operation_id=str(uuid4()))
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        _set_database_identity(connection, "client_operations", 2**63 - 1)
    sequences = _sequence_snapshot(postgres_engine)
    output = io.BytesIO()
    with pytest.raises(BackupError) as error:
        export_project(postgres_engine, UUID(project["id"]), output)
    assert error.value.code == "backup_identity_unsupported"
    assert output.getvalue() == b""
    assert _sequence_snapshot(postgres_engine) == sequences


def test_all_sequence_maxima_are_validated_before_first_setval(
    api, project, work_payload, postgres_engine,
):
    create_work(api, project, work_payload, client_operation_id=str(uuid4()))
    sequences = _sequence_snapshot(postgres_engine)
    with pytest.raises(BackupError) as error, postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        # The receipt sequence precedes work_events in the advancement plan.
        # Its safe but larger maximum must not be applied before the later error.
        _set_database_identity(connection, "client_operations", 1000)
        _set_database_identity(connection, "work_events", 2**63 - 1)
        advance_sequences(connection)
    assert error.value.code == "backup_identity_unsupported"
    assert _sequence_snapshot(postgres_engine) == sequences


def _set_database_identity(connection, table, value):
    # Fixtures deliberately seed legal bigint values beyond the archive domain;
    # restore the exact identity definition before testing the engine's checks.
    connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN id SET GENERATED BY DEFAULT"))
    connection.execute(text(f"UPDATE {table} SET id=:id"), {"id": value})
    connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN id SET GENERATED ALWAYS"))
