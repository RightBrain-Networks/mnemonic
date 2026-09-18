"""Large immutable stages are independent of bounded interactive project mutations."""

from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.models import Transcript
from mnemonic_api.transcript_normalization import NormalizedConversation
from mnemonic_api.transcript_normalized_storage import persist_normalization
from mnemonic_jobs.ledger import JobContext

if TYPE_CHECKING:
    from mnemonic_api.transcript_indexing import TranscriptJob

INDEX_LEASE_SECONDS = 900


def index_claim_filter(job: TranscriptJob):
    return (
        Transcript.id == job.transcript_id, Transcript.generation == job.generation,
        Transcript.snapshot_id == job.snapshot_id,
        Transcript.copy_sha256 == job.copy.sha256,
        (Transcript.status == "processing") | (Transcript.reindex_status == "processing"),
        Transcript.lease_token == job.lease_token,
    )


def stage_normalization(factory: sessionmaker[Session], job: TranscriptJob,
                        normalized: NormalizedConversation,
                        context: JobContext | None) -> bool:
    """Readers keep their active revision while a complete new revision is committed.

    The job heartbeat remains free to renew during bulk insertion. Ownership is
    locked only immediately before this transaction commits, and checked again
    in the separate project transaction that activates the revision. A crash
    before staging commit leaves no partial manifest; a later crash permits reuse.
    """
    with factory() as database:
        database.execute(text("SET LOCAL transaction_timeout = '900s'"))
        database.execute(text("SET LOCAL statement_timeout = '900s'"))
        database.execute(text("SET LOCAL lock_timeout = '2s'"))
        current = select(Transcript.id).where(*index_claim_filter(job))
        if database.scalar(current) is None:
            return False
        persist_normalization(database, job.transcript_id, normalized)
        if context is not None:
            context.assert_owned(database)
        if database.scalar(current) is None:
            return False
        database.commit()
        return True
