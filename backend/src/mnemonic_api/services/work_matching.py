"""One SQL matching decision shared by work selection and supporting evidence.

Bare terms preserve the existing English work-row or individual-checkpoint vector
and substring lanes. Each constrained phrase must occur within one stored field.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    ColumnElement,
    String,
    and_,
    cast,
    false,
    func,
    literal_column,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import TSQUERY

from mnemonic_api.models import Checkpoint, WorkItem
from mnemonic_api.search_query import QueryIntent
from mnemonic_api.services.work_phrase_windows import phrase_matches
from mnemonic_api.work_search_fields import WorkField

TS_RANK_NORMALIZATION = 32


@dataclass(frozen=True)
class MatchField:
    key: str
    name: WorkField
    column: Any
    indexed: bool = False


@dataclass(frozen=True)
class MatchUnit:
    fields: tuple[MatchField, ...]
    vector: Any
    intent: QueryIntent

    def plain_condition(self, query: str):
        query = query.strip()
        if not query:
            return true()
        return or_(self.vector.bool_op("@@")(func.plainto_tsquery("english", query)),
                   *(field.column.icontains(query, autoescape=True) for field in self.fields))

    @property
    def condition(self):
        if self.intent.mode == "literal":
            return or_(false(), *(exact_field(field, self.intent.text) for field in self.fields))
        if not self.intent.phrases:
            return self.plain_condition(self.intent.text)
        return and_(self.plain_condition(self.intent.unquoted), *(
            or_(false(), *(phrase_field(field, phrase) for field in self.fields))
            for phrase in self.intent.phrases))

    @property
    def score(self):
        return func.ts_rank_cd(self.vector, func.plainto_tsquery("english", self.intent.text),
                               TS_RANK_NORMALIZATION)

    def contribution(self, field: MatchField):
        if self.intent.mode == "literal":
            return exact_field(field, self.intent.text)
        query = (self.intent.unquoted if self.intent.phrases else self.intent.text).strip()
        condition = false()
        if query.strip():
            terms = func.plainto_tsquery("english", query)
            any_term = cast(func.replace(cast(terms, String), " & ", " | "), TSQUERY)
            condition = or_(field.column.icontains(query, autoescape=True), and_(
                self.vector.bool_op("@@")(terms), true() if field.indexed else false(),
                func.to_tsvector("english", func.coalesce(field.column, "")).bool_op("@@")(
                    any_term)))
        return or_(condition, *(phrase_field(field, phrase) for phrase in self.intent.phrases))


def _tag_predicate(predicate):
    tag = func.unnest(Checkpoint.tags).column_valued("matched_tag")
    return select(1).where(predicate(tag)).correlate(Checkpoint).exists()


def phrase_field(field: MatchField, phrase: str):
    def predicate(column):
        return phrase_matches(column, phrase)
    return _tag_predicate(predicate) if field.name == "tags" else predicate(field.column)


def exact_field(field: MatchField, query: str):
    def predicate(column):
        return column.contains(query, autoescape=True)
    return _tag_predicate(predicate) if field.name == "tags" else predicate(field.column)


def work_unit(intent: QueryIntent, fields: Sequence[WorkField]) -> MatchUnit:
    columns = (
        MatchField("title", "title", WorkItem.title, True),
        MatchField("summary", "summary", WorkItem.summary, True),
        MatchField("work_id", "identifiers", cast(WorkItem.id, String)),
    )
    selected = tuple(field for field in columns if field.name in fields)
    if "title" in fields and "summary" in fields:
        vector = WorkItem.search_vector
    else:
        weights = {"title": literal_column("'A'"), "summary": literal_column("'B'")}
        vectors = [func.setweight(func.to_tsvector("english", field.column), weights[field.name])
                   for field in selected if field.indexed]
        vector = vectors[0] if vectors else func.to_tsvector("english", "")
    return MatchUnit(selected, vector, intent)


def checkpoint_unit(intent: QueryIntent, fields: Sequence[WorkField]) -> MatchUnit:
    columns = (
        MatchField("checkpoint", "checkpoint", Checkpoint.prompt, True),
        MatchField("tags", "tags", func.array_to_string(Checkpoint.tags, " ")),
        MatchField("checkpoint_id", "identifiers", cast(Checkpoint.id, String)),
        *(MatchField(name, "provenance", getattr(Checkpoint, name)) for name in (
            "source_client", "source_session_id", "source_model", "source_session_url",
            "repository_branch", "verified_against")),
    )
    selected = tuple(field for field in columns if field.name in fields)
    vector = Checkpoint.search_vector if "checkpoint" in fields else func.to_tsvector("english", "")
    return MatchUnit(selected, vector, intent)


@dataclass(frozen=True)
class LexicalMatch:
    condition: ColumnElement[bool]
    score: ColumnElement[Any]


def lexical_match(intent: QueryIntent, fields: Sequence[WorkField]) -> LexicalMatch:
    work, checkpoint = work_unit(intent, fields), checkpoint_unit(intent, fields)
    in_checkpoint = select(Checkpoint.id).where(
        Checkpoint.work_item_id == WorkItem.id, checkpoint.condition).exists()
    best_checkpoint_rank = select(func.max(checkpoint.score)).where(
        Checkpoint.work_item_id == WorkItem.id).scalar_subquery()
    return LexicalMatch(or_(work.condition, in_checkpoint),
                        func.greatest(work.score, func.coalesce(best_checkpoint_rank, 0.0)))
