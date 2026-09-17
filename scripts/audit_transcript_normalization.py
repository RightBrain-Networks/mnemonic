#!/usr/bin/env python3
"""Read-only native/normalized transcript integrity audit; never print transcript bodies."""

import argparse
import hashlib
import json
import os
import stat
from collections import Counter

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_normalization import (
    NORMALIZER_VERSION,
    canonical_json,
    normalize_transcript,
)
from sqlalchemy import text


class ReadOnlyTranscriptStorage(TranscriptStorage):
    @staticmethod
    def _secure_directory(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise PermissionError("Native capture directories must be owned and private")


def verify_capture(row, storage, counts):
    try:
        with storage.open(row["storage_key"]) as source:
            content = source.read()
        if hashlib.sha256(content).hexdigest() != row["copy_sha256"]:
            counts["native_hash_mismatch"] += 1
            return
        normalized = normalize_transcript(content, row["client"], row["snapshot_id"])
    except ExtractionError as error:
        prefix = "ready_parse_failure" if row["status"] == "ready" else "rejected_source"
        counts[f"{prefix}:{error.code}"] += 1
        return
    except OSError:
        counts["native_copy_unreadable"] += 1
        return
    counts["native_copies_verified"] += 1
    if row["normalizer_version"] == NORMALIZER_VERSION:
        if normalized.sha256 != row["normalized_sha256"]:
            counts["normalized_hash_mismatch"] += 1
        else:
            counts["normalized_manifests_verified"] += 1
    for code in normalized.metadata.get("transcript:normalization_warnings", []):
        name, value = code.rsplit("=", 1)
        counts[f"coverage:{name}"] += int(value)



def verify_stored_segments(connection, row, counts):
    if row["normalized_revision"] is None:
        return
    digest = hashlib.sha256()
    statement = text("""
        SELECT segment_data FROM transcript_segments
        WHERE transcript_id=:id AND revision=:revision ORDER BY ordinal
    """).execution_options(yield_per=100)
    with connection.execute(statement, {"id": row["id"],
                            "revision": row["normalized_revision"]}) as segments:
        for segment in segments.scalars():
            digest.update(canonical_json(segment) + b"\n")
    if digest.hexdigest() != row["normalized_sha256"]:
        counts["stored_segments_hash_mismatch"] += 1
    else:
        counts["stored_normalizations_verified"] += 1


def audit(settings: Settings, *, verify_native: bool) -> dict:
    engine = build_engine(settings)
    counts: Counter = Counter()
    states: Counter = Counter()
    storage = ReadOnlyTranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    try:
        with engine.connect() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            rows = connection.execute(text("""
                SELECT id, client, status, copy_status, storage_key, copy_sha256,
                       snapshot_id, normalizer_version, normalized_sha256, normalized_revision,
                       reindex_status
                FROM transcripts ORDER BY id
            """))
            for row in rows.mappings():
                states[f"{row['client']}:{row['status']}:{row['copy_status']}:"
                       f"normalizer={row['normalizer_version']}:reindex={row['reindex_status']}"] += 1
                counts["transcripts"] += 1
                if verify_native and row["copy_status"] == "ready":
                    verify_capture(row, storage, counts)
                    verify_stored_segments(connection, row, counts)
    finally:
        engine.dispose()
    return {"normalizer_version": NORMALIZER_VERSION, "verified_native": verify_native,
            "states": dict(sorted(states.items())), "counts": dict(sorted(counts.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-native", action="store_true",
                        help="Read every retained native copy and verify current manifest hashes")
    args = parser.parse_args()
    report = audit(Settings(), verify_native=args.verify_native)
    print(json.dumps(report, indent=2))
    failures = {"native_hash_mismatch", "normalized_hash_mismatch", "native_copy_unreadable",
                "stored_segments_hash_mismatch"}
    return int(any(key in failures or key.startswith("ready_parse_failure:")
                   for key in report["counts"]))


if __name__ == "__main__":
    raise SystemExit(main())
