"""Per-term counts distinguish an empty conjunction from absent indexed terms."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

SearchFacet = Literal["work_items", "artifacts", "transcripts"]
MatchCount = Annotated[StrictInt, Field(ge=0)]


class DiagnosticModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TermMatchCounts(DiagnosticModel):
    # Null means this source was not searched; zero is an observed count.
    work_items: MatchCount | None = None
    artifacts: MatchCount | None = None
    transcripts: MatchCount | None = None


class TermDiagnostic(DiagnosticModel):
    term: Annotated[str, Field(min_length=1, max_length=1000)]
    matches: TermMatchCounts


TermDiagnostics = Annotated[list[TermDiagnostic], Field(max_length=500)]


class SearchScope(DiagnosticModel):
    searched_facets: Annotated[list[SearchFacet], Field(max_length=3)]
    transcripts: Literal["searched", "omitted_by_default", "not_selected"]
    transcript_search_hint: Literal[
        'Agent sessions can be searched by explicitly including "transcripts" in facets '
        'or calling search_transcript_contents.'
    ] = (
        'Agent sessions can be searched by explicitly including "transcripts" in facets '
        'or calling search_transcript_contents.'
    )


def diagnostics_match(
    diagnostics: list[TermDiagnostic], total: int, searched: list[SearchFacet],
) -> bool:
    if total and diagnostics or len({item.term for item in diagnostics}) != len(diagnostics):
        return False
    return all(
        (count is not None) == (facet in searched)
        for item in diagnostics for facet, count in item.matches.model_dump().items()
    )
