"""Metadata-only reciprocal links follow the owning work's current project."""

from pathlib import PurePosixPath
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from mnemonic_api.models import Transcript
from mnemonic_api.services.transcripts import transcript_query
from mnemonic_api.transcript_work_schemas import WorkTranscriptLink, WorkTranscriptLinks


def work_transcripts(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
) -> WorkTranscriptLinks:
    statement = transcript_query(project_id).where(Transcript.work_item_id == work_item_id)
    total = database.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = database.scalars(
        statement.options(defer(Transcript.normalized_text)).order_by(
            func.coalesce(Transcript.last_updated_at, Transcript.created_at).desc(), Transcript.id
        ).limit(20)
    ).all()
    return WorkTranscriptLinks(
        total=total,
        omitted_count=total - len(rows),
        items=[
            WorkTranscriptLink(
                id=row.id,
                project_id=project_id,
                work_item_id=work_item_id,
                filename=PurePosixPath(row.source_path).name,
                client=row.client,
                kind=row.kind,
                status=row.status,
                last_updated_at=row.last_updated_at,
                session_ids=row.extracted_metadata.get("transcript:session_id", []),
                models=row.extracted_metadata.get("transcript:model", []),
            )
            for row in rows
        ],
    )
