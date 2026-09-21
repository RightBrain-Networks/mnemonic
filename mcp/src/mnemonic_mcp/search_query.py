"""Public query intent and bounded work evidence, independent of the search engine."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic.experimental.missing_sentinel import MISSING
from pydantic_core import PydanticCustomError

from .input_schema import InputValidationError
from .validation_rules import VALIDATION_RULES

QueryMode = Literal["terms", "phrase", "literal"]
WorkField = Literal["title", "summary", "tags", "checkpoint", "identifiers", "provenance"]
WORK_FIELDS: tuple[WorkField, ...] = (
    "title", "summary", "tags", "checkpoint", "identifiers", "provenance",
)


def normalized_work_fields(fields: list[WorkField]) -> list[WorkField]:
    return [field for field in WORK_FIELDS if field in fields]


WorkFields = Annotated[list[WorkField], Field(min_length=1, max_length=6),
                       AfterValidator(normalized_work_fields)]


def constrained_query(query: str | None, mode: QueryMode) -> bool:
    return mode != "terms" or '"' in (query or "")


def validate_query(query: str | None, mode: QueryMode, *, semantic: bool = False,
                   fields: list[WorkField] | None = None) -> None:
    text = (query or "").strip()
    if mode == "literal" and not text:
        raise PydanticCustomError("exact_query_requires_text", "Exact search requires q")
    if mode == "terms" and text.count('"') % 2:
        raise PydanticCustomError("unclosed_query_phrase", "Close each quoted phrase")
    phrases = [text] if mode == "phrase" else text.split('"')[1::2] if mode == "terms" else []
    if any(not any(char.isalnum() for char in phrase) for phrase in phrases):
        raise PydanticCustomError("query_phrase_requires_terms", "A phrase needs searchable words")
    if semantic and constrained_query(text, mode):
        raise PydanticCustomError("semantic_requires_unconstrained_work_query",
                                  "Semantic search requires unconstrained terms")
    if semantic and fields is not None and fields != list(WORK_FIELDS):
        raise PydanticCustomError("semantic_requires_all_work_fields",
                                  "Semantic search requires all work fields")


class WorkMatchExcerpt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: WorkField
    text: Annotated[str, Field(max_length=320)]
    matched_member_id: UUID
    checkpoint_id: UUID | None = None
    match_type: Literal["lexical", "substring", "phrase", "literal"]


class WorkMatchEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excerpts_truncated: StrictBool
    evidence_mode: Literal["lexical", "semantic", "browse"]
    matched_fields: Annotated[list[WorkField], Field(max_length=6)]
    excerpts: Annotated[list[WorkMatchExcerpt], Field(max_length=3)]

    @model_validator(mode="after")
    def coherent_evidence(self) -> Self:
        if self.matched_fields != normalized_work_fields(self.matched_fields):
            raise ValueError("Matched work fields must be unique and canonically ordered")
        fields = [item.field for item in self.excerpts]
        if fields != normalized_work_fields(fields) or not set(fields) <= set(self.matched_fields):
            raise ValueError("Excerpts must identify unique matching fields")
        if sum(len(item.text) for item in self.excerpts) > 320:
            raise ValueError("Work excerpts exceed the shared character budget")
        if self.evidence_mode != "lexical" and (
            self.matched_fields or self.excerpts or self.excerpts_truncated
        ):
            raise ValueError("Browse and semantic evidence cannot claim lexical matches")
        if self.evidence_mode == "lexical" and not self.matched_fields:
            raise ValueError("Lexical evidence requires a matching field")
        if any(item.field in {"title", "summary"} and item.checkpoint_id is not None
               or item.field in {"tags", "checkpoint", "provenance"}
               and item.checkpoint_id is None for item in self.excerpts):
            raise ValueError("Excerpt checkpoint provenance disagrees with its field")
        if len({item.checkpoint_id for item in self.excerpts if item.checkpoint_id}) > 1:
            raise ValueError("Work evidence must refer to one matching checkpoint")
        return self


def evidence_matches(evidence: WorkMatchEvidence, member_id: UUID, query: str | None,
                     mode: QueryMode, fields: list[WorkField], semantic: bool = False) -> bool:
    expected = "browse" if not (query or "").strip() else "semantic" if semantic else "lexical"
    allowed = {expected, "lexical"} if expected == "semantic" else {expected}
    return (evidence.evidence_mode in allowed and set(evidence.matched_fields) <= set(fields)
            and all(item.model_fields_set == set(type(item).model_fields)
                    and item.matched_member_id == member_id
                    and (mode != "literal" or item.match_type == "literal")
                    and (mode != "phrase" or item.match_type == "phrase")
                    for item in evidence.excerpts))


def validate_tool_query(query: str | None, mode: QueryMode, *, semantic: bool = False,
                        fields: list[WorkField] | None = None) -> None:
    try:
        validate_query(query, mode, semantic=semantic, fields=fields)
    except PydanticCustomError as error:
        location, message = VALIDATION_RULES[error.type]
        raise InputValidationError(f"Mnemonic rejected the input. Check: {location} "
                        f"({error.type}). {message}") from None


def content_search_query(query: str | MISSING, q: str | MISSING) -> str:
    if query is MISSING and q is MISSING:
        raise InputValidationError(
            "Mnemonic rejected the input. Check: query (missing). "
            "Supply query or its q alias."
        )
    if query is not MISSING and q is not MISSING:
        raise InputValidationError(
            "Mnemonic rejected the input. Supply exactly one of query or q, not both."
        )
    if query is not MISSING:
        return query
    assert q is not MISSING
    return q
