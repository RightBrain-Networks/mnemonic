"""Coverage and locator checks shared by dedicated and unified transcript searches."""

from .transcript_models import CompactTranscriptRead, TranscriptRead
from .transcript_segments import ContentKind


def transcript_coverage_complete(item: TranscriptRead | CompactTranscriptRead) -> bool:
    return (item.status == "ready" and item.index_status == "ready" and item.copy_status == "ready"
            and item.normalization_status == "ready" and not item.truncated
            and not item.normalization_incomplete)


def transcript_match_scope(
    item: TranscriptRead | CompactTranscriptRead, fulltext: bool,
    content_kinds: list[ContentKind] | None = None,
) -> bool:
    if not fulltext and (item.snippet is not None or item.segment_id is not None):
        return False
    if item.segment_id is not None and not item.snippet:
        return False
    if content_kinds and item.snippet is not None:
        return item.segment_id is not None and item.content_kind in content_kinds
    return True
