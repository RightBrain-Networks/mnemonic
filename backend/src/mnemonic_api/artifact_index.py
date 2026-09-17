"""A single rebuildable index, with optional private disk storage for transcripts.

The database snapshot supplies the cache identity and documents. Only one index
is cached per API process; changing project or corpus discards it. A short lock
prevents simultaneous rebuilds from multiplying memory and CPU consumption.
"""

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol, cast

import tantivy

from mnemonic_api.errors import ApplicationError
from mnemonic_api.search_exploration import wants_diagnostics
from mnemonic_api.search_exploration_schemas import DiagnosticsMode
from mnemonic_api.search_index_storage import SearchIndexStorage
from mnemonic_api.search_query import QueryIntent, QueryMode, analyzer, parse_query
from mnemonic_api.search_snippets import supporting_snippet


@dataclass(frozen=True)
class SearchDocument:
    identity: str
    metadata: str
    content: str = ""
    metadata_parts: tuple[str, ...] | None = None
    content_parts: tuple[str, ...] | None = None


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


class _CountedSearchResult(Protocol):
    # The pinned Tantivy wheel exposes count but does not declare it in its stubs.
    @property
    def count(self) -> int: ...


def _analyzer(*, fold_accents: bool = True) -> tantivy.TextAnalyzer:
    return analyzer(fold_accents=fold_accents)


def literal_terms(query: str, *, fold_accents: bool = True) -> list[str]:
    """Index terms select defaults; diagnostic labels preserve source-specific accents."""
    return list(dict.fromkeys(_analyzer(fold_accents=fold_accents).analyze(query)))


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


def _phrase(schema: tantivy.Schema, field: str, text: str) -> tantivy.Query:
    tokens = analyzer().analyze(text)
    return (_term(schema, field, tokens[0]) if len(tokens) == 1 else
            tantivy.Query.phrase_query(schema, field, list(tokens)))


def _intent_query(schema: tantivy.Schema, intent: QueryIntent, fulltext: bool) -> tantivy.Query:
    fields = ["metadata", "content"] if fulltext else ["metadata"]
    clauses = [(tantivy.Occur.Must, _literal_query(schema, intent.tokens, fulltext))]
    for phrase in intent.phrases:
        clauses.append((tantivy.Occur.Must, tantivy.Query.boolean_query([
            (tantivy.Occur.Should, _phrase(schema, field, phrase)) for field in fields
        ])))
    return tantivy.Query.boolean_query(clauses)


def _evidence_query(schema: tantivy.Schema, intent: QueryIntent, field: str) -> tantivy.Query:
    clauses = [(tantivy.Occur.Should, _term(schema, field, token))
               for token in analyzer().analyze(intent.unquoted)]
    clauses.extend((tantivy.Occur.Should, _phrase(schema, field, phrase))
                   for phrase in intent.phrases)
    return tantivy.Query.boolean_query(clauses)


def _identities(searcher: tantivy.Searcher, query: tantivy.Query, count: int) -> set[str]:
    return {
        cast(str, searcher.doc(address).get_first("identity"))
        for _, address in searcher.search(query, limit=count).hits
    }


class ArtifactSearchIndex:
    def __init__(self, directory: Path | None = None) -> None:
        self._storage = SearchIndexStorage(directory) if directory is not None else None
        self._lock = threading.Lock()
        self._key: str | None = None
        self._index: tantivy.Index | None = None
        self._schema = _schema()
        self._analyzer = _analyzer()

    def _build(self, documents: Iterable[SearchDocument]) -> tantivy.Index:
        path = self._storage.prepare() if self._storage is not None else None
        index = tantivy.Index(self._schema, path=path, reuse=False)
        index.config_reader(reload_policy="Manual")
        index.register_tokenizer("artifact", self._analyzer)
        writer = index.writer(heap_size=15_000_000, num_threads=1)
        try:
            for document in documents:
                writer.add_document(tantivy.Document(
                    identity=document.identity,
                    metadata=list(document.metadata_parts) if document.metadata_parts is not None
                    else document.metadata,
                    content=list(document.content_parts) if document.content_parts is not None
                    else document.content
                ))
            writer.commit()
        except BaseException:
            writer.rollback()
            raise
        finally:
            # Finish writer threads before exposing or removing disk files.
            writer.wait_merging_threads()
        index.reload()
        return index

    def search(
        self, key: str, documents: Callable[[], Iterable[SearchDocument]],
        *, query: str, fulltext: bool, count: int, query_mode: QueryMode = "terms",
        literal_matches: Callable[[], list[SearchHit]] | None = None,
        diagnostics: DiagnosticsMode = "on_empty",
    ) -> IndexSearchResult:
        intent = parse_query(query, query_mode)
        if query_mode == "literal" and literal_matches is None:
            raise ValueError("Literal search requires an access-checked exact matcher")
        exact_hits = literal_matches() if query_mode == "literal" and literal_matches else []
        if not intent.tokens or (query_mode == "literal" and not wants_diagnostics(
            diagnostics, query, len(exact_hits),
        )):
            return IndexSearchResult(exact_hits, None)
        if not self._lock.acquire(timeout=0.25):
            raise ApplicationError(
                503, "artifact_search_busy", "Artifact search is busy. Try this read again shortly."
            )
        try:
            result = self._search_locked(key, documents, intent, fulltext, count)
            if query_mode == "literal":
                return IndexSearchResult(exact_hits, result.searcher)
            return result
        except (OSError, ValueError):
            if self._storage is None:
                raise
            raise _storage_error() from None
        finally:
            self._lock.release()

    def _search_locked(
        self, key: str, documents: Callable[[], Iterable[SearchDocument]],
        intent: QueryIntent, fulltext: bool, count: int,
    ) -> IndexSearchResult:
        if not count:
            self._clear_locked()
            return IndexSearchResult([], None)
        if self._key != key or self._index is None:
            # Discard the old corpus before building: no overlapping cached projects.
            self._index = None
            self._key = None
            self._index = self._load_or_build(key, documents)
            self._key = key
        try:
            return self._query(intent, fulltext, count)
        except (OSError, ValueError):
            if self._storage is None:
                raise
            # Some segment failures are detected only when querying. Rebuild
            # once from the current database snapshot, then fail without looping.
            self._clear_locked()
            self._index = self._load_or_build(key, documents)
            self._key = key
            try:
                return self._query(intent, fulltext, count)
            except (OSError, ValueError):
                self._clear_locked()
                raise

    def _query(self, intent: QueryIntent, fulltext: bool, count: int) -> IndexSearchResult:
        assert self._index is not None
        searcher = self._index.searcher()
        if intent.mode == "literal":
            return IndexSearchResult([], searcher)
        matches = searcher.search(_intent_query(self._schema, intent, fulltext), limit=count)
        metadata_ids = _identities(
            searcher, _evidence_query(self._schema, intent, "metadata"), count,
        )
        content_ids = (
            _identities(searcher, _evidence_query(self._schema, intent, "content"), count)
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

    def _load_or_build(self, key: str, documents: Callable[[], Iterable[SearchDocument]]):
        # Generation-isolated disk layout invalidates the old flat derived
        # cache; PostgreSQL documents remain the source of truth.
        storage_key = f"{version('tantivy')}:schema1:generation2:{key}"
        if self._storage is not None and self._storage.reusable(storage_key):
            try:
                # The constructor's reuse=True also creates an empty index
                # when metadata is missing. Open only an existing snapshot.
                index = tantivy.Index.open(self._storage.path)
                if index.schema != self._schema:
                    raise ValueError("Cached search schema differs")
                index.config_reader(reload_policy="Manual")
                index.register_tokenizer("artifact", self._analyzer)
                return index
            except (OSError, ValueError):
                # A damaged/incompatible derived index can be reconstructed.
                index = None
                self._storage.discard()
        try:
            index = self._build(documents())
            if self._storage is not None:
                self._storage.publish(storage_key)
            return index
        except BaseException:
            if self._storage is not None:
                self._storage.discard()
            raise

    def start(self) -> None:
        if not self._lock.acquire(timeout=0.25):
            raise ApplicationError(503, "transcript_search_busy", "Transcript search is busy.")
        try:
            if self._storage is not None:
                try:
                    self._storage.open()
                except OSError:
                    raise _storage_error() from None
        finally:
            self._lock.release()

    def _clear_locked(self) -> None:
        self._index = None
        self._key = None
        if self._storage is not None:
            self._storage.open()
            self._storage.discard()

    def clear(self) -> None:
        if not self._lock.acquire(timeout=0.25):
            raise ApplicationError(503, "transcript_search_busy",
                "Transcript search is busy. Retry the preserved rebuild request shortly.")
        try:
            self._clear_locked()
        except OSError:
            raise _storage_error() from None
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._lock:
            self._index = None
            self._key = None
            if self._storage is not None:
                self._storage.close()

    def term_counts(
        self, terms: list[str], fulltext: bool, searcher: tantivy.Searcher | None,
    ) -> dict[str, int]:
        # The retained searcher pins this request's filtered, access-checked
        # corpus even if another request replaces the process-wide cache.
        return {
            term: cast(_CountedSearchResult, searcher.search(
                _literal_query(self._schema, self._analyzer.analyze(term), fulltext),
                limit=1, count=True,
            )).count
            if searcher is not None else 0
            for term in terms
        }

    def snippet(self, text: str, query: str, searcher: tantivy.Searcher) -> str | None:
        """Prefer supporting evidence while the caller retains its immutable searcher."""
        return supporting_snippet(text, query, self._analyzer.analyze)


def _storage_error() -> ApplicationError:
    return ApplicationError(503, "transcript_index_unavailable",
        "Transcript index storage is unavailable. Check its mount, permissions, "
        "and whether another API process is using the directory.")
