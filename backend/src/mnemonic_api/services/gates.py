"""Durable human-gate mutations and bounded attention/history reads."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.errors import (
    ApplicationError,
    conflict,
    gate_already_resolved,
    gate_context_changed,
    gate_not_found,
    gate_secret_echo,
)
from mnemonic_api.models import (
    Checkpoint,
    WorkEvent,
    WorkGate,
    WorkItem,
)
from mnemonic_api.schemas import (
    HumanAttentionItem,
    HumanAttentionListQuery,
    HumanAttentionPage,
    HumanGateContextRevision,
    HumanGateListQuery,
    HumanGatePage,
    HumanGateRead,
    HumanGateRequestCreate,
    HumanGateResolutionCreate,
)
from mnemonic_api.services.hierarchy import ancestor_paths
from mnemonic_api.services.work_events import (
    database_now,
    stage_human_attention_requested,
    stage_human_attention_resolved,
)

_RELATIONSHIP_EVENT_TYPES = (
    "dependency_added",
    "dependency_removed",
    "relationship_added",
    "relationship_removed",
)
_CURSOR_VERSION = 1
_CURSOR_MAX_BYTES = 2048
_CURSOR_MAX_SEQUENCE = 2**63 - 1


def _invalid_cursor() -> ApplicationError:
    return ApplicationError(
        422,
        "invalid_cursor",
        "The continuation cursor is invalid for this scope or filter.",
    )


def _encode_cursor(
    *,
    endpoint: Literal["attention", "history"],
    project_id: UUID,
    work_item_id: UUID | None,
    status: str | None,
    direction: Literal["asc", "desc"],
    last_sequence: int,
) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "endpoint": endpoint,
        "project_id": str(project_id),
        "work_item_id": str(work_item_id) if work_item_id is not None else None,
        "status": status,
        "direction": direction,
        "last_sequence": last_sequence,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode("ascii")


def _decode_cursor(
    cursor: str | None,
    *,
    endpoint: Literal["attention", "history"],
    project_id: UUID,
    work_item_id: UUID | None,
    status: str | None,
    direction: Literal["asc", "desc"],
) -> int | None:
    if cursor is None:
        return None
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        if len(raw) > _CURSOR_MAX_BYTES:
            raise ValueError
        payload = json.loads(raw.decode("ascii"))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise _invalid_cursor() from None
    expected_keys = {
        "v",
        "endpoint",
        "project_id",
        "work_item_id",
        "status",
        "direction",
        "last_sequence",
    }
    expected_work_id = str(work_item_id) if work_item_id is not None else None
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or type(payload["v"]) is not int
        or payload["v"] != _CURSOR_VERSION
        or payload["endpoint"] != endpoint
        or payload["project_id"] != str(project_id)
        or payload["work_item_id"] != expected_work_id
        or payload["status"] != status
        or payload["direction"] != direction
        or isinstance(payload["last_sequence"], bool)
        or not isinstance(payload["last_sequence"], int)
        or payload["last_sequence"] < 1
        or payload["last_sequence"] > _CURSOR_MAX_SEQUENCE
    ):
        raise _invalid_cursor()
    return payload["last_sequence"]


def _durable_gate_text_values(
    payload: HumanGateRequestCreate | HumanGateResolutionCreate,
) -> tuple[str, ...]:
    dumped = payload.model_dump(
        mode="json",
        exclude={"client_operation_id"},
    )

    def text_values(value: object) -> Iterable[str]:
        if isinstance(value, dict):
            for item in value.values():
                yield from text_values(item)
        elif isinstance(value, list):
            for item in value:
                yield from text_values(item)
        elif isinstance(value, str):
            yield value

    return tuple(text_values(dumped))


def reject_gate_secret_echo(
    payload: HumanGateRequestCreate | HumanGateResolutionCreate,
    *,
    known_secret_values: Iterable[str],
) -> None:
    """Reject stable request-known controls before receipt reservation or replay."""
    controls = {value for value in known_secret_values if value}
    operation_id = payload.client_operation_id
    if operation_id is not None:
        controls.add(str(operation_id))
    if any(
        control in value
        for value in _durable_gate_text_values(payload)
        for control in controls
    ):
        raise gate_secret_echo()


def _current_context_revision(
    database: Session,
    work_item: WorkItem,
) -> HumanGateContextRevision:
    return _current_context_revisions(database, [work_item.id])[work_item.id]


def _current_context_revisions(
    database: Session,
    work_item_ids: Sequence[UUID],
) -> dict[UUID, HumanGateContextRevision]:
    if not work_item_ids:
        return {}
    current_checkpoint = (
        select(Checkpoint.id)
        .where(
            Checkpoint.work_item_id == WorkItem.id,
            Checkpoint.kind == "context",
        )
        .order_by(Checkpoint.created_at.desc(), Checkpoint.id.desc())
        .limit(1)
        .correlate(WorkItem)
        .scalar_subquery()
    )
    relationship_count = (
        select(func.count())
        .select_from(WorkEvent)
        .where(
            WorkEvent.work_item_id == WorkItem.id,
            WorkEvent.event_type.in_(_RELATIONSHIP_EVENT_TYPES),
        )
        .correlate(WorkItem)
        .scalar_subquery()
    )
    rows = database.execute(
        select(
            WorkItem.id,
            WorkItem.version,
            current_checkpoint.label("context_checkpoint_id"),
            relationship_count.label("relationship_event_count"),
        ).where(WorkItem.id.in_(set(work_item_ids)))
    )
    revisions: dict[UUID, HumanGateContextRevision] = {}
    for row in rows:
        if row.context_checkpoint_id is None:
            raise ApplicationError(
                503,
                "gate_revision_unavailable",
                "The current work revision is unavailable.",
            )
        revisions[row.id] = HumanGateContextRevision(
            work_version=row.version,
            context_checkpoint_id=row.context_checkpoint_id,
            relationship_event_count=int(row.relationship_event_count),
        )
    return revisions


def human_gate_read(
    gate: WorkGate | Mapping[str, Any],
    current_revision: HumanGateContextRevision,
) -> HumanGateRead:
    def value(name: str) -> Any:
        if isinstance(gate, Mapping):
            return gate[name]
        return getattr(gate, name)

    requested_revision = HumanGateContextRevision(
        work_version=value("requested_work_version"),
        context_checkpoint_id=value("requested_context_checkpoint_id"),
        relationship_event_count=value("requested_relationship_event_count"),
    )
    resolved_at = value("resolved_at")
    resolved_revision = (
        HumanGateContextRevision(
            work_version=value("resolved_work_version"),
            context_checkpoint_id=value("resolved_context_checkpoint_id"),
            relationship_event_count=value("resolved_relationship_event_count"),
        )
        if resolved_at is not None
        else None
    )
    return HumanGateRead(
        id=value("id"),
        project_id=value("project_id"),
        work_item_id=value("work_item_id"),
        gate_type=value("gate_type"),
        question=value("question"),
        requested_by_client=value("requested_by_client"),
        requested_by_session_id=value("requested_by_session_id"),
        requested_by_model=value("requested_by_model"),
        requested_context_revision=requested_revision,
        created_at=value("created_at"),
        status="resolved" if resolved_at is not None else "unresolved",
        current_context_revision=current_revision,
        resolved_at=resolved_at,
        resolution=value("resolution"),
        resolved_by_client=value("resolved_by_client"),
        resolved_by_session_id=value("resolved_by_session_id"),
        resolved_by_model=value("resolved_by_model"),
        resolved_context_revision=resolved_revision,
    )


def request_human_gate(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    payload: HumanGateRequestCreate,
) -> HumanGateRead:
    from mnemonic_api.services.work_items import require_work_item

    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    if work_item.status != "pending":
        raise conflict("work_not_pending", "Only pending work can request human input.")
    revision = _current_context_revision(database, work_item)
    created_at = database_now(database)
    gate = WorkGate(
        id=uuid4(),
        project_id=project_id,
        work_item_id=work_item.id,
        gate_type=payload.gate_type,
        question=payload.question,
        requested_by_client=payload.requested_by_client,
        requested_by_session_id=payload.requested_by_session_id,
        requested_by_model=payload.requested_by_model,
        requested_work_version=revision.work_version,
        requested_context_checkpoint_id=revision.context_checkpoint_id,
        requested_relationship_event_count=revision.relationship_event_count,
        created_at=created_at,
    )
    database.add(gate)
    database.flush()
    database.execute(
        update(WorkItem)
        .where(WorkItem.id == work_item.id)
        .values(updated_at=func.greatest(WorkItem.updated_at, created_at))
        .execution_options(synchronize_session=False)
    )
    stage_human_attention_requested(database, gate)
    database.flush()
    return human_gate_read(gate, revision)


def _locked_gate(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
) -> WorkGate:
    gate = database.scalar(
        select(WorkGate)
        .where(
            WorkGate.id == gate_id,
            WorkGate.project_id == project_id,
            WorkGate.work_item_id == work_item_id,
        )
        .with_for_update()
    )
    if gate is None:
        raise gate_not_found()
    return gate


def resolve_human_gate(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    gate_id: UUID,
    payload: HumanGateResolutionCreate,
) -> HumanGateRead:
    from mnemonic_api.services.work_items import require_work_item

    work_item = require_work_item(database, project_id, work_item_id, lock=True)
    from mnemonic_api.services.duplicates import require_canonical_work_item

    require_canonical_work_item(database, work_item)
    gate = _locked_gate(database, project_id, work_item_id, gate_id)
    if gate.resolved_at is not None:
        raise gate_already_resolved()
    revision = _current_context_revision(database, work_item)
    if payload.reviewed_context_revision != revision:
        raise gate_context_changed()

    resolved_at = database_now(database)
    gate.resolved_at = resolved_at
    gate.resolution = payload.resolution
    gate.resolved_by_client = payload.resolved_by_client
    gate.resolved_by_session_id = payload.resolved_by_session_id
    gate.resolved_by_model = payload.resolved_by_model
    gate.resolved_work_version = revision.work_version
    gate.resolved_context_checkpoint_id = revision.context_checkpoint_id
    gate.resolved_relationship_event_count = revision.relationship_event_count
    database.flush()
    database.execute(
        update(WorkItem)
        .where(WorkItem.id == work_item.id)
        .values(updated_at=func.greatest(WorkItem.updated_at, resolved_at))
        .execution_options(synchronize_session=False)
    )
    stage_human_attention_resolved(database, gate)
    database.flush()
    return human_gate_read(gate, revision)


def list_human_attention(
    database: Session,
    project_id: UUID,
    filters: HumanAttentionListQuery,
) -> HumanAttentionPage:
    from mnemonic_api.services.work_context import work_summaries
    from mnemonic_api.services.work_items import require_project, require_work_item

    begin_coherent_read(database)
    as_of = database.scalar(select(func.transaction_timestamp()))
    if as_of is None:
        raise RuntimeError("Database did not provide a transaction timestamp")
    if filters.work_item_id is None:
        require_project(database, project_id)
    else:
        require_work_item(database, project_id, filters.work_item_id)
    last_sequence = _decode_cursor(
        filters.cursor,
        endpoint="attention",
        project_id=project_id,
        work_item_id=filters.work_item_id,
        status="unresolved",
        direction="asc",
    )
    conditions = [
        WorkGate.project_id == project_id,
        WorkGate.resolved_at.is_(None),
    ]
    if filters.work_item_id is not None:
        conditions.append(WorkGate.work_item_id == filters.work_item_id)
    total = int(
        database.scalar(
            select(func.count()).select_from(WorkGate).where(*conditions)
        )
        or 0
    )
    if filters.limit == 0:
        return HumanAttentionPage(items=[], total=total, limit=0, next_cursor=None)
    page_conditions = list(conditions)
    if last_sequence is not None:
        page_conditions.append(WorkGate.attention_sequence > last_sequence)
    gates = list(
        database.scalars(
            select(WorkGate)
            .join(
                WorkItem,
                and_(
                    WorkItem.id == WorkGate.work_item_id,
                    WorkItem.project_id == WorkGate.project_id,
                    WorkItem.deleted_at.is_(None),
                ),
            )
            .where(*page_conditions)
            .order_by(WorkGate.attention_sequence)
            .limit(filters.limit + 1)
        )
    )
    has_more = len(gates) > filters.limit
    gates = gates[: filters.limit]
    work_ids = list(dict.fromkeys(gate.work_item_id for gate in gates))
    work_items = list(
        database.scalars(
            select(WorkItem).where(
                WorkItem.project_id == project_id,
                WorkItem.id.in_(work_ids),
                WorkItem.deleted_at.is_(None),
            )
        )
    )
    summaries = {
        item.work_item.id: item for item in work_summaries(database, work_items, as_of=as_of)
    }
    paths, truncated = ancestor_paths(database, project_id, work_ids)
    for work_id, summary in summaries.items():
        summary.ancestor_path = paths.get(work_id, [])
        summary.ancestor_path_truncated = work_id in truncated
    revisions = _current_context_revisions(database, work_ids)
    items = [
        HumanAttentionItem(
            gate=human_gate_read(gate, revisions[gate.work_item_id]),
            summary=summaries[gate.work_item_id],
        )
        for gate in gates
    ]
    next_cursor = (
        _encode_cursor(
            endpoint="attention",
            project_id=project_id,
            work_item_id=filters.work_item_id,
            status="unresolved",
            direction="asc",
            last_sequence=gates[-1].attention_sequence,
        )
        if has_more and gates
        else None
    )
    return HumanAttentionPage(
        items=items,
        total=total,
        limit=filters.limit,
        next_cursor=next_cursor,
    )


def list_work_gates(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    filters: HumanGateListQuery,
) -> HumanGatePage:
    from mnemonic_api.services.work_items import missing_work_item, require_project

    begin_coherent_read(database)
    require_project(database, project_id)
    work_item = database.scalar(
        select(WorkItem).where(
            WorkItem.project_id == project_id,
            WorkItem.id == work_item_id,
        )
    )
    if work_item is None:
        raise missing_work_item(database, project_id)
    last_sequence = _decode_cursor(
        filters.cursor,
        endpoint="history",
        project_id=project_id,
        work_item_id=work_item_id,
        status=filters.status,
        direction="desc",
    )
    conditions: list[Any] = [WorkGate.work_item_id == work_item_id]
    if filters.status == "unresolved":
        conditions.append(WorkGate.resolved_at.is_(None))
    elif filters.status == "resolved":
        conditions.append(WorkGate.resolved_at.is_not(None))
    total = int(
        database.scalar(
            select(func.count()).select_from(WorkGate).where(*conditions)
        )
        or 0
    )
    page_conditions = list(conditions)
    if last_sequence is not None:
        page_conditions.append(WorkGate.attention_sequence < last_sequence)
    gates = list(
        database.scalars(
            select(WorkGate)
            .where(*page_conditions)
            .order_by(WorkGate.attention_sequence.desc())
            .limit(filters.limit + 1)
        )
    )
    has_more = len(gates) > filters.limit
    gates = gates[: filters.limit]
    revision = _current_context_revision(database, work_item)
    next_cursor = (
        _encode_cursor(
            endpoint="history",
            project_id=project_id,
            work_item_id=work_item_id,
            status=filters.status,
            direction="desc",
            last_sequence=gates[-1].attention_sequence,
        )
        if has_more and gates
        else None
    )
    return HumanGatePage(
        items=[human_gate_read(gate, revision) for gate in gates],
        total=total,
        limit=filters.limit,
        next_cursor=next_cursor,
    )
