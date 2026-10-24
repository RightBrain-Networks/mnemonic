"""Response coherence for permanent knowledge mutation receipts."""

import hashlib
from collections.abc import Mapping
from typing import cast
from uuid import UUID

from pydantic.experimental.missing_sentinel import MISSING

from mnemonic_api.knowledge_schemas import (
    DecisionPutRequest,
    DecisionPutResult,
    DecisionScopeRequest,
    DecisionScopeResult,
    DomainCreateRequest,
    DomainRenameRequest,
    KnowledgeDomainRead,
)
from mnemonic_api.repository_identity import canonical_repository
from mnemonic_api.schemas import APIModel
from mnemonic_api.services.decision_dependencies import same_annotations, superseded_ids


def created_domain_matches(
    project_id: UUID,
    target: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    request, result = cast(DomainCreateRequest, payload), cast(KnowledgeDomainRead, typed)
    return (
        result.project_id == project_id
        and result.name == request.name
        and result.prefix == request.prefix
        and result.version == 1
        and (result.automatic_decision_reviews == request.automatic_decision_reviews
             or result.automatic_decision_reviews is MISSING
             and request.automatic_decision_reviews == "inherit")
    )


def renamed_domain_matches(
    project_id: UUID,
    target: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    request, result = cast(DomainRenameRequest, payload), cast(KnowledgeDomainRead, typed)
    return (
        result.project_id == project_id
        and str(result.id) == target.get("domain_id")
        and (request.name is MISSING or result.name == request.name)
        and (request.automatic_decision_reviews is MISSING
             or result.automatic_decision_reviews == request.automatic_decision_reviews)
        and result.version in {request.expected_version, request.expected_version + 1}
    )


def put_decision_matches(
    project_id: UUID,
    target: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    request, result = cast(DecisionPutRequest, payload), cast(DecisionPutResult, typed)
    row = result.decision
    identity = (
        row.project_id == project_id
        and row.domain_id == request.domain_id
        and row.sha256 == hashlib.sha256(request.content.encode("utf-8")).hexdigest()
        and row.size_bytes == len(request.content.encode("utf-8"))
        and (request.decision_id is None or row.decision_id == request.decision_id)
    )
    if not identity or not result.created:
        return identity
    return (
        row.revision == (0 if request.expected_revision is None else request.expected_revision + 1)
        and (row.title, row.state, row.media_type)
        == (request.title, request.state, request.media_type)
        and (row.author_kind, row.agent_session_id, row.model_id)
        == (request.author_kind, request.agent_session_id, request.model_id)
        and set(row.related_decision_ids) == set(request.related_decision_ids)
        and set(row.related_work_item_ids) == set(request.related_work_item_ids)
        and set(row.related_artifact_ids) == set(request.related_artifact_ids)
        and set(row.supersedes) == set(superseded_ids(request))
        and same_annotations(row, request)
        and _same_supersession_declarations(request, result)
    )


def _same_supersession_declarations(request: DecisionPutRequest, result: DecisionPutResult) -> bool:
    expected = request.change.unchanged_supersessions if request.change else []
    actual = result.decision.change.unchanged_supersessions if result.decision.change else []
    return sorted(item.model_dump_json() for item in expected) == sorted(
        item.model_dump_json() for item in actual
    )


def appended_scope_matches(
    project_id: UUID,
    target: Mapping[str, str],
    payload: APIModel,
    typed: APIModel,
) -> bool:
    request, result = cast(DecisionScopeRequest, payload), cast(DecisionScopeResult, typed)
    row = result.scope
    return (
        row.project_id == project_id
        and str(row.document_id) == target.get("document_id")
        and canonical_repository(row.repository) == canonical_repository(request.repository)
        and row.completion_report_id is None
        and (row.revision, row.commit_hash, row.work_item_id, row.agent_session_id)
        == (request.revision, request.commit_hash, request.work_item_id, request.agent_session_id)
    )
