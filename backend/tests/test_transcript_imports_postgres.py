"""Folder imports deduplicate enrollment, survive retries, and retain project ownership."""

import io
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_indexing import claim_transcript_job, complete_transcript_job
from mnemonic_backup.archive import restore_project

from .test_leases_postgres import create_work, expire_lease, item_path
from .test_project_backup_archive import _export, _snapshot
from .test_transcript_indexing_postgres import collection, read, register, run
from .test_transcript_lifecycle_postgres import claim

pytestmark = pytest.mark.postgres


def import_folder(api, project, folder, operation=None):
    return api.post(collection(project) + "/import", json={
        "directory": str(folder), "client_operation_id": operation or str(uuid4()),
    })


def source(folder, name="old.jsonl"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"type":"user","message":{"role":"user",'
                    '"content":"historical lavender badger"}}\n')
    return path


def test_imports_nested_sources_skip_enrollment_and_replay_without_rescanning(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, enrolled, original = register(api, project, work_payload, tmp_path)
    source(tmp_path, "old/subagents/agent-one.jsonl")
    (tmp_path / "sessions-index.json").write_text("{}")
    operation = str(uuid4())
    imported = import_folder(api, project, tmp_path, operation)
    assert imported.status_code == 200, imported.text
    assert imported.json() == {"project_id": project["id"], "client_operation_id": operation,
        "directory": str(tmp_path), "imported": 1, "existing": 1, "skipped": 0}
    assert run(api)
    assert not run(api)  # The agent's active source was not reset or queued by import.
    page = api.get(collection(project)).json()
    assert page["total"] == 2
    rows = {row["id"]: row for row in page["items"]}
    assert rows[enrolled["id"]] == enrolled
    historical = next(row for row in rows.values() if row["kind"] == "imported")
    assert historical["status"] == "ready"
    assert historical["work_item_id"] is historical["lease_generation_id"] is None
    assert historical["session_id"] is None
    again = import_folder(api, project, tmp_path)
    assert again.json()["imported"] == 0 and again.json()["existing"] == 2
    api.app.state.settings.transcript_allowed_roots = []
    assert import_folder(api, project, tmp_path, operation).json() == imported.json()
    conflict = import_folder(api, project, tmp_path / "other", operation)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "transcript_import_conflict"
    assert original.exists()
    expire_lease(postgres_engine, work["id"])


@pytest.mark.parametrize("enroll_first", [True, False])
@pytest.mark.parametrize("suffix", ["", "/", "/.", "/.//./"])
def test_path_aliases_and_client_aliases_do_not_duplicate_sources(
    api, project, work_payload, tmp_path, enroll_first, suffix,
):
    path = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    assertion = {"client": "claude-code", "path": f"/{tmp_path}//./{path.name}{suffix}"}
    if enroll_first:
        claim(api, item_path(project, work), source=assertion)
    initial = import_folder(api, project, tmp_path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["imported"] == (0 if enroll_first else 1)
    if not enroll_first:
        claim(api, item_path(project, work), source=assertion)
    page = api.get(collection(project)).json()
    assert page["total"] == 1
    assert page["items"][0]["kind"] == "primary"
    assert page["items"][0]["source_path"] == assertion["path"]
    assert not run(api)


@pytest.mark.parametrize("indexed", [False, True])
def test_later_enrollment_reuses_import_and_invalidates_stale_extraction(
    api, project, work_payload, tmp_path, postgres_engine, indexed,
):
    path = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    assert import_folder(api, project, tmp_path).status_code == 200
    original = api.get(collection(project)).json()["items"][0]
    if indexed:
        assert run(api)
        old = None
    else:
        old = claim_transcript_job(api.app.state.session_factory, api.app.state.settings)
        assert old is not None
    work = create_work(api, project, work_payload)["work_item"]
    receipt, payload = claim(api, item_path(project, work),
                             source={"client": "claude_code", "path": str(path)})
    assert api.post(item_path(project, work) + "/claim", json=payload).json() == receipt
    if old:
        complete_transcript_job(api.app.state.session_factory, old, None,
                                ExtractionError("stale_import"))
    assert not run(api)
    current = read(api, project, original)
    assert current["kind"] == "primary" and current["work_item_id"] == work["id"]
    assert current["status"] == "waiting" and current["text_sha256"] is None
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert read(api, project, original)["status"] == "ready"
    assert api.get(collection(project)).json()["total"] == 1


def test_concurrent_imports_and_enrollment_share_project_serialization(
    api, project, work_payload, tmp_path,
):
    path = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        imports = [pool.submit(import_folder, api, project, tmp_path) for _ in range(2)]
        enrollment = pool.submit(claim, api, item_path(project, work),
                                 source={"client": "claude_code", "path": str(path)})
        responses = [future.result(timeout=15) for future in imports]
        enrollment.result(timeout=15)
    assert all(response.status_code == 200 for response in responses)
    page = api.get(collection(project)).json()
    assert page["total"] == 1 and page["items"][0]["kind"] == "primary"


def test_imports_obey_pause_limits_scope_search_rebuild_and_backup(
    api, project, tmp_path, postgres_engine,
):
    path = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    settings = f"/api/v1/projects/{project['id']}/transcript-settings"
    assert api.patch(settings, json={"enabled": False, "max_file_size_bytes": 1024,
                                    "expected_revision": 1}).status_code == 200
    operation = str(uuid4())
    imported = import_folder(api, project, tmp_path, operation).json()
    assert not run(api)
    assert api.patch(settings, json={"enabled": True, "max_file_size_bytes": 1024,
                                    "expected_revision": 2}).status_code == 200
    assert run(api)
    row = api.get(collection(project)).json()["items"][0]
    search = f"/api/v1/projects/{project['id']}/search"
    query = {"q": "lavender", "facets": ["transcripts"]}
    assert api.post(search, json=query).json()["total"] == 0
    result = api.post(search, json={**query, "fulltext": True}).json()
    assert result["total"] == 1 and result["items"][0]["transcript"]["id"] == row["id"]
    assert api.get(collection(project), params={"work_item_id": str(uuid4())})\
        .json()["total"] == 0
    other = api.post("/api/v1/projects", json={"name": "Separate imports"}).json()
    assert api.get(collection(other) + "/" + row["id"] + "/text").status_code == 404
    assert import_folder(api, other, tmp_path).json()["imported"] == 1
    assert run(api)
    original = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    response = api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())})
    assert response.json()["queued"] == 1
    assert run(api)
    path.unlink()
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = _snapshot(postgres_engine, project)
    for table in ("transcripts", "transcript_imports"):
        assert restored[table] == original[table]
    assert import_folder(api, project, tmp_path, operation).json() == imported
    assert api.get(collection(other)).json()["total"] == 1
    assert "lavender" in api.get(collection(project) + "/" + row["id"] + "/text").json()["text"]


def test_unreadable_import_is_atomic_and_invalid_formats_retain_failure(api, project, tmp_path):
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    response = import_folder(api, project, tmp_path / "missing")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "transcript_import_scan_failed"
    assert api.get(collection(project)).json()["total"] == 0
    source(tmp_path).write_text("not json")
    assert import_folder(api, project, tmp_path).json()["imported"] == 1
    assert run(api)
    row = api.get(collection(project)).json()["items"][0]
    assert row["status"] == "failed" and row["error_code"] == "transcript_invalid_format"
    assert import_folder(api, project, tmp_path).json()["existing"] == 1
    with api.app.state.session_factory() as database:
        assert len(database.scalars(select(Transcript)).all()) == 1


@pytest.mark.parametrize("phase", ["pending", "processing", "ready"])
@pytest.mark.parametrize("suffix", ["", "//./"])
def test_move_removes_only_redundant_imports_and_preserves_enrolled_history(
    api, project, work_payload, tmp_path, postgres_engine, phase, suffix,
):
    from .test_work_item_moves_postgres import _move_payload

    path = source(tmp_path)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    claim(api, item_path(project, work),
          source={"client": "claude_code", "path": str(path) + suffix})
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    original = api.get(collection(project)).json()["items"][0]
    source(tmp_path, "unrelated.jsonl")
    target = api.post("/api/v1/projects", json={"name": "Imported destination"}).json()
    operation = str(uuid4())
    receipt = import_folder(api, target, tmp_path, operation).json()
    imported = next(row for row in api.get(collection(target)).json()["items"]
                    if row["source_path"] == str(path))
    old_jobs = []
    if phase == "processing":
        for _ in range(2):
            old_jobs.append(claim_transcript_job(api.app.state.session_factory,
                                                 api.app.state.settings))
    elif phase == "ready":
        assert run(api) and run(api)
    payload = _move_payload(target, work["version"])
    moved = api.post(item_path(project, work) + "/move", json=payload)
    assert moved.status_code == 200, moved.text
    for job in old_jobs:
        assert job is not None
        complete_transcript_job(api.app.state.session_factory, job, None,
                                ExtractionError("stale_import"))
    rows = api.get(collection(target)).json()["items"]
    assert len(rows) == 2
    assert {row["source_path"] for row in rows} == {str(path) + suffix,
                                                 str(tmp_path / "unrelated.jsonl")}
    assert read(api, target, original) == {**original, "project_id": target["id"]}
    assert api.get(collection(project)).json()["total"] == 0
    assert api.get(collection(target) + "/" + imported["id"]).status_code == 404
    assert import_folder(api, target, tmp_path, operation).json() == receipt
    again = import_folder(api, target, tmp_path).json()
    assert again["imported"] == 0 and again["existing"] == 2
    assert api.post(item_path(project, work) + "/move", json=payload).json() == moved.json()


def test_move_failure_rolls_back_redundant_import_removal(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from mnemonic_api.errors import conflict

    from .test_work_item_moves_postgres import _move_payload

    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    target = api.post("/api/v1/projects", json={"name": "Rollback imports"}).json()
    assert import_folder(api, target, tmp_path).json()["imported"] == 1
    original = api.get(collection(target)).json()

    def reject_event(*args, **kwargs):
        raise conflict("synthetic_move_failure", "Synthetic rollback check.")

    monkeypatch.setattr("mnemonic_api.services.work_items.stage_work_moved_events", reject_event)
    moved = api.post(item_path(project, work) + "/move",
                     json=_move_payload(target, work["version"]))
    assert moved.status_code == 409
    assert api.get(collection(target)).json() == original
    assert api.get(collection(project)).json()["total"] == 1
