"""A corrected shared-filesystem allowlist recovers existing enrollment failures."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mnemonic_api.models import Transcript, WorkLease
from mnemonic_api.transcript_indexing import claim_transcript_job

from .test_leases_postgres import expire_lease, item_path
from .test_transcript_indexing_postgres import Parser, collection, read, register, run

pytestmark = pytest.mark.postgres


def test_restored_roots_recover_primary_and_nested_subagents_without_duplicates(
    api, project, work_payload, tmp_path,
):
    work, receipt, _, primary = register(api, project, work_payload, tmp_path)
    child = tmp_path / "session" / "subagents" / "workflows" / "wf-synthetic" / "agent.jsonl"
    child.parent.mkdir(parents=True, mode=0o700)
    child.write_bytes(primary.read_bytes())
    child.chmod(0o600)
    api.app.state.settings.transcript_allowed_roots = []
    payload = {"lease_token": receipt["lease_token"],
               "actor": {"actor_client": "claude-code", "actor_session_id": "transcript-test"},
               "subagent_transcripts": [{"client": "claude-code", "path": str(child)}]}
    release_path = item_path(project, work) + "/release-claim"
    released = api.post(release_path, json=payload)
    assert released.status_code == 200, released.text
    assert run(api) and run(api)
    assert not run(api)
    failed = api.get(collection(project)).json()["items"]
    assert len(failed) == 2
    assert {row["kind"] for row in failed} == {"primary", "subagent"}
    assert all(row["error_code"] == "transcript_path_not_allowed" for row in failed)
    ids = {row["id"] for row in failed}
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    assert run(api) and run(api)
    assert not run(api)
    ready = api.get(collection(project)).json()["items"]
    assert {row["id"] for row in ready} == ids
    assert all(row["status"] == "ready" and row["error_code"] is None for row in ready)
    assert {row["source_path"] for row in ready} == {str(primary), str(child)}
    assert api.post(release_path, json=payload).status_code == 200
    assert {row["id"] for row in api.get(collection(project)).json()["items"]} == ids


def test_recovery_waits_for_active_generation_and_enabled_settings(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    settings = api.app.state.settings
    settings.transcript_allowed_roots = []
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    factory = api.app.state.session_factory
    with factory() as database:
        lease = database.scalar(select(WorkLease).where(WorkLease.work_item_id == UUID(work["id"])))
        lease.expires_at = datetime.now(UTC) + timedelta(minutes=5)
        row = database.get(Transcript, UUID(record["id"]))
        row.attempts = 3
        database.commit()
    settings.transcript_allowed_roots = [tmp_path]
    assert not run(api)
    expire_lease(postgres_engine, work["id"])
    path = f"/api/v1/projects/{project['id']}/transcript-settings"
    pause = {"enabled": False, "max_file_size_bytes": 1024, "expected_revision": 1}
    assert api.patch(path, json=pause).status_code == 200
    assert not run(api)
    resumed = api.patch(path, json={**pause, "enabled": True, "expected_revision": 2})
    assert resumed.status_code == 200
    assert run(api)
    assert read(api, project, record)["status"] == "ready"
    with factory() as database:
        assert database.get(Transcript, UUID(record["id"])).attempts == 1


@pytest.mark.parametrize("source_kind", [
    "plain", "aliases", "sibling", "wildcard", "traversal", "root", "root_slashes", "root_dot",
])
def test_recovery_matches_exact_roots_and_posix_aliases(api, project, tmp_path, source_kind):
    root = tmp_path / "private%_root"
    root.mkdir()
    source = root / "session.jsonl"
    source.write_text('{"type":"user","message":{"role":"user","content":"synthetic"}}\n')
    paths = {
        "plain": str(source),
        "aliases": "/" + str(root) + "//./session.jsonl",
        "sibling": str(root) + "-outside/session.jsonl",
        "wildcard": str(tmp_path / "privateZZroot" / "session.jsonl"),
        "traversal": str(root) + "/../private%_root/session.jsonl",
        "root": str(root) + "/",
        "root_slashes": str(root) + "//",
        "root_dot": str(root) + "/./",
    }
    identity = uuid4()
    factory = api.app.state.session_factory
    with factory() as database:
        database.add(Transcript(id=identity, import_project_id=UUID(project["id"]), kind="imported",
            client="claude-code", source_path=paths[source_kind], status="failed",
            error_code="transcript_path_not_allowed", attempts=1))
        database.commit()
    api.app.state.settings.transcript_allowed_roots = [root]
    eligible = source_kind in {"plain", "aliases"}
    assert run(api) is eligible
    with factory() as database:
        record = database.get(Transcript, identity)
        assert record.source_path == paths[source_kind]
        assert record.status == ("ready" if eligible else "failed")
        assert record.attempts == 1
    assert not run(api)


def test_recovery_still_rejects_symlinks_and_does_not_loop(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    api.app.state.settings.transcript_allowed_roots = []
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    target = tmp_path / "untrusted.jsonl"
    source.rename(target)
    source.symlink_to(target)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    parser = Parser()
    assert run(api, parser)
    assert read(api, project, record)["error_code"] == "transcript_io_error"
    assert parser.calls == []
    assert not run(api, parser)


@pytest.mark.parametrize("error", ["transcript_io_error", "transcript_unsupported_format",
                                    "transcript_invalid_format", "extraction_parse_failed"])
def test_recovery_does_not_retry_unrelated_terminal_errors(
    api, project, work_payload, tmp_path, postgres_engine, error,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory = api.app.state.session_factory
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.status = "failed"
        row.error_code = error
        database.commit()
    assert claim_transcript_job(factory, api.app.state.settings) is None
    assert read(api, project, record)["error_code"] == error
