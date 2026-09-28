"""Search text and matching segment locators from the common conversation format."""

from collections.abc import Iterator, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mnemonic_api.artifact_index import SearchDocument, literal_terms
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_normalization import Segment, segment_text
from mnemonic_api.transcript_normalized_storage import SEGMENTS
from mnemonic_api.transcript_search_corpus import TranscriptCorpus


def segment_scope(record: Transcript, content_kinds: Sequence[str] | None = None):
    scope = ((SEGMENTS.c.transcript_id == record.id)
             & (SEGMENTS.c.revision == record.normalized_revision))
    if content_kinds:
        scope &= SEGMENTS.c.content_kind.in_(content_kinds)
    return scope


def filtered_documents(database: Session, records: TranscriptCorpus,
                       content_kinds: Sequence[str], metadata) -> Iterator[SearchDocument]:
    for record in records:
        parts = () if record.status != "ready" else _filtered_parts(database, record, content_kinds)
        yield SearchDocument(str(record.id), metadata(record), content_parts=parts)


def _filtered_parts(database: Session, record: Transcript, content_kinds: Sequence[str]):
    rows = database.scalars(select(SEGMENTS.c.segment_data.op("-")("payload")).where(
        segment_scope(record, content_kinds)).order_by(SEGMENTS.c.ordinal)
        .execution_options(yield_per=1))
    try:
        for value in rows:
            yield segment_text(Segment(**value))
    finally:
        rows.close()


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
