"""Native size, memory, complete-content, and retained-copy policy regressions."""

import hashlib
import io
import json
import subprocess
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_copying import copy_next_transcript
from mnemonic_api.transcript_fulltext import text_digest
from mnemonic_api.transcript_normalization import normalize_transcript
from mnemonic_api.transcript_spool import TranscriptSegments
from mnemonic_api.transcript_upgrades import refresh_outdated_normalizations

from .test_leases_postgres import expire_lease
from .test_transcript_indexing_postgres import collection, read, register, run


@pytest.mark.parametrize("client", ["claude_code", "codex"])
def test_streamed_native_normalization_has_identical_canonical_evidence(client):
    rows = ([{"role": "user", "content": "hello 🦊"},
             {"role": "assistant", "content": [{"type": "tool_use", "id": "call",
                "name": "test", "input": {"limit": 7}}]},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call",
                                            "content": "complete"}]}]
            if client == "claude_code" else [
                {"type": "session_meta", "payload": {"id": "test"}},
                {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": "hello 🦊"}]}},
                {"type": "response_item", "payload": {"type": "function_call_output",
                    "call_id": "call", "output": "complete"}},
                {"type": "response_item", "payload": {"type": "function_call",
                    "call_id": "call", "name": "test", "arguments": '{"limit":7}'}}])
    data = b"\n".join(json.dumps(row).encode() for row in rows)
    snapshot = uuid4()
    expected = normalize_transcript(data, client, snapshot)
    actual = normalize_transcript(io.BytesIO(data), client, snapshot)
    try:
        assert isinstance(actual.segments, TranscriptSegments)
        assert list(actual.segments) == list(expected.segments)
        assert (actual.sha256, actual.source_sha256, actual.metadata) == (
            expected.sha256, expected.source_sha256, expected.metadata)
        assert text_digest(actual.segments) == text_digest(expected.segments)
    finally:
        actual.segments.close()


def test_large_native_stream_does_not_allocate_a_file_sized_buffer(tmp_path):
    source = tmp_path / "large.jsonl"
    # Real native bookkeeping can dwarf readable text; all raw bytes must survive.
    line = json.dumps({"type": "file-history-snapshot", "snapshot": "x" * 65_000}).encode() + b"\n"
    with source.open("wb") as output:
        for _ in range(1100):
            output.write(line)
        output.write(b'{"role":"user","content":"final evidence"}\n')
    assert source.stat().st_size > 64 * 1024 * 1024
    tracemalloc.start()
    try:
        with source.open("rb") as content:
            result = normalize_transcript(content, "claude_code", uuid4())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    try:
        assert result.segments[-1].text == "final evidence"
        assert peak < 16 * 1024 * 1024
    finally:
        result.segments.close()


def test_retained_copy_can_be_read_and_adopted_after_capture_limit_is_lowered(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_bytes(b'{"role":"user","content":"retained evidence"}\n')
    identity, snapshot = uuid4(), uuid4()
    storage = TranscriptStorage(tmp_path / "private", 1000)
    copied = storage.capture(identity, snapshot, str(source), [tmp_path])
    source.unlink()
    reduced = TranscriptStorage(storage.root, 1)
    with reduced.open_copy(copied) as content:
        assert content.read().endswith(b'"retained evidence"}\n')
    assert reduced.capture(identity, snapshot, str(source), [tmp_path]) == copied
    with storage.open(copied.storage_key) as content:
        path = content.name
    # open() is descriptor based; tamper through the known private fixture path.
    assert path is not None
    (storage.root / copied.storage_key).write_bytes(b"changed")
    with pytest.raises(ExtractionError, match="transcript_copy_integrity_failed"):
        with reduced.open_copy(copied):
            pytest.fail("Corrupt retained bytes must never be exposed")


@pytest.mark.postgres
@pytest.mark.parametrize("client", ["claude_code", "codex"])
def test_full_conversation_survives_artifact_budget_search_paging_download_and_rebuild(
    api, project, work_payload, tmp_path, postgres_engine, client, monkeypatch,
):
    work, _, record, source = register(api, project, work_payload, tmp_path, client=client)
    with source.open("w") as output:
        for number in range(18):
            content = f"section {number} " + "ordinary " * 55_000
            row = ({"role": "user", "content": content} if client == "claude_code" else
                   {"type": "response_item", "payload": {"type": "message", "role": "user",
                    "content": [{"type": "input_text", "text": content}]}})
            output.write(json.dumps(row) + "\n")
        row = ({"role": "assistant", "content": "tailneedle 🦊 final evidence"}
               if client == "claude_code" else {"type": "response_item", "payload": {
                   "type": "message", "role": "assistant", "content": [
                       {"type": "output_text", "text": "tailneedle 🦊 final evidence"}]}})
        output.write(json.dumps(row) + "\n")
    api.app.state.settings.artifact_extraction_max_chars = 10
    expire_lease(postgres_engine, work["id"])
    # An unavailable artifact extractor has no bearing on native conversations.
    def reject_tika(*args, **kwargs):
        pytest.fail("Native transcripts must never call the artifact extractor")

    monkeypatch.setattr("mnemonic_api.artifact_tika.TikaExtractor.extract", reject_tika)
    assert run(api)
    ready = read(api, project, record)
    assert ready["status"] == "ready" and not ready["truncated"]
    endpoint = collection(project) + "/" + record["id"]
    hit = api.get(collection(project), params={"detail": "full", "query": "tailneedle",
                                              "fulltext": True}).json()
    assert hit["total"] == 1 and hit["items"][0]["segment_id"]
    page = api.get(endpoint + "/text", params={"offset": 8_000_001, "limit": 20}).json()
    assert page["total_chars"] > 8_000_000 and len(page["text"]) == 20
    downloaded = api.get(endpoint + "/content")
    assert downloaded.status_code == 200
    assert downloaded.text.endswith("tailneedle 🦊 final evidence")
    assert hashlib.sha256(downloaded.content).hexdigest() == ready["text_sha256"]
    assert int(downloaded.headers["content-length"]) == len(downloaded.content)
    assert page["text"] == downloaded.text[8_000_001:8_000_021]
    # Feed real REST replies through the separately installed MCP server. No
    # shared fixture adds omitted fields or repairs ranking/disclosure metadata.
    root = Path(__file__).resolve().parents[2]
    python = root / "mcp/.venv/bin/python"
    assert python.exists(), "Install the separate MCP environment for contract integration"
    cases = [
        {"tool": "list_transcripts", "arguments": {"project_id": project["id"],
            "detail": "full"}, "response": api.get(collection(project), params={
                "detail": "full", "limit": 20}).json()},
        {"tool": "get_transcript_text", "arguments": {"project_id": project["id"],
            "transcript_id": record["id"], "expected_sha256": ready["text_sha256"],
            "offset": 8_000_001, "limit": 20}, "response": page},
    ]
    probe = subprocess.run([str(python), str(root / "backend/tests/transcript_mcp_probe.py")],
        input=json.dumps(cases), text=True, capture_output=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert "2 unmodified REST responses" in probe.stdout
    # Accepted bytes remain usable if the source disappears and policy tightens.
    source.unlink()
    api.app.state.settings.transcript_max_bytes = 1
    assert api.post(collection(project) + "/rebuild", json={
        "client_operation_id": str(uuid4())}).status_code == 200
    assert run(api)
    assert read(api, project, record)["text_sha256"] == ready["text_sha256"]


@pytest.mark.postgres
@pytest.mark.parametrize("guard", ["active", "paused"])
def test_size_failure_rechecks_after_limit_change_but_respects_guards(
    api, project, work_payload, tmp_path, postgres_engine, guard,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    settings = api.app.state.settings
    settings.transcript_max_bytes = 1
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert read(api, project, record)["copy_error_code"] == "transcript_too_large"
    settings.transcript_max_bytes = 100_000
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            copy_next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
    if guard == "paused":
        assert api.patch(f"/api/v1/projects/{project['id']}/transcript-settings", json={
            "enabled": False, "max_file_size_bytes": 100_000, "expected_revision": 1,
        }).status_code == 200
    else:
        from sqlalchemy import text
        with postgres_engine.begin() as connection:
            connection.execute(text("UPDATE work_leases SET expires_at=clock_timestamp() "
                "+ interval '10 minutes' WHERE work_item_id=:work"), {"work": work["id"]})
    assert not copy_next_transcript(api.app.state.session_factory, settings)
    if guard == "paused":
        assert api.patch(f"/api/v1/projects/{project['id']}/transcript-settings", json={
            "enabled": True, "max_file_size_bytes": 100_000, "expected_revision": 2,
        }).status_code == 200
    else:
        expire_lease(postgres_engine, work["id"])
    assert run(api)
    assert read(api, project, record)["status"] == "ready"


@pytest.mark.postgres
def test_worker_refreshes_historical_truncated_text_without_recopy(
    api, project, work_payload, tmp_path, postgres_engine,
):
    work, _, record, source = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    assert run(api)
    with api.app.state.session_factory.begin() as database:
        database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
            normalized_text="user: rare", text_sha256=hashlib.sha256(b"user: rare").hexdigest(),
            truncated=True))
    source.unlink()
    with api.app.state.session_factory.begin() as database:
        assert refresh_outdated_normalizations(database, api.app.state.settings) == 1
    assert run(api)
    ready = read(api, project, record)
    assert not ready["truncated"]
    assert api.get(collection(project), params={"query": "needle", "fulltext": True})\
        .json()["total"] == 1
    with api.app.state.session_factory() as database:
        assert database.scalar(select(Transcript.copy_attempts)) == 1


def test_rejected_native_stream_closes_its_private_spool(monkeypatch):
    from mnemonic_api import transcript_spool

    opened = []
    original = transcript_spool.TemporaryFile

    def temporary(*args, **kwargs):
        result = original(*args, **kwargs)
        opened.append(result)
        return result

    monkeypatch.setattr(transcript_spool, "TemporaryFile", temporary)
    content = io.BytesIO(b'{"role":"user","content":"valid first record"}\n{broken\n')
    with pytest.raises(ExtractionError):
        normalize_transcript(content, "claude_code", uuid4())
    assert opened and all(file.closed for file in opened)


@pytest.mark.postgres
def test_index_storage_exhaustion_rechecks_slowly_and_reuses_complete_segments(
    api, project, work_payload, tmp_path, postgres_engine,
):
    import errno

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    for _ in range(3):
        with api.app.state.session_factory.begin() as database:
            database.execute(update(Transcript).values(
                next_attempt_at=datetime.now(UTC) - timedelta(seconds=1)))
        assert run(api, stage_error=OSError(errno.ENOSPC, "simulated full volume"))
    failed = read(api, project, record)
    assert failed["status"] == "failed"
    assert failed["normalization_status"] == "ready"
    assert failed["error_code"] == "transcript_storage_full"
    assert not run(api)
    with api.app.state.session_factory.begin() as database:
        stored = database.get(Transcript, UUID(record["id"]))
        assert stored.next_attempt_at > datetime.now(UTC) + timedelta(minutes=4)
        stored.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    assert run(api)
    ready = read(api, project, record)
    assert ready["status"] == "ready" and not ready["truncated"]
    assert ready["normalized_revision"] == failed["normalized_revision"]
    with api.app.state.session_factory() as database:
        assert database.scalar(select(Transcript.copy_attempts)) == 1


@pytest.mark.postgres
def test_copy_mutation_during_normalization_cannot_publish_unverified_segments(
    api, project, work_payload, tmp_path, postgres_engine, monkeypatch,
):
    from mnemonic_api import transcript_indexing
    from mnemonic_api.models import TranscriptNormalization

    work, _, record, _ = register(api, project, work_payload, tmp_path)
    expire_lease(postgres_engine, work["id"])
    settings = api.app.state.settings
    assert copy_next_transcript(api.app.state.session_factory, settings)
    with api.app.state.session_factory() as database:
        stored = database.get(Transcript, UUID(record["id"]))
        native = settings.transcript_root / stored.storage_key
    original = transcript_indexing.normalize_transcript

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        with native.open("ab") as output:
            output.write(b"unverified")
        return result

    monkeypatch.setattr(transcript_indexing, "normalize_transcript", mutate)
    assert run(api)
    failed = read(api, project, record)
    assert failed["status"] == "failed"
    assert failed["error_code"] == "transcript_copy_integrity_failed"
    assert failed["normalized_revision"] is None and failed["text_sha256"] is None
    with api.app.state.session_factory() as database:
        assert database.scalar(select(TranscriptNormalization.transcript_id)) is None


def test_enrollment_rejects_known_task_execution_journal(tmp_path):
    from mnemonic_api.transcript_access import TranscriptAccessError
    from mnemonic_api.transcript_source_identity import capture_identity

    path = tmp_path / "task.jsonl"
    path.write_text('{"type":"started","agentId":"worker","key":"task"}\n')
    with path.open("rb") as source:
        with pytest.raises(TranscriptAccessError, match="transcript_not_native_session"):
            capture_identity(source.fileno(), str(path))
