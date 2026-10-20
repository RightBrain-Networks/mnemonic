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
    work_item = SimpleNamespace(
        title="Title",
        summary="Retrieval summary",
    )
    text = embedding_text(work_item, "p" * (EMBED_BODY_CHARS + 50))
    assert text == f"Title\nRetrieval summary\n{'p' * EMBED_BODY_CHARS}"


def test_embedding_text_includes_bounded_recent_checkpoints():
    work_item = SimpleNamespace(title="Title", summary="Summary")
    text = embedding_text(
        work_item, "Prompt", ["old", "x" * EMBED_COMMENT_CHARS, "new"]
    )
    assert text.startswith("Title\nSummary\nPrompt\n")
    assert text.endswith("new")
    assert len(text.split("Prompt\n", 1)[1]) == EMBED_COMMENT_CHARS


def test_model_pool_allows_independent_calls_and_enforces_its_worker_bound(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    from mnemonic_api import semantic

    entered, release = Event(), Event()
    lock = Lock()
    active = peak = 0
    identities = set()

    class Worker:
        def __init__(self, threads):
            assert threads == 1

        def embed_query(self, _text):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                identities.add(id(self))
                if active == 2:
                    entered.set()
            try:
                assert release.wait(5)
                return [1, 0]
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(semantic, "_EmbeddingWorker", Worker)
    model = semantic.FastembedEmbedder(workers=2, threads=1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(model.embed_query, "synthetic") for _ in range(3)]
        try:
            assert entered.wait(5), "Independent model workers should execute concurrently"
        finally:
            release.set()
        assert [future.result(5) for future in futures] == [[1, 0]] * 3
    assert peak == 2 and len(identities) == 2


def test_model_pool_returns_worker_after_a_native_failure(monkeypatch):
    from mnemonic_api import semantic

    class Worker:
        def __init__(self, threads):
            self.failed = False

        def embed_query(self, _text):
            if not self.failed:
                self.failed = True
                raise ValueError("synthetic model failure")
            return [1, 0]

    monkeypatch.setattr(semantic, "_EmbeddingWorker", Worker)
    model = semantic.FastembedEmbedder()
    with pytest.raises(ValueError):
        model.embed_query("first")
    assert model.embed_query("next") == [1, 0]


def test_native_model_bounds_document_batches_without_changing_order_or_query(monkeypatch):
    import sys

    from mnemonic_api import semantic

    loads = []
    batches = []
    queries = []

    class Model:
        def __init__(self, name, *, cache_dir, threads, enable_cpu_mem_arena=True):
            loads.append((name, cache_dir, threads, enable_cpu_mem_arena))

        def embed(self, texts, batch_size=256):
            for start in range(0, len(texts), batch_size):
                batch = texts[start:start + batch_size]
                batches.append(batch)
                yield from ([int(text), 1] for text in batch)

        def query_embed(self, texts):
            queries.extend(texts)
            yield [1, 0]

    monkeypatch.setitem(sys.modules, "fastembed", SimpleNamespace(TextEmbedding=Model))
    monkeypatch.setenv("MNEMONIC_EMBEDDING_CACHE", "/synthetic/model-cache")
    embedder = semantic.FastembedEmbedder(workers=2, threads=1)
    texts = [str(index) for index in range(19)]
    assert loads == []  # Models remain lazy and serial calls reuse one pool member.
    assert embedder.embed_documents(texts) == [[float(index), 1.0] for index in range(19)]
    assert max(map(len, batches)) <= 4
    assert [text for batch in batches for text in batch] == texts
    assert embedder.embed_query("exact query") == [1.0, 0.0]
    assert queries == ["exact query"]
    assert loads == [(semantic.EMBED_MODEL, "/synthetic/model-cache", 1, False)]
    assert embedder.embed_documents([]) == []
