"""Vocabulary counts over current matching work before pagination."""

from uuid import UUID

from sqlalchemy import cast, func, literal, or_, select, union_all
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from mnemonic_api.models import WorkItem
from mnemonic_api.search_exploration_schemas import TagCount, TagCountPage, TagCountRequest


def work_tag_counts(database: Session, members: dict[UUID, UUID],
                    request: TagCountRequest) -> TagCountPage:
    if not members:
        return TagCountPage(items=[], total=0, limit=request.limit, offset=request.offset)
    # One JSONB parameter avoids PostgreSQL's parameter ceiling on large projects.
    mapping = func.jsonb_each_text(cast(
        {str(member): str(root) for member, root in members.items()}, JSONB,
    )).table_valued("key", "value")
    # Matching aliases contribute their canonical work's current labels, never
    # frozen historical assignments from before a merge or catalog edit.
    source = WorkItem.__table__.join(mapping, WorkItem.id == cast(mapping.c.value, PGUUID))
    assigned = select(mapping.c.value.label("canonical_id"),
                      func.unnest(WorkItem.tags).label("tag")).select_from(source)
    remediation = select(mapping.c.value, literal("remediation")).select_from(source).where(
        WorkItem.remediation_depth > 0)
    decision_review = select(mapping.c.value, literal("dir")).select_from(source).where(or_(
        WorkItem.decision_review_revision_id.is_not(None),
        WorkItem.decision_review_change_id.is_not(None)))
    tagged = union_all(assigned, remediation, decision_review).subquery()
    tag = tagged.c.tag.collate("C")
    counts = select(tag.label("tag"),
                    func.count(func.distinct(tagged.c.canonical_id)).label("count"))\
        .group_by(tag).subquery()
    total = database.scalar(select(func.count()).select_from(counts)) or 0
    rows = database.execute(select(counts).order_by(counts.c.tag)
        .offset(request.offset).limit(request.limit)).mappings()
    items = [TagCount.model_validate(row) for row in rows]
    next_offset = request.offset + len(items)
    return TagCountPage(items=items, total=total, limit=request.limit, offset=request.offset,
                        next_offset=next_offset if next_offset < total else None)
