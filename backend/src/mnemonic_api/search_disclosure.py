"""Bounded, source-specific descriptions of the search that actually ran."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mnemonic_api.search_diagnostics import SearchFacet
from mnemonic_api.search_exploration_schemas import DateBounds, DiagnosticsMode
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_query import QueryMode, parse_query
from mnemonic_api.transcript_normalization import ContentKind
from mnemonic_api.work_search_fields import WORK_FIELDS, WorkFields


class DisclosureModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkAppliedFilters(DisclosureModel, DateBounds):
    status_scope: Literal["effective", "work_item"] = Field(
        default="effective", exclude_if=lambda value: value == "effective",
    )
    work_fields: WorkFields = Field(default_factory=lambda: list(WORK_FIELDS))
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


class ArtifactAppliedFilters(DisclosureModel, DateBounds):
    semantic: bool = Field(default=False, exclude_if=lambda value: value is False)
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: bool = False
    sensitive: bool | None = None
    mime_type: str | None = None
    created_by_agent_session_id: str | None = None


class TranscriptAppliedFilters(DisclosureModel, DateBounds):
    content_kinds: list[ContentKind] | None = Field(default=None, min_length=1, max_length=8)
    work_item_id: UUID | None = None
    agent_session_id: str | None = None
    client: str | None = None
    kind: Literal["primary", "subagent", "imported"] | None = None
    status: Literal["waiting", "pending", "processing", "ready", "failed"] | None = None


class AppliedSearchFilters(DisclosureModel):
    project_id: UUID | None
    project_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=10)
    # Null identifies an unsearched source, including a disabled artifact library.
    work_items: WorkAppliedFilters | None = None
    artifacts: ArtifactAppliedFilters | None = None
    transcripts: TranscriptAppliedFilters | None = None


class SourceQueryInterpretation(DisclosureModel):
    match_mode: Literal[
        "browse", "postgresql_plain_terms_or_substring", "hybrid_lexical_semantic", "all_terms",
        "postgresql_phrase_terms", "phrase", "literal",
        "semantic_passages",
    ]
    fields: list[Literal[
        "title", "summary", "tags", "checkpoint", "identifiers", "provenance",
        "metadata", "content",
    ]] = Field(max_length=6)
    # Work lexical fields, including checkpoint prose, are controlled by work_fields.
    fulltext: bool | None


class QueryInterpretation(DisclosureModel):
    query_mode: QueryMode = "terms"
    q: str = Field(max_length=1000)
    work_items: SourceQueryInterpretation | None = None
    artifacts: SourceQueryInterpretation | None = None
    transcripts: SourceQueryInterpretation | None = None


class SearchWarning(DisclosureModel):
    code: Literal["phrase_operators_ignored", "sensitive_content_withheld"] = (
        "phrase_operators_ignored"
    )
    sources: list[SearchFacet] = Field(min_length=1, max_length=3)
    message: Literal[
        "Quoted phrases are not supported; quotation marks do not require adjacent words.",
        "Sensitive artifact contents were withheld; zero matches do not establish absence."
    ] = "Quoted phrases are not supported; quotation marks do not require adjacent words."


class SearchDisclosure(DisclosureModel):
    diagnostics: DiagnosticsMode = "on_empty"
    applied_filters: AppliedSearchFilters
    query_interpretation: QueryInterpretation
    warnings: list[SearchWarning] = Field(max_length=2)

    @model_validator(mode="after")
    def disclose_withholding(self) -> SearchDisclosure:
        self.warnings = [warning for warning in self.warnings
                         if warning.code != "sensitive_content_withheld"]
        self.warnings += withholding_warnings(self)
        return self


def withholding_warnings(page) -> list[SearchWarning]:
    coverage = getattr(page, "coverage", None)
    count = getattr(page, "sensitive_content_withheld", 0)
    if coverage is not None:
        count = coverage.artifacts.sensitive_content_withheld
    return [SearchWarning(
        code="sensitive_content_withheld", sources=["artifacts"],
        message="Sensitive artifact contents were withheld; zero matches do not establish absence.",
    )] if count else []


def search_disclosure(
    project_id: ProjectSelection, q: str | None, *,
    work_items: WorkAppliedFilters | None = None,
    artifacts: ArtifactAppliedFilters | None = None,
    transcripts: TranscriptAppliedFilters | None = None,
    semantic: bool = False, fulltext: bool = False, query_mode: QueryMode = "terms",
    diagnostics: DiagnosticsMode = "on_empty",
) -> SearchDisclosure:
    query = (q or "").strip()
    applied = AppliedSearchFilters(
        project_id=project_id if isinstance(project_id, UUID) else None,
        project_ids=(None if isinstance(project_id, UUID)
                     else list(selected_project_ids(project_id))),
        work_items=work_items, artifacts=artifacts, transcripts=transcripts,
    )
    intent = parse_query(query, query_mode)
    interpretation = QueryInterpretation(q=query, query_mode=query_mode)
    sources: list[SearchFacet] = []
    if work_items is not None:
        sources.append("work_items")
        mode = "hybrid_lexical_semantic" if semantic else "postgresql_plain_terms_or_substring"
        if intent.constrained:
            mode = "literal" if query_mode == "literal" else "postgresql_phrase_terms"
        interpretation.work_items = SourceQueryInterpretation(
            match_mode=mode if query else "browse", fulltext=None,
            fields=list(work_items.work_fields),
        )
    for facet, filters in (("artifacts", artifacts), ("transcripts", transcripts)):
        if filters is None:
            continue
        sources.append(facet)
        content_mode = ("literal" if query_mode == "literal" else
                        "phrase" if intent.constrained else "all_terms")
        source = SourceQueryInterpretation(
            match_mode=content_mode if query else "browse", fulltext=fulltext,
            fields=["metadata", "content"] if fulltext else ["metadata"],
        )
        if (facet == "artifacts" and isinstance(filters, ArtifactAppliedFilters)
                and filters.semantic):
            source.match_mode = "semantic_passages"
            source.fields = ["content"]
        setattr(interpretation, facet, source)
    return SearchDisclosure(
        diagnostics=diagnostics, applied_filters=applied, query_interpretation=interpretation,
        warnings=[],
    )
