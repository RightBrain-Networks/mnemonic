"""Page-only work pointers share readiness facts without loading checkpoint bodies."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.models import WorkItem
from mnemonic_api.schemas import CompactWorkHit, WorkIdentityPointer, WorkMatchEvidence
from mnemonic_api.services.hierarchy import ancestor_paths
from mnemonic_api.services.readiness import (
    readiness,
    readiness_inputs,
    review_statuses,
    work_search_status,
)


def compact_work_hits(
    database: Session, project_id: UUID, work_items: Sequence[WorkItem], *,
    ranks: dict[UUID, int], matched_members: dict[UUID, WorkIdentityPointer], as_of: datetime,
    evidence: dict[UUID, WorkMatchEvidence],
) -> list[CompactWorkHit]:
    if not work_items:
        return []
    ids = [item.id for item in work_items]
    blockers, gates, leases, dropped, canonical = readiness_inputs(database, ids, as_of=as_of)
    reviews = review_statuses(database, ids)
    paths, truncated = ancestor_paths(database, project_id, ids)
    return [CompactWorkHit(
        id=item.id, project_id=project_id, title=item.title, status=item.status,
        **evidence[matched_members[item.id].id].model_dump(),
        priority=item.priority, updated_at=item.updated_at, rank=ranks[item.id],
        canonical_work_item_id=canonical.get(item.id, item.id),
        search_status=work_search_status(
            item.status, item.id in leases, item.id in dropped, reviews.get(item.id),
        ),
        display_state=readiness(
            item, leases.get(item.id), blockers.get(item.id, 0), item.id in dropped,
            gates.get(item.id, 0), canonical_work_item_id=canonical.get(item.id, item.id),
            review_status=reviews.get(item.id),
        ).display_state,
        ancestor_path=paths.get(item.id, []), ancestor_path_truncated=item.id in truncated,
        matched_member=(matched_members[item.id]
                        if matched_members[item.id].id != item.id else None),
    ) for item in work_items]
