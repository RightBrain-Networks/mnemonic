"""Snapshot reads of the database-produced project activity journal."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ProjectActivity, ProjectActivityHead, WorkEvent
from mnemonic_api.phase12_schemas import ProjectActivityPage, ProjectActivityRead
from mnemonic_api.services.activity_cursors import cursor_base, decode_cursor, encode_cursor
from mnemonic_api.services.work_items import require_project


def activity_head(database: Session, project_id: UUID) -> ProjectActivityHead:
    require_project(database, project_id)
    head = database.get(ProjectActivityHead, project_id)
    if head is None:
        raise ApplicationError(
            503, "project_activity_unavailable", "Project activity is unavailable."
        )
    return head


def activity_page(
    database: Session,
    project_id: UUID,
    *,
    after: str | None,
    start: str | None,
    limit: int,
) -> ProjectActivityPage:
    head = activity_head(database, project_id)
    position = head.last_sequence if start == "now" else 0
    if after is not None:
        position = int(decode_cursor(after, head, "activity", {})["after"])
    rows = database.execute(
        select(ProjectActivity, WorkEvent.event_type)
        .outerjoin(WorkEvent, WorkEvent.id == ProjectActivity.work_event_id)
        .where(
            ProjectActivity.project_id == project_id,
            ProjectActivity.sequence > position,
            ProjectActivity.sequence <= head.last_sequence,
        )
        .order_by(ProjectActivity.sequence)
        .limit(limit + 1)
    ).all()
    more = len(rows) > limit
    items = []
    for entry, event_type in rows[:limit]:
        data = {
            field: getattr(entry, field)
            for field in ProjectActivityRead.model_fields
            if field != "event_type"
        }
        for field in ("sequence", "work_event_id", "settings_revision"):
            data[field] = str(data[field]) if data[field] is not None else None
        items.append(ProjectActivityRead(**data, event_type=event_type))
    last = int(items[-1].sequence) if items else position
    result = ProjectActivityPage(
        project_id=project_id,
        stream_id=head.stream_id,
        items=items,
        has_more=more,
        next_cursor=encode_cursor({**cursor_base(head, "activity"), "after": str(last)}),
        through_sequence=str(head.last_sequence),
        historical_through_sequence=str(head.historical_through_sequence),
        historical_coverage="recorded_work_events_only",
    )
    if len(result.model_dump_json().encode()) > 512 * 1024:
        raise ApplicationError(
            503, "project_activity_unavailable", "Project activity is unavailable."
        )
    return result
