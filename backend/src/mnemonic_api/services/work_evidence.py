"""Page-only, bounded evidence from the same SQL units that select work hits."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Integer, String, case, cast, false, func, null, or_, select, union_all
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Session

from mnemonic_api.models import Checkpoint, WorkItem
from mnemonic_api.schemas import WorkItemListQuery, WorkMatchEvidence, WorkMatchExcerpt
from mnemonic_api.search_query import parse_query
from mnemonic_api.services.work_matching import (
    MatchField,
    MatchUnit,
    checkpoint_unit,
    phrase_field,
    work_unit,
)
from mnemonic_api.services.work_phrase_windows import qualifying_phrase_text
from mnemonic_api.work_search_fields import WORK_FIELDS


def _winning_units(database: Session, identities: Sequence[UUID], work: MatchUnit,
                   checkpoint: MatchUnit) -> dict[UUID, UUID | None]:
    candidates = union_all(
        select(WorkItem.id.label("member_id"), cast(null(), PGUUID).label("checkpoint_id"),
               work.score.label("score")).where(WorkItem.id.in_(identities), work.condition),
        select(Checkpoint.work_item_id, Checkpoint.id, checkpoint.score).where(
            Checkpoint.work_item_id.in_(identities), checkpoint.condition),
    ).subquery()
    ranked = select(candidates.c.member_id, candidates.c.checkpoint_id, func.row_number().over(
        partition_by=candidates.c.member_id,
        order_by=(candidates.c.score.desc(), candidates.c.checkpoint_id.asc().nulls_first()),
    ).label("position")).subquery()
    return dict(database.execute(select(ranked.c.member_id, ranked.c.checkpoint_id).where(
        ranked.c.position == 1)).tuples().all())


def _contributions(database: Session, winners: dict[UUID, UUID | None], work: MatchUnit,
                   checkpoint: MatchUnit) -> dict[UUID, tuple[list[MatchField], list[MatchField]]]:
    result = {}
    for unit, model, identifiers in (
        (work, WorkItem, {member: member for member, cp in winners.items() if cp is None}),
        (checkpoint, Checkpoint, {cp: member for member, cp in winners.items() if cp is not None}),
    ):
        if not identifiers:
            continue
        rows = database.execute(select(model.id, *(
            unit.contribution(field).label(field.key) for field in unit.fields
        ), *(_single_field_condition(field, unit) for field in unit.fields))
            .where(model.id.in_(identifiers)))
        width = len(unit.fields)
        for row in rows:
            contributions = [field for field, matches in zip(
                unit.fields, row[1:width + 1], strict=True) if matches]
            proving = [field for field, matches in zip(
                unit.fields, row[width + 1:], strict=True) if matches]
            result[identifiers[row[0]]] = contributions, proving
    return result


def _single_field_condition(field: MatchField, unit: MatchUnit):
    vector = func.to_tsvector("english", func.coalesce(field.column, "")) if field.indexed else (
        func.to_tsvector("english", ""))
    return MatchUnit((field,), vector, unit.intent).condition


def _tag_excerpt_column(field: MatchField, unit: MatchUnit):
    if field.name != "tags" or not unit.intent.constrained:
        return field.column
    tag = func.unnest(Checkpoint.tags).column_valued("excerpt_tag")
    predicate = (tag.contains(unit.intent.text, autoescape=True) if unit.intent.mode == "literal"
                 else or_(*(func.to_tsvector("english", tag).bool_op("@@")(
                     func.phraseto_tsquery("english", phrase)) for phrase in unit.intent.phrases)))
    # A tag array is not a phrase boundary: choose one actual qualifying element.
    selected = select(tag).where(predicate).correlate(Checkpoint).limit(1).scalar_subquery()
    return func.coalesce(selected, field.column)


def _excerpt_expression(field: MatchField, unit: MatchUnit, budget: int):
    column = _tag_excerpt_column(field, unit)
    intent = unit.intent
    if intent.mode == "literal":
        return _around(column, func.strpos(column, intent.text), budget), "literal"
    query = (intent.unquoted if intent.phrases else intent.text).strip()
    phrases = [(phrase_field(field, phrase), func.phraseto_tsquery("english", phrase))
               for phrase in intent.phrases]
    plain_query = func.plainto_tsquery("english", query)
    headline_query = case(*phrases, else_=plain_query) if phrases else plain_query
    headline = func.ts_headline("english", column, headline_query,
                               "MaxWords=20,MinWords=1,MaxFragments=1")
    # PostgreSQL chooses the actual inflected supporting token. Hydrate a bounded
    # slice of the original field around it, rather than returning HTML markup.
    token = func.substring(headline, "<b>(.*?)</b>")
    lexical_position = func.strpos(func.lower(column), func.lower(token))
    substring = column.icontains(query, autoescape=True) if query.strip() else false()
    phrase = or_(*(predicate for predicate, _ in phrases)) if phrases else false()
    position = case((phrase, lexical_position),
                    (substring, func.strpos(func.lower(column), query.lower())),
                    else_=lexical_position)
    kind = case((phrase, "phrase"), (substring, "substring"), else_="lexical")
    ordinary = _around(column, position, budget)
    if phrases:
        supported = case(*[
            (predicate, _phrase_excerpt(column, phrase_text, budget))
            for (predicate, _), phrase_text in zip(phrases, intent.phrases, strict=True)
        ], else_=ordinary)
        return supported, kind
    return ordinary, kind


def _phrase_excerpt(column, phrase: str, budget: int):
    query = func.phraseto_tsquery("english", phrase)
    qualifying = qualifying_phrase_text(column, phrase)
    headline = func.ts_headline("english", qualifying, query,
        'StartSel="",StopSel="",MaxWords=35,MinWords=1,MaxFragments=1')
    starts = (1, func.greatest(1, func.length(headline) - budget + 1),
              func.greatest(1, (func.length(headline) - budget) / 2))
    windows = [func.substr(headline, cast(start, Integer), budget) for start in starts]
    # Headline highlighting alone does not honor phrase positions. Emit only a
    # bounded window that satisfies the same English phrase query as selection.
    return case(*[(func.to_tsvector("english", window).bool_op("@@")(query), window)
                  for window in windows], else_=null())


def _around(column, position, budget: int):
    start = func.greatest(1, func.coalesce(position, 1) - budget // 3)
    return func.substr(column, start, budget)


def work_match_evidence(database: Session, identities: Sequence[UUID],
                        filters: WorkItemListQuery) -> dict[UUID, WorkMatchEvidence]:
    intent = parse_query(filters.q, filters.query_mode)
    default = "semantic" if filters.semantic and intent.text else "browse"
    result = {identity: WorkMatchEvidence(evidence_mode=default, matched_fields=[], excerpts=[],
                                          excerpts_truncated=False)
              for identity in identities}
    if not intent.text or not identities:
        return result
    work = work_unit(intent, filters.work_fields)
    checkpoint = checkpoint_unit(intent, filters.work_fields)
    winners = _winning_units(database, identities, work, checkpoint)
    contributors = _contributions(database, winners, work, checkpoint)
    statements, slots = [], []
    for member_id, (fields, proving) in contributors.items():
        unique = {field.name: field for field in reversed(fields)}
        chosen = proving[:1] or [unique[name] for name in WORK_FIELDS if name in unique][:3]
        result[member_id] = WorkMatchEvidence(evidence_mode="lexical", matched_fields=[
            name for name in WORK_FIELDS if name in unique], excerpts=[],
            excerpts_truncated=not proving and len(unique) > len(chosen))
        for field in chosen:
            checkpoint_id = winners[member_id]
            model, unit = (WorkItem, work) if checkpoint_id is None else (Checkpoint, checkpoint)
            budget = min(320 if intent.constrained else 160, 320 // len(chosen))
            text, kind = _excerpt_expression(field, unit, budget)
            statements.append(select(cast(text, String), cast(kind, String)).where(
                model.id == (checkpoint_id or member_id)))
            slots.append((member_id, checkpoint_id, field.name))
    if statements:
        for slot, row in zip(slots, database.execute(union_all(*statements)), strict=True):
            member_id, checkpoint_id, field = slot
            if row[0] is None:
                result[member_id].excerpts_truncated = True
                continue
            result[member_id].excerpts.append(WorkMatchExcerpt(field=field, text=row[0],
                matched_member_id=member_id, checkpoint_id=checkpoint_id, match_type=row[1]))
    return result
