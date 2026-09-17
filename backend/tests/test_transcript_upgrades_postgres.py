"""A worker upgrade refreshes retained captures without losing search or lease guards."""

from uuid import UUID

import pytest
from sqlalchemy import select, text, update

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript, WorkLease
from mnemonic_api.transcript_job_queue import enqueue_transcript_jobs
from mnemonic_api.transcript_upgrades import refresh_outdated_normalizations

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import Parser, collection, read, register, run

pytestmark = pytest.mark.postgres


def refresh(api):
    with api.app.state.session_factory() as database:
        return refresh_outdated_normalizations(database, api.app.state.settings)


def stale(api, record):
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"]))
                         .values(normalizer_version=1))


def test_upgrade_uses_retained_native_copy_preserves_search_and_is_idempotent(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from dataclasses import replace

    from mnemonic_api import transcript_normalization

    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    normalize = transcript_normalization.normalize_transcript
    with monkeypatch.context() as previous:
        previous.setattr(transcript_normalization, "NORMALIZER_VERSION", 1)
        previous.setattr("mnemonic_api.transcript_indexing.normalize_transcript",
                         lambda *args: replace(normalize(*args), normalizer_version=1))
        assert run(api)
    original = read(api, project, record)
    assert original["normalizer_version"] == 1
    source.unlink()
    assert refresh(api) == 1
    assert refresh(api) == 0
    queued = read(api, project, record)
    assert queued["status"] == "ready" and queued["index_status"] == "pending"
    assert queued["copy_status"] == "ready" and queued["text_sha256"] == original["text_sha256"]
    page = api.get(collection(project), params={"query": "needle", "fulltext": True}).json()
    assert page["total"] == 1
    with api.app.state.session_factory.begin() as database:
        assert enqueue_transcript_jobs(database, api.app.state.settings) == 1
    assert run(api)
    current = read(api, project, record)
    assert current["normalizer_version"] == 2 and current["index_status"] == "ready"
    assert current["sha256"] == original["sha256"]
    assert current["normalized_revision"] != original["normalized_revision"]
    assert current["index_created_at"] > original["index_created_at"]
    assert current["last_updated_at"] == original["last_updated_at"]
    assert refresh(api) == 0


@pytest.mark.parametrize("guard", ["active", "paused"])
def test_upgrade_defers_until_active_session_or_pause_ends_without_changing_lease(
    api, project, work_payload, tmp_path, postgres_engine, guard,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    stale(api, record)
    with api.app.state.session_factory.begin() as database:
        if guard == "active":
            database.execute(text("UPDATE work_leases SET expires_at=clock_timestamp() "
                                  "+ interval '10 minutes' WHERE work_item_id=:id"),
                             {"id": work["id"]})
        else:
            database.execute(text("INSERT INTO transcript_settings(project_id,enabled) "
                                  "VALUES (:id,false)"), {"id": project["id"]})
        before = database.execute(select(WorkLease.__table__)).mappings().all()
        generation = database.scalar(select(Transcript.generation))
    assert refresh(api) == 0
    assert read(api, project, record)["index_status"] == "ready"
    with api.app.state.session_factory.begin() as database:
        assert database.execute(select(WorkLease.__table__)).mappings().all() == before
        assert database.scalar(select(Transcript.generation)) == generation
        if guard == "paused":
            database.execute(text("UPDATE transcript_settings SET enabled=true"))
    if guard == "active":
        expire_lease(postgres_engine, work["id"])
    assert refresh(api) == 1
    assert run(api) and read(api, project, record)["normalizer_version"] == 2


def test_failed_upgrade_keeps_search_and_does_not_restart_exhausted_attempts(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    original = read(api, project, record)
    from mnemonic_api import transcript_normalization

    monkeypatch.setattr(transcript_normalization, "NORMALIZER_VERSION", 3)
    assert refresh(api) == 1
    assert run(api, Parser(error=ExtractionError("extraction_parse_failed")))
    failed = read(api, project, record)
    assert failed["status"] == "ready" and failed["index_status"] == "failed"
    assert failed["text_sha256"] == original["text_sha256"]
    assert failed["normalized_revision"] == original["normalized_revision"]
    with api.app.state.session_factory() as database:
        before = database.execute(select(Transcript.generation, Transcript.attempts)).one()
    assert refresh(api) == 0 and refresh(api) == 0
    with api.app.state.session_factory() as database:
        assert database.execute(select(Transcript.generation, Transcript.attempts)).one() == before
    assert api.get(collection(project), params={"query": "needle", "fulltext": True})\
        .json()["total"] == 1


def test_upgraded_context_is_searchable_and_rebuild_uses_persisted_representation(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    import json
    from uuid import uuid4

    work, _, record, source = register(api, project, work_payload, tmp_path)
    with source.open("a") as output:
        output.write(json.dumps({"type": "attachment", "attachment": {
            "type": "instructions", "files": [{"content": "restoredcontextneedle"}]}}) + "\n")
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    ready = read(api, project, record)
    hit = api.get(collection(project), params={"query": "restoredcontextneedle", "fulltext": True})\
        .json()["items"][0]
    assert hit["id"] == record["id"] and hit["content_kind"] == "system_text"
    assert not ready["normalization_incomplete"]
    source.unlink()
    monkeypatch.setattr("mnemonic_api.transcript_indexing.normalize_transcript",
                        lambda *_: pytest.fail("Current normalized segments must be reused"))
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4())}).status_code == 200
    assert run(api)
    assert read(api, project, record)["normalized_revision"] == ready["normalized_revision"]
    page = api.get(collection(project), params={"query": "restoredcontextneedle", "fulltext": True})
    assert page.json()["total"] == 1


def test_upgrade_waits_for_and_rechecks_an_uncommitted_lease_renewal(
    api, project, work_payload, tmp_path, postgres_engine,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from mnemonic_api.models import WorkItem
    from mnemonic_api.services.leases import renew_lease_record
    from mnemonic_api.services.project_mutations import project_mutation

    work, receipt, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    stale(api, record)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE work_leases SET "
            "acquired_at=clock_timestamp()-interval '2 seconds', "
            "renewed_at=clock_timestamp()-interval '1 second', "
            "expires_at=clock_timestamp()+interval '1 second' WHERE work_item_id=:id"),
            {"id": work["id"]})
    renewed, release = Event(), Event()

    def renew():
        with api.app.state.session_factory() as database:
            with project_mutation(database, UUID(project["id"])):
                row = database.scalar(select(WorkItem).where(
                    WorkItem.id == UUID(work["id"])).with_for_update())
                renew_lease_record(database, row, receipt["lease_token"], lease_minutes=15)
                renewed.set()
                assert release.wait(5)
                database.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        renewing = executor.submit(renew)
        assert renewed.wait(5)
        try:
            with postgres_engine.connect() as connection:
                connection.execute(text("SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM "
                    "(expires_at-clock_timestamp()))) + 0.02) FROM work_leases "
                    "WHERE work_item_id=:id"), {"id": work["id"]})
            refreshing = executor.submit(refresh, api)
            with pytest.raises(TimeoutError):
                refreshing.result(timeout=0.1)
        finally:
            release.set()
        renewing.result(timeout=5)
        assert refreshing.result(timeout=5) == 0
    assert read(api, project, record)["index_status"] == "ready"
    expire_lease(postgres_engine, work["id"])
    assert refresh(api) == 1


def test_upgrade_batches_are_bounded_and_resume_without_duplicate_generations(
    api, project, tmp_path,
):
    import json
    from uuid import uuid4

    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    for number in range(21):
        (tmp_path / f"session-{number}.jsonl").write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "synthetic content"}}))
    response = api.post(collection(project) + "/import", json={
        "directory": str(tmp_path), "client_operation_id": str(uuid4())})
    assert response.status_code == 200, response.text
    for _ in range(21):
        assert run(api)
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).values(normalizer_version=1))
        generations = dict(database.execute(select(Transcript.id, Transcript.generation)).all())
    assert refresh(api) == 20
    assert refresh(api) == 1
    assert refresh(api) == 0
    with api.app.state.session_factory() as database:
        for identity, generation in database.execute(select(Transcript.id, Transcript.generation)):
            assert generation == generations[identity] + 1
