"""Local dense retrieval and deterministic fusion with PostgreSQL lexical search."""

from __future__ import annotations

import hashlib
import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from mnemonic_api.database import database_sqlstate
from mnemonic_api.models import WorkItem, WorkItemEmbedding

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_BODY_CHARS = 1500
EMBED_COMMENT_CHARS = 1500
EMBED_BATCH_SIZE = 16
EMBED_CONFIG = (
    f"{EMBED_MODEL}:work-title-summary-initial-{EMBED_BODY_CHARS}-later-"
    f"{EMBED_COMMENT_CHARS}:v3"
)
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RRF_K = 60
RRF_LEXICAL_WEIGHT = 3.0
EMBED_CACHE_LOCK_TIMEOUT_MS = 50
EMBED_CACHE_STATEMENT_TIMEOUT_MS = 5_000

logger = logging.getLogger(__name__)

_BOUNDED_EMBEDDING_TEXTS_SQL = """
SELECT work.id,
       pg_catalog.left(initial_checkpoint.prompt, :body_chars) AS initial_prompt,
       COALESCE((
           SELECT pg_catalog.right(
               pg_catalog.string_agg(
                   tail_checkpoint.prompt,
                   E'\\n' ORDER BY tail_checkpoint.created_at, tail_checkpoint.id
               ),
               :tail_chars
           )
           FROM (
               SELECT pg_catalog.right(
                          later.prompt,
                          CAST(:tail_chars AS integer)
                      ) AS prompt,
                      later.created_at,
                      later.id,
                      pg_catalog.sum(
                          LEAST(
                              pg_catalog.length(later.prompt),
                              CAST(:tail_chars AS integer)
                          ) + 1
                      ) OVER (
                          ORDER BY later.created_at DESC, later.id DESC
                      ) AS reverse_characters
               FROM checkpoints AS later
               WHERE later.work_item_id = work.id
                 AND later.id <> work.initial_checkpoint_id
           ) AS tail_checkpoint
           WHERE tail_checkpoint.reverse_characters
                 - (LEAST(
                     pg_catalog.length(tail_checkpoint.prompt),
                     CAST(:tail_chars AS integer)
                 ) + 1) <= CAST(:tail_chars AS integer)
       ), '') AS checkpoint_tail
FROM work_items AS work
JOIN checkpoints AS initial_checkpoint
  ON initial_checkpoint.id = work.initial_checkpoint_id
 AND initial_checkpoint.work_item_id = work.id
WHERE work.id = ANY(CAST(:work_item_ids AS uuid[]))
"""


class Embedder(Protocol):
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastembedEmbedder:
    """Thread-safe lazy wrapper around the image-bundled local embedding model."""

    def __init__(self) -> None:
        self._model = None
        self._lock = Lock()

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            cache_dir = os.getenv("MNEMONIC_EMBEDDING_CACHE")
            self._model = TextEmbedding(EMBED_MODEL, cache_dir=cache_dir)
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            return [[float(value) for value in vector] for vector in self._load().embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        with self._lock:
            vector = next(iter(self._load().query_embed([text])))
            return [float(value) for value in vector]


def warm_embedding_model() -> None:
    """Download/load the configured model during an image build, never during a request."""
    FastembedEmbedder().embed_query(BGE_QUERY_PREFIX + "warm local semantic retrieval")


def embedding_text(
    work_item: WorkItem, initial_prompt: str, later_checkpoints: Sequence[str] = ()
) -> str:
    prompt = initial_prompt[:EMBED_BODY_CHARS]
    progress = "\n".join(later_checkpoints)[-EMBED_COMMENT_CHARS:]
    base = f"{work_item.title}\n{work_item.summary}\n{prompt}"
    return f"{base}\n{progress}" if progress else base


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_vector(vector: Sequence[float], dimensions: int | None = None) -> bool:
    return (
        bool(vector)
        and (dimensions is None or len(vector) == dimensions)
        and all(math.isfinite(value) for value in vector)
    )


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not _valid_vector(left) or not _valid_vector(right, len(left)):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _embedding_texts(
    database: Session, work_items: Sequence[WorkItem]
) -> dict[UUID, str]:
    ids = [work_item.id for work_item in work_items]
    if not ids:
        return {}
    rows = database.execute(
        text(_BOUNDED_EMBEDDING_TEXTS_SQL),
        {
            "work_item_ids": ids,
            "body_chars": EMBED_BODY_CHARS,
            "tail_chars": EMBED_COMMENT_CHARS,
        },
    ).mappings()
    captured = {
        row["id"]: (str(row["initial_prompt"]), str(row["checkpoint_tail"]))
        for row in rows
    }
    if set(captured) != set(ids):
        raise ValueError("A semantic candidate is missing its initial checkpoint")
    return {
        work_item.id: embedding_text(
            work_item,
            captured[work_item.id][0],
            (captured[work_item.id][1],),
        )
        for work_item in work_items
    }


@dataclass(frozen=True)
class EmbeddingCandidate:
    work_item: WorkItem
    text: str
    digest: str
    cached_vector: tuple[float, ...] | None


@dataclass(frozen=True)
class EmbeddingCacheUpdate:
    work_item_id: UUID
    project_id: UUID
    work_version: int
    digest: str
    vector: tuple[float, ...]


def semantic_query_vector(embedder: Embedder, query: str) -> tuple[float, ...]:
    """Run query inference before a database snapshot occupies a connection."""
    vector = tuple(embedder.embed_query(BGE_QUERY_PREFIX + query))
    if not _valid_vector(vector):
        raise ValueError("Embedding model returned an invalid query vector")
    return vector


def capture_embedding_candidates(
    database: Session,
    work_items: Sequence[WorkItem],
    *,
    dimensions: int,
) -> list[EmbeddingCandidate]:
    """Capture bounded text and valid cache rows inside the response snapshot."""
    if not work_items:
        return []
    texts = _embedding_texts(database, work_items)
    digests = {work_item_id: _digest(value) for work_item_id, value in texts.items()}
    cached = {
        row.work_item_id: row
        for row in database.scalars(
            select(WorkItemEmbedding).where(
                WorkItemEmbedding.work_item_id.in_([item.id for item in work_items])
            )
        )
    }
    candidates: list[EmbeddingCandidate] = []
    for work_item in work_items:
        cached_row = cached.get(work_item.id)
        vector = (
            tuple(cached_row.vector)
            if cached_row is not None
            and cached_row.model == EMBED_CONFIG
            and cached_row.digest == digests[work_item.id]
            and _valid_vector(cached_row.vector, dimensions)
            else None
        )
        candidates.append(
            EmbeddingCandidate(
                work_item=work_item,
                text=texts[work_item.id],
                digest=digests[work_item.id],
                cached_vector=vector,
            )
        )
    return candidates


def rank_embedding_candidates(
    candidates: Sequence[EmbeddingCandidate],
    lexical_ids: Sequence[UUID],
    query_vector: Sequence[float],
    embedder: Embedder,
) -> tuple[list[UUID], list[EmbeddingCacheUpdate]]:
    """Rank an immutable snapshot and return disposable cache work separately."""
    if not candidates:
        return [], []
    dimensions = len(query_vector)
    vectors = {
        candidate.work_item.id: list(candidate.cached_vector)
        for candidate in candidates
        if candidate.cached_vector is not None
    }
    stale = [candidate for candidate in candidates if candidate.cached_vector is None]
    updates: list[EmbeddingCacheUpdate] = []
    for start in range(0, len(stale), EMBED_BATCH_SIZE):
        batch = stale[start : start + EMBED_BATCH_SIZE]
        embedded = embedder.embed_documents([candidate.text for candidate in batch])
        if len(embedded) != len(batch):
            raise ValueError("Embedding model returned the wrong number of vectors")
        if any(not _valid_vector(vector, dimensions) for vector in embedded):
            raise ValueError("Embedding model returned an invalid vector")
        for candidate, vector in zip(batch, embedded, strict=True):
            vectors[candidate.work_item.id] = list(vector)
            updates.append(
                EmbeddingCacheUpdate(
                    work_item_id=candidate.work_item.id,
                    project_id=candidate.work_item.project_id,
                    work_version=candidate.work_item.version,
                    digest=candidate.digest,
                    vector=tuple(vector),
                )
            )
    similarities = {
        candidate.work_item.id: cosine_similarity(
            query_vector, vectors.get(candidate.work_item.id, [])
        )
        for candidate in candidates
    }
    # Python's stable sort preserves the deterministic updated/id base order for ties.
    base = sorted((candidate.work_item for candidate in candidates), key=lambda item: item.id.int)
    base.sort(key=lambda item: item.updated_at, reverse=True)
    dense = sorted(base, key=lambda item: similarities[item.id], reverse=True)
    dense_rank = {work_item.id: rank for rank, work_item in enumerate(dense, start=1)}
    lexical_rank = {work_item_id: rank for rank, work_item_id in enumerate(lexical_ids, start=1)}

    def fusion_score(work_item: WorkItem) -> float:
        dense_score = 1.0 / (RRF_K + dense_rank[work_item.id])
        lexical_position = lexical_rank.get(work_item.id)
        lexical_score = RRF_LEXICAL_WEIGHT / (RRF_K + lexical_position) if lexical_position else 0.0
        return dense_score + lexical_score

    ranked = sorted(
        base,
        key=lambda item: (
            fusion_score(item),
            similarities[item.id],
            -lexical_rank.get(item.id, 10**9),
        ),
        reverse=True,
    )
    return [work_item.id for work_item in ranked], updates


def persist_embedding_updates(
    database: Session, updates: Sequence[EmbeddingCacheUpdate]
) -> None:
    """Persist snapshot vectors with a fresh, row-locked version/digest compare-and-set."""
    if not updates:
        return
    updates_by_id = {update.work_item_id: update for update in updates}
    # The response session retains its repeatable-read identity map after commit.
    # A distinct session is required both to observe committed writers and to
    # leave the captured response objects untouched for later serialization.
    try:
        with Session(bind=database.get_bind()) as cache_database:
            cache_database.execute(
                text(
                    "SELECT set_config('lock_timeout', :lock_timeout, true), "
                    "set_config('statement_timeout', :statement_timeout, true)"
                ),
                {
                    "lock_timeout": f"{EMBED_CACHE_LOCK_TIMEOUT_MS}ms",
                    "statement_timeout": f"{EMBED_CACHE_STATEMENT_TIMEOUT_MS}ms",
                },
            )
            current_work = list(
                cache_database.scalars(
                    select(WorkItem)
                    .where(
                        WorkItem.id.in_(updates_by_id),
                        WorkItem.deleted_at.is_(None),
                    )
                    .order_by(WorkItem.id)
                    .with_for_update(skip_locked=True)
                )
            )
            current_texts = _embedding_texts(cache_database, current_work)
            rows = [
                {
                    "work_item_id": work_item.id,
                    "model": EMBED_CONFIG,
                    "digest": update.digest,
                    "vector": list(update.vector),
                }
                for work_item in current_work
                if (update := updates_by_id[work_item.id]).project_id
                == work_item.project_id
                and update.work_version == work_item.version
                and update.digest == _digest(current_texts[work_item.id])
            ]
            if rows:
                statement = insert(WorkItemEmbedding).values(rows)
                cache_database.execute(
                    statement.on_conflict_do_update(
                        index_elements=[WorkItemEmbedding.work_item_id],
                        set_={
                            "model": statement.excluded.model,
                            "digest": statement.excluded.digest,
                            "vector": statement.excluded.vector,
                            "updated_at": statement.excluded.updated_at,
                        },
                    )
                )
            cache_database.commit()
    except DBAPIError as error:
        if database_sqlstate(error) not in {"55P03", "57014"}:
            raise
        logger.warning("Semantic cache refresh skipped after bounded database wait")
