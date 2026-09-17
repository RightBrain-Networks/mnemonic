"""Independent, bounded unified-search request and response contracts."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .artifact_models import (
    ArtifactClient,
    ArtifactIndexingStatus,
    ArtifactSearchMatch,
    ArtifactSession,
    ArtifactToolSearchMatch,
    CompactArtifactMatch,
)
from .artifact_semantic import ArtifactEmbeddingCoverage, validate_artifact_semantic
from .external_records import ExternalURL
from .models import CompactWorkHit, DuplicateScope, SearchStatus, WorkSearchHit
from .search_diagnostics import SearchScope, TermDiagnostics
from .search_disclosure import SearchDetail, SearchDisclosure
from .search_exploration import DateBounds, DiagnosticsMode, TagCountPage, TagCountRequest
from .search_query import WORK_FIELDS, QueryMode, WorkFields, validate_query
from .search_ranking import (
    FacetScoreTypes,
    FacetTotalKinds,
    ScoreType,
    SearchHitRanking,
    SemanticDisposition,
    TotalKind,
)
from .transcript_models import CompactTranscriptRead, TranscriptRead, TranscriptStatus
from .transcript_segments import ContentKinds

SearchFacet = Literal["work_items", "artifacts", "transcripts"]
SearchQuery = Annotated[str, Field(max_length=1000)]
SearchLimit = Annotated[StrictInt, Field(ge=1, le=100)]
SearchOffset = Annotated[StrictInt, Field(ge=0, le=1_000_000)]
SearchProjectIDs = Annotated[list[UUID], Field(min_length=1, max_length=10)]
SearchScore = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class SearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchSort(SearchModel):
    by: Literal["relevance", "created_at", "updated_at", "priority"] = "relevance"
    direction: Literal["asc", "desc"] = "desc"


class FacetOrder(SearchModel):
    facet: SearchFacet
    sort: SearchSort | None = None


def _distinct_facets(value: list[SearchFacet]) -> list[SearchFacet]:
    if len(set(value)) != len(value):
        raise ValueError("Search facets must be unique")
    return value


def _distinct_order(value: list[FacetOrder]) -> list[FacetOrder]:
    if len({item.facet for item in value}) != len(value):
        raise ValueError("Facet ordering must be unique")
    return value


SearchFacets = Annotated[
    list[SearchFacet], Field(min_length=1, max_length=3), AfterValidator(_distinct_facets),
]
SearchFacetOrder = Annotated[
    list[FacetOrder], Field(max_length=3), AfterValidator(_distinct_order),
]


class WorkSearchFilters(DateBounds, SearchModel):
    work_fields: WorkFields = Field(default_factory=lambda: list(WORK_FIELDS))
    status: SearchStatus = "all"
    tag: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    source_client: ArtifactClient | None = None
    source_session_id: ArtifactSession | None = None
    duplicate_scope: DuplicateScope = "canonical"
    canonical_work_item_id: UUID | None = None
    external_url: ExternalURL | None = None
    semantic: StrictBool = False

    @model_validator(mode="after")
    def canonical_scope(self) -> Self:
        if self.canonical_work_item_id is not None and self.duplicate_scope == "canonical":
            raise ValueError("Canonical work filtering requires aliases or all duplicate scope")
        return self


class ArtifactSearchFilters(DateBounds, SearchModel):
    semantic: StrictBool = False
    work_item_id: UUID | None = None
    artifact_id: UUID | None = None
    include_deleted: StrictBool = False
    sensitive: StrictBool | None = None
    mime_type: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    created_by_agent_session_id: ArtifactSession | None = None


class TranscriptSearchFilters(DateBounds, SearchModel):
    content_kinds: ContentKinds | None = None
    work_item_id: UUID | None = None
    agent_session_id: ArtifactSession | None = None
    client: ArtifactClient | None = None
    kind: Literal["primary", "subagent", "imported"] | None = None
    status: TranscriptStatus | None = None


class SearchFilters(SearchModel):
    work_items: WorkSearchFilters = Field(default_factory=WorkSearchFilters)
    artifacts: ArtifactSearchFilters = Field(default_factory=ArtifactSearchFilters)
    transcripts: TranscriptSearchFilters = Field(default_factory=TranscriptSearchFilters)


def _all_facets() -> list[SearchFacet]:
    return ["work_items", "artifacts", "transcripts"]


class SearchRequest(SearchModel):
    query_mode: QueryMode = "terms"
    diagnostics: DiagnosticsMode = "on_empty"
    tag_counts: TagCountRequest | None = None
    q: SearchQuery = ""
    facets: SearchFacets = Field(default_factory=_all_facets)
    fulltext: StrictBool = False
    detail: SearchDetail = "compact"
    filters: SearchFilters = Field(default_factory=SearchFilters)
    sort: SearchSort = Field(default_factory=SearchSort)
    facet_order: SearchFacetOrder = Field(default_factory=list)
    limit: SearchLimit = 20
    offset: SearchOffset = 0

    @model_validator(mode="after")
    def content_kind_requires_fulltext(self) -> Self:
        validate_query(self.q, self.query_mode, semantic=self.filters.work_items.semantic,
                       fields=self.filters.work_items.work_fields)
        if self.filters.artifacts.semantic:
            validate_artifact_semantic(self.q, self.query_mode, self.fulltext,
                                       "artifacts" in self.facets)
        if self.filters.transcripts.content_kinds and not self.fulltext:
            raise PydanticCustomError("content_kinds_requires_fulltext",
                                      "content_kinds requires fulltext=true.")
        return self


class FacetTotals(SearchModel):
    work_items: Annotated[StrictInt, Field(ge=0)] = 0
    artifacts: Annotated[StrictInt, Field(ge=0)] = 0
    transcripts: Annotated[StrictInt, Field(ge=0)] = 0


class ArtifactSearchCoverage(SearchModel):
    embedding: ArtifactEmbeddingCoverage | None = None
    enabled: StrictBool = True
    indexing: ArtifactIndexingStatus = Field(default_factory=lambda: ArtifactIndexingStatus(
        pending=0, failed=0, ready=0, truncated=0,
    ))
    sensitive_content_withheld: Annotated[StrictInt, Field(ge=0)] = 0


class TranscriptSearchCoverage(SearchModel):
    unsegmented_content_omitted: Annotated[StrictInt, Field(ge=0)] = 0
    indexing_incomplete: StrictBool = False


class SearchCoverage(SearchModel):
    artifacts: ArtifactSearchCoverage = Field(default_factory=ArtifactSearchCoverage)
    transcripts: TranscriptSearchCoverage = Field(default_factory=TranscriptSearchCoverage)


class ProjectSearchCoverage(SearchModel):
    project_id: UUID
    project_name: Annotated[str, Field(min_length=1, max_length=120)]
    project_slug: Annotated[str, Field(min_length=1, max_length=100)]
    facet_totals: FacetTotals
    coverage: SearchCoverage
    indexing_incomplete: StrictBool


class SearchHitBase(SearchModel, SearchHitRanking):
    rank: Annotated[StrictInt, Field(ge=1)]
    score_type: ScoreType
    project_id: UUID
    id: UUID
    created_at: datetime
    updated_at: datetime
    score: SearchScore


class WorkFacetHit(SearchHitBase):
    facet: Literal["work_items"] = "work_items"
    work_item: WorkSearchHit | CompactWorkHit


class SearchArtifactMatch(ArtifactSearchMatch):
    snippet: Annotated[str, Field(max_length=1000)] | None = None
    # Blank-query directory browsing has no literal match fields.
    matched_fields: Annotated[list[Literal["metadata", "content"]], Field(max_length=2)]


class SearchArtifactToolMatch(ArtifactToolSearchMatch):
    matched_fields: Annotated[list[Literal["metadata", "content"]], Field(max_length=2)]


class ArtifactFacetHit(SearchHitBase):
    facet: Literal["artifacts"] = "artifacts"
    artifact: SearchArtifactMatch | CompactArtifactMatch


class TranscriptFacetHit(SearchHitBase):
    facet: Literal["transcripts"] = "transcripts"
    transcript: TranscriptRead | CompactTranscriptRead


SearchHit = Annotated[
    WorkFacetHit | ArtifactFacetHit | TranscriptFacetHit, Field(discriminator="facet"),
]


class SearchPage(SearchModel, SearchDisclosure):
    score_type: ScoreType
    total_kind: TotalKind | Literal["mixed"]
    facet_total_kinds: FacetTotalKinds
    facet_score_types: FacetScoreTypes
    semantic: SemanticDisposition = Field(default_factory=SemanticDisposition)
    tag_counts: TagCountPage | None = None
    detail: SearchDetail
    work_rank_scope: Literal["work_items"]
    project_coverage: Annotated[list[ProjectSearchCoverage], Field(min_length=1, max_length=10)]
    search_scope: SearchScope
    term_diagnostics: TermDiagnostics
    items: Annotated[list[SearchHit], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: SearchLimit
    offset: SearchOffset
    facet_totals: FacetTotals
    coverage: SearchCoverage
    indexing_incomplete: StrictBool

    @model_validator(mode="after")
    def coherent_page(self) -> Self:
        if len(self.items) != min(self.limit, max(0, self.total - self.offset)):
            raise ValueError("Search page length does not match total and pagination")
        identities = {(item.facet, item.id) for item in self.items}
        if len(identities) != len(self.items):
            raise ValueError("Search page contains duplicate identities")
        if sum(self.facet_totals.model_dump().values()) != self.total:
            raise ValueError("Search facet totals do not match total")
        for facet, total in self.facet_totals.model_dump().items():
            if sum(item.facet == facet for item in self.items) > total:
                raise ValueError("Search items exceed their facet total")
        return self


class ArtifactToolFacetHit(SearchHitBase):
    facet: Literal["artifacts"] = "artifacts"
    artifact: SearchArtifactToolMatch | CompactArtifactMatch


SearchToolHit = Annotated[
    WorkFacetHit | ArtifactToolFacetHit | TranscriptFacetHit, Field(discriminator="facet"),
]


class SearchToolPage(SearchModel, SearchDisclosure):
    score_type: ScoreType
    total_kind: TotalKind | Literal["mixed"]
    facet_total_kinds: FacetTotalKinds
    facet_score_types: FacetScoreTypes
    semantic: SemanticDisposition = Field(default_factory=SemanticDisposition)
    tag_counts: TagCountPage | None = None
    detail: SearchDetail
    work_rank_scope: Literal["work_items"]
    project_coverage: Annotated[list[ProjectSearchCoverage], Field(min_length=1, max_length=10)]
    search_scope: SearchScope
    term_diagnostics: TermDiagnostics
    items: Annotated[list[SearchToolHit], Field(max_length=100)]
    total: Annotated[StrictInt, Field(ge=0)]
    limit: SearchLimit
    offset: SearchOffset
    facet_totals: FacetTotals
    coverage: SearchCoverage
    indexing_incomplete: StrictBool
