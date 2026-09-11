"""Folder imports share transcript identity with transactional agent enrollment."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import Transcript, TranscriptImport
from mnemonic_api.services.work_items import require_project
from mnemonic_api.transcript_discovery import TranscriptDiscovery
from mnemonic_api.transcript_schemas import TranscriptImportRead, TranscriptImportRequest
from mnemonic_api.transcript_snapshots import empty_transcript_snapshot
from mnemonic_api.transcript_storage import canonical_source_path


def replay_import(database: Session, project_id: UUID,
                  payload: TranscriptImportRequest) -> TranscriptImportRead | None:
    require_project(database, project_id)
    receipt = database.get(TranscriptImport, (project_id, payload.client_operation_id))
    if receipt is None:
        return None
    if receipt.directory != payload.directory:
        raise conflict("transcript_import_conflict",
                       "Retry this import with its original folder and operation UUID.")
    return TranscriptImportRead(**{name: getattr(receipt, name)
                                  for name in TranscriptImportRead.model_fields})


def import_transcripts(database: Session, project_id: UUID, payload: TranscriptImportRequest,
                       scan: TranscriptDiscovery) -> TranscriptImportRead:
    # The caller holds the same project lock as claims, closeouts, and rebuilds.
    from mnemonic_api.services.transcripts import transcript_query

    replay = replay_import(database, project_id, payload)
    if replay is not None:
        return replay
    key = _source_key()
    existing = set(database.scalars(transcript_query(project_id).with_only_columns(key)
                                   .where(key.in_(scan.paths))))
    added = [path for path in scan.paths if path not in existing]
    database.add_all([Transcript(id=uuid4(), import_project_id=project_id,
                                source_path=path, client="claude_code", kind="imported",
                                status="pending") for path in added])
    result = TranscriptImportRead(project_id=project_id,
        client_operation_id=payload.client_operation_id, directory=payload.directory,
        imported=len(added), existing=len(scan.paths) - len(added), skipped=scan.skipped)
    database.add(TranscriptImport(**result.model_dump()))
    database.flush()
    return result


def take_imported_transcript(database: Session, project_id: UUID, source: str) -> Transcript | None:
    record = database.scalar(select(Transcript).where(
        Transcript.import_project_id == project_id,
        Transcript.source_path == canonical_source_path(source),
    ).with_for_update())
    if record is not None:
        # Enrollment takes ownership of the same public record. Invalidate any
        # extraction in flight and wait for this agent's new lease generation.
        for name, value in empty_transcript_snapshot().items():
            setattr(record, name, value)
        record.import_project_id = None
        record.generation += 1
        record.status = "waiting"
        record.lease_token = record.lease_expires_at = None
        record.indexing_started_at = record.indexing_completed_at = None
        record.error_code = None
        record.attempts = 0
        record.next_attempt_at = datetime.now(UTC)
    return record


def _source_key():
    return func.regexp_replace(
        func.regexp_replace(Transcript.source_path, r"/(\.?/)*", "/", "g"), r"/\.?$", "",
    )


def remove_imports_for_moved_work(
    database: Session, work_id: UUID, target_project_id: UUID,
) -> None:
    """Keep enrolled identities and provenance when a move meets a redundant import.

    The caller holds both project locks and the work/lease locks. Imported rows
    carry no work or lease history and no other records reference their public IDs.
    Their import receipts remain durable; deleting a duplicate also invalidates
    any extraction job that was in flight for it.
    """
    sources = select(_source_key()).where(Transcript.work_item_id == work_id)
    database.execute(delete(Transcript).where(
        Transcript.import_project_id == target_project_id,
        Transcript.source_path.in_(sources),
    ).execution_options(synchronize_session=False))
