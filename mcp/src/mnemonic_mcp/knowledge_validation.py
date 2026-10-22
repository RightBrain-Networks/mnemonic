"""Verify revision identities, content pins, and provenance before exposing responses."""

import hashlib
from uuid import UUID

from .knowledge_models import (
    DecisionPut,
    DecisionPutResult,
    DecisionRead,
    DecisionScopeAppend,
    DecisionScopeResult,
    DecisionTextRead,
)


def decision_identity(decision: DecisionRead, project_id: UUID, reference: str) -> bool:
    stable = "-".join(reference.split("-")[:2])
    return (
        decision.project_id == project_id and decision.decision_id == stable
        and decision.decision_id == f"{decision.prefix}-{decision.ordinal:05d}"
        and decision.reference == f"{decision.decision_id}-{decision.revision}"
    )


def text_matches(
    item: DecisionTextRead, project_id: UUID, reference: str, revision: int | None,
    line_start: int | None, line_end: int | None,
) -> bool:
    suffix = reference.split("-")
    wanted_revision = int(suffix[2]) if len(suffix) == 3 else revision
    if not decision_identity(item, project_id, reference) or (
        wanted_revision is not None and item.revision != wanted_revision
    ):
        return False
    if item.line_start != (line_start or 1) or item.line_end != min(
        line_end if line_end is not None else item.total_lines, item.total_lines,
    ):
        return False
    if len(item.content.splitlines(keepends=True)) != max(item.line_end - item.line_start + 1, 0):
        return False
    if item.line_start == 1 and item.line_end == item.total_lines:
        encoded = item.content.encode("utf-8")
        return (len(encoded) == item.size_bytes
                and hashlib.sha256(encoded).hexdigest() == item.sha256)
    return True


def put_matches(result: DecisionPutResult, project_id: UUID, intent: DecisionPut) -> bool:
    item = result.decision
    if not _put_metadata_matches(result, intent):
        return False
    encoded = intent.content.encode("utf-8")
    return (
        decision_identity(item, project_id, intent.decision_id or item.decision_id)
        and item.size_bytes == len(encoded)
        and item.sha256 == hashlib.sha256(encoded).hexdigest()
        and (intent.expected_revision is None or (
            item.revision == intent.expected_revision + 1 if result.created
            else item.revision >= intent.expected_revision
        ))
    )


def _put_metadata_matches(result: DecisionPutResult, intent: DecisionPut) -> bool:
    item = result.decision
    if item.domain_id != intent.domain_id:
        return False
    # A new upload deduplicates by domain/content hash and retains the original metadata.
    if not result.created and intent.decision_id is None:
        return True
    fields = {"title", "media_type", "state"}
    if result.created:
        fields |= {"author_kind", "agent_session_id", "model_id"}
    links = {"related_decision_ids", "related_work_item_ids", "related_artifact_ids", "supersedes"}
    return (all(getattr(item, key) == getattr(intent, key) for key in fields)
            and all(set(getattr(item, key)) == set(getattr(intent, key)) for key in links))


def scope_matches(
    result: DecisionScopeResult, project_id: UUID, decision_id: str, intent: DecisionScopeAppend,
) -> bool:
    item = result.scope
    return (
        item.project_id == project_id and item.decision_id == decision_id
        and all(getattr(item, key) == value for key, value in intent.model_dump(
            exclude={"client_operation_id"},
        ).items())
    )
