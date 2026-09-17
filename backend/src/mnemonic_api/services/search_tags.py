"""Paginated vocabulary counts use the matching work population before pagination."""

from uuid import UUID

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from mnemonic_api.models import Checkpoint
from mnemonic_api.search_exploration_schemas import TagCount, TagCountPage, TagCountRequest


def work_tag_counts(database: Session, members: dict[UUID, UUID],
                    request: TagCountRequest) -> TagCountPage:
    if not members:
        return TagCountPage(items=[], total=0, limit=request.limit, offset=request.offset)
    # One JSONB parameter avoids PostgreSQL's parameter ceiling for large projects.
    mapping = func.jsonb_each_text(cast(
        {str(member): str(root) for member, root in members.items()}, JSONB,
    )).table_valued("key", "value")
    tagged = select(mapping.c.value.label("canonical_id"),
                    func.unnest(Checkpoint.tags).label("tag")).select_from(
        Checkpoint.__table__.join(mapping, Checkpoint.work_item_id == cast(mapping.c.key, PGUUID))
    ).subquery()
    normalized = func.lower(tagged.c.tag).collate("C")
    counts = select(normalized.label("tag"),
                    func.count(func.distinct(tagged.c.canonical_id)).label("count"))\
        .group_by(normalized).subquery()
    total = database.scalar(select(func.count()).select_from(counts)) or 0
    rows = database.execute(select(counts).order_by(counts.c.tag)
        .offset(request.offset).limit(request.limit)).mappings()
    items = [TagCount.model_validate(row) for row in rows]
    next_offset = request.offset + len(items)
    return TagCountPage(items=items, total=total, limit=request.limit, offset=request.offset,
                        next_offset=next_offset if next_offset < total else None)
