"""Strict, ephemeral external comparison contracts; no provider or work identity."""

import re
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

from mnemonic_api.external_references import ExternalState, ExternalURL
from mnemonic_api.title_normalization import nfkc_unicode_15_1

ExternalSignal = Literal["exact_title", "lexical", "semantic"]
ExternalScope = Literal["hybrid", "lexical", "unavailable"]
SIGNAL_ORDER = ("exact_title", "lexical", "semantic")


def candidate_text(value: str) -> str:
    if "\x00" in value:
        raise ValueError("Candidate text cannot contain NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Candidate text must contain valid Unicode") from None
    return value


def candidate_title(value: str) -> str:
    candidate_text(value)
    if any(
        ord(char) < 32
        or 127 <= ord(char) <= 159
        or char
        in ("\u061c\u200e\u200f\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
        for char in value
    ):
        raise ValueError("Candidate title must be single-line plain text")
    if not value.strip():
        raise ValueError("Candidate title must not be blank")
    return value


CandidateTitle = Annotated[
    StrictStr, Field(min_length=1, max_length=500), AfterValidator(candidate_title)
]
CandidateBody = Annotated[StrictStr, Field(max_length=20_000), AfterValidator(candidate_text)]


class ExternalCandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: ExternalURL
    title: CandidateTitle
    state: ExternalState


class ExternalDuplicateCandidate(ExternalCandidateReference):
    body: CandidateBody


class ExternalDuplicateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: Annotated[StrictInt, Field(ge=1, le=10)]
    signals: list[ExternalSignal] = Field(min_length=1, max_length=3)
    reference: ExternalCandidateReference

    @model_validator(mode="after")
    def coherent_signals(self) -> Self:
        if self.signals != sorted(set(self.signals), key=SIGNAL_ORDER.index):
            raise ValueError("External signals must be unique and in canonical order")
        return self


def unique_external_candidates(
    candidates: list[ExternalDuplicateCandidate],
) -> list[ExternalDuplicateCandidate]:
    urls = [candidate.url for candidate in candidates]
    if len(urls) != len(set(urls)):
        raise ValueError("External candidate URLs must be unique")
    return candidates


ExternalCandidates = Annotated[
    list[ExternalDuplicateCandidate],
    Field(max_length=64),
    AfterValidator(unique_external_candidates),
]


class ExternalSuggestionFields(BaseModel):
    external_items: list[ExternalDuplicateSuggestion] | SkipJsonSchema[None] = Field(
        default=None, max_length=10, exclude_if=lambda value: value is None
    )
    external_candidate_count: Annotated[StrictInt, Field(ge=1, le=64)] | SkipJsonSchema[None] = (
        Field(default=None, exclude_if=lambda value: value is None)
    )
    external_scope: ExternalScope | SkipJsonSchema[None] = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def coherent_external_extension(self) -> Self:
        fields = {"external_items", "external_candidate_count", "external_scope"}
        present = fields & self.model_fields_set
        if not present:
            return self
        if present != fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("External extension fields must occur together and cannot be null")
        assert self.external_items is not None
        assert self.external_candidate_count is not None
        self._require_external_items()
        return self

    def _require_external_items(self) -> None:
        items = self.external_items or []
        if len(items) > (self.external_candidate_count or 0):
            raise ValueError("External results exceed their candidate population")
        if [item.rank for item in items] != list(range(1, len(items) + 1)):
            raise ValueError("External ranks must be contiguous")
        urls = [item.reference.url for item in items]
        if len(urls) != len(set(urls)):
            raise ValueError("External result URLs must be unique")
        if self.external_scope == "unavailable" and items:
            raise ValueError("Unavailable comparison cannot return external items")
        if self.external_scope != "hybrid" and any("semantic" in x.signals for x in items):
            raise ValueError("Only hybrid external comparison can carry semantic evidence")
        exact = ["exact_title" in item.signals for item in items]
        if exact != sorted(exact, reverse=True):
            raise ValueError("External exact matches must form the prefix")


def duplicate_title_key(value: str) -> str:
    """Mirror the shipped SQL C-collation NFKC/ASCII whitespace/lower key for guards."""
    value = nfkc_unicode_15_1(value)
    value = re.sub(r"[ \t\n\r\f\v]+", " ", value.strip(" \t\n\r\f\v"))
    mapping = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    return value.translate(mapping)


def require_external_correspondence(
    page: ExternalSuggestionFields,
    candidates: list[ExternalDuplicateCandidate],
    title: str,
    limit: int,
) -> None:
    if not candidates:
        if page.external_scope is not None:
            raise ValueError("Unsolicited external comparison")
        return
    if page.external_candidate_count != len(candidates) or page.external_items is None:
        raise ValueError("External candidate count or presence differs from the request")
    if len(page.external_items) > limit:
        raise ValueError("External results exceed the requested limit")
    by_url = {candidate.url: candidate for candidate in candidates}
    for item in page.external_items:
        candidate = by_url.get(item.reference.url)
        expected = candidate.model_dump(exclude={"body"}) if candidate is not None else None
        if item.reference.model_dump() != expected:
            raise ValueError("External result does not identify a submitted candidate")
    if page.external_scope != "unavailable":
        _require_external_exact_prefix(page.external_items, candidates, title, limit)


def _require_external_exact_prefix(
    items: list[ExternalDuplicateSuggestion],
    candidates: list[ExternalDuplicateCandidate],
    title: str,
    limit: int,
) -> None:
    key = duplicate_title_key(title)
    expected = sorted(c.url for c in candidates if duplicate_title_key(c.title) == key)[:limit]
    actual = [item.reference.url for item in items if "exact_title" in item.signals]
    if actual != expected:
        raise ValueError("External exact prefix differs from the submitted titles")
