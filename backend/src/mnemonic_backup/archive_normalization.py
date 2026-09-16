"""Verify canonical conversation records and active snapshot witnesses on restore."""

import hashlib
from collections import defaultdict

from mnemonic_api.transcript_normalization import canonical_json
from mnemonic_backup.archive_schema import invalid

_ACTIVE_FIELDS = {
    "snapshot_id": "snapshot_id",
    "source_sha256": "copy_sha256",
    "sha256": "normalized_sha256",
    "schema_version": "normalization_schema_version",
    "normalizer_version": "normalizer_version",
    "segment_count": "segment_count",
    "size_bytes": "normalized_size_bytes",
    "incomplete": "normalization_incomplete",
}


def _validate_manifest(manifest: dict, segments: list[dict]) -> None:
    if any(type(row["ordinal"]) is not int or row["ordinal"] < 0 for row in segments):
        raise invalid("A normalized transcript segment has an invalid ordinal.")
    digest = hashlib.sha256()
    size = 0
    for ordinal, segment in enumerate(sorted(segments, key=lambda row: row["ordinal"])):
        data = segment["segment_data"]
        if (not isinstance(data, dict) or segment["ordinal"] != ordinal
                or any(segment[key] != data.get(key) for key in
                       ("ordinal", "segment_id", "content_kind", "text"))):
            raise invalid("A normalized transcript segment has inconsistent content or order.")
        encoded = canonical_json(data) + b"\n"
        digest.update(encoded)
        size += len(encoded)
    expected_revision = hashlib.sha256(canonical_json([
        manifest["snapshot_id"], manifest["source_sha256"],
        manifest["schema_version"], manifest["normalizer_version"],
    ])).hexdigest()
    if (manifest["segment_count"] != len(segments) or manifest["size_bytes"] != size
            or manifest["sha256"] != digest.hexdigest()
            or manifest["revision"] != expected_revision):
        raise invalid("A normalized transcript manifest does not match its retained segments.")


def validate_normalized_transcripts(rows: dict) -> None:
    grouped = defaultdict(list)
    for segment in rows["transcript_segments"]:
        grouped[(segment["transcript_id"], segment["revision"])].append(segment)
    manifests = {}
    for manifest in rows["transcript_normalizations"]:
        key = (manifest["transcript_id"], manifest["revision"])
        _validate_manifest(manifest, grouped.pop(key, []))
        manifests[key] = manifest
    if grouped:
        raise invalid("A normalized transcript segment has no revision manifest.")
    for transcript in rows["transcripts"]:
        revision = transcript["normalized_revision"]
        if revision is None:
            continue
        manifest = manifests.get((transcript["id"], revision))
        if manifest is None or any(
            manifest[manifest_field] != transcript[transcript_field]
            for manifest_field, transcript_field in _ACTIVE_FIELDS.items()
        ):
            raise invalid("A normalized transcript revision does not match its captured source.")
