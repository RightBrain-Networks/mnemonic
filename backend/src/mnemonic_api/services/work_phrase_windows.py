"""Overlapping native-word windows avoid PostgreSQL's 256-position lexeme cap.

The parser emits compound words and URLs together with their component tokens.
Drop only those overlapping parent tokens when reconstructing native text; keep
all separators and stop words. No window joins fields or checkpoint records.
"""

import re

from sqlalchemy import Text, case, false, func, literal, select, true, type_coerce
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by

from mnemonic_api.models import Checkpoint, WorkItem


def phrase_windows(column, phrase: str):
    native = func.ts_parse("default", column).table_valued(
        "kind", "fragment", with_ordinality="ordinal").render_derived()
    tokens = select(native.c.fragment, native.c.ordinal, func.sum(case(
        (native.c.kind.in_([12, 13, 14]), 0), else_=1,
    )).over(order_by=native.c.ordinal).label("word")).where(
        native.c.kind.not_in([5, 15, 16, 17])).correlate(WorkItem, Checkpoint).subquery()
    pieces = select(tokens.c.word, func.string_agg(tokens.c.fragment, aggregate_order_by(
        literal(""), tokens.c.ordinal)).label("text")).group_by(tokens.c.word).subquery()
    words = select(func.array_agg(aggregate_order_by(pieces.c.text, pieces.c.word))
                   .label("words")).lateral()
    # One window holds at most 256 native words. Overlap is at least the query's
    # word count, including stop words; even the longest accepted q has <256 words.
    stride = max(1, 256 - len(re.findall(r"[^\W_]+", phrase)))
    starts = func.generate_series(1, func.cardinality(words.c.words), stride).table_valued(
        "position").render_derived().lateral()
    values = type_coerce(words.c.words, ARRAY(Text))
    return select(func.array_to_string(
        values[starts.c.position:starts.c.position + 255], "").label("text"),
        starts.c.position.label("word_offset"),
    ).select_from(words.join(starts, true())).subquery()


def phrase_matches(column, phrase: str):
    query = func.phraseto_tsquery("english", phrase)
    direct = func.to_tsvector("english", func.coalesce(column, "")).bool_op("@@")(query)
    windows = phrase_windows(column, phrase)
    fallback = select(1).select_from(windows).where(
        func.to_tsvector("english", windows.c.text).bool_op("@@")(query)).exists()
    contains_terms = func.to_tsvector("english", func.coalesce(column, "")).bool_op("@@")(
        func.plainto_tsquery("english", phrase))
    return case((direct, true()), (~contains_terms, false()), else_=fallback)


def qualifying_phrase_text(column, phrase: str):
    windows = phrase_windows(column, phrase)
    query = func.phraseto_tsquery("english", phrase)
    return select(windows.c.text).where(
        func.to_tsvector("english", windows.c.text).bool_op("@@")(query)
    ).order_by(windows.c.word_offset).limit(1).scalar_subquery()
