#!/usr/bin/env python3
"""Verify and reclaim repeated native transcript bytes; dry-run is the default.

Run inside the upgraded worker. Enrollment IDs, historical snapshots, hashes,
normalized text and receipts remain unchanged. Keep the complete native storage
volume in filesystem backups: shared prefix objects can serve several projects.
"""

import argparse
import json
import os
import stat

from mnemonic_api.artifact_tika import ExtractionError
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine
from mnemonic_api.transcript_copies import TranscriptStorage
from mnemonic_api.transcript_objects import MAX_NATIVE_BYTES
from mnemonic_api.transcript_reclaim import apply_reclaim, plan_reclaim, snapshots
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


class ReadOnlyTranscriptStorage(TranscriptStorage):
    @staticmethod
    def _secure_directory(descriptor: int) -> None:
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ExtractionError('transcript_copy_unavailable')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Verify and report without changing files')
    mode.add_argument('--apply', action='store_true', help='Reclaim with resumable per-row commits')
    args = parser.parse_args()
    settings = Settings()
    engine = build_engine(settings)
    storage_class = TranscriptStorage if args.apply else ReadOnlyTranscriptStorage
    storage = storage_class(settings.transcript_root, MAX_NATIVE_BYTES)
    factory = sessionmaker(engine)
    try:
        with engine.connect() as connection:
            head = connection.scalar(text('SELECT version_num FROM alembic_version'))
        if head != '0046_shared_transcript_copies':
            raise ExtractionError('transcript_reclaim_schema_mismatch')
        if not storage.root.is_dir():
            raise ExtractionError('transcript_copy_unavailable')
        with storage.maintenance_lock(exclusive=args.apply):
            rows = snapshots(factory)
            report = {'mode': 'apply' if args.apply else 'dry-run', **plan_reclaim(storage, rows)}
            print(json.dumps(report, sort_keys=True), flush=True)
            if args.apply:
                report.update(apply_reclaim(factory, storage, rows))
                report['verification_after'] = plan_reclaim(storage, snapshots(factory))
                print(json.dumps(report, sort_keys=True), flush=True)
        return 0
    except ExtractionError as error:
        print(json.dumps({'error_code': error.code}), flush=True)
        return 1
    except OSError:
        print(json.dumps({'error_code': 'transcript_reclaim_io_error'}), flush=True)
        return 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
