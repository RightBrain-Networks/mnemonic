"""Bounded, source-specific descriptions of the search that actually ran."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from .search_diagnostics import SearchFacet
from .search_exploration import DATE_FIELDS, DiagnosticsMode, EchoedDateBounds
from .search_query import WORK_FIELDS, QueryMode, WorkFields, constrained_query

SearchDetail = Literal["compact", "full"]


from .transcript_segments import ContentKinds


class DisclosureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAppliedFilters(EchoedDateBounds, DisclosureModel):
    work_fields: WorkFields = Field(default_factory=lambda: list(WORK_FIELDS))
    @field_validator("work_fields", mode="before")
    @classmethod
    def canonical_fields(cls, value: object) -> object:
        if not isinstance(value, list) or value != [field for field in WORK_FIELDS if field in value]:
            raise ValueError("Echoed work fields must be unique and canonically ordered")
        return value

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


class ArtifactAppliedFilters(EchoedDateBounds, DisclosureModel):
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: StrictBool = False
    sensitive: StrictBool | None = None
    mime_type: Annotated[str, Field(max_length=255)] | None = None
    created_by_agent_session_id: Annotated[str, Field(max_length=200)] | None = None


class TranscriptAppliedFilters(EchoedDateBounds, DisclosureModel):
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
        "browse", "postgresql_plain_terms_or_substring", "hybrid_lexical_semantic", "all_terms", "postgresql_phrase_terms", "phrase", "literal",
    ]
    fields: list[Literal[
        "title", "summary", "tags", "checkpoint", "identifiers", "provenance",
        "metadata", "content",
    ]] = Field(max_length=6)
    # Work lexical search always includes checkpoint prose: fulltext does not govern it.
    fulltext: StrictBool | None


class QueryInterpretation(DisclosureModel):
    query_mode: QueryMode = "terms"
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
    diagnostics: DiagnosticsMode = "on_empty"
    applied_filters: AppliedSearchFilters
    query_interpretation: QueryInterpretation
    warnings: list[SearchWarning] = Field(max_length=1)


def search_disclosure(
    project_id: UUID, q: str | None, *, query_mode: QueryMode = "terms",
    work_items: WorkAppliedFilters | None = None,
    artifacts: ArtifactAppliedFilters | None = None,
    transcripts: TranscriptAppliedFilters | None = None,
    semantic: bool = False, fulltext: bool = False, diagnostics: DiagnosticsMode = "on_empty",
) -> SearchDisclosure:
    query = (q or "").strip()
    applied = AppliedSearchFilters(
        project_id=project_id, work_items=work_items, artifacts=artifacts, transcripts=transcripts,
    )
    interpretation = QueryInterpretation(q=query, query_mode=query_mode)
    sources: list[SearchFacet] = []
    if work_items is not None:
        sources.append("work_items")
        mode = "hybrid_lexical_semantic" if semantic else "postgresql_plain_terms_or_substring"
        if constrained_query(query, query_mode):
            mode = "literal" if query_mode == "literal" else "postgresql_phrase_terms"
        interpretation.work_items = SourceQueryInterpretation(
            match_mode=mode if query else "browse", fulltext=None,
            fields=work_items.work_fields,
        )
    for facet, filters in (("artifacts", artifacts), ("transcripts", transcripts)):
        if filters is None:
            continue
        sources.append(facet)
        source = SourceQueryInterpretation(
            match_mode=("literal" if query_mode == "literal" else "phrase"
                        if constrained_query(query, query_mode) else "all_terms")
                       if query else "browse", fulltext=fulltext,
            fields=["metadata", "content"] if fulltext else ["metadata"],
        )
        setattr(interpretation, facet, source)
    return SearchDisclosure(
        diagnostics=diagnostics, applied_filters=applied, query_interpretation=interpretation,
        warnings=[],
    )


def disclosure_matches(actual: SearchDisclosure, expected: SearchDisclosure) -> bool:
    """An empty result must still attest to every effective filter in its wire response."""
    if "query_mode" not in actual.query_interpretation.model_fields_set:
        return False
    if "diagnostics" not in actual.model_fields_set:
        return False
    if any(getattr(actual, name) != getattr(expected, name) for name in (
        "diagnostics", "applied_filters", "query_interpretation", "warnings",
    )):
        return False
    sources = (actual.applied_filters, actual.query_interpretation)
    return all(
        source is None or (source.model_fields_set - set(DATE_FIELDS)
                           == set(type(source).model_fields) - set(DATE_FIELDS))
        for group in sources for source in (group.work_items, group.artifacts, group.transcripts)
    )
