"""Transcript indexes always spill to disk, including native development processes."""

from pathlib import Path
from tempfile import TemporaryDirectory

from mnemonic_api.artifact_index import ArtifactSearchIndex


class TranscriptSearchIndex(ArtifactSearchIndex):
    def __init__(self, directory: Path | None = None) -> None:
        self._temporary = (TemporaryDirectory(prefix="mnemonic-transcript-index-")
                           if directory is None else None)
        super().__init__(Path(self._temporary.name) if self._temporary is not None else directory)

    def close(self) -> None:
        super().close()
        if self._temporary is not None:
            self._temporary.cleanup()
