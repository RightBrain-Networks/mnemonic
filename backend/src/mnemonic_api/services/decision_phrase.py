"""Native English phrase matching beyond PostgreSQL's per-lexeme position cap."""

from sqlalchemy import Text, and_, column, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG, aggregate_order_by
from sqlalchemy.sql.expression import cast


def _lexemes(value, correlate):
    native = func.ts_debug(cast("english", REGCONFIG), value).table_valued(
        column("lexemes", ARRAY(Text)),
        with_ordinality="ordinality",
    )
    # Keep compound/URL parent lexemes: native vectors assign them a position
    # before their components. Encode stems so regex punctuation cannot change
    # the match. Stop words keep
    # one position; a phrase's internal stop words match any word at that position.
    word = func.coalesce(func.encode(func.convert_to(native.c.lexemes[1], "UTF8"), "hex"), "z")
    ordered = aggregate_order_by(literal("|"), native.c.ordinality)
    return select(func.string_agg(word, ordered)).where(
        native.c.lexemes.is_not(None),
    ).correlate(*correlate).scalar_subquery()


def long_phrase_matches(value, phrase: str, *, correlate):
    """Retain stemming, word order and stop-word distances without a tsvector."""
    wanted = func.btrim(_lexemes(literal(phrase), ()), "z|")
    expression = (literal(r"\|") + func.replace(
        func.replace(wanted, "|", r"\|"), "z", "[0-9a-fz]+",
    ) + literal(r"\|"))
    document = literal("|") + _lexemes(value, correlate) + literal("|")
    return and_(wanted != "", document.self_group().bool_op("~")(expression.self_group()))
