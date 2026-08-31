"""Local dense retrieval and deterministic fusion with PostgreSQL lexical search."""

from __future__ import annotations

import hashlib
import math
import os
from collections.abc import Sequence
from threading import Lock
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from mnemonic_api.models import Handoff, HandoffEmbedding

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_BODY_CHARS = 1500
EMBED_BATCH_SIZE = 16
EMBED_CONFIG = f"{EMBED_MODEL}:title-summary-prompt-{EMBED_BODY_CHARS}:v1"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
RRF_K = 60
RRF_LEXICAL_WEIGHT = 3.0


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


def embedding_text(handoff: Handoff) -> str:
    return f"{handoff.title}\n{handoff.summary}\n{handoff.prompt[:EMBED_BODY_CHARS]}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _valid_vector(vector: Sequence[float], dimensions: int | None = None) -> bool:
    return bool(vector) and (dimensions is None or len(vector) == dimensions) and all(
        math.isfinite(value) for value in vector
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


def _cached_embeddings(
    database: Session, handoffs: Sequence[Handoff], embedder: Embedder
) -> dict[UUID, list[float]]:
    ids = [handoff.id for handoff in handoffs]
    if not ids:
        return {}
    cached = {
        row.handoff_id: row
        for row in database.scalars(
            select(HandoffEmbedding).where(HandoffEmbedding.handoff_id.in_(ids))
        )
    }
    texts = {handoff.id: embedding_text(handoff) for handoff in handoffs}
    digests = {handoff_id: _digest(text) for handoff_id, text in texts.items()}
    vectors = {
        handoff_id: list(row.vector)
        for handoff_id, row in cached.items()
        if row.model == EMBED_CONFIG
        and row.digest == digests[handoff_id]
        and _valid_vector(row.vector)
    }
    stale = [handoff for handoff in handoffs if handoff.id not in vectors]
    for start in range(0, len(stale), EMBED_BATCH_SIZE):
        batch = stale[start : start + EMBED_BATCH_SIZE]
        embedded = embedder.embed_documents([texts[handoff.id] for handoff in batch])
        if len(embedded) != len(batch):
            raise ValueError("Embedding model returned the wrong number of vectors")
        dimensions = len(embedded[0]) if embedded else 0
        if not dimensions or any(not _valid_vector(vector, dimensions) for vector in embedded):
            raise ValueError("Embedding model returned an invalid vector")
        rows = [
            {
                "handoff_id": handoff.id,
                "model": EMBED_CONFIG,
                "digest": digests[handoff.id],
                "vector": vector,
            }
            for handoff, vector in zip(batch, embedded, strict=True)
        ]
        statement = insert(HandoffEmbedding).values(rows)
        database.execute(
            statement.on_conflict_do_update(
                index_elements=[HandoffEmbedding.handoff_id],
                set_={
                    "model": statement.excluded.model,
                    "digest": statement.excluded.digest,
                    "vector": statement.excluded.vector,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )
        database.commit()
        vectors.update(
            {handoff.id: vector for handoff, vector in zip(batch, embedded, strict=True)}
        )
    return vectors


def hybrid_rank(
    database: Session,
    handoffs: Sequence[Handoff],
    lexical_ids: Sequence[UUID],
    query: str,
    embedder: Embedder,
) -> list[Handoff]:
    """Fuse current lexical/literal ranks with dense ranks using weighted RRF."""
    if not handoffs:
        return []
    vectors = _cached_embeddings(database, handoffs, embedder)
    query_vector = embedder.embed_query(BGE_QUERY_PREFIX + query)
    if not _valid_vector(query_vector):
        raise ValueError("Embedding model returned an invalid query vector")
    similarities = {
        handoff.id: cosine_similarity(query_vector, vectors.get(handoff.id, []))
        for handoff in handoffs
    }
    # Python's stable sort preserves the deterministic updated/id base order for ties.
    base = sorted(handoffs, key=lambda item: (item.updated_at, item.id.int), reverse=True)
    dense = sorted(base, key=lambda item: similarities[item.id], reverse=True)
    dense_rank = {handoff.id: rank for rank, handoff in enumerate(dense, start=1)}
    lexical_rank = {handoff_id: rank for rank, handoff_id in enumerate(lexical_ids, start=1)}

    def fusion_score(handoff: Handoff) -> float:
        dense_score = 1.0 / (RRF_K + dense_rank[handoff.id])
        lexical_position = lexical_rank.get(handoff.id)
        lexical_score = (
            RRF_LEXICAL_WEIGHT / (RRF_K + lexical_position) if lexical_position else 0.0
        )
        return dense_score + lexical_score

    return sorted(
        base,
        key=lambda item: (
            fusion_score(item),
            similarities[item.id],
            -lexical_rank.get(item.id, 10**9),
        ),
        reverse=True,
    )
