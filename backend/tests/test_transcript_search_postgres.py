"""Large transcript collections stream cold builds and hydrate only selected snippets."""

import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event

from mnemonic_api.models import Transcript

from .test_transcript_indexing_postgres import collection
from .test_unified_search_postgres import path as unified_path

pytestmark = pytest.mark.postgres


def seed_transcripts(api, project, body, count=1):
    identities = [uuid4() for _ in range(count)]
    with api.app.state.session_factory() as database:
        for identity in identities:
            database.add(Transcript(
                id=identity, import_project_id=UUID(project["id"]), kind="imported",
                client="claude-code", source_path=f"/synthetic/archive/{identity}.jsonl",
                status="ready", normalized_text=body,
                text_sha256=hashlib.sha256(body.encode()).hexdigest(),
                indexing_completed_at=datetime.now(UTC),
            ))
        database.commit()
    return identities


def search(api, project, endpoint="content", *, query="needle", fulltext=True, **pagination):
    payload = {"query": query, "fulltext": fulltext, **pagination}
    if endpoint == "list":
        return api.get(collection(project), params=payload)
    if endpoint == "unified":
        payload["q"] = payload.pop("query")
        return api.post(unified_path(project), json={**payload, "facets": ["transcripts"]})
    return api.post(collection(project) + "/search-content", json=payload)


@contextmanager
def body_reads(engine):
    reads = []

    def before_cursor(_conn, _cursor, _statement, _parameters, context, _executemany):
        columns = getattr(getattr(context.compiled, "statement", None), "column_descriptions", [])
        if any(column.get("entity") is Transcript and column.get("name") == "normalized_text"
               for column in columns):
            reads.append(context.execution_options.get("yield_per"))

    event.listen(engine, "before_cursor_execute", before_cursor)
    try:
        yield reads
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor)


def test_large_library_search_reuses_streamed_index_and_fetches_only_page_bodies(
    api, project, postgres_engine,
):
    body = "needle aperture\n" + "archival documentation " * 80_000
    identities = seed_transcripts(api, project, body, count=20)
    assert len(body.encode()) * len(identities) > 32_000_000
    with body_reads(postgres_engine) as reads:
        first = search(api, project, limit=2, offset=3)
        assert first.status_code == 200, first.text
        assert first.json()["total"] == 20
        assert len(first.json()["items"]) == 2
        assert all("needle" in item["snippet"] for item in first.json()["items"])
        assert reads == [1, None, None]  # One streaming cursor, two page snippets.
        reads.clear()
        warm = search(api, project, "list", query="aperture", limit=1, offset=10)
        assert warm.status_code == 200, warm.text
        assert warm.json()["total"] == 20
        assert reads == [None]  # New query reuses the corpus without fetching its bodies.
        reads.clear()
        unified = search(api, project, "unified", limit=1, offset=4)
        assert unified.status_code == 200, unified.text
        assert unified.json()["total"] == 20
        assert "needle" in unified.json()["items"][0]["transcript"]["snippet"]
        assert reads == [None]  # Both search entry points share the same corpus cache.
        reads.clear()
        empty = search(api, project, limit=1, offset=20)
        assert empty.status_code == 200 and empty.json()["items"] == []
        assert reads == []


@pytest.mark.parametrize("endpoint", ["list", "content", "unified"])
def test_content_budget_uses_utf8_bytes_and_applies_even_to_warm_cache(
    api, project, postgres_engine, endpoint,
):
    body = "needle " + "🦊" * 2000
    seed_transcripts(api, project, body)
    settings = api.app.state.settings
    settings.transcript_search_max_bytes = len(body.encode())
    assert search(api, project, endpoint).status_code == 200
    settings.transcript_search_max_bytes -= 1
    with body_reads(postgres_engine) as reads:
        response = search(api, project, endpoint)
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "transcript_search_capacity"
        assert "configured size limit" in response.json()["detail"]["message"]
        assert reads == []
        metadata = search(api, project, endpoint, query="archive", fulltext=False)
        assert metadata.status_code == 200 and metadata.json()["total"] == 1
        assert reads == []  # An oversized body never blocks or loads during metadata search.
    settings.transcript_search_max_bytes += 1
    assert search(api, project, endpoint).status_code == 200


def test_unified_pagination_does_not_load_transcript_snippets_outside_global_page(
    api, project, postgres_engine, work_payload,
):
    seed_transcripts(api, project, "needle transcript", count=3)
    work = api.post(f"/api/v1/projects/{project['id']}/work-items",
                    json={**work_payload, "title": "Needle work"})
    assert work.status_code == 201, work.text
    payload = {"q": "needle", "fulltext": True, "facets": ["work_items", "transcripts"],
               "facet_order": [{"facet": "work_items"}], "limit": 1}
    with body_reads(postgres_engine) as reads:
        page = api.post(unified_path(project), json=payload)
        assert page.status_code == 200, page.text
        assert page.json()["total"] == 4
        assert page.json()["items"][0]["facet"] == "work_items"
        assert reads == [1]
        reads.clear()
        page = api.post(unified_path(project), json={**payload, "offset": 1})
        assert page.status_code == 200, page.text
        assert page.json()["items"][0]["facet"] == "transcripts"
        assert reads == [None]
