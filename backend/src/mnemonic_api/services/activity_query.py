"""Indexed change-window reads with bounded resource aggregation and polling positions."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import distinct, func, select, true, tuple_
from sqlalchemy.orm import Session

from mnemonic_api.activity_query_schemas import (
    ActivityChangeRead,
    ActivityFacetCounts,
    ActivityQueryCounts,
    ActivityQueryCoverage,
    QueryActivityPage,
    QueryActivityRequest,
)
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ProjectChange, ProjectChangeHead
from mnemonic_api.services.activity_query_current import current_predicates, current_states
from mnemonic_api.services.activity_query_cursors import ActivityWindow, activity_window
from mnemonic_api.services.project_activity import activity_head

BYTE_LIMIT = 32768
KNOWN_GAPS = [
    "Historical imports cover retained source records, not every past mutation.",
    "Overwritten historical project/settings values cannot be reconstructed.",
    "Prompt history begins at its first observed hash; unobserved edits may be missed.",
    "Prompt timestamps record observation, not the original external file edit time.",
    "Artifact metadata history does not preserve replaced or deleted file bytes.",
    "Derived cache/index updates, lease renewals/heartbeats, and time-driven expiry are omitted.",
    "Current facet membership is reevaluated on each page, not frozen at the upper sequence.",
]


def _matching_changes(
    project_id: UUID, query: QueryActivityRequest, window: ActivityWindow, as_of: datetime,
):
    conditions = [ProjectChange.project_id == project_id,
                  ProjectChange.sequence > window.lower,
                  ProjectChange.sequence <= window.upper,
                  ProjectChange.resource_type.in_(query.facets),
                  *current_predicates(project_id, query.filters, as_of)]
    if query.since is not None:
        conditions.append(ProjectChange.occurred_at >= query.since)
    if query.until is not None:
        conditions.append(ProjectChange.occurred_at < query.until)
    return select(ProjectChange).where(*conditions).cte("matching_changes")


def _counts(database: Session, matching, query: QueryActivityRequest):
    rows = database.execute(select(
        matching.c.resource_type, func.count().label("changes"),
        func.count(distinct(matching.c.resource_id)).label("resources"),
        func.count().filter(matching.c.origin == "history_import").label("imported"),
    ).group_by(matching.c.resource_type))
    counts = {facet: ActivityFacetCounts(changes=0, resources=0) for facet in query.facets}
    imported = 0
    for row in rows:
        counts[row.resource_type] = ActivityFacetCounts(
            changes=row.changes, resources=row.resources,
        )
        imported += row.imported
    return ActivityQueryCounts(
        changes=sum(row.changes for row in counts.values()),
        resources=sum(row.resources for row in counts.values()), by_facet=counts,
    ), imported


def _groups(database: Session, matching, query: QueryActivityRequest, window: ActivityWindow):
    keys = [matching.c.resource_type, matching.c.resource_id]
    if query.view == "events":
        keys.append(matching.c.sequence)
    grouped = select(
        matching.c.resource_type, matching.c.resource_id,
        func.min(matching.c.sequence).label("first_sequence"),
        func.max(matching.c.sequence).label("last_sequence"),
        func.min(matching.c.occurred_at).label("first_occurred_at"),
        func.max(matching.c.occurred_at).label("last_occurred_at"),
        func.count().label("change_count"),
        func.array_agg(distinct(matching.c.operation)).label("operations"),
        func.array_agg(distinct(matching.c.origin)).label("origins"),
    ).group_by(*keys).cte("change_groups")
    return list(database.execute(select(grouped).where(
        grouped.c.last_sequence > window.last,
    ).order_by(grouped.c.last_sequence).limit(query.limit + 1)).mappings())


def _changed_fields(database: Session, matching, groups) -> dict[tuple[str, UUID], list[str]]:
    keys = [(row["resource_type"], row["resource_id"]) for row in groups]
    if not keys:
        return {}
    fields = func.unnest(matching.c.changed_fields).table_valued("field").render_derived()
    rows = database.execute(select(
        matching.c.resource_type, matching.c.resource_id,
        func.array_agg(distinct(fields.c.field)).label("fields"),
    ).select_from(matching.join(fields, true())).where(
        tuple_(matching.c.resource_type, matching.c.resource_id).in_(keys),
    ).group_by(matching.c.resource_type, matching.c.resource_id))
    return {(row.resource_type, row.resource_id): sorted(row.fields) for row in rows}


def _summary(first: ProjectChange, last: ProjectChange, count: int, fields: list[str]) -> str:
    number = "change" if count == 1 else "changes"
    summary = f"{count} recorded {number}; latest operation: {last.operation}."
    before = first.before_state.get("status", first.before_state.get("state"))
    after = last.after_state.get("status", last.after_state.get("state"))
    if before is not None and after is not None and before != after:
        summary += f" State changed from {before} to {after}."
    if fields:
        summary += " Changed fields: " + ", ".join(fields) + "."
    return summary


def _items(
    database: Session, project_id: UUID, matching, groups,
    query: QueryActivityRequest, as_of: datetime,
) -> list[ActivityChangeRead]:
    sequences = {row[key] for row in groups for key in ("first_sequence", "last_sequence")}
    records = {row.sequence: row for row in database.scalars(select(ProjectChange).where(
        ProjectChange.project_id == project_id, ProjectChange.sequence.in_(sequences),
    ))}
    fields = _changed_fields(database, matching, groups) if query.view == "resources" else {}
    current = current_states(database, project_id, [
        (row["resource_type"], row["resource_id"]) for row in groups
    ], as_of)
    return [_item(row, records, fields, current, query.view) for row in groups]


def _item(row, records, fields, current, view: str) -> ActivityChangeRead:
    first = records[row["first_sequence"]]
    last = records[row["last_sequence"]]
    identity = (last.resource_type, last.resource_id)
    changed = fields.get(identity, []) if view == "resources" else last.changed_fields
    return ActivityChangeRead(
        resource_type=last.resource_type, resource_id=last.resource_id,
        title=last.title, revision=last.revision,
        first_sequence=str(first.sequence), last_sequence=str(last.sequence),
        first_occurred_at=row["first_occurred_at"], last_occurred_at=row["last_occurred_at"],
        recorded_at=last.recorded_at, change_count=row["change_count"],
        operations=sorted(row["operations"]), origins=sorted(row["origins"]),
        changed_fields=changed,
        before_state=first.before_state, after_state=last.after_state,
        summary=_summary(first, last, row["change_count"], changed), current=current.get(identity),
    )


def _bounded_page(
    values: dict[str, Any], query: QueryActivityRequest, window: ActivityWindow,
) -> QueryActivityPage:
    page = QueryActivityPage(**values, next_cursor=None, checkpoint=None)
    while True:
        position = int(page.items[-1].last_sequence) if page.items else window.last
        page.next_cursor = window.page_cursor(query, position) if page.has_more else None
        page.checkpoint = (None if page.has_more or query.until is not None
                           else window.checkpoint(query))
        if len(page.model_dump_json().encode()) <= BYTE_LIMIT:
            return page
        if len(page.items) <= 1:
            raise ApplicationError(503, "activity_query_too_large",
                                   "One activity result exceeds the response byte limit.")
        page.items.pop()
        page.has_more = True
        page.page_limited_by_bytes = True


def query_activity(
    database: Session, project_id: UUID, query: QueryActivityRequest,
) -> QueryActivityPage:
    stream = activity_head(database, project_id)
    head = database.get(ProjectChangeHead, project_id)
    if head is None:
        raise ApplicationError(503, "activity_query_unavailable",
                               "Project changes are unavailable.")
    window = activity_window(query, project_id, stream.stream_id, head.last_sequence)
    as_of = database.scalar(select(func.clock_timestamp()))
    assert as_of is not None
    matching = _matching_changes(project_id, query, window, as_of)
    counts, imported = _counts(database, matching, query)
    groups = _groups(database, matching, query, window)
    items = _items(database, project_id, matching, groups[:query.limit], query, as_of)
    return _bounded_page({
        "project_id": project_id, "stream_id": stream.stream_id, "view": query.view,
        "items": items, "through_sequence": str(window.upper),
        "has_more": len(groups) > query.limit, "counts": counts,
        "coverage": ActivityQueryCoverage(capture_started_at=head.capture_started_at,
                                           imported_changes=imported, known_gaps=KNOWN_GAPS),
    }, query, window)
