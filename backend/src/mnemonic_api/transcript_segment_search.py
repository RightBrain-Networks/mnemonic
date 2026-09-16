"""Search text and matching segment locators from the common conversation format."""

from collections.abc import Iterator, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import SearchDocument, literal_terms
from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_normalization import Segment, segment_text
from mnemonic_api.transcript_normalized_storage import SEGMENTS


def segment_scope(record: Transcript, content_kinds: Sequence[str] | None = None):
    scope = ((SEGMENTS.c.transcript_id == record.id)
             & (SEGMENTS.c.revision == record.normalized_revision))
    if content_kinds:
        scope &= SEGMENTS.c.content_kind.in_(content_kinds)
    return scope


def filtered_documents(database: Session, records: list[Transcript], content_kinds: Sequence[str],
                       maximum: int, metadata) -> Iterator[SearchDocument]:
    scope = SEGMENTS.join(Transcript.__table__,
        (Transcript.id == SEGMENTS.c.transcript_id)
        & (Transcript.normalized_revision == SEGMENTS.c.revision))
    size = database.scalar(select(func.sum(func.octet_length(SEGMENTS.c.text)))
        .select_from(scope).where(Transcript.status == "ready",
                                 Transcript.id.in_([record.id for record in records]),
                                 SEGMENTS.c.content_kind.in_(content_kinds))) or 0
    if size > maximum:
        raise ApplicationError(503, "transcript_search_capacity",
                               "Selected transcript content exceeds search capacity.")
    measured = 0
    for record in records:
        if record.status != "ready":
            yield SearchDocument(str(record.id), metadata(record))
            continue
        rows = database.scalars(select(SEGMENTS.c.segment_data.op("-")("payload")).where(
            segment_scope(record, content_kinds)).order_by(SEGMENTS.c.ordinal)
            .execution_options(yield_per=1))
        # One transcript body at a time; no retained corpus of hydrated bodies.
        parts = []
        try:
            for value in rows:
                part = segment_text(Segment(**value))
                measured += len(part.encode("utf-8")) + 2
                if measured > maximum:
                    raise ApplicationError(503, "transcript_search_capacity",
                                           "Selected transcript content exceeds search capacity.")
                parts.append(part)
        finally:
            rows.close()
        yield SearchDocument(str(record.id), metadata(record), "\n\n".join(parts))


def matching_segment(database: Session, record: Transcript, query: str,
                     content_kinds: Sequence[str] | None = None) -> tuple[Segment, str] | None:
    remaining = None if content_kinds else database.scalar(select(
        func.length(Transcript.normalized_text)).where(Transcript.id == record.id))
    terms = set(literal_terms(query))
    best, best_score = None, (0, 0)
    rows = database.scalars(select(SEGMENTS.c.segment_data.op("-")("payload")).where(
        segment_scope(record, content_kinds)).order_by(SEGMENTS.c.ordinal)
        .execution_options(yield_per=1))
    for value in rows:
        segment = Segment(**value)
        searchable = segment_text(segment)
        if not searchable:
            continue
        if remaining is not None:
            if remaining <= 0:
                break
            searchable = searchable[:remaining]
            remaining -= len(searchable) + 2
        # Same unstemmed, accent-folded Tantivy analyzer as transcript matching.
        coverage = len(terms.intersection(literal_terms(searchable)))
        exact = bool(query.strip('"').casefold() in searchable.casefold())
        score = (int(exact), coverage)
        if coverage and score > best_score:
            best, best_score = (segment, searchable), score
    rows.close()
    return best
