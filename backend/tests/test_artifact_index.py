"""Exercise the real Rust index, not a mock lexical substitute."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument
from mnemonic_api.artifact_search_schemas import ArtifactSearchRequest
from mnemonic_api.errors import ApplicationError


def search(index, documents, query, *, fulltext=False, key="project:revision"):
    return index.search(
        key, lambda: iter(documents), query=query, fulltext=fulltext, count=len(documents)
    )


def test_real_tantivy_metadata_opt_in_mixed_terms_and_plain_snippets():
    index = ArtifactSearchIndex()
    documents = [
        SearchDocument(
            "one", "Quarterly report", "Confidential café payroll <script>alert()</script>"
        ),
        SearchDocument("two", "Payroll summary", "Ordinary numbers"),
    ]
    assert [hit.identity for hit in search(index, documents, "payroll").hits] == ["two"]
    result = search(index, documents, "report café", fulltext=True)
    assert [(hit.identity, hit.metadata, hit.content) for hit in result.hits] == [
        ("one", True, True)
    ]
    assert result.searcher is not None
    snippet = index.snippet(documents[0].content, "café", result.searcher)
    assert "café" in snippet
    assert "<script>" in snippet  # Quoted data, never an HTML fragment to render.
    assert "<b>" not in snippet


def test_query_operators_are_literal_and_cannot_opt_into_content():
    index = ArtifactSearchIndex()
    documents = [SearchDocument("one", "plain title", "private hidden")]
    for query in ["content:hidden", "*", "title OR hidden", '"hidden"', "-plain"]:
        hits = search(index, documents, query).hits
        assert bool(hits) is (query == "-plain")
    assert search(index, documents, "hidden", fulltext=True).hits
    assert not search(index, documents, "plain absent", fulltext=True).hits


def test_cache_reuses_snapshot_and_rebuilds_when_identity_changes():
    index = ArtifactSearchIndex()
    old = [SearchDocument("one", "file", "obsolete")]
    assert search(index, old, "obsolete", fulltext=True).hits

    def no_load():
        pytest.fail("An unchanged corpus must not reload extracted bodies")

    assert index.search("project:revision", no_load, query="obsolete", fulltext=True, count=1).hits
    assert not search(
        index,
        [SearchDocument("one", "file", "new")],
        "obsolete",
        fulltext=True,
        key="project:revision2",
    ).hits
    assert not search(
        index,
        [SearchDocument("one", "file")],
        "obsolete",
        fulltext=True,
        key="project:deleted",
    ).hits


def test_rank_ties_are_stable_and_project_switch_does_not_leak():
    index = ArtifactSearchIndex()
    docs = [SearchDocument("z", "same"), SearchDocument("a", "same")]
    assert [hit.identity for hit in search(index, docs, "same").hits] == ["a", "z"]
    assert not search(index, [SearchDocument("b", "other")], "same", key="other-project").hits


def test_empty_corpus_evicts_previous_content_index():
    index = ArtifactSearchIndex()
    assert search(index, [SearchDocument("a", "file", "private")], "private", fulltext=True).hits
    assert not search(index, [], "private", fulltext=True, key="empty").hits
    assert index._index is None


def test_busy_index_returns_explicit_bounded_error():
    index = ArtifactSearchIndex()
    with index._lock:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(search, index, [SearchDocument("id", "name")], "name")
            with pytest.raises(ApplicationError) as failure:
                future.result(timeout=2)
    assert failure.value.detail["code"] == "artifact_search_busy"


@pytest.mark.parametrize("query", ["", " ", "\x00", "a\nb", "\ud800", "x" * 201])
def test_search_query_validation(query):
    with pytest.raises(ValueError):
        ArtifactSearchRequest(q=query)
