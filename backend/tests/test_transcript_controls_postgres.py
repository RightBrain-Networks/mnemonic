"""Server ordering and filesystem health accounting used by transcript controls."""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import update

from mnemonic_api.models import Transcript
from mnemonic_api.transcript_health import TranscriptHealthReporter
from mnemonic_api.transcript_usage import directory_usage

from .test_transcript_indexing_postgres import collection, register


@pytest.mark.postgres
@pytest.mark.parametrize("field", ["name", "size", "session", "indexing", "updated"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_transcript_sort_is_stable_across_pages_and_search_results(
    api, project, work_payload, tmp_path, field, direction,
):
    identities = []
    for number in range(3):
        directory = tmp_path / str(number)
        directory.mkdir()
        _, _, record, _ = register(api, project, work_payload, directory,
                                    filename=f"name-{number}.jsonl")
        # Metadata sorting must work even before bodies can be indexed.
        with api.app.state.session_factory.begin() as database:
            database.execute(update(Transcript).where(Transcript.id == UUID(record["id"])).values(
                size_bytes=(number + 1) * 100,
                last_updated_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number)))
        identities.append(record["id"])
    path = collection(project)
    params = {"sort_by": field, "sort_direction": direction, "detail": "full", "limit": 100}
    full = api.get(path, params=params)
    assert full.status_code == 200, full.text
    expected = [row["id"] for row in full.json()["items"]]
    assert len(set(expected)) == 3
    if field in {"name", "size", "updated"}:
        assert expected == (identities if direction == "asc" else list(reversed(identities)))
    else:
        assert expected == sorted(identities)  # Ties use immutable identity in either direction.
    for query in [None, "name"]:
        options = params | ({"query": query} if query else {})
        paged = [api.get(path, params=options | {"limit": 1, "offset": offset})
                 .json()["items"][0]["id"] for offset in range(3)]
        assert paged == expected
    assert api.get(path, params={"sort_by": "source_path; DROP TABLE transcripts"})\
        .status_code == 422


def test_storage_usage_measures_allocated_files_without_following_links(tmp_path):
    root = tmp_path / "private"
    root.mkdir()
    source = root / "retained.jsonl"
    with source.open("wb") as content:
        content.write(b"native\n")
        content.truncate(100_000_000)
    os.link(source, root / "same-file.jsonl")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "not-a-transcript").write_bytes(b"x" * 100_000)
    (root / "symlink").symlink_to(outside, target_is_directory=True)
    usage = directory_usage(root)
    assert usage.complete and usage.error_code is None
    assert usage.logical_bytes == 100_000_000
    assert usage.bytes == (source.stat().st_blocks + root.stat().st_blocks
                           + (root / "symlink").lstat().st_blocks) * 512
    volume = os.statvfs(root)
    assert usage.total_bytes == volume.f_blocks * volume.f_frsize
    assert usage.free_bytes is not None and usage.free_bytes <= usage.total_bytes
    missing = directory_usage(tmp_path / "missing")
    assert not missing.complete and missing.bytes is None and missing.free_bytes is None


@pytest.mark.postgres
def test_health_reports_worker_native_storage_and_api_index_storage_separately(
    api, project, tmp_path,
):
    settings = api.app.state.settings
    settings.transcript_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.transcript_index_dir = tmp_path / "index"
    settings.transcript_index_dir.mkdir(mode=0o700)
    (settings.transcript_root / "orphaned-snapshot").write_bytes(b"native" * 1000)
    (settings.transcript_index_dir / "derived-index").write_bytes(b"index" * 1000)
    with api.app.state.session_factory.begin() as database:
        TranscriptHealthReporter().publish(database, settings)
    response = api.get(collection(project) + "/health")
    assert response.status_code == 200, response.text
    storage = response.json()["storage"]
    assert storage["scope"] == "all_projects" and storage["database_bytes"] > 0
    for name in ["transcripts", "index"]:
        assert storage[name]["complete"]
        assert storage[name]["bytes"] >= 5000
        assert storage[name]["free_bytes"] > 0


@pytest.mark.postgres
@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_indexing_sort_orders_the_visible_copy_index_and_waiting_states(
    api, project, work_payload, tmp_path, postgres_engine, direction,
):
    from .test_leases_postgres import expire_lease
    from .test_transcript_indexing_postgres import run

    identities = {}
    for state in ["Indexed", "Copy failed", "Failed", "Waiting"]:
        folder = tmp_path / state
        folder.mkdir()
        work, _, record, source = register(api, project, work_payload, folder)
        identities[state] = record["id"]
        if state == "Waiting":
            continue
        if state == "Copy failed":
            source.unlink()
        if state == "Failed":
            source.write_text("malformed native content")
        expire_lease(postgres_engine, work["id"])
        assert run(api)
    expected = [identities[state] for state in sorted(identities, reverse=direction == "desc")]
    for offset in range(4):
        result = api.get(collection(project), params={"sort_by": "indexing",
            "sort_direction": direction, "limit": 1, "offset": offset}).json()
        assert result["items"][0]["id"] == expected[offset]
