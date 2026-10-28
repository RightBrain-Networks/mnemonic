"""Large archives retain integrity while restore rows live in private disk spools."""

import errno
import io
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from mnemonic_backup.archive_format import read_archive, write_archive
from mnemonic_backup.archive_schema import HEAD, TABLES, BackupError
from mnemonic_backup.archive_spool import RestoreRows


def test_spooled_records_persist_mutations_and_order_segments(tmp_path):
    with RestoreRows(("transcripts", "transcript_segments"), directory=tmp_path) as rows:
        rows["transcripts"].append({"id": "first", "generation": 3})
        rows["transcripts"].append({"id": "second", "generation": 8})
        for index, row in enumerate(rows["transcripts"]):
            row["generation"] += 1
            rows["transcripts"][index] = row
        assert [row["generation"] for row in rows["transcripts"]] == [4, 9]
        for ordinal in (2, 0, 1):
            rows["transcript_segments"].append({
                "transcript_id": "first", "revision": "rev", "ordinal": ordinal,
            })
        assert [row["ordinal"] for row in rows["transcript_segments"].group(
            "first", "rev",
        )] == [0, 1, 2]
        assert list(rows["transcript_segments"].groups()) == [("first", "rev")]
        directory = Path(rows.directory.name)
        assert directory.stat().st_mode & 0o077 == 0
    assert not directory.exists()


def _archive(text):
    records = {name: [] for name in TABLES}
    records["transcripts"].append({"normalized_text": text})
    header = {
        "format": "mnemonic-project-backup-v1", "schema": HEAD, "schema_signature": "a" * 64,
        "project_id": str(uuid4()), "created_at": "2026-10-28T00:00:00+00:00",
        "counts": {name: len(value) for name, value in records.items()},
    }
    output = io.BytesIO()
    write_archive(output, header, records, 32 * 1024 * 1024)
    return output.getvalue()


def test_large_unicode_record_roundtrips_to_disk_beyond_old_eight_mib_limit(tmp_path):
    content = "café\\\n" * 900_000
    source = _archive(content)
    with RestoreRows(TABLES, directory=tmp_path) as rows:
        _, restored = read_archive(io.BytesIO(source), 32 * 1024 * 1024, rows=rows)
        assert restored is rows
        assert rows["transcripts"][0]["normalized_text"] == content
    assert not list(tmp_path.iterdir())


def test_corrupt_archive_and_size_failure_remove_restore_spools(tmp_path):
    source = _archive("retained text")
    for content, maximum in ((source[:-10], 32 * 1024 * 1024), (source, 50)):
        with pytest.raises(BackupError), RestoreRows(TABLES, directory=tmp_path) as rows:
            read_archive(io.BytesIO(content), maximum, rows=rows)
        assert not list(tmp_path.iterdir())



def test_sqlite_full_during_restore_maps_to_storage_full_and_cleans_spool(tmp_path):
    with pytest.raises(OSError) as raised:
        with RestoreRows(("transcripts",), directory=tmp_path) as rows:
            pages = rows.database.execute("PRAGMA page_count").fetchone()[0]
            rows.database.execute(f"PRAGMA max_page_count = {pages}")
            rows["transcripts"].append({"text": "x" * 131072})
    assert raised.value.errno == errno.ENOSPC
    assert not list(tmp_path.iterdir())


def test_sqlite_full_during_spool_creation_cleans_private_directory(tmp_path, monkeypatch):
    connect = sqlite3.connect

    def limited_connection(path):
        database = connect(path)
        database.execute("PRAGMA max_page_count = 1")
        return database

    monkeypatch.setattr("mnemonic_backup.archive_spool.sqlite3.connect", limited_connection)
    with pytest.raises(OSError) as raised:
        RestoreRows(("transcripts",), directory=tmp_path)
    assert raised.value.errno == errno.ENOSPC
    assert not list(tmp_path.iterdir())


def test_other_sqlite_errors_are_not_reported_as_storage_full(tmp_path):
    with pytest.raises(sqlite3.OperationalError) as raised:
        with RestoreRows(("transcripts",), directory=tmp_path) as rows:
            rows.database.execute("SELECT * FROM missing_table")
    assert raised.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
    assert not list(tmp_path.iterdir())
