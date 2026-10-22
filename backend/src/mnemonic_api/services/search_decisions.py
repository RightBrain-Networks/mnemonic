"""Search immutable revisions in one PostgreSQL snapshot, hydrating only the page."""

import re
from uuid import UUID

from sqlalchemy import (
    String,
    Text,
    and_,
    any_,
    case,
    cast,
    column,
    distinct,
    false,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG, TSQUERY
from sqlalchemy.orm import Session, undefer

from mnemonic_api.decision_search_schemas import DecisionSearchMatch
from mnemonic_api.models import DecisionDocument, DecisionRevision, KnowledgeDomain
from mnemonic_api.search_exploration import date_conditions
from mnemonic_api.search_projects import ProjectSelection, selected_project_ids
from mnemonic_api.search_query import QueryIntent, analyzer, parse_query
from mnemonic_api.search_schemas import DecisionFacetHit, SearchHit, SearchRequest
from mnemonic_api.search_snippets import phrase_snippet, supporting_snippet
from mnemonic_api.services.decision_phrase import long_phrase_matches
from mnemonic_api.services.search_sources import SearchCandidate, SearchSource
from mnemonic_api.services.work_phrase_windows import phrase_matches, phrase_windows

_CORRELATE = (DecisionRevision, DecisionDocument, KnowledgeDomain)


def _identifier():
    ordinal = cast(DecisionDocument.ordinal, String)
    padded = func.lpad(ordinal, func.greatest(5, func.length(ordinal)), "0")
    return KnowledgeDomain.prefix + "-" + padded


def _metadata():
    return func.concat_ws(" ", _identifier() + "-" + cast(DecisionRevision.revision, String),
                          DecisionRevision.title, KnowledgeDomain.name, DecisionRevision.state,
                          DecisionRevision.agent_session_id, DecisionRevision.model_id)


def _columns(database: Session):
    return database.query(DecisionRevision, DecisionDocument, KnowledgeDomain).join(
        DecisionDocument, DecisionRevision.document_id == DecisionDocument.id,
    ).join(KnowledgeDomain, DecisionDocument.domain_id == KnowledgeDomain.id)


def _scope(request: SearchRequest, projects: ProjectSelection):
    filters = request.filters.decisions
    clauses = [DecisionRevision.project_id.in_(selected_project_ids(projects)),
               *date_conditions(filters, DecisionDocument.created_at, DecisionRevision.created_at)]
    if not filters.all_revisions:
        clauses.append(DecisionRevision.revision == DecisionDocument.current_revision)
    if filters.domain_ids is not None:
        clauses.append(KnowledgeDomain.id.in_(filters.domain_ids))
    if filters.state is not None:
        clauses.append(DecisionRevision.state == filters.state)
    return clauses


def _vector(value, query: str):
    # A valid 1 MiB body can exceed PostgreSQL's 1 MiB tsvector limit after
    # lexeme expansion. Large bodies retain only query lexemes before building
    # a vector. ts_debug streams words without a document-sized tsvector.
    wanted = func.tsvector_to_array(func.to_tsvector("english", query))
    debug = func.ts_debug(cast("english", REGCONFIG), value).table_valued(
        column("lexemes", ARRAY(Text)),
    )
    words = func.unnest(debug.c.lexemes).table_valued("term").render_derived()
    matched = select(func.array_to_tsvector(func.coalesce(
        func.array_agg(distinct(words.c.term)), cast([], ARRAY(Text)),
    ))).select_from(debug.join(words, true())).where(
        words.c.term == any_(wanted),
    ).correlate(*_CORRELATE).scalar_subquery()
    return case((func.length(value) <= 65_536,
                 func.to_tsvector("english", func.coalesce(value, ""))), else_=matched)


def _plain(column, text: str):
    return or_(_vector(column, text).bool_op("@@")(func.plainto_tsquery("english", text)),
               column.icontains(text, autoescape=True))


def _phrase(value, phrase: str):
    if len(re.findall(r"[^\W_]+", phrase)) >= 256:
        return long_phrase_matches(value, phrase, correlate=_CORRELATE)
    windows = phrase_windows(value, phrase, correlate=_CORRELATE)
    bounded = select(1).select_from(windows).where(
        func.to_tsvector("english", windows.c.text).bool_op("@@")(
            func.phraseto_tsquery("english", phrase)),
    ).exists()
    return case((func.length(value) <= 65_536,
                 phrase_matches(value, phrase, correlate=_CORRELATE)), else_=bounded)


def _condition(columns, intent: QueryIntent):
    if not intent.text:
        return true()
    if intent.mode == "literal":
        return or_(*(column.contains(intent.text, autoescape=True) for column in columns))
    combined = func.concat_ws(" ", *columns)
    if not intent.phrases:
        return _plain(combined, intent.text)
    plain = _plain(combined, intent.unquoted) if intent.unquoted.strip() else true()
    return and_(plain, *(or_(*(_phrase(column, phrase) for column in columns))
        for phrase in intent.phrases))


def _scores(columns, intent: QueryIntent):
    if not intent.text:
        return literal(0.0)
    return func.ts_rank(_vector(func.concat_ws(" ", *columns), intent.text),
                        func.plainto_tsquery("english", intent.text), 32)


def _contribution(value, intent: QueryIntent):
    if not intent.text:
        return false()
    if intent.mode == "literal":
        return value.contains(intent.text, autoescape=True)
    query = (intent.unquoted if intent.phrases else intent.text).strip()
    condition = false()
    if query:
        terms = func.plainto_tsquery("english", query)
        any_term = cast(func.replace(cast(terms, String), " & ", " | "), TSQUERY)
        condition = or_(_vector(value, query).bool_op("@@")(any_term),
                        value.icontains(query, autoescape=True))
    return or_(condition, *(_phrase(value, phrase) for phrase in intent.phrases))


def _boundary_contribution(metadata, intent: QueryIntent):
    query = intent.unquoted if intent.phrases else intent.text
    if intent.mode == "literal" or not query.strip():
        return false()
    # Plain terms also match a substring of metadata + " " + content. Keep
    # less than one query's width from each side so this evidence can only
    # describe a match spanning the boundary, never an unrelated whole field.
    width = len(query) - 1
    boundary = func.concat_ws(" ", func.right(metadata, width),
                              func.left(DecisionRevision.content, width))
    return boundary.icontains(query, autoescape=True)


def _snippet(content: str, request: SearchRequest) -> str | None:
    intent = parse_query(request.q, request.query_mode)
    analyze = analyzer().analyze
    if intent.phrases:
        return phrase_snippet(content, intent.phrases, intent.unquoted, analyze)
    return supporting_snippet(content, request.q, analyze, literal=request.query_mode == "literal")


def _match(revision, document, domain, item, request, matched) -> DecisionSearchMatch:
    identity = f"{domain.prefix}-{document.ordinal:05d}"
    return DecisionSearchMatch(
        id=revision.id, project_id=revision.project_id, decision_id=identity,
        reference=f"{identity}-{revision.revision}", domain_id=domain.id, domain_name=domain.name,
        prefix=domain.prefix, ordinal=document.ordinal, revision=revision.revision,
        title=revision.title, state=revision.state, media_type=revision.media_type,
        size_bytes=revision.size_bytes, sha256=revision.sha256, created_at=document.created_at,
        updated_at=revision.created_at, author_kind=revision.author_kind,
        agent_session_id=revision.agent_session_id, model_id=revision.model_id,
        rank=item.source_rank, score=matched[0],
        score_type="postgresql_lexical" if request.q else "none",
        matched_fields=[name for name, yes in zip(("metadata", "content"), matched[1:], strict=True)
                        if yes and request.q],
        snippet=_snippet(revision.content, request) if request.q and request.fulltext else None,
    )


def decision_source(
    database: Session, project_id: ProjectSelection, request: SearchRequest,
) -> SearchSource:
    clauses = _scope(request, project_id)
    intent = parse_query(request.q, request.query_mode)
    metadata = _metadata()
    columns = [metadata, DecisionRevision.content] if request.fulltext else [metadata]
    boundary_match = _boundary_contribution(metadata, intent) if request.fulltext else false()
    metadata_match = or_(_contribution(metadata, intent), boundary_match)
    content_match = (or_(_contribution(DecisionRevision.content, intent), boundary_match)
                     if request.fulltext else false())
    query = _columns(database).filter(*clauses, _condition(columns, intent))
    rows = query.with_entities(
        DecisionRevision.id, DecisionRevision.project_id,
        DecisionDocument.created_at, DecisionRevision.created_at,
        _scores(columns, intent), metadata_match.label("metadata_match"),
        content_match.label("content_match"),
    ).all()
    matches = {row[0]: (float(row[4]), row[5], row[6]) for row in rows}
    candidates = [SearchCandidate(facet="decisions", id=row[0], project_id=row[1],
                                 created_at=row[2], updated_at=row[3], score=float(row[4]))
                  for row in rows]

    def hydrate(page: list[SearchCandidate]) -> dict[UUID, SearchHit]:
        selected = _columns(database).filter(DecisionRevision.id.in_([item.id for item in page]))
        if request.fulltext and request.q:
            selected = selected.options(undefer(DecisionRevision.content))
        records = {revision.id: (revision, document, domain)
                   for revision, document, domain in selected}
        return {item.id: DecisionFacetHit(**item.fields(), decision=_match(
            *records[item.id], item, request, matches[item.id])) for item in page}

    def term_counts(terms: list[str]) -> dict[str, int]:
        return {term: _columns(database).filter(
            *clauses, _condition(columns, parse_query(term)),
        ).count() for term in terms}

    return SearchSource(candidates, hydrate, term_counts)
