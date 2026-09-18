"""Bounded, durable refresh of old normalizations after a worker upgrade."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api import transcript_normalization as normalization
from mnemonic_api.config import Settings
from mnemonic_api.models import Transcript, TranscriptSettings, WorkItem
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_indexing import _active_generation, _lock_claim_candidate


def _outdated():
    return (select(Transcript, TranscriptSettings.max_file_size_bytes)
        .outerjoin(WorkItem, WorkItem.id == Transcript.work_item_id)
        .outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
        .where(func.coalesce(TranscriptSettings.enabled, True), ~_active_generation(),
               Transcript.status == "ready", Transcript.copy_status == "ready",
               Transcript.reindex_status.is_(None), or_(
                   Transcript.truncated,
                   Transcript.normalizer_version.is_(None),
                   Transcript.normalizer_version < normalization.NORMALIZER_VERSION,
                   Transcript.normalization_schema_version < normalization.SCHEMA_VERSION)))


def refresh_outdated_normalizations(database: Session, settings: Settings) -> int:
    """One project/bounded transaction; no filesystem work or user receipts.

    Commit separately from multi-project scheduling so locks cannot accumulate in
    an order opposed to moves. Recheck pause and active-generation guards after
    locking project -> work -> lease -> transcript. Pending/failed refreshes are
    excluded: the existing job ledger owns retries and their bounded budget.
    """
    with Session(bind=database.get_bind()) as maintenance:
        statement = _outdated()
        project_id = maintenance.scalar(statement.with_only_columns(transcript_project_id())
            .order_by(transcript_project_id(), Transcript.id).limit(1))
        if project_id is None:
            return 0
        with project_mutation(maintenance, project_id):
            candidates = maintenance.execute(statement.with_only_columns(
                Transcript.id, Transcript.work_item_id, transcript_project_id().label("project_id"))
                .where(transcript_project_id() == project_id)
                .order_by(Transcript.id).limit(20)).all()
            count = 0
            for candidate in candidates:
                row = _lock_claim_candidate(maintenance, candidate, settings, statement)
                if row is None:
                    continue
                record, _ = row
                record.generation += 1
                record.attempts = 0
                record.reindex_status, record.reindex_error_code = "pending", None
                record.lease_token = record.lease_expires_at = None
                record.next_attempt_at = maintenance.scalar(select(func.clock_timestamp()))
                count += 1
            maintenance.commit()
            return count
