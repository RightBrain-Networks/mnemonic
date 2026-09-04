"""Atomic completion-evidence assembly and bounded event-backed history reads."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import exists, func, literal_column, select
from sqlalchemy.orm import Session

from mnemonic_api.database import begin_coherent_read
from mnemonic_api.errors import ApplicationError, completion_evidence_unavailable
from mnemonic_api.models import (
    ArtifactReference,
    Checkpoint,
    ClientOperation,
    VerificationResult,
    WorkEvent,
    WorkItem,
)
from mnemonic_api.schemas import (
    COMPLETION_EVENT_ID_MAX,
    ArtifactReferenceRead,
    CommandVerificationRead,
    CompletionCheckpointPointer,
    CompletionEvidenceEpisodeRead,
    CompletionEvidenceInput,
    CompletionEvidenceListQuery,
    CompletionEvidencePage,
    CompletionEvidencePayloadRead,
    ObservationVerificationRead,
    VerificationResultRead,
    WorkCompletionRead,
)
from mnemonic_api.services.duplicates import canonical_projection
from mnemonic_api.services.work_items import require_work_item

_CURSOR_ENDPOINT = "completion_evidence"
_CURSOR_DIRECTION = "desc"
_CURSOR_VERSION = 1
_CURSOR_MAX_DECODED_BYTES = 2048
_CURSOR_KEYS = frozenset(
    {
        "as_of_completion_event_id",
        "direction",
        "endpoint",
        "last_completion_event_id",
        "project_id",
        "v",
        "work_item_id",
    }
)


def _invalid_cursor() -> ApplicationError:
    return ApplicationError(
        422,
        "invalid_cursor",
        "The continuation cursor is invalid for this project and work item.",
    )


def _event_id(value: object) -> int:
    if (
        not isinstance(value, str)
        or not value
        or value[0] == "0"
        or not value.isascii()
        or not value.isdecimal()
        or len(value) > 19
    ):
        raise _invalid_cursor()
    parsed = int(value)
    if parsed < 1 or parsed > COMPLETION_EVENT_ID_MAX or str(parsed) != value:
        raise _invalid_cursor()
    return parsed


def _encode_cursor(
    *,
    project_id: UUID,
    work_item_id: UUID,
    as_of_event_id: int,
    last_event_id: int,
) -> str:
    payload = {
        "as_of_completion_event_id": str(as_of_event_id),
        "direction": _CURSOR_DIRECTION,
        "endpoint": _CURSOR_ENDPOINT,
        "last_completion_event_id": str(last_event_id),
        "project_id": str(project_id),
        "v": _CURSOR_VERSION,
        "work_item_id": str(work_item_id),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(
    database: Session,
    cursor: str,
    *,
    project_id: UUID,
    work_item_id: UUID,
) -> tuple[int, int]:
    try:
        if "=" in cursor:
            raise ValueError
        raw = base64.b64decode(
            cursor + "=" * (-len(cursor) % 4),
            altchars=b"-_",
            validate=True,
        )
        if len(raw) > _CURSOR_MAX_DECODED_BYTES:
            raise ValueError
        payload = json.loads(raw.decode("ascii"))
        canonical = (
            base64.urlsafe_b64encode(
                json.dumps(
                    payload,
                    ensure_ascii=True,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("ascii")
            )
            .rstrip(b"=")
            .decode("ascii")
        )
    except UnicodeError, ValueError, TypeError, json.JSONDecodeError:
        raise _invalid_cursor() from None
    if (
        not isinstance(payload, dict)
        or set(payload) != _CURSOR_KEYS
        or canonical != cursor
        or type(payload["v"]) is not int
        or payload["v"] != _CURSOR_VERSION
        or payload["endpoint"] != _CURSOR_ENDPOINT
        or payload["direction"] != _CURSOR_DIRECTION
        or payload["project_id"] != str(project_id)
        or payload["work_item_id"] != str(work_item_id)
    ):
        raise _invalid_cursor()
    as_of = _event_id(payload["as_of_completion_event_id"])
    last = _event_id(payload["last_completion_event_id"])
    if last > as_of:
        raise _invalid_cursor()
    named_ids = set(
        database.scalars(
            select(WorkEvent.id).where(
                WorkEvent.project_id == project_id,
                WorkEvent.work_item_id == work_item_id,
                WorkEvent.event_type == "work_completed",
                WorkEvent.id.in_((as_of, last)),
            )
        )
    )
    if named_ids != {as_of, last}:
        raise _invalid_cursor()
    return as_of, last


def _verification_read(row: VerificationResult) -> VerificationResultRead:
    model = (
        CommandVerificationRead
        if row.verification_type == "command"
        else ObservationVerificationRead
    )
    return model.model_validate(row)


def hydrate_completion_evidence(
    database: Session,
    checkpoint: Checkpoint,
) -> CompletionEvidencePayloadRead | None:
    results = [
        _verification_read(row)
        for row in database.scalars(
            select(VerificationResult)
            .where(
                VerificationResult.work_item_id == checkpoint.work_item_id,
                VerificationResult.completion_checkpoint_id == checkpoint.id,
            )
            .order_by(VerificationResult.position)
        )
    ]
    artifacts = [
        ArtifactReferenceRead.model_validate(row)
        for row in database.scalars(
            select(ArtifactReference)
            .where(
                ArtifactReference.work_item_id == checkpoint.work_item_id,
                ArtifactReference.completion_checkpoint_id == checkpoint.id,
            )
            .order_by(ArtifactReference.position)
        )
    ]
    if not results and not artifacts:
        return None
    return CompletionEvidencePayloadRead(
        verification_results=results,
        artifact_references=artifacts,
    )


def insert_completion_evidence(
    database: Session,
    work_item: WorkItem,
    checkpoint: Checkpoint,
    evidence: CompletionEvidenceInput | None,
) -> CompletionEvidencePayloadRead | None:
    """Insert children only inside the completion transaction, then rehydrate them."""
    if evidence is None:
        return None
    for position, item in enumerate(evidence.verification_results):
        values = item.model_dump()
        database.add(
            VerificationResult(
                project_id=work_item.project_id,
                work_item_id=work_item.id,
                completion_checkpoint_id=checkpoint.id,
                position=position,
                created_at=checkpoint.created_at,
                **values,
            )
        )
    for position, item in enumerate(evidence.artifact_references):
        database.add(
            ArtifactReference(
                project_id=work_item.project_id,
                work_item_id=work_item.id,
                completion_checkpoint_id=checkpoint.id,
                position=position,
                created_at=checkpoint.created_at,
                **item.model_dump(),
            )
        )
    database.flush()
    hydrated = hydrate_completion_evidence(database, checkpoint)
    if hydrated is None:
        raise completion_evidence_unavailable()
    return hydrated


def _children_by_checkpoint(
    database: Session,
    work_item_id: UUID,
    checkpoint_ids: Sequence[UUID],
) -> tuple[dict[UUID, list[VerificationResultRead]], dict[UUID, list[ArtifactReferenceRead]]]:
    results: dict[UUID, list[VerificationResultRead]] = {
        checkpoint_id: [] for checkpoint_id in checkpoint_ids
    }
    artifacts: dict[UUID, list[ArtifactReferenceRead]] = {
        checkpoint_id: [] for checkpoint_id in checkpoint_ids
    }
    if not checkpoint_ids:
        return results, artifacts
    for row in database.scalars(
        select(VerificationResult)
        .where(
            VerificationResult.work_item_id == work_item_id,
            VerificationResult.completion_checkpoint_id.in_(checkpoint_ids),
        )
        .order_by(VerificationResult.completion_checkpoint_id, VerificationResult.position)
    ):
        results[row.completion_checkpoint_id].append(_verification_read(row))
    for row in database.scalars(
        select(ArtifactReference)
        .where(
            ArtifactReference.work_item_id == work_item_id,
            ArtifactReference.completion_checkpoint_id.in_(checkpoint_ids),
        )
        .order_by(ArtifactReference.completion_checkpoint_id, ArtifactReference.position)
    ):
        artifacts[row.completion_checkpoint_id].append(ArtifactReferenceRead.model_validate(row))
    return results, artifacts


def _exact_completion_receipt(
    body: object,
    *,
    project_id: UUID,
    work_item_id: UUID,
    checkpoint_ids: set[UUID],
) -> WorkCompletionRead:
    receipt = WorkCompletionRead.model_validate(body)
    canonical_body = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    stored_body = json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if canonical_body != stored_body:
        raise ValueError
    if (
        receipt.checkpoint.id not in checkpoint_ids
        or receipt.work_item.project_id != project_id
        or receipt.work_item.id != work_item_id
        or receipt.checkpoint.work_item_id != work_item_id
    ):
        raise ValueError
    return receipt


def _require_checkpoint_receipt_match(
    receipt_rows: Sequence[WorkCompletionRead],
    result_rows: list[VerificationResultRead],
    artifact_rows: list[ArtifactReferenceRead],
) -> None:
    expected = (
        CompletionEvidencePayloadRead(
            verification_results=result_rows,
            artifact_references=artifact_rows,
        )
        if result_rows or artifact_rows
        else None
    )
    if len(receipt_rows) > 1:
        raise ValueError
    if expected is None:
        if receipt_rows and receipt_rows[0].completion_evidence is not None:
            raise ValueError
        return
    if len(receipt_rows) != 1 or receipt_rows[0].completion_evidence != expected:
        raise ValueError


def _validate_receipt_child_sets(
    database: Session,
    *,
    project_id: UUID,
    work_item_id: UUID,
    checkpoint_ids: Sequence[UUID],
    results: dict[UUID, list[VerificationResultRead]],
    artifacts: dict[UUID, list[ArtifactReferenceRead]],
) -> None:
    """Require exact receipt parity whenever a completion has durable evidence."""
    if not checkpoint_ids:
        return
    checkpoint_set = set(checkpoint_ids)
    receipts: dict[UUID, list[WorkCompletionRead]] = {
        checkpoint_id: [] for checkpoint_id in checkpoint_ids
    }
    checkpoint_path = ClientOperation.response_body.op("#>>")(literal_column("'{checkpoint,id}'"))
    bodies = database.scalars(
        select(ClientOperation.response_body)
        .where(
            ClientOperation.project_id == project_id,
            ClientOperation.operation_kind == "complete_work",
            ClientOperation.state == "completed",
            checkpoint_path.in_(tuple(str(value) for value in checkpoint_ids)),
        )
        .limit(len(checkpoint_ids) + 1)
    )
    try:
        for body in bodies:
            receipt = _exact_completion_receipt(
                body,
                project_id=project_id,
                work_item_id=work_item_id,
                checkpoint_ids=checkpoint_set,
            )
            receipts[receipt.checkpoint.id].append(receipt)
        for checkpoint_id in checkpoint_ids:
            _require_checkpoint_receipt_match(
                receipts[checkpoint_id],
                results[checkpoint_id],
                artifacts[checkpoint_id],
            )
    except KeyError, TypeError, ValueError:
        raise completion_evidence_unavailable() from None


def _episode_reads(
    rows: Sequence[Any],
    results: dict[UUID, list[VerificationResultRead]],
    artifacts: dict[UUID, list[ArtifactReferenceRead]],
) -> list[CompletionEvidenceEpisodeRead]:
    """Assemble one bounded page while translating corrupt stored state uniformly."""
    items: list[CompletionEvidenceEpisodeRead] = []
    try:
        for row in rows:
            if row.is_sealed is not True:
                raise ValueError
            checkpoint = CompletionCheckpointPointer(
                id=row.checkpoint_id,
                work_item_id=row.checkpoint_work_item_id,
                kind=row.checkpoint_kind,
                source_client=row.checkpoint_source_client,
                source_session_id=row.checkpoint_source_session_id,
                source_model=row.checkpoint_source_model,
                repository_branch=row.checkpoint_repository_branch,
                verified_against=row.checkpoint_verified_against,
                tags=row.checkpoint_tags,
                migration_origin=row.checkpoint_migration_origin,
                legacy_record_id=row.checkpoint_legacy_record_id,
                created_at=row.checkpoint_created_at,
            )
            items.append(
                CompletionEvidenceEpisodeRead(
                    completion_event_id=str(row.completion_event_id),
                    completion_checkpoint=checkpoint,
                    verification_results=results[row.checkpoint_id],
                    artifact_references=artifacts[row.checkpoint_id],
                )
            )
    except AttributeError, KeyError, TypeError, ValueError:
        raise completion_evidence_unavailable() from None
    return items


def completion_evidence_page(
    database: Session,
    project_id: UUID,
    work_item_id: UUID,
    query: CompletionEvidenceListQuery,
) -> CompletionEvidencePage:
    begin_coherent_read(database)
    work_item = require_work_item(database, project_id, work_item_id)
    projection = canonical_projection(database, project_id, work_item)
    if query.cursor is None:
        as_of = database.scalar(
            select(func.max(WorkEvent.id)).where(
                WorkEvent.project_id == project_id,
                WorkEvent.work_item_id == work_item_id,
                WorkEvent.event_type == "work_completed",
            )
        )
        last = None
    else:
        as_of, last = _decode_cursor(
            database,
            query.cursor,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    event_filter = (
        WorkEvent.project_id == project_id,
        WorkEvent.work_item_id == work_item_id,
        WorkEvent.event_type == "work_completed",
        WorkEvent.id <= as_of if as_of is not None else WorkEvent.id < 0,
    )
    total = int(
        database.scalar(select(func.count()).select_from(WorkEvent).where(*event_filter)) or 0
    )
    linked_total, distinct_checkpoint_total = database.execute(
        select(
            func.count(),
            func.count(WorkEvent.checkpoint_id.distinct()),
        )
        .select_from(WorkEvent)
        .join(
            Checkpoint,
            (Checkpoint.work_item_id == WorkEvent.work_item_id)
            & (Checkpoint.id == WorkEvent.checkpoint_id)
            & (Checkpoint.kind == "completion"),
        )
        .where(*event_filter)
    ).one()
    if linked_total != total or distinct_checkpoint_total != total:
        raise completion_evidence_unavailable()
    structured_total = int(
        database.scalar(
            select(func.count())
            .select_from(WorkEvent)
            .where(
                *event_filter,
                (
                    exists().where(
                        VerificationResult.work_item_id == WorkEvent.work_item_id,
                        VerificationResult.completion_checkpoint_id == WorkEvent.checkpoint_id,
                    )
                    | exists().where(
                        ArtifactReference.work_item_id == WorkEvent.work_item_id,
                        ArtifactReference.completion_checkpoint_id == WorkEvent.checkpoint_id,
                    )
                ),
            )
        )
        or 0
    )
    page_filter = [*event_filter]
    if last is not None:
        page_filter.append(WorkEvent.id < last)
    rows = list(
        database.execute(
            select(
                WorkEvent.id.label("completion_event_id"),
                Checkpoint.id.label("checkpoint_id"),
                Checkpoint.work_item_id.label("checkpoint_work_item_id"),
                Checkpoint.kind.label("checkpoint_kind"),
                Checkpoint.source_client.label("checkpoint_source_client"),
                Checkpoint.source_session_id.label("checkpoint_source_session_id"),
                Checkpoint.source_model.label("checkpoint_source_model"),
                Checkpoint.repository_branch.label("checkpoint_repository_branch"),
                Checkpoint.verified_against.label("checkpoint_verified_against"),
                Checkpoint.tags.label("checkpoint_tags"),
                Checkpoint.migration_origin.label("checkpoint_migration_origin"),
                Checkpoint.legacy_record_id.label("checkpoint_legacy_record_id"),
                Checkpoint.created_at.label("checkpoint_created_at"),
                Checkpoint.completion_generation.label("completion_generation"),
                func.mnemonic_completion_episode_is_sealed(
                    Checkpoint.work_item_id,
                    Checkpoint.completion_generation,
                ).label("is_sealed"),
            )
            .select_from(WorkEvent)
            .join(
                Checkpoint,
                (Checkpoint.work_item_id == WorkEvent.work_item_id)
                & (Checkpoint.id == WorkEvent.checkpoint_id),
            )
            .where(*page_filter)
            .order_by(WorkEvent.id.desc())
            .limit(query.limit + 1)
        )
    )
    has_next = len(rows) > query.limit
    rows = rows[: query.limit]
    checkpoint_ids = [row.checkpoint_id for row in rows]
    try:
        results, artifacts = _children_by_checkpoint(database, work_item_id, checkpoint_ids)
        _validate_receipt_child_sets(
            database,
            project_id=project_id,
            work_item_id=work_item_id,
            checkpoint_ids=checkpoint_ids,
            results=results,
            artifacts=artifacts,
        )
    except KeyError, TypeError, ValueError:
        raise completion_evidence_unavailable() from None
    items = _episode_reads(rows, results, artifacts)

    tombstoned = database.scalar(
        select(
            exists().where(
                WorkEvent.work_item_id == work_item_id,
                WorkEvent.event_type == "work_deleted",
            )
        )
    )
    current_checkpoint_id: UUID | None = None
    if work_item.status == "done" and not projection.is_duplicate and not tombstoned:
        current_checkpoint_ids = list(
            database.scalars(
                select(Checkpoint.id)
                .join(
                    WorkEvent,
                    (WorkEvent.work_item_id == Checkpoint.work_item_id)
                    & (WorkEvent.checkpoint_id == Checkpoint.id)
                    & (WorkEvent.event_type == "work_completed"),
                )
                .where(
                    Checkpoint.work_item_id == work_item_id,
                    Checkpoint.kind == "completion",
                    Checkpoint.completion_generation == work_item.completion_generation,
                )
                .limit(2)
            )
        )
        if len(current_checkpoint_ids) != 1:
            raise completion_evidence_unavailable()
        current_checkpoint_id = current_checkpoint_ids[0]

    next_cursor = None
    if has_next and as_of is not None and rows:
        next_cursor = _encode_cursor(
            project_id=project_id,
            work_item_id=work_item_id,
            as_of_event_id=int(as_of),
            last_event_id=int(rows[-1].completion_event_id),
        )
    try:
        return CompletionEvidencePage(
            work_item_id=work_item.id,
            work_version=work_item.version,
            lifecycle_status=work_item.status,
            is_duplicate=projection.is_duplicate,
            canonical_work_item_id=projection.canonical_work_item.id,
            current_completion_checkpoint_id=current_checkpoint_id,
            as_of_completion_event_id=str(as_of) if as_of is not None else None,
            items=items,
            total=total,
            structured_completion_total=structured_total,
            limit=query.limit,
            next_cursor=next_cursor,
        )
    except TypeError, ValueError:
        raise completion_evidence_unavailable() from None
