"""Bounded comparison of ephemeral caller text, independently of internal ranking."""

import asyncio
import json
import logging
from dataclasses import dataclass
from time import monotonic

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from mnemonic_api.application.suggestion_resources import OwnedSuggestionWork
from mnemonic_api.external_duplicate_schemas import (
    SIGNAL_ORDER,
    ExternalCandidateReference,
    ExternalDuplicateCandidate,
    ExternalDuplicateSuggestion,
    ExternalScope,
    ExternalSignal,
    require_external_correspondence,
)
from mnemonic_api.pool_deadlines import pool_checkout_deadline
from mnemonic_api.schemas import DuplicateSuggestionPage, DuplicateSuggestionRequest
from mnemonic_api.semantic import (
    EMBED_BATCH_SIZE,
    EMBED_BODY_CHARS,
    RRF_K,
    RRF_LEXICAL_WEIGHT,
    Embedder,
    cosine_similarity,
)
from mnemonic_api.services.duplicate_suggestions import (
    TS_RANK_NORMALIZATION,
    _draft_text,
    _set_transaction_deadline,
    _valid_vector,
)

logger = logging.getLogger(__name__)
EXTERNAL_BUDGET_SECONDS = 5.0
RESPONSE_RESERVE_SECONDS = 1.0

# The shipped PostgreSQL function defines exact equality. Lexical terms are an OR
# of normalized lexemes, matching the internal lane (including stopword behavior).
_BASELINE_SQL = """
WITH candidates AS (
    SELECT value->>'url' AS url, value->>'title' AS title, value->>'body' AS body
    FROM pg_catalog.jsonb_array_elements(CAST(:candidates AS jsonb))
), query_lexemes AS (
    SELECT lexeme.value
    FROM pg_catalog.unnest(pg_catalog.tsvector_to_array(
        pg_catalog.to_tsvector('english'::regconfig, :query_text)
    )) AS lexeme(value)
), query AS (
    SELECT COALESCE(pg_catalog.string_agg(
        pg_catalog.quote_literal(value), ' | ' ORDER BY value
    ), '')::tsquery AS terms
    FROM query_lexemes
), documents AS (
    SELECT url, title,
        pg_catalog.setweight(pg_catalog.to_tsvector('english'::regconfig, title), 'A') ||
        pg_catalog.setweight(pg_catalog.to_tsvector(
            'english'::regconfig, pg_catalog.left(body, :body_chars)
        ), 'C') AS document
    FROM candidates
)
SELECT url,
       mnemonic_duplicate_title_key_v1(title) = mnemonic_duplicate_title_key_v1(:title)
           AS exact,
       CASE WHEN document @@ query.terms THEN
           pg_catalog.ts_rank_cd(document, query.terms, :normalization)
           ELSE 0 END AS lexical_score
FROM documents CROSS JOIN query
"""


@dataclass(frozen=True, slots=True)
class ExternalBaseline:
    exact_urls: tuple[str, ...]
    lexical_urls: tuple[str, ...]


def external_deadline(request_deadline: float, now: float) -> float:
    return min(now + EXTERNAL_BUDGET_SECONDS, request_deadline - RESPONSE_RESERVE_SECONDS)


async def extend_external_suggestions(
    page: DuplicateSuggestionPage,
    payload: DuplicateSuggestionRequest,
    *,
    session_factory: sessionmaker[Session],
    embedder: Embedder,
    query_vector: tuple[float, ...] | None,
    inference_permitted: bool,
    request_deadline: float,
    owned_work: OwnedSuggestionWork,
) -> DuplicateSuggestionPage:
    if not payload.external_candidates:
        return page
    deadline = external_deadline(request_deadline, monotonic())
    baseline = await _external_baseline(payload, session_factory, deadline, owned_work)
    if baseline is None:
        return _extended_page(page, payload, [], "unavailable")
    semantic_urls = None
    if inference_permitted and query_vector is not None and monotonic() < deadline:
        semantic_urls = await _external_semantic(
            payload.external_candidates, embedder, query_vector, deadline, owned_work
        )
    items = rank_external_candidates(
        payload.external_candidates, baseline, semantic_urls, payload.limit
    )
    scope: ExternalScope = "hybrid" if semantic_urls is not None else "lexical"
    return _extended_page(page, payload, items, scope)


async def _external_baseline(
    payload: DuplicateSuggestionRequest,
    session_factory: sessionmaker[Session],
    deadline: float,
    owner: OwnedSuggestionWork,
) -> ExternalBaseline | None:
    if monotonic() >= deadline:
        return None
    task = owner.start(lambda: capture_external_baseline(session_factory, payload, deadline))
    try:
        return await _await_owned(task, deadline)
    except Exception as exc:
        logger.warning("External suggestion baseline fallback (%s)", type(exc).__name__)
        return None


async def _external_semantic(
    candidates: list[ExternalDuplicateCandidate],
    embedder: Embedder,
    query_vector: tuple[float, ...],
    deadline: float,
    owner: OwnedSuggestionWork,
) -> tuple[str, ...] | None:
    task = owner.start(lambda: rank_external_semantic(candidates, embedder, query_vector, deadline))
    try:
        return await _await_owned(task, deadline)
    except Exception as exc:
        logger.warning("External suggestion semantic fallback (%s)", type(exc).__name__)
        return None


async def _await_owned[T](task: asyncio.Task[T], deadline: float) -> T:
    # wait does not cancel the worker on expiry or caller cancellation. The
    # middleware retains its permits until this very task has actually completed.
    done, _ = await asyncio.wait((task,), timeout=max(0.0, deadline - monotonic()))
    if not done or monotonic() >= deadline:
        raise TimeoutError
    return task.result()


def capture_external_baseline(
    session_factory: sessionmaker[Session],
    payload: DuplicateSuggestionRequest,
    deadline: float,
) -> ExternalBaseline:
    _require_time(deadline)
    # The worker owns checkout and the entire read-only session. Budget expiry
    # while the pool is busy returns unavailable; late checkout cannot issue SQL.
    with session_factory() as database:
        with pool_checkout_deadline(deadline):
            database.connection()
        _require_time(deadline)
        database.execute(text("SET TRANSACTION READ ONLY"))
        _set_transaction_deadline(database, deadline)
        rows = list(
            database.execute(
                text(_BASELINE_SQL),
                {
                    "candidates": json.dumps(
                        [
                            candidate.model_dump(mode="json")
                            for candidate in payload.external_candidates
                        ],
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    "query_text": _draft_text(payload),
                    "title": payload.title,
                    "body_chars": EMBED_BODY_CHARS,
                    "normalization": TS_RANK_NORMALIZATION,
                },
            ).mappings()
        )
    _require_time(deadline)
    expected = {candidate.url for candidate in payload.external_candidates}
    if len(rows) != len(expected) or {row["url"] for row in rows} != expected:
        raise ValueError("External baseline population mismatch")
    exact = tuple(sorted(row["url"] for row in rows if row["exact"]))
    lexical = tuple(
        row["url"]
        for row in sorted(
            (row for row in rows if row["lexical_score"] > 0),
            key=lambda row: (-row["lexical_score"], row["url"]),
        )
    )
    return ExternalBaseline(exact, lexical)


def rank_external_semantic(
    candidates: list[ExternalDuplicateCandidate],
    embedder: Embedder,
    query_vector: tuple[float, ...],
    deadline: float,
) -> tuple[str, ...]:
    scores: dict[str, float] = {}
    # URL sorting also makes batch composition independent of submitted order.
    ordered = sorted(candidates, key=lambda candidate: candidate.url)
    for start in range(0, len(ordered), EMBED_BATCH_SIZE):
        _require_time(deadline)
        batch = ordered[start : start + EMBED_BATCH_SIZE]
        vectors = embedder.embed_documents(
            [f"{candidate.title}\n{candidate.body[:EMBED_BODY_CHARS]}" for candidate in batch]
        )
        _require_time(deadline)
        if len(vectors) != len(batch):
            raise ValueError("External embedding cardinality mismatch")
        for candidate, vector in zip(batch, vectors, strict=True):
            if not _valid_vector(vector, len(query_vector)):
                raise ValueError("Invalid external vector")
            scores[candidate.url] = cosine_similarity(query_vector, vector)
    _require_time(deadline)
    return tuple(sorted(scores, key=lambda url: (-scores[url], url)))


def _require_time(deadline: float) -> None:
    if monotonic() >= deadline:
        raise TimeoutError


def rank_external_candidates(
    candidates: list[ExternalDuplicateCandidate],
    baseline: ExternalBaseline,
    semantic_urls: tuple[str, ...] | None,
    limit: int,
) -> list[ExternalDuplicateSuggestion]:
    exact = set(baseline.exact_urls)
    lexical_rank = {
        url: rank
        for rank, url in enumerate(
            (url for url in baseline.lexical_urls if url not in exact), start=1
        )
    }
    semantic_rank = {
        url: rank
        for rank, url in enumerate(
            (url for url in semantic_urls or () if url not in exact), start=1
        )
    }

    def score(url: str) -> float:
        lexical = RRF_LEXICAL_WEIGHT / (RRF_K + lexical_rank[url]) if url in lexical_rank else 0
        semantic = 1 / (RRF_K + semantic_rank[url]) if url in semantic_rank else 0
        return lexical + semantic

    fused = sorted(set(lexical_rank) | set(semantic_rank), key=lambda url: (-score(url), url))
    by_url = {candidate.url: candidate for candidate in candidates}
    urls = [*sorted(exact), *fused][:limit]
    signal_sets: dict[ExternalSignal, set[str]] = {
        "exact_title": exact,
        "lexical": set(baseline.lexical_urls),
        "semantic": set(semantic_urls or ()),
    }
    return [
        ExternalDuplicateSuggestion(
            rank=rank,
            signals=[signal for signal in SIGNAL_ORDER if url in signal_sets[signal]],
            reference=ExternalCandidateReference.model_validate(
                by_url[url].model_dump(exclude={"body"})
            ),
        )
        for rank, url in enumerate(urls, start=1)
    ]


def _extended_page(
    page: DuplicateSuggestionPage,
    payload: DuplicateSuggestionRequest,
    items: list[ExternalDuplicateSuggestion],
    scope: ExternalScope,
) -> DuplicateSuggestionPage:
    result = DuplicateSuggestionPage.model_validate(
        {
            **page.model_dump(mode="json"),
            "external_items": [item.model_dump(mode="json") for item in items],
            "external_candidate_count": len(payload.external_candidates),
            "external_scope": scope,
        }
    )
    require_external_correspondence(
        result, payload.external_candidates, payload.title, payload.limit
    )
    return result
