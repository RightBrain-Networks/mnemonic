"""Exact transcript content uses published segments, never flattened legacy bodies."""

from collections.abc import Callable, Iterator, Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import SearchDocument, SearchHit, literal_terms
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript
from mnemonic_api.search_query import QueryIntent, analyzer
from mnemonic_api.search_snippets import phrase_span, supporting_snippet
from mnemonic_api.transcript_normalization import ContentKind
from mnemonic_api.transcript_normalized_storage import SEGMENTS
from mnemonic_api.transcript_segment_search import segment_scope


def _published(records: Sequence[Transcript]):
    return (SEGMENTS.join(Transcript.__table__,
        (Transcript.id == SEGMENTS.c.transcript_id)
        & (Transcript.normalized_revision == SEGMENTS.c.revision)),
        (Transcript.id.in_([row.id for row in records])) & (Transcript.status == "ready"))


def preflight_segments(database: Session, records: Sequence[Transcript],
                       kinds: Sequence[str] | None, maximum: int) -> None:
    joined, scope = _published(records)
    if kinds:
        scope &= SEGMENTS.c.content_kind.in_(kinds)
    size = database.scalar(select(func.sum(func.octet_length(SEGMENTS.c.text) + 2))
        .select_from(joined).where(scope)) or 0
    if size > maximum:
        raise ApplicationError(503, "transcript_search_capacity",
                               "Selected transcript content exceeds search capacity.")


def exact_documents(database: Session, records: Sequence[Transcript], fulltext: bool,
                    kinds: Sequence[str] | None,
                    metadata: Callable[[Transcript], tuple[str, ...]]) -> Iterator[SearchDocument]:
    for record in records:
        parts = ()
        if fulltext and record.status == "ready" and record.normalized_revision is not None:
            rows = database.scalars(select(SEGMENTS.c.text).where(segment_scope(record, kinds))
                .order_by(SEGMENTS.c.ordinal).execution_options(yield_per=1))
            try:
                parts = tuple(rows)
            finally:
                rows.close()
        fields = metadata(record)
        yield SearchDocument(str(record.id), "\n".join(fields),
                             metadata_parts=fields, content_parts=parts)


def literal_hits(database: Session, records: Sequence[Transcript], query: str, fulltext: bool,
                 kinds: Sequence[str] | None,
                 metadata: Callable[[Transcript], tuple[str, ...]]) -> list[SearchHit]:
    metadata_ids = {record.id for record in records
                    if any(query in part for part in metadata(record))}
    content_ids: set[UUID] = set()
    if fulltext:
        joined, scope = _published(records)
        if kinds:
            scope &= SEGMENTS.c.content_kind.in_(kinds)
        content_ids = set(database.scalars(select(SEGMENTS.c.transcript_id).distinct()
            .select_from(joined).where(scope, SEGMENTS.c.text.contains(query, autoescape=True))))
    hits = [SearchHit(str(identity), float(2 * (identity in metadata_ids)
                                          + (identity in content_ids)),
                      identity in metadata_ids, identity in content_ids)
            for identity in metadata_ids | content_ids]
    return sorted(hits, key=lambda hit: (-hit.score, hit.identity))


def omitted_legacy(records: Sequence[Transcript], fulltext: bool, intent: QueryIntent) -> int:
    return sum(record.status == "ready" and record.normalized_revision is None for record in records
               ) if fulltext and intent.constrained else 0


def exact_evidence(database: Session, record: Transcript, intent: QueryIntent,
                   kinds: Sequence[str] | None) -> tuple[str, ContentKind, str | None] | None:
    if intent.mode == "literal":
        # SQL qualifies and clips the chosen occurrence. No complete segment or
        # tool payload is transferred even for a punctuation-only literal query.
        start = func.greatest(1, func.strpos(SEGMENTS.c.text, intent.text)
                              - min(80, max(0, 320 - len(intent.text))))
        row = database.execute(select(SEGMENTS.c.segment_id, SEGMENTS.c.content_kind,
            func.substr(SEGMENTS.c.text, start, 320)).where(segment_scope(record, kinds),
            SEGMENTS.c.text.contains(intent.text, autoescape=True))
            .order_by(SEGMENTS.c.ordinal).limit(1)).first()
        return (row[0], row[1], row[2] if len(intent.text) <= 320 else None) if row else None
    return _phrase_evidence(database, record, intent, kinds)


def _phrase_evidence(database: Session, record: Transcript, intent: QueryIntent,
                     kinds: Sequence[str] | None) -> tuple[str, ContentKind, str | None] | None:
    # Corpus admission already bounds these texts; payloads never leave SQL.
    rows = database.execute(select(SEGMENTS.c.segment_id, SEGMENTS.c.content_kind, SEGMENTS.c.text)
        .where(segment_scope(record, kinds)).order_by(SEGMENTS.c.ordinal)
        .execution_options(yield_per=1))
    fallback = None
    try:
        for identity, kind, text in rows:
            for phrase in intent.phrases:
                span = phrase_span(text, phrase, analyzer().analyze)
                if span is not None:
                    start, end = span
                    lower = max(0, start - max(0, 320 - (end - start)) // 2)
                    excerpt = text[lower:lower + 320] if end - start <= 320 else None
                    return identity, kind, excerpt
            if fallback is None and intent.unquoted.strip():
                excerpt = supporting_snippet(
                    text, intent.unquoted, literal_terms, maximum_chars=320)
                if excerpt is not None:
                    fallback = identity, kind, excerpt
    finally:
        rows.close()
    return fallback
