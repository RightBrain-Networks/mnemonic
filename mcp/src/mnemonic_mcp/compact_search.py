"""Checks supported by compact search pointers; filter envelopes cover omitted metadata."""

from uuid import UUID

from .models import CompactHierarchyHit, CompactWorkHit, DuplicateScope, SearchStatus


def compact_work_matches(
    item: CompactWorkHit, project_id: UUID, *, status: SearchStatus,
    duplicate_scope: DuplicateScope, canonical_work_item_id: UUID | None, blank_query: bool,
) -> bool:
    duplicate = item.id != item.canonical_work_item_id
    check_status = not isinstance(item, CompactHierarchyHit) or item.self_matches_filter
    return (
        item.project_id == project_id
        and (not check_status or status == "all" or item.search_status == status)
        and (duplicate_scope != "canonical" or not duplicate)
        and (duplicate_scope != "aliases" or duplicate)
        and (canonical_work_item_id is None
             or item.canonical_work_item_id == canonical_work_item_id)
        and (not (blank_query or duplicate_scope != "canonical") or item.matched_member is None)
    )
