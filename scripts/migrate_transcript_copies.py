"""Wait for the resumable worker backfill; optionally verify retained raw bytes.

Run inside the shared worker after upgrading to 0038_transcript_recovery.
This command never reads transcript bodies into its output or rewrites provenance.
"""

import argparse
import hashlib
import json
import os
import stat
import time

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.models import Transcript, TranscriptSettings, WorkItem
from mnemonic_api.services.transcripts import transcript_project_id
from mnemonic_api.transcript_indexing import _active_generation
from mnemonic_api.transcript_storage import _open_source
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session


def report(database: Session) -> dict:
    head = database.scalar(text("SELECT version_num FROM alembic_version"))
    if head != "0038_transcript_recovery":
        raise RuntimeError("Upgrade all application processes to 0038_transcript_recovery first")
    rows = database.execute(select(
        Transcript.copy_status, Transcript.copy_error_code, func.count(),
    ).group_by(Transcript.copy_status, Transcript.copy_error_code)).all()
    index_status = func.coalesce(Transcript.reindex_status, Transcript.status)
    indexes = database.execute(select(index_status, func.count()).group_by(index_status)).all()
    pending = select(func.count()).select_from(Transcript).outerjoin(
        WorkItem, WorkItem.id == Transcript.work_item_id,
    ).outerjoin(TranscriptSettings, TranscriptSettings.project_id == transcript_project_id())
    deferred = database.scalar(pending.where(
        (Transcript.copy_status != "ready") | (index_status != "ready"),
        _active_generation() | ~func.coalesce(TranscriptSettings.enabled, True),
    )) or 0
    return {
        "total": sum(count for _, _, count in rows),
        "copied": sum(count for status, _, count in rows if status == "ready"),
        "failed": sum(count for status, _, count in rows if status == "failed"),
        "pending": sum(count for status, _, count in rows if status in {"pending", "processing"}),
        "index_pending": sum(count for status, count in indexes
                             if status in {"waiting", "pending", "processing"}),
        "index_failed": sum(count for status, count in indexes if status == "failed"),
        "deferred_active_or_paused": deferred,
        "dispositions": [{"status": status, "error_code": code, "count": count}
                         for status, code, count in rows],
    }


def verify_file(settings: Settings, key: str, expected_size: int, expected_sha: str) -> bool:
    try:
        descriptor = _open_source(str(settings.transcript_root / key), [settings.transcript_root])
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                return False
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > expected_size:
                    return False
                digest.update(chunk)
        return size == expected_size and digest.hexdigest() == expected_sha
    except (OSError, ValueError, ExtractionError):
        return False


def verify(database: Session, settings: Settings) -> dict:
    rows = database.execute(select(
        Transcript.storage_key, Transcript.copy_size_bytes, Transcript.copy_sha256,
    ).where(Transcript.copy_status == "ready").execution_options(yield_per=100))
    checked, failed = 0, 0
    for key, size, digest in rows:
        checked += 1
        failed += not verify_file(settings, key, size, digest)
    return {"verified": checked - failed, "verification_failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    settings = Settings()
    engine = build_engine(settings)
    deadline = time.monotonic() + args.timeout
    try:
        while True:
            with Session(engine) as database:
                summary = report(database)
            print(json.dumps(summary, sort_keys=True), flush=True)
            if (not args.wait or not (summary["pending"] or summary["index_pending"])
                    or time.monotonic() >= deadline):
                break
            time.sleep(5)
        if args.verify:
            with Session(engine) as database:
                summary.update(verify(database, settings))
            print(json.dumps(summary, sort_keys=True), flush=True)
        raise SystemExit(int(bool(summary["pending"] or summary["failed"]
                                  or summary["index_pending"] or summary["index_failed"]
                                  or summary.get("verification_failed"))))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
