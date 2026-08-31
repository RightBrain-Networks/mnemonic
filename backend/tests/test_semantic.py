from types import SimpleNamespace

import pytest

from mnemonic_api.semantic import (
    EMBED_BODY_CHARS,
    EMBED_COMMENT_CHARS,
    cosine_similarity,
    embedding_text,
)


def test_cosine_similarity_rejects_invalid_vectors():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0
    assert cosine_similarity([float("nan")], [1.0]) == 0.0
    assert cosine_similarity([], []) == 0.0


def test_embedding_text_is_bounded_and_preserves_retrieval_fields():
    handoff = SimpleNamespace(
        title="Title",
        summary="Retrieval summary",
    )
    text = embedding_text(handoff, "p" * (EMBED_BODY_CHARS + 50))
    assert text == f"Title\nRetrieval summary\n{'p' * EMBED_BODY_CHARS}"


def test_embedding_text_includes_bounded_recent_comments():
    handoff = SimpleNamespace(title="Title", summary="Summary")
    text = embedding_text(
        handoff, "Prompt", ["old", "x" * EMBED_COMMENT_CHARS, "new"]
    )
    assert text.startswith("Title\nSummary\nPrompt\n")
    assert text.endswith("new")
    assert len(text.split("Prompt\n", 1)[1]) == EMBED_COMMENT_CHARS
