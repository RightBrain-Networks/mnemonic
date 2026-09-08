"""Durable extraction, content retention, lease recovery and mutation races on PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import undefer

from mnemonic_api.artifact_extraction import _claim_job, _complete_job, extract_next_artifact
from mnemonic_api.artifact_tika import ExtractedArtifact, ExtractionError, TikaExtractor
from mnemonic_api.models import ArtifactExtraction

from .test_artifact_tika import response as tika_response
from .test_artifacts_postgres import artifact_storage, collection, headers, upload

pytestmark = pytest.mark.postgres
__all__ = ["artifact_storage"]


class Parser:
    def __init__(self, *, callback=None, error=None):
        self.callback = callback
        self.error = error
        self.calls = []

    def extract(self, content, *, filename, size_bytes):
        value = content.read()
        self.calls.append((value, filename, size_bytes))
        if self.callback is not None:
            self.callback()
        if self.error is not None:
            raise self.error
        return ExtractedArtifact(value.decode(), {"dc:creator": ["Synthetic Author"]}, False)


def run_job(api, storage, parser):
    return extract_next_artifact(api.app.state.session_factory, storage, parser)


def rows(api, artifact):
    with api.app.state.session_factory() as database:
        return list(database.scalars(select(ArtifactExtraction).where(
            ArtifactExtraction.artifact_id == UUID(artifact["id"]),
        ).options(undefer(ArtifactExtraction.normalized_text)).order_by(ArtifactExtraction.revision)))


def test_extract_metadata_current_text_history_and_permanent_receipt(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="draft.txt", body=b"first confidential content")
    assert artifact["extraction"]["status"] == "pending"
    parser = Parser()
    assert run_job(api, artifact_storage, parser)
    assert not run_job(api, artifact_storage, parser)
    path = collection(project) + "/" + artifact["id"]
    read = api.get(path).json()
    assert read["extraction"]["status"] == "ready"
    assert read["extraction"]["metadata"] == {"dc:creator": ["Synthetic Author"]}
    assert "normalized_text" not in read["extraction"]
    assert api.get(collection(project), params={"q": "Synthetic Author"}).json()["total"] == 1
    history = api.get(path + "/history", params={"q": "Synthetic Author"}).json()
    assert history["revisions"]["total"] == 1
    assert history["revisions"]["items"][0]["extraction"] == read["extraction"]
    assert rows(api, artifact)[0].normalized_text == "first confidential content"
    with api.app.state.session_factory() as database:
        receipt = database.scalar(text("SELECT response_body FROM artifact_operations"))
    assert receipt["extraction"]["status"] == "pending"
    assert "confidential content" not in str(receipt)


def test_replace_delete_purge_text_but_keep_extracted_revision_metadata(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="draft.txt", body=b"secret first")
    parser = Parser()
    run_job(api, artifact_storage, parser)
    path = collection(project) + "/" + artifact["id"]
    response = api.put(path + "/content", content=b"replacement", headers=headers(
        {"filename": "draft.txt"}, revision=1,
    ))
    assert response.status_code == 200, response.text
    before, current = rows(api, artifact)
    assert (before.status, before.normalized_text) == ("superseded", None)
    assert before.extracted_metadata == {"dc:creator": ["Synthetic Author"]}
    assert current.status == "pending"
    run_job(api, artifact_storage, parser)
    assert rows(api, artifact)[1].normalized_text == "replacement"
    deleted = api.delete(path, headers=headers(revision=2))
    assert deleted.status_code == 200, deleted.text
    assert all(
        row.normalized_text is None and row.status == "deleted" for row in rows(api, artifact)
    )
    assert deleted.json()["extraction"]["metadata"] == {"dc:creator": ["Synthetic Author"]}
    assert not run_job(api, artifact_storage, parser)


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_parse_completion_after_mutation_cannot_publish_stale_results(
    api, project, artifact_storage, mutation,
):
    artifact = upload(api, project, filename="race.txt", body=b"obsolete secret")
    path = collection(project) + "/" + artifact["id"]

    def mutate():
        if mutation == "replace":
            response = api.put(path + "/content", content=b"new", headers=headers(
                {"filename": "race.txt"}, revision=1,
            ))
        else:
            response = api.delete(path, headers=headers(revision=1))
        assert response.status_code == 200, response.text

    assert run_job(api, artifact_storage, Parser(callback=mutate))
    old = rows(api, artifact)[0]
    assert old.normalized_text is None
    assert old.extracted_metadata == {}
    assert old.status == ("superseded" if mutation == "replace" else "deleted")


def test_retryable_outage_backoff_recovers_and_terminal_parser_failure_stops(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="retry.txt", body=b"recoverable")
    parser = Parser(error=ExtractionError("extraction_unavailable", True))
    assert run_job(api, artifact_storage, parser)
    record = rows(api, artifact)[0]
    assert record.status == "pending"
    assert record.error_code == "extraction_unavailable"
    assert record.attempts == 1
    assert not run_job(api, artifact_storage, Parser())
    with api.app.state.session_factory() as database:
        record = database.get(ArtifactExtraction, (UUID(artifact["id"]), 1))
        record.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        database.commit()
    assert run_job(api, artifact_storage, Parser())
    assert rows(api, artifact)[0].status == "ready"
    other = upload(api, project, filename="bad.txt", body=b"unsupported")
    assert run_job(api, artifact_storage, Parser(error=ExtractionError("extraction_unsupported")))
    assert rows(api, other)[0].status == "failed"
    assert not run_job(api, artifact_storage, Parser())


def test_expired_claim_can_resume_and_old_claim_cannot_publish(api, project, artifact_storage):
    artifact = upload(api, project, filename="claim.txt", body=b"current")
    factory = api.app.state.session_factory
    old = _claim_job(factory, 60)
    assert old is not None
    assert not run_job(api, artifact_storage, Parser())
    with factory() as database:
        record = database.get(ArtifactExtraction, (UUID(artifact["id"]), 1))
        record.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        database.commit()
    assert run_job(api, artifact_storage, Parser())
    _complete_job(factory, old, ExtractedArtifact("stale", {"author": ["stale"]}, False), None)
    assert rows(api, artifact)[0].normalized_text == "current"
    assert rows(api, artifact)[0].attempts == 2


@pytest.mark.parametrize("malformed_status", [[], {}])
def test_malformed_tika_status_does_not_stop_processing_later_artifacts(
    api, project, artifact_storage, malformed_status,
):
    replies = iter([
        tika_response({"status": malformed_status}, status=503),
        tika_response([{"tk:content": "current"}]),
    ])
    parser = TikaExtractor(
        api.app.state.settings, transport=httpx.MockTransport(lambda _: next(replies)),
    )
    failed = upload(api, project, filename="bad.txt", body=b"unavailable")
    assert run_job(api, artifact_storage, parser)
    record = rows(api, failed)[0]
    assert record.status == "failed"
    assert record.error_code == "extraction_invalid_response"
    current = upload(api, project, filename="next.txt", body=b"current")
    assert run_job(api, artifact_storage, parser)
    record = rows(api, current)[0]
    assert record.status == "ready"
    assert record.normalized_text == "current"
    assert not run_job(api, artifact_storage, parser)


@pytest.mark.parametrize("damage", ["missing", "mismatch"])
def test_missing_or_changed_bytes_never_reach_parser(api, project, artifact_storage, damage):
    artifact = upload(api, project, filename="file.txt", body=b"original")
    path = artifact_storage.root / project["id"] / artifact["id"] / "file.txt"
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"tampered")
    parser = Parser()
    assert run_job(api, artifact_storage, parser)
    assert parser.calls == []
    assert rows(api, artifact)[0].status == "failed"


def test_pending_failed_replacement_clears_old_text_before_publish(
    api, project, artifact_storage, monkeypatch,
):
    artifact = upload(api, project, filename="pending.txt", body=b"old secret")
    run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    original = artifact_storage.publish

    def unavailable(_stage):
        raise OSError("Synthetic unavailable storage")

    monkeypatch.setattr(artifact_storage, "publish", unavailable)
    response = api.put(path + "/content", content=b"new", headers=headers(
        {"filename": "pending.txt"}, revision=1,
    ))
    assert response.status_code == 503
    assert rows(api, artifact)[0].normalized_text is None
    assert rows(api, artifact)[0].status == "superseded"
    assert not run_job(api, artifact_storage, Parser())
    monkeypatch.setattr(artifact_storage, "publish", original)
    assert api.get(path).json()["revision"] == 2
    assert run_job(api, artifact_storage, Parser())
    assert rows(api, artifact)[1].normalized_text == "new"


def test_extraction_metadata_is_immutable_and_content_cannot_be_restored_after_delete(
    api, project, artifact_storage, postgres_engine,
):
    artifact = upload(api, project, filename="guard.txt", body=b"content")
    run_job(api, artifact_storage, Parser())
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("UPDATE artifact_extractions SET extracted_metadata='{}'::jsonb"))
    path = collection(project) + "/" + artifact["id"]
    assert api.delete(path, headers=headers(revision=1)).status_code == 200
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text(
            "UPDATE artifact_extractions SET status='ready', normalized_text='old'"
        ))
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(text("DELETE FROM artifact_extractions"))


def test_schema_new_jobs_have_distinct_claims(api, project, artifact_storage):
    for filename in ("one.txt", "two.txt"):
        upload(api, project, filename=filename, body=b"current")
    factory = api.app.state.session_factory
    first = _claim_job(factory, 60)
    second = _claim_job(factory, 60)
    assert first is not None and second is not None
    assert first.artifact_id != second.artifact_id
    assert first.lease_token != second.lease_token != uuid4()


def test_lowered_positive_upload_limit_does_not_block_existing_extraction(
    api, project, artifact_storage,
):
    artifact = upload(api, project, filename="larger.txt", body=b"existing larger content")
    artifact_storage.max_bytes = 1
    api.app.state.settings.artifact_max_bytes = 1
    assert run_job(api, artifact_storage, Parser())
    assert rows(api, artifact)[0].normalized_text == "existing larger content"


def test_pending_failed_delete_has_no_searchable_text_and_recovers(
    api, project, artifact_storage, monkeypatch,
):
    artifact = upload(api, project, filename="delete.txt", body=b"obsolete content")
    run_job(api, artifact_storage, Parser())
    path = collection(project) + "/" + artifact["id"]
    original = artifact_storage.delete

    def unavailable(_path):
        raise OSError("Synthetic unavailable storage")

    monkeypatch.setattr(artifact_storage, "delete", unavailable)
    assert api.delete(path, headers=headers(revision=1)).status_code == 503
    row = rows(api, artifact)[0]
    assert row.status == "deleted"
    assert row.normalized_text is None
    assert row.extracted_metadata == {"dc:creator": ["Synthetic Author"]}
    assert not run_job(api, artifact_storage, Parser())
    monkeypatch.setattr(artifact_storage, "delete", original)
    assert api.get(path).json()["deleted_at"] is not None
