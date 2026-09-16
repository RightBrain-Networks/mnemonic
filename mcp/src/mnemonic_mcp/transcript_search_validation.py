"""Coverage and locator checks shared by dedicated and unified transcript searches."""

from .search_query import QueryMode, constrained_query
from .transcript_models import CompactTranscriptRead, TranscriptRead
from .transcript_segments import ContentKind


def transcript_coverage_complete(item: TranscriptRead | CompactTranscriptRead) -> bool:
    return (item.status == "ready" and item.index_status == "ready" and item.copy_status == "ready"
            and item.normalization_status == "ready" and not item.truncated
            and not item.normalization_incomplete)


def transcript_match_scope(
    item: TranscriptRead | CompactTranscriptRead, fulltext: bool,
    content_kinds: list[ContentKind] | None = None,
    query: str | None = None, query_mode: QueryMode = "terms",
) -> bool:
    fields = item.matched_fields
    if len(set(fields)) != len(fields):
        return False
    content = "content" in fields
    if not fulltext and (content or item.snippet is not None or item.segment_id is not None):
        return False
    if item.snippet_omission_reason is not None and (
        not content or item.snippet is not None or item.segment_id is None
        or not constrained_query(query, query_mode)
    ):
        return False
    if item.segment_id is not None and (
        not content or not item.snippet and item.snippet_omission_reason is None
    ):
        return False
    if item.snippet is not None and not content:
        return False
    if content and (content_kinds or constrained_query(query, query_mode)):
        return item.segment_id is not None and (
            not content_kinds or item.content_kind in content_kinds)
    return True
