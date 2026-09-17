"""Deterministic bounded passage positions and effective local-model configuration."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from mnemonic_api.artifact_tokenizer import PassageTokenizer
from mnemonic_api.models import Base
from mnemonic_api.semantic import EMBED_MODEL

INDEXES = Base.metadata.tables["artifact_passage_indexes"]
PASSAGES = Base.metadata.tables["artifact_passages"]
PASSAGE_CHARS = 1500
PASSAGE_OVERLAP = 200
PASSAGE_BATCH_SIZE = 16
PASSAGE_STRIDE = PASSAGE_CHARS - PASSAGE_OVERLAP
CHUNK_CONFIG = "tokenfit1500o200v2"
PASSAGE_MODEL = EMBED_MODEL
BATCH_CHARS = PASSAGE_STRIDE * (PASSAGE_BATCH_SIZE - 1) + PASSAGE_CHARS


@dataclass(frozen=True)
class Passage:
    ordinal: int
    start: int
    end: int
    text: str
    token_count: int


def next_passage_offset(passage: Passage) -> int:
    overlap = min(PASSAGE_OVERLAP, (passage.end - passage.start) // 4)
    return passage.end - overlap


def passage_batch(text: str, offset: int, ordinal: int, total_chars: int,
                  tokenizer: PassageTokenizer) -> list[Passage]:
    results = []
    start = offset
    for index in range(PASSAGE_BATCH_SIZE):
        if start >= total_chars:
            break
        end = min(total_chars, start + PASSAGE_CHARS)
        fragment = text[start - offset:end - offset]
        if len(fragment) != end - start:
            raise ValueError("A passage batch must contain its complete source range")
        length, token_count = tokenizer.fit(fragment)
        passage = Passage(ordinal + index, start, start + length, fragment[:length], token_count)
        results.append(passage)
        if passage.end == total_chars:
            break
        start = next_passage_offset(passage)
    return results


def passage_identity(artifact_id: UUID, revision: int, text_sha256: str, model: str,
                     chunk_config: str, passage: Passage) -> str:
    return hashlib.sha256(json.dumps([str(artifact_id), revision, text_sha256, model,
                                     chunk_config, passage.start, passage.end],
                                    separators=(",", ":")).encode()).hexdigest()
