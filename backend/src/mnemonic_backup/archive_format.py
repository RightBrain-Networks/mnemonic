"""A bounded bzip2 JSON-lines archive; uploaded bytes can never become SQL."""

import bz2
import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any, BinaryIO
from uuid import UUID

from mnemonic_backup.archive_schema import TABLES, BackupError, canonical, invalid

FORMAT = "mnemonic-project-backup-v1"
CHUNK = 64 * 1024


def _write_all(output: BinaryIO, value: bytes) -> None:
    remaining = memoryview(value)
    while remaining:
        amount = output.write(remaining)
        if amount is None or amount <= 0:
            raise OSError("The backup archive could not be written.")
        remaining = remaining[amount:]


def write_archive(output: BinaryIO, header: dict, rows: dict, max_bytes: int) -> None:
    write_stream_archive(output, _values(header, rows), max_bytes=max_bytes)


def write_stream_archive(output: BinaryIO, values: Iterator[dict], *,
                         max_bytes: int | None = None) -> None:
    """Incrementally encode and compress rows without buffering a project or line."""
    compressor = bz2.BZ2Compressor(9)
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256()
    total = 0
    for value in values:
        for token in encoder.iterencode(value):
            # The JSON encoder can emit one large transcript string; slice it
            # before UTF-8 encoding/compression to avoid a second full-row copy.
            for offset in range(0, len(token), CHUNK):
                chunk = token[offset:offset + CHUNK].encode()
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise BackupError(413, "backup_too_large",
                                      "Project data exceeds the backup limit.")
                digest.update(chunk)
                _write_all(output, compressor.compress(chunk))
        total += 1
        digest.update(b"\n")
        _write_all(output, compressor.compress(b"\n"))
    footer = canonical({"sha256": digest.hexdigest()}) + b"\n"
    if max_bytes is not None and total + len(footer) > max_bytes:
        raise BackupError(413, "backup_too_large", "Project data exceeds the backup limit.")
    _write_all(output, compressor.compress(footer))
    _write_all(output, compressor.flush())


def _values(header: dict, rows: dict) -> Iterator[dict]:
    yield header
    for table in TABLES:
        for row in rows[table]:
            yield {"table": table, "row": row}


def _decompressed(source: BinaryIO, max_bytes: int) -> Iterator[bytes]:
    decoder = bz2.BZ2Decompressor()
    total = 0
    while not decoder.eof:
        data = source.read(CHUNK) if decoder.needs_input else b""
        if decoder.needs_input and not data:
            raise invalid("The compressed backup is truncated.")
        try:
            chunk = decoder.decompress(data, max_length=CHUNK)
        except (OSError, EOFError, ValueError) as error:
            raise invalid("The uploaded file is not a valid bzip2 backup.") from error
        total += len(chunk)
        if total > max_bytes:
            raise BackupError(413, "backup_too_large", "Expanded backup exceeds the byte limit.")
        yield chunk
    if decoder.unused_data or source.read(1):
        raise invalid("The compressed backup contains unexpected trailing data.")


def _lines(source: BinaryIO, max_bytes: int) -> Iterator[bytes]:
    pending = bytearray()
    scanned = 0
    for chunk in _decompressed(source, max_bytes):
        pending.extend(chunk)
        while (end := pending.find(b"\n", scanned)) >= 0:
            if end + 1 > max_bytes:
                raise invalid("A backup record exceeds the byte limit.")
            line = bytes(memoryview(pending)[:end + 1])
            del pending[:end + 1]
            scanned = 0
            yield line
        scanned = len(pending)
        if len(pending) > max_bytes:
            raise invalid("A backup record exceeds the byte limit.")
    if pending:
        raise invalid("The backup is missing its final record terminator.")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise invalid("The backup contains duplicate JSON fields.")
        value[key] = item
    return value


def _parse(line: bytes) -> dict:
    try:
        value = json.loads(line, object_pairs_hook=_unique_object)
        if not isinstance(value, dict) or not _canonical_matches(value, line):
            raise invalid("The backup contains noncanonical JSON data.")
        return value
    except (ValueError, RecursionError, UnicodeError) as error:
        raise invalid() from error


def _canonical_matches(value: dict, line: bytes) -> bool:
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"), allow_nan=False)
    view = memoryview(line)
    position = 0
    for token in encoder.iterencode(value):
        for offset in range(0, len(token), CHUNK):
            encoded = token[offset:offset + CHUNK].encode()
            if view[position:position + len(encoded)] != encoded:
                return False
            position += len(encoded)
    return view[position:] == b"\n"


def read_archive(
    source: BinaryIO, max_bytes: int, *, rows: dict | None = None,
) -> tuple[dict, dict]:
    lines = iter(_lines(source, max_bytes))
    first = next(lines, None)
    if first is None:
        raise invalid()
    header = _parse(first)
    required = {"format", "schema", "schema_signature", "project_id", "created_at", "counts"}
    if set(header) != required or header["format"] != FORMAT:
        raise invalid("This is not a supported Mnemonic project backup.")
    _validate_header(header)
    rows = {name: [] for name in TABLES} if rows is None else rows
    digest = hashlib.sha256(first)
    footer_seen = False
    for line in lines:
        if footer_seen:
            raise invalid("The backup contains records after its checksum.")
        value = _parse(line)
        if set(value) == {"sha256"}:
            footer_seen = True
            if value["sha256"] != digest.hexdigest():
                raise invalid("The backup checksum does not match its contents.")
        else:
            _append_row(value, rows)
            digest.update(line)
    if not footer_seen or header["counts"] != {name: len(rows[name]) for name in TABLES}:
        raise invalid("The backup is incomplete or its row counts do not match.")
    return header, rows


def _append_row(value: dict, rows: dict) -> None:
    if set(value) != {"table", "row"} or not isinstance(value["table"], str):
        raise invalid()
    if value["table"] not in rows or not isinstance(value["row"], dict):
        raise invalid("The backup contains an unknown table or invalid row.")
    rows[value["table"]].append(value["row"])


def _validate_header(header: dict) -> None:
    scalar_fields = ("schema", "schema_signature", "project_id", "created_at")
    if any(not isinstance(header[name], str) for name in scalar_fields):
        raise invalid("The backup header contains invalid metadata.")
    try:
        if str(UUID(header["project_id"])) != header["project_id"]:
            raise ValueError
        if datetime.fromisoformat(header["created_at"]).utcoffset() is None:
            raise ValueError
    except ValueError as error:
        raise invalid("The backup header contains an invalid identity or timestamp.") from error
    counts = header["counts"]
    if not isinstance(counts, dict) or set(counts) != set(TABLES):
        raise invalid("The backup header table inventory does not match this release.")
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise invalid("The backup header contains invalid row counts.")
