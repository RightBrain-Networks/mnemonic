"""Recreate the pre-shared-copy layout for historical migration fixtures only."""

from sqlalchemy import select

from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.models import Transcript
from mnemonic_api.transcript_copies import TranscriptCopy, TranscriptStorage


def restore_legacy_native_layout(api):
    settings, factory = api.app.state.settings, api.app.state.session_factory
    storage = TranscriptStorage(settings.transcript_root, settings.transcript_max_bytes)
    writer = ArtifactStorage(storage.root, settings.transcript_max_bytes)
    with factory.begin() as database:
        rows = database.scalars(select(Transcript).where(Transcript.copy_status == 'ready')).all()
        for row in rows:
            copy = TranscriptCopy(row.storage_key, row.copy_sha256, row.copy_size_bytes)
            with storage.open_copy(copy) as source:
                stage = writer.stage(row.id, row.snapshot_id, 'transcript.jsonl',
                                     iter(lambda: source.read(1024 * 1024), b''))
            writer.publish(stage)
            row.storage_key = stage.relative_path
