"""Resolve only explicitly journal-approved recovery sources and byte pins."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.models import Transcript, TranscriptRecovery
from mnemonic_api.transcript_copies import TranscriptCopyPin


def effective_copy_path():
    approved = select(TranscriptRecovery.replacement_path).where(
        TranscriptRecovery.operation_id == Transcript.recovery_operation_id,
        TranscriptRecovery.transcript_id == Transcript.id,
        TranscriptRecovery.original_source_path == Transcript.source_path,
    ).correlate(Transcript).scalar_subquery()
    return func.coalesce(approved, Transcript.source_path)


def approved_copy_source(database: Session,
                          record: Transcript) -> tuple[str, TranscriptCopyPin | None]:
    if record.recovery_operation_id is None:
        return record.source_path, None
    receipt = database.get(TranscriptRecovery, record.recovery_operation_id)
    latest = database.scalar(select(func.max(TranscriptRecovery.resulting_generation)).where(
        TranscriptRecovery.transcript_id == record.id))
    if receipt is None or (
        receipt.transcript_id != record.id or receipt.original_source_path != record.source_path
        or receipt.resulting_generation > record.generation
        or receipt.resulting_generation != latest
    ):
        raise ExtractionError("transcript_recovery_invalid")
    return receipt.replacement_path, TranscriptCopyPin(receipt.expected_sha256,
                                                      receipt.expected_size_bytes)
