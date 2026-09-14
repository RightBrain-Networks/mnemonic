"""Validate recovery approvals before restore advances delivery generations."""

from typing import Any

from mnemonic_backup.archive_schema import invalid


def validate_recovery_rows(rows: dict[str, list[dict[str, Any]]]) -> None:
    if any(not isinstance(row["operation_id"], str)
           for row in rows["transcript_recoveries"]):
        raise invalid("A transcript recovery has an invalid operation identity.")
    recoveries = {row["operation_id"]: row for row in rows["transcript_recoveries"]}
    latest: dict[str, int] = {}
    for recovery in recoveries.values():
        generation = recovery["resulting_generation"]
        if type(generation) is not int:
            raise invalid("A transcript recovery has an invalid generation.")
        identifier = recovery["transcript_id"]
        latest[identifier] = max(latest.get(identifier, 0), generation)
    for transcript in rows["transcripts"]:
        operation = transcript["recovery_operation_id"]
        if operation is None:
            continue
        if not isinstance(operation, str):
            raise invalid("A transcript recovery has an invalid operation identity.")
        recovery = recoveries.get(operation)
        if recovery is None or not _matching_witness(transcript, recovery, latest):
            raise invalid("A transcript recovery does not match its source or approved snapshot.")


def _matching_witness(transcript: dict, recovery: dict, latest: dict[str, int]) -> bool:
    if (recovery["transcript_id"] != transcript["id"]
            or recovery["original_source_path"] != transcript["source_path"]
            or type(transcript["generation"]) is not int
            or transcript["generation"] < recovery["resulting_generation"]
            or latest[transcript["id"]] != recovery["resulting_generation"]):
        return False
    return transcript["copy_status"] != "ready" or (
        transcript["copy_sha256"] == recovery["expected_sha256"]
        and transcript["copy_size_bytes"] == recovery["expected_size_bytes"]
    )
