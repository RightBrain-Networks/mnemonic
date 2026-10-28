"""Private disk-backed restore rows; memory use follows one record, not a project."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory


class SpoolRows:
    def __init__(self, database: sqlite3.Connection, table: str):
        self.database = database
        self.table = table
        self.count = 0

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[dict]:
        cursor = self.database.execute(
            "SELECT payload FROM rows WHERE table_name = ? ORDER BY position", (self.table,),
        )
        for (payload,) in cursor:
            yield json.loads(payload)

    def __getitem__(self, index: int) -> dict:
        found = self.database.execute(
            "SELECT payload FROM rows WHERE table_name = ? AND position = ?",
            (self.table, index),
        ).fetchone()
        if found is None:
            raise IndexError(index)
        return json.loads(found[0])

    def __setitem__(self, index: int, row: dict) -> None:
        if not 0 <= index < self.count:
            raise IndexError(index)
        self.database.execute(
            "UPDATE rows SET payload = ? WHERE table_name = ? AND position = ?",
            (json.dumps(row, separators=(",", ":"), allow_nan=False), self.table, index),
        )

    def append(self, row: dict) -> None:
        group = None
        ordinal = None
        if self.table == "transcript_segments":
            group = json.dumps([row.get("transcript_id"), row.get("revision")])
            ordinal = row.get("ordinal")
            # Malformed ordinals are rejected by the normalizer validation.
            ordinal = ordinal if type(ordinal) is int else None
        self.database.execute(
            "INSERT INTO rows VALUES (?, ?, ?, ?, ?)",
            (self.table, self.count, group, ordinal,
             json.dumps(row, separators=(",", ":"), allow_nan=False)),
        )
        self.count += 1

    def groups(self) -> Iterator[tuple[str, str]]:
        for (key,) in self.database.execute(
            "SELECT DISTINCT group_key FROM rows WHERE table_name = ?", (self.table,),
        ):
            yield tuple(json.loads(key))

    def group(self, transcript_id: str, revision: str) -> Iterator[dict]:
        key = json.dumps([transcript_id, revision])
        for (payload,) in self.database.execute(
            "SELECT payload FROM rows WHERE table_name = ? AND group_key = ? "
            "ORDER BY ordinal, position", (self.table, key),
        ):
            yield json.loads(payload)


class RestoreRows(dict[str, SpoolRows]):
    """Explicitly closed by the restore context, including on validation failure."""

    def __init__(self, tables: tuple[str, ...], *, directory: Path | None = None):
        super().__init__()
        self.directory = TemporaryDirectory(prefix=".mnemonic-restore-", dir=directory)
        try:
            self.database = sqlite3.connect(Path(self.directory.name) / "rows.sqlite")
            self.database.execute("PRAGMA journal_mode = OFF")
            self.database.execute("PRAGMA synchronous = OFF")
            self.database.execute("PRAGMA temp_store = FILE")
            self.database.execute("PRAGMA cache_size = -2048")
            self.database.execute(
                "CREATE TABLE rows (table_name TEXT, position INTEGER, group_key TEXT, "
                "ordinal INTEGER, payload TEXT, PRIMARY KEY(table_name, position))",
            )
            self.database.execute(
                "CREATE INDEX segment_groups ON rows (table_name, group_key, ordinal, position)",
            )
            self.update((name, SpoolRows(self.database, name)) for name in tables)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if hasattr(self, "database"):
            self.database.close()
        self.directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
