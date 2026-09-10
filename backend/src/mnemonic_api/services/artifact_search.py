"""Project-scoped Tantivy search from one coherent PostgreSQL snapshot."""

import hashlib
import json
from collections.abc import Iterator
from typing import Any, Literal
from uuid import UUID

import tantivy
from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, defer

from mnemonic_api.artifact_access_schemas import ArtifactAccessRequest
from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument, SearchHit
from mnemonic_api.artifact_search_schemas import (
    ArtifactIndexingStatus,
    ArtifactSearchMatch,
    ArtifactSearchPage,
    ArtifactSearchRequest,
)
from mnemonic_api.models import Artifact, ArtifactExtraction, ArtifactWorkLink
from mnemonic_api.services.artifact_approvals import require_sensitive_access
from mnemonic_api.services.artifacts import _has_pending_operation, artifact_read
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project

__all__ = ["ArtifactSearchIndex", "search_artifact_contents"]

type Corpus = list[tuple[Artifact, ArtifactExtraction | None]]


def _corpus(database: Session, project_id: UUID, filters: ArtifactSearchRequest) -> Corpus:
    clauses = [
        Artifact.project_id == project_id, Artifact.revision > 0, ~_has_pending_operation(),
    ]
    if not filters.include_deleted:
        clauses.append(Artifact.deleted_at.is_(None))
    if filters.artifact_id is not None:
        clauses.append(Artifact.id == filters.artifact_id)
    if filters.work_item_id is not None:
        clauses.append(exists(select(ArtifactWorkLink.artifact_id).where(
            ArtifactWorkLink.artifact_id == Artifact.id,
            ArtifactWorkLink.work_item_id == filters.work_item_id,
        )))
    rows = database.execute(
        select(Artifact, ArtifactExtraction)
        .outerjoin(ArtifactExtraction, and_(
            ArtifactExtraction.artifact_id == Artifact.id,
            ArtifactExtraction.revision == Artifact.revision,
        ))
        .options(defer(ArtifactExtraction.normalized_text))
        .where(*clauses)
        .order_by(Artifact.id)
    )
    return [(artifact, extraction) for artifact, extraction in rows]


def _metadata(artifact: Artifact, extraction: ArtifactExtraction | None) -> dict[str, Any]:
    return {
        "filename": artifact.filename,
        "description": artifact.description,
        "mime_type": artifact.mime_type,
        "sha256": artifact.sha256,
        "agent_session_id": artifact.created_by_agent_session_id,
        "actor_client": artifact.created_by_client,
        "extracted": extraction.extracted_metadata if extraction and not artifact.sensitive else {},
    }


def _metadata_text(artifact: Artifact, extraction: ArtifactExtraction | None) -> str:
    # Index values only: JSON field names and nulls are not artifact properties.
    values = [
        value for value in _metadata(artifact, extraction).values()
        if isinstance(value, str) and value.strip()
    ]
    if extraction is not None and not artifact.sensitive:
        values.extend(
            value for property_values in extraction.extracted_metadata.values()
            for value in property_values if value.strip()
        )
    return "\n".join(values)


def _signature(
    project_id: UUID, corpus: Corpus, fulltext: bool, approved: set[UUID],
) -> str:
    digest = hashlib.sha256(f"{project_id}:{fulltext}".encode())
    for artifact, extraction in corpus:
        digest.update(json.dumps({
            "id": str(artifact.id), "revision": artifact.revision,
            "deleted": artifact.deleted_at is not None,
            "sensitive": artifact.sensitive, "approved": artifact.id in approved,
            "metadata": _metadata(artifact, extraction),
            "extracted_at": str(extraction.extracted_at) if extraction else None,
            "status": extraction.status if extraction else "pending",
        }, ensure_ascii=True, sort_keys=True).encode())
    return digest.hexdigest()


def _indexing(corpus: Corpus) -> ArtifactIndexingStatus:
    counts = ArtifactIndexingStatus()
    for artifact, extraction in corpus:
        if artifact.deleted_at is not None:
            continue
        status = extraction.status if extraction else "pending"
        if status in {"pending", "processing"}:
            counts.pending += 1
        elif status == "failed":
            counts.failed += 1
        elif status == "ready":
            counts.ready += 1
            counts.truncated += int(extraction is not None and extraction.truncated)
    return counts


def _documents(
    database: Session, corpus: Corpus, fulltext: bool, approved: set[UUID],
) -> Iterator[SearchDocument]:
    # Stream bodies separately from the lightweight cache fingerprint. A cache
    # hit and every metadata-only search avoid reading extracted content entirely.
    content_ids = {
        artifact.id for artifact, extraction in corpus
        if fulltext and artifact.deleted_at is None
        and (not artifact.sensitive or artifact.id in approved)
        and extraction is not None and extraction.status == "ready"
    }
    by_id = {artifact.id: (artifact, extraction) for artifact, extraction in corpus}
    for artifact, extraction in corpus:
        if artifact.id not in content_ids:
            yield SearchDocument(
                str(artifact.id), _metadata_text(artifact, extraction)
            )
    if not content_ids:
        return
    rows = database.execute(
        select(ArtifactExtraction.artifact_id, ArtifactExtraction.normalized_text)
        .join(Artifact, and_(
            Artifact.id == ArtifactExtraction.artifact_id,
            Artifact.revision == ArtifactExtraction.revision,
        ))
        .where(Artifact.id.in_(content_ids))
        .order_by(Artifact.id)
        .execution_options(yield_per=8)
    )
    for identity, content in rows:
        artifact, extraction = by_id[identity]
        yield SearchDocument(
            str(identity), _metadata_text(artifact, extraction),
            content or "",
        )


def _match(
    database: Session, index: ArtifactSearchIndex, corpus: dict[str, Artifact],
    hit: SearchHit, filters: ArtifactSearchRequest, searcher: tantivy.Searcher | None,
) -> ArtifactSearchMatch:
    artifact = corpus[hit.identity]
    fields: list[Literal["metadata", "content"]] = []
    snippet = None
    if hit.metadata:
        fields.append("metadata")
    if hit.content:
        fields.append("content")
        content = database.scalar(select(ArtifactExtraction.normalized_text).where(
            ArtifactExtraction.artifact_id == artifact.id,
            ArtifactExtraction.revision == artifact.revision,
        ))
        if searcher is not None:
            snippet = index.snippet(content or "", filters.q, searcher)
    return ArtifactSearchMatch(
        artifact=artifact_read(database, artifact), score=hit.score,
        snippet=snippet, matched_fields=fields,
    )


def _approved_contents(
    database: Session, corpus: Corpus, filters: ArtifactSearchRequest, human_dashboard: bool,
) -> set[UUID]:
    approved: set[UUID] = set()
    if not filters.fulltext:
        return approved
    access = ArtifactAccessRequest(
        **filters.model_dump(include=set(ArtifactAccessRequest.model_fields)),
    )
    scope = filters.model_dump(mode="json", exclude=set(ArtifactAccessRequest.model_fields))
    for artifact, _ in corpus:
        if not artifact.sensitive or artifact.deleted_at is not None:
            continue
        if not human_dashboard and filters.artifact_id is None:
            continue
        require_sensitive_access(
            database, artifact, "search", access, scope, human_dashboard=human_dashboard,
        )
        approved.add(artifact.id)
    return approved


def _search_page(
    database: Session, project_id: UUID, filters: ArtifactSearchRequest,
    index: ArtifactSearchIndex, human_dashboard: bool,
) -> ArtifactSearchPage:
    require_project(database, project_id)
    corpus = _corpus(database, project_id, filters)
    approved = _approved_contents(database, corpus, filters, human_dashboard)
    result = index.search(
        _signature(project_id, corpus, filters.fulltext, approved),
        lambda: _documents(database, corpus, filters.fulltext, approved),
        query=filters.q, fulltext=filters.fulltext, count=len(corpus),
    )
    identities = {str(artifact.id): artifact for artifact, _ in corpus}
    return ArtifactSearchPage(
        items=[
            _match(database, index, identities, hit, filters, result.searcher)
            for hit in result.hits[filters.offset:filters.offset + filters.limit]
        ],
        total=len(result.hits), limit=filters.limit, offset=filters.offset,
        fulltext=filters.fulltext, indexing=_indexing(corpus),
        sensitive_content_withheld=sum(
            1 for artifact, _ in corpus if filters.fulltext and artifact.sensitive
            and artifact.deleted_at is None and artifact.id not in approved
        ),
    )


def search_artifact_contents(
    database: Session, project_id: UUID, filters: ArtifactSearchRequest,
    index: ArtifactSearchIndex, *, human_dashboard: bool = False,
) -> ArtifactSearchPage:
    # Content mutations, sensitivity changes and extraction publication all hold
    # this lock. Keep corpus, approval consumption, snippets and revisions coherent
    # until the read and its audit commit; a concurrent token use must wait.
    with project_mutation(database, project_id, protected=True, domain_seconds=120):
        page = _search_page(database, project_id, filters, index, human_dashboard)
        database.commit()
        return page
