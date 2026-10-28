"""Project folder settings and enablement fence import and worker publication."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copying import (
    claim_transcript_copy,
    complete_transcript_copy,
    copy_next_transcript,
)
from mnemonic_api.transcript_indexing import claim_transcript_job, complete_transcript_job

from .test_artifact_extraction_migration_postgres import migrate
from .test_leases_postgres import create_work, expire_lease, item_path
from .test_transcript_imports_postgres import import_folder, source
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def save(api, project, enabled=True, directories=None, revision=1):
    return api.patch(f"/api/v1/projects/{project['id']}/transcript-settings", json={
        "enabled": enabled, "directories": directories or [], "expected_revision": revision})


def test_folder_rows_add_edit_remove_and_conflict(api, project, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    path = f"/api/v1/projects/{project['id']}/transcript-settings"
    assert api.get(path).json() == {"enabled": True, "directories": [], "revision": 1,
                                   "allowed_roots": [str(tmp_path)]}
    assert save(api, project, directories=[str(tmp_path)]).json()["directories"] == [str(tmp_path)]
    assert save(api, project, directories=[]).status_code == 409
    missing = str(tmp_path / "missing")
    assert save(api, project, directories=[missing], revision=2).status_code == 200
    health = api.get(collection(project) + "/health").json()
    assert any(warning["path"] == missing for warning in health["warnings"])
    assert save(api, project, directories=[], revision=3).status_code == 200
    health = api.get(collection(project) + "/health").json()
    assert not any(warning["path"] == missing for warning in health["warnings"])
    assert save(api, project, directories=[str(tmp_path), str(tmp_path) + "/."],
                revision=4).status_code == 422


def test_disabled_import_rebuild_and_receipt_replay(api, project, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    source(tmp_path)
    operation = str(uuid4())
    imported = import_folder(api, project, tmp_path, operation).json()
    rebuild = {"client_operation_id": str(uuid4())}
    receipt = api.post(collection(project) + "/rebuild", json=rebuild).json()
    assert save(api, project, enabled=False).status_code == 200
    assert import_folder(api, project, tmp_path).status_code == 409
    assert import_folder(api, project, tmp_path, operation).json() == imported
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4())}).status_code == 409
    assert api.post(collection(project) + "/rebuild", json=rebuild).json() == receipt
    assert not run(api)
    assert save(api, project, revision=2).status_code == 200
    assert run(api)


@pytest.mark.parametrize("phase", ["copy", "index"])
def test_disabling_fences_inflight_jobs_and_resume_retries(
    api, project, work_payload, tmp_path, postgres_engine, phase,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    if phase == "copy":
        job = claim_transcript_copy(factory, settings)
    else:
        assert copy_next_transcript(factory, settings)
        job = claim_transcript_job(factory, settings)
    assert job is not None
    assert save(api, project, enabled=False).status_code == 200
    if phase == "copy":
        complete_transcript_copy(factory, job, None, ExtractionError("stale_disabled"))
    else:
        complete_transcript_job(factory, job, None, ExtractionError("stale_disabled"))
    current = read(api, project, record)
    assert current["error_code"] is None and current["copy_error_code"] is None
    assert not run(api)
    assert save(api, project, revision=2).status_code == 200
    assert run(api)
    assert read(api, project, record)["status"] == "ready"


@pytest.mark.parametrize("phase", ["copy", "index"])
def test_repeated_disable_refunds_only_interrupted_attempts(
    api, project, work_payload, tmp_path, postgres_engine, phase,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    if phase == "index":
        assert copy_next_transcript(factory, settings)
    claim_job = claim_transcript_copy if phase == "copy" else claim_transcript_job
    with factory.begin() as database:
        row = database.get(Transcript, UUID(record["id"]))
        setattr(row, "copy_attempts" if phase == "copy" else "attempts", 1)
    for iteration in range(9):
        assert claim_job(factory, settings) is not None
        assert save(api, project, enabled=False, revision=2 * iteration + 1).status_code == 200
        assert save(api, project, revision=2 * iteration + 2).status_code == 200
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        assert getattr(row, "copy_attempts" if phase == "copy" else "attempts") == 1
    assert run(api)
    assert read(api, project, record)["status"] == "ready"


def test_disabled_enrollment_still_claims_work_without_import(api, project, work_payload, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    path = source(tmp_path)
    assert save(api, project, enabled=False).status_code == 200
    work = create_work(api, project, work_payload)["work_item"]
    claimed, _ = claim(api, item_path(project, work),
                       source={"client": "claude_code", "path": str(path)})
    assert claimed["lease_token"]
    assert api.get(collection(project)).json()["total"] == 0


def test_migration_seeds_failed_sources_without_thousands_of_healthy_folders(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, original, _ = register(api, project, work_payload, tmp_path)
    with api.app.state.session_factory.begin() as database:
        record = database.get(Transcript, UUID(original["id"]))
        database.add_all([Transcript(
            id=uuid4(), work_item_id=record.work_item_id,
            lease_generation_id=record.lease_generation_id, client="codex",
            session_id="fixture", source_path=f"/sessions/project-{number}/session.jsonl",
            kind="primary") for number in range(1001)])
        database.add(Transcript(id=uuid4(), work_item_id=record.work_item_id,
            lease_generation_id=record.lease_generation_id, client="codex", session_id="fixture",
            source_path="/sessions/broken/session.jsonl", kind="primary",
            copy_status="failed", copy_error_code="transcript_source_missing"))
    migrate(postgres_engine, "0068_artifact_tags", downgrade=True)
    migrate(postgres_engine, "head")
    with postgres_engine.connect() as connection:
        directories = connection.scalar(text("SELECT directories FROM transcript_settings "
                                               "WHERE project_id=:project"),
                                         {"project": project["id"]})
    assert directories == ["/sessions/broken"]


def test_browse_endpoint_requires_project_and_respects_source_boundary(api, project, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    (tmp_path / "folder").mkdir()
    endpoint = collection(project) + "/browse"
    roots = api.get(endpoint).json()
    assert roots["directory"] is None and roots["entries"][0]["path"] == str(tmp_path)
    assert api.get(endpoint, params={"query": "no-such-root"}).json()["entries"] == []
    folders = api.get(endpoint, params={"directory": str(tmp_path), "query": "FOLDER"}).json()
    assert folders["entries"] == [{"name": "folder", "path": str(tmp_path / "folder")}]
    assert api.get(endpoint, params={"directory": "/"}).status_code == 422
    assert api.get(f"/api/v1/projects/{uuid4()}/transcripts/browse").status_code == 404


def test_capture_and_recovery_ignore_retired_project_size_limit(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from mnemonic_api.services.transcript_recoveries import inspect_recovery_target

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    assert save(api, project).status_code == 200
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE transcript_settings SET max_file_size_bytes=1"))
    target = inspect_recovery_target(factory, settings, UUID(record["id"]), UUID(project["id"]))
    assert target.maximum_bytes == settings.transcript_max_bytes
    job = claim_transcript_copy(factory, settings)
    assert job is not None and job.maximum_bytes == settings.transcript_max_bytes
