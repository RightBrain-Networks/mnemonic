"""A single rebuildable RAM index, never an additional copy on the filesystem.

The database snapshot supplies the cache identity and documents. Only one index
is cached per API process; changing project or corpus discards it. A short lock
prevents simultaneous rebuilds from multiplying memory and CPU consumption.
"""

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import cast

import tantivy

from mnemonic_api.errors import ApplicationError


@dataclass(frozen=True)
class SearchDocument:
    identity: str
    metadata: str
    content: str = ""


@dataclass(frozen=True)
class SearchHit:
    identity: str
    score: float
    metadata: bool
    content: bool


@dataclass(frozen=True)
class IndexSearchResult:
    hits: list[SearchHit]
    searcher: tantivy.Searcher | None


def _analyzer() -> tantivy.TextAnalyzer:
    return (
        tantivy.TextAnalyzerBuilder(tantivy.Tokenizer.simple())
        .filter(tantivy.Filter.remove_long(200))
        .filter(tantivy.Filter.lowercase())
        .filter(tantivy.Filter.ascii_fold())
        .build()
    )


def _schema() -> tantivy.Schema:
    builder = tantivy.SchemaBuilder()
    builder.add_text_field("identity", stored=True, tokenizer_name="raw")
    builder.add_text_field("metadata", tokenizer_name="artifact")
    builder.add_text_field("content", tokenizer_name="artifact")
    return builder.build()


def _term(schema: tantivy.Schema, field: str, token: str) -> tantivy.Query:
    query = tantivy.Query.term_query(schema, field, token)
    return tantivy.Query.boost_query(query, 2.0) if field == "metadata" else query


def _any_terms(schema: tantivy.Schema, field: str, tokens: list[str]) -> tantivy.Query:
    return tantivy.Query.boolean_query(
        [(tantivy.Occur.Should, _term(schema, field, token)) for token in tokens]
    )


def _literal_query(schema: tantivy.Schema, tokens: list[str], fulltext: bool) -> tantivy.Query:
    fields = ["metadata", "content"] if fulltext else ["metadata"]
    return tantivy.Query.boolean_query([
        (tantivy.Occur.Must, tantivy.Query.boolean_query([
            (tantivy.Occur.Should, _term(schema, field, token)) for field in fields
        ]))
        for token in tokens
    ])


def _identities(searcher: tantivy.Searcher, query: tantivy.Query, count: int) -> set[str]:
    return {
        cast(str, searcher.doc(address).get_first("identity"))
        for _, address in searcher.search(query, limit=count).hits
    }


class ArtifactSearchIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: str | None = None
        self._index: tantivy.Index | None = None
        self._schema = _schema()
        self._analyzer = _analyzer()

    def _build(self, documents: Iterable[SearchDocument]) -> tantivy.Index:
        index = tantivy.Index(self._schema)
        index.register_tokenizer("artifact", self._analyzer)
        writer = index.writer(heap_size=15_000_000, num_threads=1)
        for document in documents:
            writer.add_document(tantivy.Document(
                identity=document.identity, metadata=document.metadata, content=document.content
            ))
        writer.commit()
        # Release the writer's heap/worker before exposing the immutable index.
        del writer
        index.reload()
        return index

    def search(
        self, key: str, documents: Callable[[], Iterable[SearchDocument]],
        *, query: str, fulltext: bool, count: int,
    ) -> IndexSearchResult:
        tokens = list(dict.fromkeys(self._analyzer.analyze(query)))
        if not tokens:
            return IndexSearchResult([], None)
        if not self._lock.acquire(timeout=0.25):
            raise ApplicationError(
                503, "artifact_search_busy", "Artifact search is busy. Try this read again shortly."
            )
        try:
            return self._search_locked(key, documents, tokens, fulltext, count)
        finally:
            self._lock.release()

    def _search_locked(
        self, key: str, documents: Callable[[], Iterable[SearchDocument]],
        tokens: list[str], fulltext: bool, count: int,
    ) -> IndexSearchResult:
        if not count:
            self._index = None
            self._key = None
            return IndexSearchResult([], None)
        if self._key != key or self._index is None:
            # Discard the old corpus before building: no overlapping cached projects.
            self._index = None
            self._key = None
            self._index = self._build(documents())
            self._key = key
        searcher = self._index.searcher()
        matches = searcher.search(_literal_query(self._schema, tokens, fulltext), limit=count)
        metadata_ids = _identities(searcher, _any_terms(self._schema, "metadata", tokens), count)
        content_ids = (
            _identities(searcher, _any_terms(self._schema, "content", tokens), count)
            if fulltext else set()
        )
        hits = [
            SearchHit(
                identity=identity,
                score=float(score),
                metadata=identity in metadata_ids,
                content=identity in content_ids,
            )
            for score, address in matches.hits
            for identity in [cast(str, searcher.doc(address).get_first("identity"))]
        ]
        return IndexSearchResult(
            sorted(hits, key=lambda hit: (-hit.score, hit.identity)), searcher
        )

    def snippet(self, text: str, query: str, searcher: tantivy.Searcher) -> str:
        """Use Tantivy's plain fragment, never its HTML renderer."""
        tokens = list(dict.fromkeys(self._analyzer.analyze(query)))
        # Keep the query's immutable searcher alive while rendering this page,
        # even if another request replaces the single cached project index.
        generator = tantivy.SnippetGenerator.create(
            searcher, _any_terms(self._schema, "content", tokens), self._schema, "content"
        )
        generator.set_max_num_chars(320)
        return generator.snippet_from_doc(tantivy.Document(content=text)).fragment()[:1000]
