"""One project search, with optional facet filters and explicit group ordering."""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from mnemonic_api.artifact_search_schemas import ArtifactIndexingStatus, ArtifactSearchMatch
from mnemonic_api.schemas import (
    APIModel,
    ClientName,
    ExternalURL,
    SessionID,
    Tag,
    WorkSearchHit,
)
from mnemonic_api.transcript_schemas import TranscriptRead, TranscriptStatus

SearchFacet = Literal["work_items", "artifacts", "transcripts"]


def _default_facets() -> list[SearchFacet]:
    return ["work_items", "artifacts", "transcripts"]


class SearchSort(APIModel):
    by: Literal["relevance", "created_at", "updated_at", "priority"] = "relevance"
    direction: Literal["asc", "desc"] = "desc"


class FacetOrder(APIModel):
    facet: SearchFacet
    sort: SearchSort | None = None


class WorkSearchFilters(APIModel):
    status: Literal[
        "pending", "active", "to-review", "dropped", "deferred", "done",
        "wont-do", "promoted", "all",
    ] = "all"
    tag: Tag | None = None
    source_client: ClientName | None = None
    source_session_id: SessionID | None = None
    external_url: ExternalURL | None = None
    duplicate_scope: Literal["canonical", "aliases", "all"] = "canonical"
    canonical_work_item_id: UUID | None = None
    semantic: bool = False

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str | None) -> str | None:
        return value.lower() if value else value

    @model_validator(mode="after")
    def root_requires_alias_scope(self) -> Self:
        if self.canonical_work_item_id is not None and self.duplicate_scope == "canonical":
            raise ValueError("canonical_work_item_id requires duplicate_scope=aliases or all")
        return self


class ArtifactSearchFilters(APIModel):
    artifact_id: UUID | None = None
    work_item_id: UUID | None = None
    include_deleted: bool = False
    sensitive: bool | None = None
    mime_type: str | None = Field(default=None, min_length=1, max_length=255)
    created_by_agent_session_id: SessionID | None = None


class TranscriptSearchFilters(APIModel):
    work_item_id: UUID | None = None
    agent_session_id: SessionID | None = None
    client: ClientName | None = None
    kind: Literal["primary", "subagent"] | None = None
    status: TranscriptStatus | None = None


class SearchFilters(APIModel):
    work_items: WorkSearchFilters = Field(default_factory=WorkSearchFilters)
    artifacts: ArtifactSearchFilters = Field(default_factory=ArtifactSearchFilters)
    transcripts: TranscriptSearchFilters = Field(default_factory=TranscriptSearchFilters)


class SearchRequest(APIModel):
    q: str = Field(default="", max_length=1000)
    facets: list[SearchFacet] = Field(
        default_factory=_default_facets, min_length=1,
        max_length=3,
    )
    fulltext: bool = False
    filters: SearchFilters = Field(default_factory=SearchFilters)
    sort: SearchSort = Field(default_factory=SearchSort)
    facet_order: list[FacetOrder] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("q")
    @classmethod
    def literal_query(cls, value: str) -> str:
        value.encode("utf-8")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Search text cannot contain controls")
        return value.strip()

    @model_validator(mode="after")
    def valid_facets(self) -> Self:
        if len(set(self.facets)) != len(self.facets):
            raise ValueError("facets cannot contain duplicates")
        ordered = [group.facet for group in self.facet_order]
        if len(set(ordered)) != len(ordered) or not set(ordered).issubset(self.facets):
            raise ValueError("facet_order must contain unique selected facets")
        if self.sort.by == "priority" and self.facets != ["work_items"]:
            raise ValueError("Global priority sorting requires only the work_items facet")
        for group in self.facet_order:
            if group.sort and group.sort.by == "priority" and group.facet != "work_items":
                raise ValueError("priority sorting requires the work_items facet")
        if self.filters.work_items.semantic and (not self.q or "work_items" not in self.facets):
            raise ValueError("semantic search requires q and the work_items facet")
        return self


class SearchHitBase(APIModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    score: float = Field(ge=0, allow_inf_nan=False)


class WorkFacetHit(SearchHitBase):
    facet: Literal["work_items"] = "work_items"
    work_item: WorkSearchHit


class ArtifactFacetHit(SearchHitBase):
    facet: Literal["artifacts"] = "artifacts"
    artifact: ArtifactSearchMatch


class TranscriptFacetHit(SearchHitBase):
    facet: Literal["transcripts"] = "transcripts"
    transcript: TranscriptRead


SearchHit = Annotated[
    WorkFacetHit | ArtifactFacetHit | TranscriptFacetHit, Field(discriminator="facet")
]


class FacetTotals(APIModel):
    work_items: int = Field(default=0, ge=0)
    artifacts: int = Field(default=0, ge=0)
    transcripts: int = Field(default=0, ge=0)


class ArtifactSearchCoverage(APIModel):
    enabled: bool = True
    indexing: ArtifactIndexingStatus = Field(default_factory=ArtifactIndexingStatus)
    sensitive_content_withheld: int = Field(default=0, ge=0)


class TranscriptSearchCoverage(APIModel):
    indexing_incomplete: bool = False


class SearchCoverage(APIModel):
    artifacts: ArtifactSearchCoverage = Field(default_factory=ArtifactSearchCoverage)
    transcripts: TranscriptSearchCoverage = Field(default_factory=TranscriptSearchCoverage)


class SearchPage(APIModel):
    items: list[SearchHit]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    facet_totals: FacetTotals
    coverage: SearchCoverage
    indexing_incomplete: bool
