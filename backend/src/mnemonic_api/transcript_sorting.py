"""Stable transcript ordering is applied before pagination, including search hits."""

from sqlalchemy import case, func, literal

from mnemonic_api.models import Transcript


def _indexing_label():
    copy = Transcript.copy_status
    replacement = case(
        (copy == "failed", " · Copy failed"), (copy == "processing", " · Copying"),
        (copy == "pending", " · Copy queued"),
        (Transcript.reindex_status == "failed", " · Reindex failed"),
        (Transcript.reindex_status == "pending", " · Reindex queued"),
        (Transcript.reindex_status == "processing", " · Reindexing"), else_="")
    return case(
        (Transcript.status == "ready", literal("Indexed") + replacement),
        (copy == "failed", "Copy failed"), (copy == "processing", "Copying"),
        ((copy == "pending") & (Transcript.status != "waiting"), "Copy queued"),
        (Transcript.status == "failed", "Failed"),
        (Transcript.status == "processing", "Indexing"),
        ((Transcript.status == "pending") | (Transcript.kind == "imported"), "Queued"),
        else_="Waiting for work to leave Active")


def transcript_order(sort_by: str | None, direction: str):
    columns = {
        "name": func.lower(func.regexp_replace(Transcript.source_path, r"^.*/", "")),
        "size": func.coalesce(Transcript.size_bytes, Transcript.copy_size_bytes),
        "session": Transcript.kind,
        "indexing": _indexing_label(),
        "updated": Transcript.last_updated_at,
    }
    column = (columns[sort_by] if sort_by else
              func.coalesce(Transcript.last_updated_at, Transcript.created_at))
    ordered = column.asc() if direction == "asc" else column.desc()
    return ordered.nulls_last(), Transcript.id.asc()
