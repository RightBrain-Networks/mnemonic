"""Project bounded supersession evidence without rewriting captured assessment episodes."""

import difflib
import re
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from mnemonic_api.models import (
    DecisionImpactAssessment,
    DecisionRelationship,
    DecisionRevision,
    DecisionSupersessionDetail,
)
from mnemonic_api.supersession_delta_schemas import (
    SupersedesSection,
    SupersessionDelta,
    SupersessionDetail,
)

SCAN_LIMIT = 65536
SECTION_BYTES = 8192
DIFF_BYTES = 16384
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _headings(lines: list[str]) -> Iterator[tuple[int, int, str]]:
    fence: str | None = None
    for index, line in enumerate(lines):
        marker = _FENCE.match(line)
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not (
                marker[2].strip()
            ):
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        if heading := _ATX.match(line):
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
            yield index, len(heading[1]), title
        elif line.strip() and index + 1 < len(lines) and (underline := _SETEXT.match(
            lines[index + 1],
        )):
            yield index, 1 if underline[1][0] == "=" else 2, line.strip()


def _section_ranges(lines: list[str]) -> list[tuple[int, int]]:
    selected, start, level = [], None, 0
    for index, heading_level, title in _headings(lines):
        if start is not None and heading_level <= level:
            selected.append((start, index))
            start, level = None, 0
        if start is None and title.casefold() == "supersedes":
            start, level = index, heading_level
    if start is not None:
        selected.append((start, len(lines)))
    return selected


def _clip(value: str, maximum: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    return encoded[:maximum].decode("utf-8", errors="ignore"), len(encoded) > maximum


def supersedes_section(content: str, *, include_text: bool = True) -> SupersedesSection:
    bounded = content[:SCAN_LIMIT]
    lines = bounded.splitlines()
    selected = _section_ranges(lines)
    value, clipped = _clip("\n\n".join("\n".join(lines[start:end])
                                     for start, end in selected), SECTION_BYTES)
    return SupersedesSection(
        found=bool(selected), section_count=len(selected),
        text=value if include_text else None,
        truncated=clipped or len(content) > SCAN_LIMIT,
        source_characters_scanned=len(bounded),
    )


def _section_diff(before: SupersedesSection, after: SupersedesSection,
                  before_reference: str | None, after_reference: str) -> tuple[str, bool]:
    lines = difflib.unified_diff(
        (before.text or "").splitlines(), (after.text or "").splitlines(),
        fromfile=before_reference or "/dev/null", tofile=after_reference, lineterm="",
    )
    chunks, size = [], 0
    for line in lines:
        size += len(line.encode("utf-8")) + 1
        if size > DIFF_BYTES:
            return "\n".join(chunks), True
        chunks.append(line)
    return "\n".join(chunks), False


def _detail(database: Session, source: DecisionRevision | None,
            origin: DecisionRevision, target_reference: str) -> SupersessionDetail | None:
    if source is None:
        return None
    pair = database.execute(select(DecisionSupersessionDetail, DecisionRevision.revision).join(
        DecisionRevision, DecisionRevision.id == DecisionSupersessionDetail.target_revision_id,
    ).where(DecisionSupersessionDetail.revision_id == source.id,
            DecisionRevision.document_id == origin.document_id)).one_or_none()
    if pair is None:
        return None
    detail, revision = pair
    return SupersessionDetail.model_validate({
        "decision_id": target_reference.rsplit("-", 1)[0], "revision": revision,
        "extent": detail.extent, "reason": detail.reason,
    })


def supersession_delta(
    database: Session, row: DecisionImpactAssessment, origin: DecisionRevision,
    replacement: DecisionRevision, target_reference: str, after_reference: str,
    *, include_diff: bool = True,
) -> SupersessionDelta | None:
    if row.trigger != "supersession":
        return None
    previous = database.scalar(select(DecisionRevision).where(
        DecisionRevision.document_id == replacement.document_id,
        DecisionRevision.revision == replacement.revision - 1,
    ))
    previous_reference = (f"{after_reference.rsplit('-', 1)[0]}-{previous.revision}"
                          if previous is not None else None)
    old = _detail(database, previous, origin, target_reference)
    new = _detail(database, replacement, origin, target_reference)
    continued = previous is not None and database.scalar(select(DecisionRelationship.revision_id)
        .where(DecisionRelationship.revision_id == previous.id,
               DecisionRelationship.target_document_id == origin.document_id,
               DecisionRelationship.kind == "supersedes")) is not None
    before = supersedes_section(previous.content if previous else "")
    after = supersedes_section(replacement.content)
    prose, clipped = _section_diff(before, after, previous_reference, after_reference)
    if not include_diff:
        before.text = after.text = None
    return SupersessionDelta(
        target_reference=target_reference, before_reference=previous_reference,
        after_reference=after_reference,
        kind="continued-supersession" if continued else "new-supersession",
        details_before=old, details_after=new, metadata_changed=not continued or old != new,
        section_before=before, section_after=after,
        unified_diff=prose if include_diff else None,
        truncated=clipped or before.truncated or after.truncated,
    )
