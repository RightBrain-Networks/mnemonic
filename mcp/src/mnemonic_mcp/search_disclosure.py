"""Bounded, source-specific descriptions of the search that actually ran."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .search_diagnostics import SearchFacet

SearchDetail = Literal["compact", "full"]


from .transcript_segments import ContentKinds


class DisclosureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAppliedFilters(DisclosureModel):
    status: Literal[
        "pending", "active", "to-review", "dropped", "deferred", "done",
        "wont-do", "promoted", "all",
    ] = "all"
    tag: Annotated[str, Field(max_length=50)] | None = None
    source_client: Annotated[str, Field(max_length=80)] | None = None
    source_session_id: Annotated[str, Field(max_length=200)] | None = None
    external_url: Annotated[str, Field(max_length=2000)] | None = None
    duplicate_scope: Literal["canonical", "aliases", "all"] = "canonical"
    canonical_work_item_id: UUID | None = None
    view: Literal["full", "roots"] = "full"


class ArtifactAppliedFilters(DisclosureModel):
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: StrictBool = False
    sensitive: StrictBool | None = None
    mime_type: Annotated[str, Field(max_length=255)] | None = None
    created_by_agent_session_id: Annotated[str, Field(max_length=200)] | None = None


class TranscriptAppliedFilters(DisclosureModel):
    content_kinds: ContentKinds | None = None
    work_item_id: UUID | None = None
    agent_session_id: Annotated[str, Field(max_length=200)] | None = None
    client: Annotated[str, Field(max_length=80)] | None = None
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
    fulltext: StrictBool | None


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


def disclosure_matches(actual: SearchDisclosure, expected: SearchDisclosure) -> bool:
    """An empty result must still attest to every effective filter in its wire response."""
    if any(getattr(actual, name) != getattr(expected, name) for name in (
        "applied_filters", "query_interpretation", "warnings",
    )):
        return False
    sources = (actual.applied_filters, actual.query_interpretation)
    return all(
        source is None or source.model_fields_set == set(type(source).model_fields)
        for group in sources for source in (group.work_items, group.artifacts, group.transcripts)
    )
