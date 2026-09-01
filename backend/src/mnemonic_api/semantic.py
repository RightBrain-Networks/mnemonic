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

from mnemonic_api.models import Checkpoint, WorkItem, WorkItemEmbedding

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


def _cached_embeddings(
    database: Session, work_items: Sequence[WorkItem], embedder: Embedder
) -> dict[UUID, list[float]]:
    ids = [work_item.id for work_item in work_items]
    if not ids:
        return {}
    cached = {
        row.work_item_id: row
        for row in database.scalars(
            select(WorkItemEmbedding).where(WorkItemEmbedding.work_item_id.in_(ids))
        )
    }
    initial_ids = {work_item.initial_checkpoint_id for work_item in work_items}
    initial_prompts = dict(
        database.execute(
            select(Checkpoint.id, Checkpoint.prompt).where(Checkpoint.id.in_(initial_ids))
        ).all()
    )
    later_texts: dict[UUID, str] = {}
    initial_by_work = {
        work_item.id: work_item.initial_checkpoint_id for work_item in work_items
    }
    for work_item_id, checkpoint_id, prompt in database.execute(
        select(Checkpoint.work_item_id, Checkpoint.id, Checkpoint.prompt)
        .where(Checkpoint.work_item_id.in_(ids))
        .order_by(Checkpoint.created_at, Checkpoint.id)
    ):
        if checkpoint_id == initial_by_work[work_item_id]:
            continue
        previous = later_texts.get(work_item_id, "")
        combined = f"{previous}\n{prompt}" if previous else prompt
        later_texts[work_item_id] = combined[-EMBED_COMMENT_CHARS:]
    texts = {
        work_item.id: embedding_text(
            work_item,
            initial_prompts[work_item.initial_checkpoint_id],
            (later_texts.get(work_item.id, ""),),
        )
        for work_item in work_items
    }
    digests = {work_item_id: _digest(text) for work_item_id, text in texts.items()}
    vectors = {
        work_item_id: list(row.vector)
        for work_item_id, row in cached.items()
        if row.model == EMBED_CONFIG
        and row.digest == digests[work_item_id]
        and _valid_vector(row.vector)
    }
    stale = [work_item for work_item in work_items if work_item.id not in vectors]
    for start in range(0, len(stale), EMBED_BATCH_SIZE):
        batch = stale[start : start + EMBED_BATCH_SIZE]
        embedded = embedder.embed_documents([texts[work_item.id] for work_item in batch])
        if len(embedded) != len(batch):
            raise ValueError("Embedding model returned the wrong number of vectors")
        dimensions = len(embedded[0]) if embedded else 0
        if not dimensions or any(not _valid_vector(vector, dimensions) for vector in embedded):
            raise ValueError("Embedding model returned an invalid vector")
        rows = [
            {
                "work_item_id": work_item.id,
                "model": EMBED_CONFIG,
                "digest": digests[work_item.id],
                "vector": vector,
            }
            for work_item, vector in zip(batch, embedded, strict=True)
        ]
        statement = insert(WorkItemEmbedding).values(rows)
        database.execute(
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
        vectors.update(
            {work_item.id: vector for work_item, vector in zip(batch, embedded, strict=True)}
        )
    return vectors


def hybrid_rank(
    database: Session,
    work_items: Sequence[WorkItem],
    lexical_ids: Sequence[UUID],
    query: str,
    embedder: Embedder,
) -> list[WorkItem]:
    """Fuse current lexical/literal ranks with dense ranks using weighted RRF."""
    if not work_items:
        return []
    vectors = _cached_embeddings(database, work_items, embedder)
    query_vector = embedder.embed_query(BGE_QUERY_PREFIX + query)
    if not _valid_vector(query_vector):
        raise ValueError("Embedding model returned an invalid query vector")
    similarities = {
        work_item.id: cosine_similarity(query_vector, vectors.get(work_item.id, []))
        for work_item in work_items
    }
    # Python's stable sort preserves the deterministic updated/id base order for ties.
    base = sorted(work_items, key=lambda item: (item.updated_at, item.id.int), reverse=True)
    dense = sorted(base, key=lambda item: similarities[item.id], reverse=True)
    dense_rank = {work_item.id: rank for rank, work_item in enumerate(dense, start=1)}
    lexical_rank = {work_item_id: rank for rank, work_item_id in enumerate(lexical_ids, start=1)}

    def fusion_score(work_item: WorkItem) -> float:
        dense_score = 1.0 / (RRF_K + dense_rank[work_item.id])
        lexical_position = lexical_rank.get(work_item.id)
        lexical_score = RRF_LEXICAL_WEIGHT / (RRF_K + lexical_position) if lexical_position else 0.0
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
