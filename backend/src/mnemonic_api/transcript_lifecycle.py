"""Register transcript sources in the same transaction as claims and closeouts."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import conflict
from mnemonic_api.models import WorkItem, WorkLease
from mnemonic_api.transcript_locations import TranscriptLocation, TranscriptSources


def register_claim_transcript(
    database: Session, work: WorkItem, lease: WorkLease, source: TranscriptLocation | None,
) -> None:
    if source is None:
        return
    from mnemonic_api.services.transcripts import register_transcripts

    register_transcripts(database, work, lease.lease_generation_id, lease.holder_client,
                         lease.holder_session_id, [source.model_dump()], "primary")


def require_same_claim_transcript(
    database: Session, lease: WorkLease, source: TranscriptLocation | None,
) -> None:
    """A claim receipt retry must not silently amend its transcript assertion."""
    from mnemonic_api.models import Transcript

    retained = database.scalars(select(Transcript).where(
        Transcript.work_item_id == lease.work_item_id,
        Transcript.lease_generation_id == lease.lease_generation_id,
        Transcript.kind == "primary",
    )).all()
    expected = [] if source is None else [(source.client, source.path)]
    if [(row.client, row.source_path) for row in retained] != expected:
        raise conflict("claim_transcript_conflict",
                       "Retry a claim with its original transcript location or null assertion.")


def register_closeout_transcripts(
    database: Session, work: WorkItem, sources: TranscriptSources | None,
    client: str, session_id: str,
) -> None:
    if not sources:
        return
    from mnemonic_api.services.transcripts import register_transcripts

    lease = database.scalar(select(WorkLease).where(
        WorkLease.work_item_id == work.id,
    ).with_for_update())
    generation = lease.lease_generation_id if lease is not None else uuid4()
    if lease is not None:
        client, session_id = lease.holder_client, lease.holder_session_id
    register_transcripts(database, work, generation, client, session_id,
                         [source.model_dump() for source in sources], "subagent")
