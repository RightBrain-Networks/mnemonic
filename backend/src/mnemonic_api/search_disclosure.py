"""Bounded, source-specific descriptions of the search that actually ran."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mnemonic_api.search_diagnostics import SearchFacet


class DisclosureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAppliedFilters(DisclosureModel):
    status: Literal[
        "pending", "active", "to-review", "dropped", "deferred", "done",
        "wont-do", "promoted", "all",
    ] = "all"
    tag: str | None = None
    source_client: str | None = None
    source_session_id: str | None = None
    external_url: str | None = None
    duplicate_scope: Literal["canonical", "aliases", "all"] = "canonical"
    canonical_work_item_id: UUID | None = None
    view: Literal["full", "roots"] = "full"


class ArtifactAppliedFilters(DisclosureModel):
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: bool = False
    sensitive: bool | None = None
    mime_type: str | None = None
    created_by_agent_session_id: str | None = None


class TranscriptAppliedFilters(DisclosureModel):
    work_item_id: UUID | None = None
    agent_session_id: str | None = None
    client: str | None = None
    kind: Literal["primary", "subagent", "imported"] | None = None
    status: Literal["waiting", "pending", "processing", "ready", "failed"] | None = None


class AppliedSearchFilters(DisclosureModel):
    project_id: UUID
    # Null identifies an unsearched source, including a disabled artifact library.
    work_items: WorkAppliedFilters | None = None
    artifacts: ArtifactAppliedFilters | None = None
    transcripts: TranscriptAppliedFilters | None = None


class SourceQueryInterpretation(DisclosureModel):
    match_mode: Literal[
        "browse", "postgresql_plain_terms_or_substring", "hybrid_lexical_semantic", "all_terms",
    ]
    fields: list[Literal[
        "title", "summary", "tags", "checkpoint", "identifiers", "provenance",
        "metadata", "content",
    ]] = Field(max_length=6)
    # Work lexical search always includes checkpoint prose: fulltext does not govern it.
    fulltext: bool | None


class QueryInterpretation(DisclosureModel):
    q: str = Field(max_length=1000)
    work_items: SourceQueryInterpretation | None = None
    artifacts: SourceQueryInterpretation | None = None
    transcripts: SourceQueryInterpretation | None = None


class SearchWarning(DisclosureModel):
    code: Literal["phrase_operators_ignored"] = "phrase_operators_ignored"
    sources: list[SearchFacet] = Field(min_length=1, max_length=3)
    message: Literal[
        "Quoted phrases are not supported; quotation marks do not require adjacent words."
    ] = "Quoted phrases are not supported; quotation marks do not require adjacent words."


class SearchDisclosure(DisclosureModel):
    applied_filters: AppliedSearchFilters
    query_interpretation: QueryInterpretation
    warnings: list[SearchWarning] = Field(max_length=1)


def search_disclosure(
    project_id: UUID, q: str | None, *,
    work_items: WorkAppliedFilters | None = None,
    artifacts: ArtifactAppliedFilters | None = None,
    transcripts: TranscriptAppliedFilters | None = None,
    semantic: bool = False, fulltext: bool = False,
) -> SearchDisclosure:
    query = (q or "").strip()
    applied = AppliedSearchFilters(
        project_id=project_id, work_items=work_items, artifacts=artifacts, transcripts=transcripts,
    )
    interpretation = QueryInterpretation(q=query)
    sources: list[SearchFacet] = []
    if work_items is not None:
        sources.append("work_items")
        mode = "hybrid_lexical_semantic" if semantic else "postgresql_plain_terms_or_substring"
        interpretation.work_items = SourceQueryInterpretation(
            match_mode=mode if query else "browse", fulltext=None,
            fields=["title", "summary", "tags", "checkpoint", "identifiers", "provenance"],
        )
    for facet, filters in (("artifacts", artifacts), ("transcripts", transcripts)):
        if filters is None:
            continue
        sources.append(facet)
        source = SourceQueryInterpretation(
            match_mode="all_terms" if query else "browse", fulltext=fulltext,
            fields=["metadata", "content"] if fulltext else ["metadata"],
        )
        setattr(interpretation, facet, source)
    return SearchDisclosure(
        applied_filters=applied, query_interpretation=interpretation,
        warnings=[SearchWarning(sources=sources)] if '"' in query and sources else [],
    )
