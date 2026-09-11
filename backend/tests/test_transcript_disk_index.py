"""Real disk-index persistence, containment, failure cleanup and exclusive ownership."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from mnemonic_api.artifact_index import ArtifactSearchIndex, SearchDocument
from mnemonic_api.errors import ApplicationError

from .test_artifact_index import search


@pytest.fixture
def directory(tmp_path):
    path = tmp_path / "private index"
    path.mkdir(mode=0o700)
    return path


@pytest.fixture
def index(directory):
    value = ArtifactSearchIndex(directory)
    value.start()
    try:
        yield value
    finally:
        value.close()


def no_load():
    pytest.fail("A current disk snapshot must reopen without loading bodies")


def test_disk_cache_survives_restart_and_keeps_files_private(directory):
    docs = [SearchDocument("one", "Report", "café needle"),
            SearchDocument("two", "Needle", "unrelated")]
    index = ArtifactSearchIndex(directory)
    try:
        expected = search(index, docs, "needle", fulltext=True).hits
        assert (directory / "snapshot" / "meta.json").is_file()
        assert all(path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
                   for path in directory.rglob("*"))
    finally:
        index.close()
    reopened = ArtifactSearchIndex(directory)
    try:
        result = reopened.search(
            "project:revision", no_load, query="needle", fulltext=True, count=2)
        assert result.hits == expected
        assert reopened.snippet(docs[0].content, "cafe", result.searcher) == "café needle"
        assert [hit.identity for hit in reopened.search(
            "project:revision", no_load, query="needle", fulltext=False, count=2).hits] == ["two"]
    finally:
        reopened.close()


def test_changed_corpus_and_explicit_clear_discard_old_disk_cache(index, directory):
    old = search(index, [SearchDocument("one", "file", "oldneedle")], "oldneedle", fulltext=True)
    old_files = {path.name for path in (directory / "snapshot").glob("*.store")}
    current = [SearchDocument("two", "file", "newneedle")]
    assert not search(index, current, "oldneedle", fulltext=True, key="changed").hits
    current_files = {path.name for path in (directory / "snapshot").glob("*.store")}
    assert not old_files.intersection(current_files)
    # A response already being hydrated keeps its immutable searcher after eviction.
    assert index.snippet("oldneedle", "oldneedle", old.searcher) == "oldneedle"
    index.clear()
    assert not (directory / ".key").exists()
    assert not (directory / "snapshot").exists()
    assert search(index, current, "newneedle", fulltext=True, key="changed").hits


@pytest.mark.parametrize("failure", [OSError("private disk failure"),
                                     ApplicationError(503, "transcript_search_capacity", "Limit")])
def test_failed_build_is_never_reused_and_next_search_recovers(index, directory, failure):
    def broken():
        yield SearchDocument("one", "partial", "needle")
        raise failure

    with pytest.raises(ApplicationError) as rejected:
        index.search("partial", broken, query="needle", fulltext=True, count=2)
    assert rejected.value.detail["code"] == (
        failure.detail["code"] if isinstance(failure, ApplicationError)
        else "transcript_index_unavailable")
    assert "private disk failure" not in str(rejected.value.detail)
    assert not (directory / ".key").exists()
    assert not (directory / "snapshot").exists()
    assert search(index, [SearchDocument("two", "complete", "needle")], "needle",
                  fulltext=True).hits


def test_corrupt_completed_index_rebuilds_from_current_database_documents(index, directory):
    docs = [SearchDocument("one", "report", "needle")]
    assert search(index, docs, "needle", fulltext=True).hits
    index.close()
    (directory / "snapshot" / "meta.json").write_text("broken derived index")
    recovered = ArtifactSearchIndex(directory)
    try:
        assert search(recovered, docs, "needle", fulltext=True).hits
    finally:
        recovered.close()


@pytest.mark.parametrize("condition", ["missing", "file", "symlink", "public", "occupied"])
def test_bad_configured_directory_never_modifies_unrelated_contents(tmp_path, condition):
    target = tmp_path / "index"
    sentinel = tmp_path / "private-source"
    sentinel.write_bytes(b"unchanged source transcript")
    if condition == "file":
        target.write_bytes(b"not a directory")
    elif condition == "symlink":
        target.symlink_to(tmp_path, target_is_directory=True)
    elif condition in {"public", "occupied"}:
        target.mkdir(mode=0o755 if condition == "public" else 0o700)
        if condition == "occupied":
            (target / "source.jsonl").write_bytes(b"not cache data")
    index = ArtifactSearchIndex(target)
    try:
        with pytest.raises(ApplicationError, match="503") as rejected:
            index.start()
        assert rejected.value.detail["code"] == "transcript_index_unavailable"
        assert sentinel.read_bytes() == b"unchanged source transcript"
        if condition == "occupied":
            assert (target / "source.jsonl").read_bytes() == b"not cache data"
    finally:
        index.close()


def test_owner_mismatch_is_rejected_without_changing_permissions(directory, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: directory.stat().st_uid + 1)
    index = ArtifactSearchIndex(directory)
    with pytest.raises(ApplicationError):
        index.start()
    assert directory.stat().st_mode & 0o777 == 0o700
    assert list(directory.iterdir()) == []


def test_index_directory_cannot_be_shared_by_two_processes(index, directory):
    program = '''
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from mnemonic_api.artifact_index import ArtifactSearchIndex
from mnemonic_api.errors import ApplicationError
index = ArtifactSearchIndex(Path(sys.argv[1]))
try:
    index.start()
except ApplicationError as error:
    assert error.detail["code"] == "transcript_index_unavailable"
else:
    raise AssertionError("A second writer acquired the index directory")
'''
    subprocess.run([sys.executable, "-c", program, str(directory)], check=True, timeout=10)
    index.close()
    reopened = ArtifactSearchIndex(directory)
    reopened.start()
    reopened.close()


def test_cache_symlink_is_not_followed(index, directory, tmp_path):
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    sentinel = external / "source.jsonl"
    sentinel.write_bytes(b"untouched")
    (directory / "snapshot").symlink_to(external, target_is_directory=True)
    with pytest.raises(ApplicationError):
        search(index, [SearchDocument("one", "needle")], "needle")
    assert sentinel.read_bytes() == b"untouched"


def test_configuration_directory_changes_where_the_index_is_created(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    for location in (first, second):
        index = ArtifactSearchIndex(location)
        try:
            assert search(index, [SearchDocument("one", "needle")], "needle").hits
        finally:
            index.close()
        assert (location / "snapshot" / "meta.json").exists()
    assert first.stat().st_ino != second.stat().st_ino


def test_clear_does_not_wait_indefinitely_for_a_busy_index(index):
    with index._lock, ThreadPoolExecutor(max_workers=1) as executor:
        attempt = executor.submit(index.clear)
        with pytest.raises(ApplicationError) as rejected:
            attempt.result(timeout=2)
        assert rejected.value.detail["code"] == "transcript_search_busy"


def test_publicly_writable_ancestor_is_rejected_before_index_creation(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    directory = parent / "private"
    directory.mkdir(mode=0o700)
    index = ArtifactSearchIndex(directory)
    with pytest.raises(ApplicationError):
        index.start()
    assert list(directory.iterdir()) == []
