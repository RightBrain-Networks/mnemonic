"""Durable activation, lease recovery, rebuild races and scoped snapshot retrieval."""

import asyncio
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select, text, update

from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError
from mnemonic_api.models import Transcript, WorkItem, WorkLease
from mnemonic_api.services import transcripts as transcript_service
from mnemonic_api.services.leases import renew_lease_record
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.transcript_indexing import (
    claim_transcript_job,
    complete_transcript_job,
    index_next_transcript,
    transcript_indexing_loop,
)
from mnemonic_backup.archive import restore_project

from .test_leases_postgres import create_work, expire_lease, item_path
from .test_project_backup_archive import _export, _snapshot
from .test_transcript_lifecycle_postgres import claim
from .test_work_item_moves_postgres import _move_payload

pytestmark = pytest.mark.postgres


class Parser:
    def __init__(self, callback=None, error=None):
        self.calls = []
        self.callback = callback
        self.error = error

    def extract(self, content, *, filename, size_bytes):
        data = content.read()
        self.calls.append((data, filename, size_bytes))
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return ExtractedArtifact(data.decode(), {"dc:creator": ["Synthetic Author"]}, False)


def collection(project):
    return f"/api/v1/projects/{project['id']}/transcripts"


def register(api, project, work_payload, tmp_path, *, client="claude-code"):
    source = tmp_path / "session.jsonl"
    source.write_text(json.dumps({"type": "user", "sessionId": "synthetic-session",
                                 "message": {"role": "user",
                                             "content": "rare needle in transcript"}})
                      + "\n")
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    work = create_work(api, project, work_payload)["work_item"]
    receipt, _ = claim(api, item_path(project, work),
                       source={"client": client, "path": str(source)})
    response = api.get(collection(project))
    assert response.status_code == 200, response.text
    return work, receipt, response.json()["items"][0], source


def run(api, parser=None):
    return index_next_transcript(api.app.state.session_factory, api.app.state.settings,
                                 parser or Parser())


def read(api, project, record):
    response = api.get(collection(project) + "/" + record["id"])
    assert response.status_code == 200, response.text
    return response.json()


def test_generation_waits_for_expiry_and_publishes_complete_snapshot(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    parser = Parser()
    assert record["status"] == "waiting"
    assert not run(api, parser)
    expire_lease(postgres_engine, work["id"])
    assert run(api, parser)
    assert not run(api, parser)
    ready = read(api, project, record)
    assert ready["status"] == "ready"
    assert ready["indexing_started_at"] <= ready["indexing_completed_at"]
    assert ready["mime_type"] == "application/x-ndjson"
    assert ready["size_bytes"] == source.stat().st_size
    assert ready["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "normalized_text" not in ready
    assert parser.calls[0][1] == "transcript.txt"
    path = collection(project) + "/" + ready["id"]
    text = api.get(path + "/text", params={"limit": 12}).json()
    assert text["next_offset"] == 12
    download = api.get(path + "/content", params={"expected_sha256": ready["text_sha256"]})
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == ready["text_sha256"]
    assert text["text"] == download.text[:12]
    source.unlink()
    assert api.get(path + "/content").content == download.content
    assert api.get(path + "/text", params={"expected_sha256": "0" * 64}).status_code == 409


def test_metadata_search_opt_in_fulltext_and_project_isolation(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    path = collection(project)
    assert api.get(path, params={"query": "needle"}).json()["total"] == 0
    assert api.get(path, params={"query": "Synthetic Author"}).json()["total"] == 1
    page = api.post(path + "/search-content", json={"query": "needle", "fulltext": True}).json()
    assert page["total"] == 1
    assert "needle" in page["items"][0]["snippet"]
    assert not page["indexing_incomplete"]
    other = api.post("/api/v1/projects", json={"name": "Other"}).json()
    assert api.get(collection(other), params={"fulltext": True}).json()["total"] == 0
    assert api.get(collection(other) + "/" + record["id"] + "/text").status_code == 404


def test_rebuild_receipt_retries_do_not_reset_inflight_or_publish_old_claim(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    old = claim_transcript_job(factory, settings)
    assert old is not None
    payload = {"client_operation_id": str(uuid4())}
    rebuild = collection(project) + "/rebuild"
    assert api.post(rebuild, json=payload).json() == {
        "queued": 1, "project_id": project["id"], **payload}
    complete_transcript_job(factory, old, None, ExtractionError("old_claim_error"))
    assert read(api, project, record)["status"] == "waiting"
    assert run(api)
    ready = read(api, project, record)
    assert api.post(rebuild, json=payload).json() == {
        "queued": 1, "project_id": project["id"], **payload}
    assert read(api, project, record) == ready
    assert not run(api)


def test_rebuild_during_tika_call_discards_result_and_clears_old_search(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert api.get(collection(project), params={"query": "needle", "fulltext": True})\
        .json()["total"] == 1
    def rebuild():
        return api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())})
    assert rebuild().status_code == 200
    assert run(api, Parser(callback=rebuild))
    assert read(api, project, record)["status"] == "waiting"
    assert api.get(collection(project), params={"query": "needle", "fulltext": True})\
        .json()["total"] == 0


@pytest.mark.parametrize("failure,code", [
    ("missing", "transcript_io_error"),
    ("client", "transcript_unsupported_client"),
    ("format", "transcript_invalid_format"),
    ("tika", "extraction_parse_failed"),
])
def test_failures_have_terminal_metadata_and_do_not_poison_queue(
    api, project, work_payload, tmp_path, postgres_engine, failure, code,
):
    work, _, record, source = register(api, project, work_payload, tmp_path,
                                      client="unknown" if failure == "client" else "claude-code")
    if failure == "missing":
        source.unlink()
    if failure == "format":
        source.write_text("broken json")
    expire_lease(postgres_engine, work["id"])
    parser = Parser(error=ExtractionError("extraction_parse_failed")) if failure == "tika" else None
    assert run(api, parser)
    failed = read(api, project, record)
    assert failed["status"] == "failed"
    assert failed["error_code"] == code
    assert failed["indexing_started_at"] <= failed["indexing_completed_at"]
    if failure == "tika":
        assert failed["format"] == "claude-code-jsonl"
        assert failed["mime_type"] == "application/x-ndjson"
        assert failed["sha256"] is not None
    assert not run(api)
    assert api.get(collection(project)).json()["indexing_incomplete"]


def test_settings_pause_claims_revision_guard_and_effective_operator_limit(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    path = f"/api/v1/projects/{project['id']}/transcript-settings"
    current = api.get(path).json()
    assert current["allowed_roots"] == [str(tmp_path)]
    payload = {"enabled": False, "max_file_size_bytes": 1024, "expected_revision": 1}
    assert api.patch(path, json=payload).json()["revision"] == 2
    assert api.patch(path, json=payload).status_code == 409
    assert not run(api)
    payload.update(enabled=True, expected_revision=2)
    assert api.patch(path, json=payload).status_code == 200
    assert run(api)
    payload.update(expected_revision=3, max_file_size_bytes=268435456)
    assert api.patch(path, json=payload).status_code == 422


def test_worker_reclaims_expired_jobs_and_bounds_transient_retries(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    factory, settings = api.app.state.session_factory, api.app.state.settings
    abandoned = claim_transcript_job(factory, settings)
    assert abandoned is not None
    with factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        database.commit()
    assert run(api, Parser(error=ExtractionError("extraction_unavailable", True)))
    assert read(api, project, record)["status"] == "pending"
    with factory() as database:
        row = database.scalar(select(Transcript))
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        database.commit()
    assert run(api, Parser(error=ExtractionError("extraction_unavailable", True)))
    assert read(api, project, record)["status"] == "failed"
    complete_transcript_job(factory, abandoned, None, ExtractionError("stale_claim"))
    assert read(api, project, record)["error_code"] == "extraction_unavailable"


@pytest.mark.parametrize("fulltext", [False, True])
def test_capacity_preflight_rejects_before_loading_corpus(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch, fulltext,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    with api.app.state.session_factory() as database:
        row = database.get(Transcript, UUID(record["id"]))
        if fulltext:
            row.normalized_text = "🦊" * 2000
            row.text_sha256 = hashlib.sha256(row.normalized_text.encode()).hexdigest()
        else:
            row.extracted_metadata = {"synthetic": ["m" * 4000]}
        database.commit()
    if fulltext:
        api.app.state.settings.transcript_search_max_bytes = 4096
    else:
        monkeypatch.setattr(transcript_service, "_SEARCH_MAX_BYTES", 4096)
    body_queries = []

    def before_cursor(_conn, _cursor, _statement, _parameters, context, _executemany):
        columns = getattr(getattr(context.compiled, "statement", None), "column_descriptions", [])
        if any(column.get("entity") is Transcript and column.get("name") == "Transcript"
               for column in columns):
            body_queries.append(context.execution_options.get("yield_per"))

    event.listen(postgres_engine, "before_cursor_execute", before_cursor)
    try:
        response = api.get(collection(project), params={"query": "anything", "fulltext": fulltext})
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_cursor)
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "transcript_search_capacity"
    assert body_queries == []


def test_corpus_cursor_fetches_one_document_from_a_coherent_snapshot(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    preflight = transcript_service._preflight_corpus
    batches = []

    def grow_after_preflight(database, statement, fulltext, maximum_content_bytes):
        preflight(database, statement, fulltext, maximum_content_bytes)
        with api.app.state.session_factory() as writer:
            value = "🦊" * 2000
            writer.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
                normalized_text=value, text_sha256=hashlib.sha256(value.encode()).hexdigest()))
            writer.commit()

    def before_cursor(_conn, _cursor, _statement, _parameters, context, _executemany):
        columns = getattr(getattr(context.compiled, "statement", None), "column_descriptions", [])
        if any(column.get("entity") is Transcript and column.get("name") == "Transcript"
               for column in columns):
            batches.append(context.execution_options.get("yield_per"))

    monkeypatch.setattr(transcript_service, "_preflight_corpus", grow_after_preflight)
    api.app.state.settings.transcript_search_max_bytes = 4096
    event.listen(postgres_engine, "before_cursor_execute", before_cursor)
    try:
        response = api.get(collection(project), params={"query": "needle", "fulltext": True})
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_cursor)
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert "needle" in response.json()["items"][0]["snippet"]
    assert batches == [1]
    changed = api.get(collection(project), params={"query": "needle", "fulltext": True})
    assert changed.status_code == 503
    assert changed.json()["detail"]["code"] == "transcript_search_capacity"


def test_search_admission_bounds_parallel_corpus_loading(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    work, _, _, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    entered, release = Event(), Event()
    bounded = transcript_service._bounded_records
    calls = []

    def pause_load(database, statement, fulltext, maximum_content_bytes):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return bounded(database, statement, fulltext, maximum_content_bytes)

    monkeypatch.setattr(transcript_service, "_bounded_records", pause_load)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(api.get, collection(project),
                                params={"query": "needle", "fulltext": True})
        assert entered.wait(5)
        try:
            second = api.get(collection(project), params={"query": "needle", "fulltext": True})
            assert second.status_code == 503, second.text
            assert second.json()["detail"]["code"] == "transcript_search_busy"
            assert len(calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert api.get(collection(project), params={"query": "needle"}).status_code == 200


def test_moved_work_transcript_follows_current_project_without_source_leakage(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    old_path = collection(project) + "/" + record["id"]
    snapshot = api.get(old_path + "/content").content
    target = api.post("/api/v1/projects", json={"name": "Transcript move target"}).json()
    response = api.post(item_path(project, work) + "/move",
                        json=_move_payload(target, work["version"]))
    assert response.status_code == 200, response.text
    for suffix in ("", "/text", "/content"):
        assert api.get(old_path + suffix).status_code == 404
    page = api.get(collection(project), params={"query": "needle", "fulltext": True})
    assert page.json()["total"] == 0
    moved = api.get(collection(target)).json()["items"]
    assert len(moved) == 1
    assert moved[0]["id"] == record["id"]
    assert moved[0]["project_id"] == target["id"]
    assert api.get(collection(target) + "/" + record["id"] + "/content").content == snapshot


def test_populated_transcript_archive_restores_snapshots_settings_and_rebuild_receipts(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    settings_path = f"/api/v1/projects/{project['id']}/transcript-settings"
    response = api.patch(settings_path, json={"enabled": True, "max_file_size_bytes": 1024,
                                             "expected_revision": 1})
    assert response.status_code == 200, response.text
    payload = {"client_operation_id": str(uuid4())}
    receipt = api.post(collection(project) + "/rebuild", json=payload).json()
    assert run(api)
    original = _snapshot(postgres_engine, project)
    archive = _export(postgres_engine, project)
    assert original["transcripts"][0]["normalized_text"] == "user: rare needle in transcript"
    assert original["transcript_settings"]
    assert original["transcript_rebuilds"]
    assert api.post(collection(project) + "/rebuild",
                    json={"client_operation_id": str(uuid4())}).status_code == 200
    source.unlink()
    assert run(api)
    assert read(api, project, record)["status"] == "failed"
    restore_project(postgres_engine, UUID(project["id"]), io.BytesIO(archive))
    restored = _snapshot(postgres_engine, project)
    for table in ("transcripts", "transcript_settings", "transcript_rebuilds"):
        assert restored[table] == original[table]
    assert api.post(collection(project) + "/rebuild", json=payload).json() == receipt
    assert read(api, project, record)["status"] == "ready"
    download = api.get(collection(project) + "/" + record["id"] + "/content")
    assert download.text == "user: rare needle in transcript"
    assert not source.exists()


@pytest.mark.parametrize("encoder_failure", [False, True])
def test_actual_background_loop_survives_bad_tool_input_and_indexes_following_work(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch, encoder_failure,
):
    bad_dir, good_dir = tmp_path / "bad", tmp_path / "good"
    bad_dir.mkdir()
    good_dir.mkdir()
    bad_work, _, bad_record, source = register(api, project, work_payload, bad_dir)
    good_work, _, good_record, _ = register(api, project,
        {**work_payload, "title": "Following valid transcript"}, good_dir)
    api.app.state.settings.transcript_allowed_roots = [tmp_path]
    nested = "leaf"
    for _ in range(80 if not encoder_failure else 2):
        nested = {"nested": nested}
    source.write_text(json.dumps({"role": "assistant", "content": [
        {"type": "tool_use", "name": "Synthetic", "input": nested}]}))
    if encoder_failure:
        encode = json.JSONEncoder.iterencode

        def overflow(self, value, *args, **kwargs):
            if value == nested:
                raise RecursionError("private encoder diagnostics")
            return encode(self, value, *args, **kwargs)

        monkeypatch.setattr(json.JSONEncoder, "iterencode", overflow)
    expire_lease(postgres_engine, bad_work["id"])
    expire_lease(postgres_engine, good_work["id"])
    factory = api.app.state.session_factory

    async def exercise():
        task = asyncio.create_task(transcript_indexing_loop(
            factory, api.app.state.settings, Parser()))
        try:
            async with asyncio.timeout(5):
                while True:
                    if task.done():
                        await task
                        pytest.fail("Background indexing stopped unexpectedly")
                    with factory() as database:
                        rows = database.execute(select(Transcript.id, Transcript.status)).all()
                        statuses = dict(rows)
                    if statuses.get(UUID(good_record["id"])) == "ready":
                        return statuses
                    await asyncio.sleep(0.02)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    statuses = asyncio.run(exercise())
    assert statuses[UUID(bad_record["id"])] == "failed"
    assert statuses[UUID(good_record["id"])] == "ready"
    assert read(api, project, bad_record)["error_code"] == (
        "transcript_invalid_format" if encoder_failure else "transcript_nesting_limit")


def test_uncommitted_renewal_is_observed_before_transcript_claim(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, receipt, record, _ = register(api, project, work_payload, tmp_path)
    factory = api.app.state.session_factory
    # Begin with a short but still live lease so the real renewal guard passes.
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE work_leases SET "
            "acquired_at=clock_timestamp()-interval '2 seconds', "
            "renewed_at=clock_timestamp()-interval '1 second', "
            "expires_at=clock_timestamp()+interval '1 second' WHERE work_item_id=:id"),
            {"id": UUID(work["id"])})
    renewed, release = Event(), Event()

    def renew_without_committing():
        with factory() as database:
            with project_mutation(database, UUID(project["id"])):
                row = database.scalar(select(WorkItem).where(
                    WorkItem.id == UUID(work["id"])).with_for_update())
                renew_lease_record(database, row, receipt["lease_token"], lease_minutes=15)
                renewed.set()
                assert release.wait(5)
                database.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        renewal = executor.submit(renew_without_committing)
        assert renewed.wait(5)
        try:
            # A nonlocking MVCC read sees the old expiry, exactly as the worker's
            # candidate lookup does before it reaches the serialized recheck.
            with postgres_engine.connect() as connection:
                connection.execute(text("SELECT pg_sleep(GREATEST(0, EXTRACT(EPOCH FROM "
                    "(expires_at-clock_timestamp()))) + 0.02) FROM work_leases "
                    "WHERE work_item_id=:id"), {"id": UUID(work["id"])})
            claim_future = executor.submit(claim_transcript_job, factory, api.app.state.settings)
            with pytest.raises(TimeoutError):
                claim_future.result(timeout=0.1)
        finally:
            release.set()
        renewal.result(timeout=5)
        assert claim_future.result(timeout=5) is None
    assert read(api, project, record)["status"] == "waiting"
    with factory() as database:
        lease = database.get(WorkLease, UUID(work["id"]))
        assert lease.expires_at > datetime.now(UTC)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert read(api, project, record)["status"] == "ready"


@pytest.mark.parametrize("mutation", ["register", "rebuild"])
def test_list_counts_and_items_share_snapshot_across_concurrent_mutation(
    api, project, work_payload, tmp_path, postgres_engine, mutation,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    changed = False

    def after_count(_conn, _cursor, statement, _parameters, _context, _executemany):
        nonlocal changed
        if changed or "count(" not in statement.lower() or "transcripts" not in statement:
            return
        changed = True
        with api.app.state.session_factory() as writer:
            with project_mutation(writer, UUID(project["id"])):
                if mutation == "rebuild":
                    transcript_service.rebuild_transcripts(writer, UUID(project["id"]), uuid4())
                else:
                    current_work = writer.get(WorkItem, UUID(work["id"]))
                    transcript_service.register_transcripts(
                        writer, current_work, UUID(record["lease_generation_id"]),
                        "claude-code", "additional-session",
                        [{"client": "claude-code", "path": str(tmp_path / "extra.jsonl")}],
                        "subagent")
                writer.commit()

    event.listen(postgres_engine, "after_cursor_execute", after_count)
    try:
        response = api.get(collection(project))
    finally:
        event.remove(postgres_engine, "after_cursor_execute", after_count)
    assert response.status_code == 200, response.text
    assert changed
    snapshot = response.json()
    assert snapshot["total"] == len(snapshot["items"]) == 1
    assert snapshot["items"][0]["status"] == "ready"
    assert not snapshot["indexing_incomplete"]
    current = api.get(collection(project)).json()
    assert current["indexing_incomplete"]
    assert current["total"] == (2 if mutation == "register" else 1)


@pytest.mark.parametrize("replacement", ["malformed", "missing"])
def test_retry_replaces_all_provenance_when_source_changes(
    api, project, work_payload, tmp_path, postgres_engine, replacement,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    source.write_text(json.dumps({"type": "user", "sessionId": "obsoleteprovenancetoken",
                                 "message": {"role": "user", "content": "first source"}}))
    expire_lease(postgres_engine, work["id"])
    assert run(api, Parser(error=ExtractionError("extraction_unavailable", True)))
    first = read(api, project, record)
    assert first["status"] == "pending"
    assert first["metadata"]["transcript:session_id"] == ["obsoleteprovenancetoken"]
    assert first["format"] is not None and first["mime_type"] is not None
    if replacement == "malformed":
        source.write_bytes(b'{"unfinished":')
        expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        expected_size = source.stat().st_size
    else:
        source.unlink()
        expected_hash = expected_size = None
    with api.app.state.session_factory() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        database.commit()
    assert run(api)
    current = read(api, project, record)
    assert current["status"] == "failed"
    assert current["sha256"] == expected_hash
    assert current["size_bytes"] == expected_size
    assert current["format"] is None and current["mime_type"] is None
    assert current["metadata"] == {}
    assert current["text_sha256"] is None
    assert not current["truncated"]
    page = api.get(collection(project), params={"query": "obsoleteprovenancetoken"})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 0


def test_new_attempt_and_rebuild_clear_all_previous_snapshot_fields(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api, Parser(error=ExtractionError("extraction_unavailable", True)))
    with api.app.state.session_factory() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        database.commit()
    job = claim_transcript_job(api.app.state.session_factory, api.app.state.settings)
    assert job is not None
    processing = read(api, project, record)
    assert processing["status"] == "processing"
    for field in ("sha256", "size_bytes", "mime_type", "format", "text_sha256"):
        assert processing[field] is None
    assert processing["metadata"] == {}
    complete_transcript_job(api.app.state.session_factory, job, None,
                            ExtractionError("extraction_unavailable", True))
    response = api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())})
    assert response.status_code == 200, response.text
    assert run(api)
    assert read(api, project, record)["metadata"]
    response = api.post(collection(project) + "/rebuild",
                        json={"client_operation_id": str(uuid4())})
    assert response.status_code == 200, response.text
    waiting = read(api, project, record)
    for field in ("sha256", "size_bytes", "mime_type", "format", "text_sha256"):
        assert waiting[field] is None
    assert waiting["metadata"] == {}
    assert waiting["status"] == "waiting"


def test_bulk_rebuild_updates_without_materializing_transcripts_and_fences_old_worker(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    job = claim_transcript_job(api.app.state.session_factory, api.app.state.settings)
    assert job is not None
    # PostgreSQL produces the corpus; no Python list of rows or IDs is required.
    with postgres_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO transcripts(id, work_item_id, lease_generation_id, client, session_id,
                                    source_path, kind, extracted_metadata)
            SELECT gen_random_uuid(), :work, :generation, 'claude-code', 'bulk-session',
                   '/synthetic/bulk-' || number || '.jsonl', 'subagent',
                   jsonb_build_object('property', jsonb_build_array(repeat('x', 500)))
            FROM generate_series(1, 500) AS number
        """), {"work": UUID(work["id"]), "generation": UUID(record["lease_generation_id"])})
    loaded, updates = [], []

    def before_cursor(_conn, _cursor, statement, _parameters, context, _executemany):
        columns = getattr(getattr(context.compiled, "statement", None), "column_descriptions", [])
        if any(column.get("entity") is Transcript and column.get("name") == "Transcript"
               for column in columns):
            loaded.append(statement)
        if statement.startswith("UPDATE transcripts"):
            updates.append(statement)

    payload = {"client_operation_id": str(uuid4())}
    event.listen(postgres_engine, "before_cursor_execute", before_cursor)
    try:
        response = api.post(collection(project) + "/rebuild", json=payload)
        replay = api.post(collection(project) + "/rebuild", json=payload)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", before_cursor)
    assert response.status_code == replay.status_code == 200, response.text
    assert response.json() == replay.json()
    assert response.json()["queued"] == 501
    assert loaded == []
    assert len(updates) == 1 and "RETURNING" not in updates[0]
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM transcripts WHERE generation=2 "
                                      "AND status='waiting' AND extracted_metadata='{}'::jsonb")) \
            == 501
    complete_transcript_job(api.app.state.session_factory, job, None,
                            ExtractionError("obsolete_worker_error"))
    current = read(api, project, record)
    assert current["status"] == "waiting"
    assert current["error_code"] is None
    assert current["metadata"] == {}
