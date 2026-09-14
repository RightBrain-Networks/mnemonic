"""Real disk-index persistence, containment, failure cleanup and exclusive ownership."""

import errno
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
from uuid import UUID, uuid4

import pytest
import tantivy

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


def snapshot_directory(directory):
    generations = list((directory / "snapshot").iterdir())
    assert len(generations) == 1
    generation = generations[0]
    assert generation.is_dir() and str(UUID(generation.name)) == generation.name
    return generation


def test_disk_cache_survives_restart_and_keeps_files_private(directory):
    docs = [SearchDocument("one", "Report", "café needle"),
            SearchDocument("two", "Needle", "unrelated")]
    index = ArtifactSearchIndex(directory)
    try:
        expected = search(index, docs, "needle", fulltext=True).hits
        assert (snapshot_directory(directory) / "meta.json").is_file()
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
    old_files = {path.name for path in snapshot_directory(directory).glob("*.store")}
    current = [SearchDocument("two", "file", "newneedle")]
    assert not search(index, current, "oldneedle", fulltext=True, key="changed").hits
    current_files = {path.name for path in snapshot_directory(directory).glob("*.store")}
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


@pytest.mark.parametrize("damage", [
    "metadata", "missing_snapshot", "missing_metadata", "pos", "term", "store",
])
def test_corrupt_completed_index_rebuilds_from_current_database_documents(index, directory, damage):
    docs = [SearchDocument("one", "report", "needle")]
    assert search(index, docs, "needle", fulltext=True).hits
    snapshot = snapshot_directory(directory)
    index.close()
    if damage == "metadata":
        (snapshot / "meta.json").write_text("broken derived index")
    elif damage == "missing_snapshot":
        shutil.rmtree(directory / "snapshot")
    elif damage == "missing_metadata":
        (snapshot / "meta.json").unlink()
    else:
        next(snapshot.glob(f"*.{damage}")).write_bytes(b"")
    assert (directory / ".key").is_file()
    loads = []

    def reload_documents():
        loads.append(True)
        return docs

    recovered = ArtifactSearchIndex(directory)
    try:
        result = recovered.search("project:revision", reload_documents, query="needle",
                                  fulltext=True, count=1)
        assert [hit.identity for hit in result.hits] == ["one"]
        assert loads == [True]
        assert recovered.search("project:revision", no_load, query="needle", fulltext=True,
                                count=1).hits == result.hits
    finally:
        recovered.close()
    reopened = ArtifactSearchIndex(directory)
    try:
        assert reopened.search("project:revision", no_load, query="needle", fulltext=True,
                               count=1).hits
    finally:
        reopened.close()


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
        assert (snapshot_directory(location) / "meta.json").exists()
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


def test_persistent_query_failure_rebuilds_at_most_once(index, directory, monkeypatch):
    original_query = index._query
    attempts = []

    def unavailable(*_args):
        attempts.append(True)
        raise OSError("private storage error")

    monkeypatch.setattr(index, "_query", unavailable)
    with pytest.raises(ApplicationError) as rejected:
        search(index, [SearchDocument("one", "needle")], "needle")
    assert rejected.value.detail["code"] == "transcript_index_unavailable"
    assert "private storage error" not in str(rejected.value.detail)
    assert len(attempts) == 2
    assert not (directory / ".key").exists()
    assert not (directory / "snapshot").exists()
    monkeypatch.setattr(index, "_query", original_query)
    assert search(index, [SearchDocument("one", "needle")], "needle").hits


def test_corrupt_position_recovery_detaches_before_a_late_reader_callback(
    index, directory, monkeypatch,
):
    """Tantivy's queued automatic-reader reload may recreate its metadata lock.

    Opening a truncated positions segment succeeds; its first query fails.
    Even after switching to Manual, an already-dispatched reload can still
    execute OpenOptions.create(true) for .tantivy-meta.lock during cleanup.
    """
    docs = [SearchDocument("one", "report", "needle")]
    assert search(index, docs, "needle", fulltext=True).hits
    snapshot = snapshot_directory(directory)
    index.close()
    next(snapshot.glob("*.pos")).write_bytes(b"")
    original_rmdir = os.rmdir
    callbacks = []

    def late_callback(path, *args, **kwargs):
        if not callbacks:
            try:
                descriptor = os.open(snapshot / ".tantivy-meta.lock",
                                     os.O_WRONLY | os.O_CREAT, 0o600)
            except FileNotFoundError:
                callbacks.append("detached")
            else:
                os.close(descriptor)
                callbacks.append("recreated")
        return original_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "rmdir", late_callback)
    recovered = ArtifactSearchIndex(directory)
    try:
        result = search(recovered, docs, "needle", fulltext=True)
        assert [hit.identity for hit in result.hits] == ["one"]
        assert callbacks == ["detached"]
    finally:
        recovered.close()


def test_late_reader_cannot_reach_replacement_and_old_searcher_stays_readable(index, directory):
    old = search(index, [SearchDocument("one", "report", "oldneedle")],
                 "oldneedle", fulltext=True)
    old_path = snapshot_directory(directory).resolve()
    current = search(index, [SearchDocument("two", "report", "newneedle")],
                     "newneedle", fulltext=True, key="new-corpus")
    replacement = snapshot_directory(directory).resolve()
    assert replacement != old_path
    with pytest.raises(FileNotFoundError):
        os.open(old_path / ".tantivy-meta.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    assert [hit.identity for hit in current.hits] == ["two"]
    assert old.searcher is not None
    old_hits = old.searcher.search(tantivy.Query.all_query(), limit=1).hits
    assert old.searcher.doc(old_hits[0][1]).get_first("identity") == "one"
    assert index.snippet("oldneedle", "oldneedle", old.searcher) == "oldneedle"
    assert index.search("new-corpus", no_load, query="newneedle", fulltext=True, count=1).hits


def test_old_flat_production_cache_rebuilds_once_and_reopens_without_database_reload(
    index, directory,
):
    assert search(index, [SearchDocument("old", "obsolete")], "obsolete").hits
    generation = snapshot_directory(directory)
    index.close()
    staging = directory / "flat-cache"
    generation.rename(staging)
    (directory / "snapshot").rmdir()
    staging.rename(directory / "snapshot")
    (directory / ".key").write_text(f"{version('tantivy')}:schema1:project:revision")
    assert (directory / "snapshot" / "meta.json").is_file()
    loads = []

    def current_documents():
        loads.append(True)
        return [SearchDocument("current", "needle")]

    replacement = ArtifactSearchIndex(directory)
    try:
        result = replacement.search("project:revision", current_documents,
                                     query="needle", fulltext=False, count=1)
        assert [hit.identity for hit in result.hits] == ["current"] and loads == [True]
        assert (snapshot_directory(directory) / "meta.json").is_file()
    finally:
        replacement.close()
    reopened = ArtifactSearchIndex(directory)
    try:
        assert reopened.search("project:revision", no_load,
                               query="needle", fulltext=False, count=1).hits == result.hits
    finally:
        reopened.close()


def test_inflight_lock_creation_defers_only_retired_cleanup_until_restart(
    index, directory, monkeypatch,
):
    assert search(index, [SearchDocument("old", "needle")], "needle").hits
    generation = snapshot_directory(directory)
    retained_parent = os.open(generation, os.O_RDONLY | os.O_DIRECTORY)
    original_rmdir = os.rmdir
    callbacks = []

    def finishing_callback(path, *args, **kwargs):
        if not callbacks:
            # Model an open already past parent-path resolution when retirement
            # started: its retained parent inode can still receive the lock.
            descriptor = os.open(".tantivy-meta.lock", os.O_WRONLY | os.O_CREAT,
                                 0o600, dir_fd=retained_parent)
            os.close(descriptor)
            callbacks.append(True)
        return original_rmdir(path, *args, **kwargs)

    try:
        monkeypatch.setattr(os, "rmdir", finishing_callback)
        index.clear()
        assert callbacks == [True]
        assert not (directory / "snapshot").exists() and not (directory / ".key").exists()
        assert len(list(directory.glob(".retired-*"))) == 1
    finally:
        os.close(retained_parent)
        index.close()
    reopened = ArtifactSearchIndex(directory)
    loads = []

    def current_documents():
        loads.append(True)
        return [SearchDocument("current", "needle")]

    try:
        reopened.start()
        assert not list(directory.glob(".retired-*"))
        result = reopened.search("current", current_documents,
                                  query="needle", fulltext=False, count=1)
        assert [hit.identity for hit in result.hits] == ["current"] and loads == [True]
    finally:
        reopened.close()


@pytest.mark.parametrize("condition", ["symlink", "public", "file", "owner"])
def test_retired_cache_cleanup_rejects_unsafe_entries_without_following_them(
    index, directory, tmp_path, condition, monkeypatch,
):
    index.close()
    external = tmp_path / "external"
    external.mkdir(mode=0o700)
    sentinel = external / "source.jsonl"
    sentinel.write_bytes(b"unrelated source bytes")
    retired = directory / f".retired-{uuid4()}"
    if condition == "symlink":
        retired.symlink_to(external, target_is_directory=True)
    elif condition == "public":
        retired.mkdir(mode=0o755)
        retired.chmod(0o755)
    elif condition == "file":
        retired.write_bytes(b"not a retired directory")
    else:
        retired.mkdir(mode=0o700)
        inode = retired.stat().st_ino
        original_fstat = os.fstat

        def other_owner(descriptor):
            info = original_fstat(descriptor)
            if info.st_ino == inode:
                fields = list(info)
                fields[4] = info.st_uid + 1
                return os.stat_result(fields)
            return info

        monkeypatch.setattr(os, "fstat", other_owner)
    reopened = ArtifactSearchIndex(directory)
    try:
        with pytest.raises(ApplicationError) as rejected:
            reopened.start()
        assert rejected.value.detail["code"] == "transcript_index_unavailable"
        assert sentinel.read_bytes() == b"unrelated source bytes"
        assert retired.exists()
    finally:
        reopened.close()


def test_retired_cleanup_permission_failure_remains_explicit(index, directory, monkeypatch):
    assert search(index, [SearchDocument("old", "needle")], "needle").hits

    def denied(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "private cleanup failure")

    monkeypatch.setattr(shutil, "rmtree", denied)
    with pytest.raises(ApplicationError) as rejected:
        index.clear()
    assert rejected.value.detail["code"] == "transcript_index_unavailable"
    assert not (directory / "snapshot").exists() and not (directory / ".key").exists()
    assert len(list(directory.glob(".retired-*"))) == 1


def test_cached_generation_symlink_is_never_followed(index, directory, tmp_path):
    assert search(index, [SearchDocument("old", "needle")], "needle").hits
    generation = snapshot_directory(directory)
    index.close()
    shutil.rmtree(generation)
    external = tmp_path / "external-generation"
    external.mkdir(mode=0o700)
    sentinel = external / "source.jsonl"
    sentinel.write_bytes(b"unrelated bytes")
    generation.symlink_to(external, target_is_directory=True)
    reopened = ArtifactSearchIndex(directory)
    try:
        with pytest.raises(ApplicationError):
            reopened.search("project:revision", no_load, query="needle", fulltext=False, count=1)
        assert sentinel.read_bytes() == b"unrelated bytes"
        assert generation.is_symlink()
    finally:
        reopened.close()
