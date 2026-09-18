"""Private, temporary segment storage keeps normalization independent of file size."""

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict
from tempfile import TemporaryFile
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from mnemonic_api.transcript_normalization import Segment


class TranscriptSegments(Sequence["Segment"]):
    def __init__(self) -> None:
        self._file = TemporaryFile(mode="w+b")
        self._offsets = [0]

    def append(self, segment: Segment) -> None:
        from mnemonic_api.transcript_normalization import canonical_json

        data = canonical_json(asdict(segment))
        self._file.write(data)
        self._offsets.append(self._offsets[-1] + len(data))

    def __len__(self) -> int:
        return len(self._offsets) - 1

    @overload
    def __getitem__(self, index: int) -> Segment: ...

    @overload
    def __getitem__(self, index: slice) -> list[Segment]: ...

    def __getitem__(self, index: int | slice) -> Segment | list[Segment]:
        from mnemonic_api.transcript_normalization import Segment

        if isinstance(index, slice):
            return [self[item] for item in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        self._file.flush()
        start, end = self._offsets[index:index + 2]
        return Segment(**json.loads(os.pread(self._file.fileno(), end - start, start)))

    def __iter__(self) -> Iterator[Segment]:
        for index in range(len(self)):
            yield self[index]

    def close(self) -> None:
        self._file.close()


@contextmanager
def discard_on_error(segments: Sequence[Segment]):
    """Ownership passes to the caller only when a complete result is returned."""
    try:
        yield
    except BaseException:
        if isinstance(segments, TranscriptSegments):
            segments.close()
        raise
