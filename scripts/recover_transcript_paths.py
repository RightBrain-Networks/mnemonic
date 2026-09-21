"""Prepare and apply explicit operator-approved transcript path recoveries.

Run in the shared worker's environment. This is not an agent or public API write.
Preparation reads only eligible approved files, streams their hashes, and writes
a new private intent file. Application records that exact intent and queues the
normal RabbitMQ copy job; it never copies or indexes inline. Retain the intent
unchanged after an uncertain outcome and apply the same file again.
"""

import argparse
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.errors import ApplicationError
from mnemonic_api.services.transcript_recoveries import (
    TranscriptRecoveryRequest,
    apply_transcript_recovery,
    describe_recovery_source,
    inspect_recovery_target,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

HEAD = "0047_artifact_transfer"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class RecoveryOutcomeUnknown(Exception):
    """A transient wrapper may hide a committed transaction; replay is required."""


class RecoveryMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    transcript_id: UUID
    project_id: UUID
    original_source_path: Annotated[str, Field(min_length=1, max_length=4096)]
    replacement_path: Annotated[str, Field(min_length=1, max_length=4096)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence: Annotated[str, Field(min_length=1, max_length=2000)]


class DeferredRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    transcript_id: UUID
    error_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")]


class RecoveryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal[1]
    requests: Annotated[list[TranscriptRecoveryRequest], Field(max_length=1000)]
    deferred: Annotated[list[DeferredRecovery], Field(max_length=1000)]

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        if type(value) is not int:
            raise ValueError("Recovery manifest version must be an integer")
        return value

    @model_validator(mode="after")
    def distinct_intents(self):
        identities = [item.transcript_id for item in [*self.requests, *self.deferred]]
        operations = [item.operation_id for item in self.requests]
        if len(identities) != len(set(identities)) or len(operations) != len(set(operations)):
            raise ValueError("Duplicate recovery identity")
        if not identities or len(identities) > 1000:
            raise ValueError("Recovery manifest must contain between 1 and 1000 entries")
        return self


@contextmanager
def parent_directory(path: Path) -> Iterator[tuple[int, str]]:
    """Anchor each directory component so no symlink can redirect private IO."""
    absolute = path.absolute()
    if ".." in absolute.parts or absolute == Path("/"):
        raise ValueError("Use a file path without parent traversal")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/", flags)
    try:
        for part in absolute.parts[1:-1]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor, absolute.name
    finally:
        os.close(descriptor)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_private_json(path: Path) -> str:
    with parent_directory(path) as (parent, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError("Recovery inputs must be private, owner-held regular files")
        content = source.read(MAX_MANIFEST_BYTES + 1)
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("Recovery input exceeds its byte limit")
    decoded = json.loads(content, object_pairs_hook=unique_object)
    # Strict JSON validation below accepts UUID strings without relaxing Python types.
    return json.dumps(decoded, allow_nan=False)


def write_manifest(path: Path, manifest: RecoveryManifest) -> None:
    content = (manifest.model_dump_json(indent=2) + "\n").encode()
    if len(content) > MAX_MANIFEST_BYTES:
        raise ValueError("Recovery manifest exceeds its byte limit")
    with parent_directory(path) as (parent, name):
        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(parent)


def prepare_mapping(factory, settings: Settings, mapping: RecoveryMapping):
    target = inspect_recovery_target(factory, settings, mapping.transcript_id, mapping.project_id)
    if target.original_source_path != mapping.original_source_path:
        raise ApplicationError(409, "transcript_recovery_stale", "Original assertion changed.")
    source = describe_recovery_source(settings, mapping.replacement_path, target.maximum_bytes)
    return TranscriptRecoveryRequest(
        **mapping.model_dump(), operation_id=uuid4(), expected_generation=target.generation,
        expected_snapshot_id=target.snapshot_id, expected_sha256=source.sha256,
        expected_size_bytes=source.size_bytes,
    )


def prepare(factory, settings: Settings, mappings_path: Path, output: Path) -> dict:
    mappings = TypeAdapter(Annotated[list[RecoveryMapping], Field(min_length=1, max_length=1000)])
    entries = mappings.validate_json(read_private_json(mappings_path))
    if len({item.transcript_id for item in entries}) != len(entries):
        raise ValueError("Duplicate recovery mapping")
    requests, deferred = [], []
    for entry in entries:
        try:
            requests.append(prepare_mapping(factory, settings, entry))
        except ApplicationError as error:
            deferred.append(DeferredRecovery(
                transcript_id=entry.transcript_id, error_code=error.detail["code"]))
        except ExtractionError as error:
            deferred.append(DeferredRecovery(
                transcript_id=entry.transcript_id, error_code=error.code))
    manifest = RecoveryManifest(version=1, requests=requests, deferred=deferred)
    write_manifest(output, manifest)
    return {"prepared": len(requests), "deferred": len(deferred),
            "dispositions": [entry.model_dump(mode="json") for entry in deferred]}


def apply(factory, settings: Settings, request_path: Path) -> dict:
    manifest = RecoveryManifest.model_validate_json(read_private_json(request_path))
    applied, refused = [], []
    for request in manifest.requests:
        try:
            result = apply_transcript_recovery(factory, settings, request)
            applied.append(result.model_dump(mode="json"))
        except ApplicationError as error:
            if error.status_code >= 500:
                # project_mutation sanitizes a lost commit acknowledgment into
                # a 503 ApplicationError. Never report that as a definite refusal
                # or continue the batch after losing knowledge of its outcome.
                raise RecoveryOutcomeUnknown from None
            refused.append({"transcript_id": str(request.transcript_id),
                            "error_code": error.detail["code"]})
    return {"applied_or_replayed": len(applied), "refused": len(refused),
            "deferred": len(manifest.deferred), "results": applied, "refusals": refused,
            "note": "Recovery acceptance is not copy or indexing completion."}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser(
        "prepare", help="Freeze explicit mappings without DB writes")
    prepare_parser.add_argument("--mappings", type=Path, required=True)
    prepare_parser.add_argument(
        "--output", type=Path, required=True, help="New private intent file")
    apply_parser = commands.add_parser("apply", help="Apply or replay the unchanged private intent")
    apply_parser.add_argument("--request", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    engine = None
    try:
        settings = Settings()
        engine = build_engine(settings)
        with Session(engine) as database:
            if database.scalar(text("SELECT version_num FROM alembic_version")) != HEAD:
                raise ValueError("Upgrade all application processes to the current schema first")
        factory = sessionmaker(engine)
        summary = (prepare(factory, settings, args.mappings, args.output)
                   if args.command == "prepare" else apply(factory, settings, args.request))
        print(json.dumps(summary, sort_keys=True), flush=True)
        return int(bool(summary.get("deferred") or summary.get("refused")))
    except (SQLAlchemyError, RecoveryOutcomeUnknown):
        print(json.dumps({"error_code": "transcript_recovery_outcome_unknown",
                          "instructions": "Retain the prepared file unchanged. Reapply it to "
                          "recover any committed result; do not prepare a replacement intent."}))
        return 3
    except (OSError, ValueError, ValidationError, RecursionError, ApplicationError,
            ExtractionError):
        # Validation errors and DB exceptions can contain complete inputs/SQL parameters.
        print(json.dumps({"error_code": "transcript_recovery_input_or_environment_invalid",
                          "instructions": "Check the private files, schema and configuration. "
                          "Do not overwrite a prepared request after an uncertain apply."}))
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
