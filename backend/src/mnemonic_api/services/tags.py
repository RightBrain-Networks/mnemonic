"""Current work labels and the human-managed project vocabulary."""

import secrets
from collections.abc import Mapping
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError, conflict, not_found
from mnemonic_api.models import ProjectTag, WorkDuplicateMerge, WorkItem
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.work_events import database_now, stage_work_changed
from mnemonic_api.tag_schemas import TagCatalog, TagCreate, TagDelete, TagOrder, TagPatch

# Pastels provide high contrast with the dashboard's dark tag text.
PALETTE = ("#b8d8f0", "#c3dfca", "#dec9ed", "#edd1bc", "#eadfa9", "#b8dedb")


def catalog(database: Session, project_id: UUID) -> TagCatalog:
    rows = database.scalars(select(ProjectTag).where(ProjectTag.project_id == project_id)
                            .order_by(ProjectTag.menu_order, ProjectTag.id)).all()
    return TagCatalog(project_id=project_id, items=rows, total=len(rows))


def require_assignments(database: Session, project_id: UUID, tags: list[str] | None) -> None:
    if tags is None:
        return
    found = set(database.scalars(select(ProjectTag.name).where(
        ProjectTag.project_id == project_id, ProjectTag.name.in_(tags))))
    if set(tags) != found:
        raise ApplicationError(422, "unknown_tags", "Choose tags from this project's tag catalog.")


def tag_condition(tag: str):
    intrinsic = {
        "remediation": WorkItem.remediation_depth > 0,
        "dir": or_(WorkItem.decision_review_revision_id.is_not(None),
                   WorkItem.decision_review_change_id.is_not(None)),
    }
    assigned = WorkItem.tags.contains([tag])
    current = WorkItem.id.not_in(select(WorkDuplicateMerge.source_work_item_id))
    return and_(current, or_(assigned, intrinsic[tag]) if tag in intrinsic else assigned)


def tag_conditions(tags: list[str], mode: str):
    clauses = [tag_condition(tag) for tag in tags]
    return and_(*clauses) if mode == "and" else or_(*clauses)


def _tag(database: Session, project_id: UUID, tag_id: UUID) -> ProjectTag:
    row = database.scalar(select(ProjectTag).where(ProjectTag.project_id == project_id,
                                                 ProjectTag.id == tag_id).with_for_update())
    if row is None:
        raise not_found("tag_not_found", "Tag not found in this project.")
    if row.system:
        raise conflict("system_tag_immutable", "System tags cannot be changed or deleted.")
    return row


def _unique(database: Session, project_id: UUID, name: str, tag_id: UUID | None = None) -> None:
    query = select(ProjectTag.id).where(
        ProjectTag.project_id == project_id, ProjectTag.name == name)
    if tag_id is not None:
        query = query.where(ProjectTag.id != tag_id)
    if database.scalar(query) is not None:
        raise conflict("tag_name_conflict", "A tag with that normalized name already exists.")


def create_tag(database: Session, project_id: UUID, payload: TagCreate) -> TagCatalog:
    _unique(database, project_id, payload.name)
    last = database.scalar(select(func.max(ProjectTag.menu_order)).where(
        ProjectTag.project_id == project_id))
    database.add(ProjectTag(id=uuid4(), project_id=project_id, name=payload.name,
        description=payload.description, color=(payload.color or secrets.choice(PALETTE)).lower(),
        menu_order=(last + 1) if last is not None else 0, system=False))
    database.flush()
    return catalog(database, project_id)


def update_tag(database: Session, project_id: UUID, tag_id: UUID,
               payload: TagPatch) -> TagCatalog:
    row = _tag(database, project_id, tag_id)
    if payload.name is not None and row.name != payload.name:
        _unique(database, project_id, payload.name, row.id)
        _change_assignments(database, project_id, row.name, payload.name)
    for key, value in payload.model_dump(exclude_unset=True,
                                       exclude={"client_operation_id"}).items():
        setattr(row, key, value.lower() if key == "color" else value)
    database.flush()
    return catalog(database, project_id)


def delete_tag(database: Session, project_id: UUID, tag_id: UUID) -> TagCatalog:
    row = _tag(database, project_id, tag_id)
    _change_assignments(database, project_id, row.name, None)
    database.delete(row)
    database.flush()
    return catalog(database, project_id)



def _change_assignments(database: Session, project_id: UUID, name: str,
                        replacement: str | None) -> None:
    items = database.scalars(select(WorkItem).where(WorkItem.project_id == project_id,
        WorkItem.deleted_at.is_(None), WorkItem.tags.contains([name]),
        WorkItem.id.not_in(select(WorkDuplicateMerge.source_work_item_id)))
        .order_by(WorkItem.id).with_for_update()).all()
    now = database_now(database)
    for item in items:
        before = {"status": item.status, "tags": item.tags}
        values = [replacement if tag == name else tag for tag in item.tags or []]
        item.tags = [tag for tag in values if tag is not None] or None
        item.version += 1
        item.updated_at = now
        stage_work_changed(database, item, before=before, requested_fields=["tags"],
                           actor=None, created_at=now)
    database.flush()

def reorder_tags(database: Session, project_id: UUID, payload: TagOrder) -> TagCatalog:
    rows = database.scalars(select(ProjectTag).where(ProjectTag.project_id == project_id)
                            .with_for_update()).all()
    if {row.id for row in rows} != set(payload.tag_ids):
        raise conflict("tag_order_conflict", "Refresh the tag catalog and order every current tag.")
    positions = {tag_id: index for index, tag_id in enumerate(payload.tag_ids)}
    for row in rows:
        row.menu_order = positions[row.id]
    database.flush()
    return catalog(database, project_id)


def tag_response_matches(project_id: UUID, target: Mapping[str, str], payload: APIModel,
                         typed: APIModel) -> bool:
    result = cast(TagCatalog, typed)
    if result.project_id != project_id or any(row.project_id != project_id for row in result.items):
        return False
    if result.total != len(result.items):
        return False
    rows = {str(row.id): row for row in result.items}
    if isinstance(payload, TagDelete):
        return target.get("tag_id") not in rows
    if isinstance(payload, TagOrder):
        return [row.id for row in result.items] == payload.tag_ids
    if isinstance(payload, TagCreate):
        row = next((row for row in result.items if row.name == payload.name), None)
        return (row is not None and row.description == payload.description and not row.system
                and (payload.color is None or row.color == payload.color))
    if isinstance(payload, TagPatch):
        row = rows.get(target.get("tag_id", ""))
        return row is not None and all(getattr(row, key) == value for key, value in
            payload.model_dump(exclude_unset=True, exclude={"client_operation_id"}).items())
    return False
