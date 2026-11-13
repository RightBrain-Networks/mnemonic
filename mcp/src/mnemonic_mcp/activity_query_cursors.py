"""Validate returned opaque activity positions without changing caller tokens."""

import base64
import binascii
import hashlib
import json
from typing import Literal

from pydantic import ValidationError

from .activity_query_models import QueryActivityPage, QueryActivityRequest
from .phase12_models import DecimalString, Phase12Wire


class Position(Phase12Wire):
    v: Literal[1]
    project_id: str
    stream_id: str
    scope: str


class CheckpointPosition(Position):
    kind: Literal["checkpoint"]
    after: DecimalString


class PagePosition(Position):
    kind: Literal["page"]
    lower: DecimalString
    upper: DecimalString
    last: DecimalString


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def activity_scope(request: QueryActivityRequest, *, polling: bool = False) -> str:
    omitted = {"cursor", "start", "limit"}
    if polling:
        omitted |= {"since", "until", "after"}
    return hashlib.sha256(_canonical(request.model_dump(mode="json", exclude=omitted))).hexdigest()


def _decode[PositionT: Position](
    token: str, model: type[PositionT], page: QueryActivityPage, scope: str,
) -> PositionT:
    raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
    position = model.model_validate_json(raw, strict=True)
    canonical = base64.urlsafe_b64encode(_canonical(position.model_dump())).decode().rstrip("=")
    if token != canonical or position.project_id != str(page.project_id):
        raise ValueError("Activity position is not canonical or belongs to another project")
    if position.stream_id != str(page.stream_id) or position.scope != scope:
        raise ValueError("Activity position belongs to another stream or query")
    return position


def query_positions_match(page: QueryActivityPage, request: QueryActivityRequest) -> bool:
    try:
        _check_positions(page, request)
    except (ValueError, TypeError, binascii.Error, ValidationError):
        return False
    return True


def _check_positions(page: QueryActivityPage, request: QueryActivityRequest) -> None:
    polling_scope = activity_scope(request, polling=True)
    lower = "0"
    if request.after is not None:
        lower = _decode(request.after, CheckpointPosition, page, polling_scope).after
    last = lower
    if request.cursor is not None:
        previous = _decode(request.cursor, PagePosition, page, activity_scope(request))
        if previous.lower != lower or previous.upper != page.through_sequence:
            raise ValueError("Continuation changed its frozen activity window")
        last = previous.last
    if int(last) > int(page.through_sequence):
        raise ValueError("Activity position exceeds returned high water")
    if any(int(item.last_sequence) <= int(last) for item in page.items):
        raise ValueError("Activity response repeated an already accepted position")
    if page.next_cursor is not None:
        following = _decode(page.next_cursor, PagePosition, page, activity_scope(request))
        if (following.lower != lower or following.upper != page.through_sequence
                or following.last != page.items[-1].last_sequence):
            raise ValueError("Continuation position disagrees with its page")
    if page.checkpoint is not None:
        checkpoint = _decode(page.checkpoint, CheckpointPosition, page, polling_scope)
        if checkpoint.after != page.through_sequence:
            raise ValueError("Checkpoint does not acknowledge the captured high water")
