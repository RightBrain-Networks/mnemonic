"""Small explicit search language: conjunctions, quoted phrases, and literal text."""

from dataclasses import dataclass
from typing import Literal

import tantivy

from mnemonic_api.validation_rules import validation_rule

QueryMode = Literal["terms", "phrase", "literal"]


def analyzer(*, fold_accents: bool = True) -> tantivy.TextAnalyzer:
    builder = (tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
               .filter(tantivy.Filter.remove_long(200)).filter(tantivy.Filter.lowercase()))
    if fold_accents:
        builder = builder.filter(tantivy.Filter.ascii_fold())
    return builder.build()


@dataclass(frozen=True)
class QueryIntent:
    text: str
    mode: QueryMode
    unquoted: str
    phrases: tuple[str, ...]

    @property
    def tokens(self) -> list[str]:
        return list(dict.fromkeys(analyzer().analyze(self.text)))

    @property
    def constrained(self) -> bool:
        return self.mode != "terms" or bool(self.phrases)


def parse_query(query: str | None, mode: QueryMode = "terms") -> QueryIntent:
    text = (query or "").strip()
    if mode == "literal":
        if not text:
            raise validation_rule("exact_query_requires_text")
        return QueryIntent(text, mode, "", ())
    parts = text.split('"') if mode == "terms" else ["", text, ""]
    if len(parts) % 2 == 0:
        raise validation_rule("unclosed_query_phrase")
    phrases = tuple(parts[1::2])
    if any(not analyzer().analyze(phrase) for phrase in phrases):
        raise validation_rule("query_phrase_requires_terms")
    return QueryIntent(text, mode, " ".join(parts[::2]), phrases)
