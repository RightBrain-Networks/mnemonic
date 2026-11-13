"""Opaque positions bound to one project stream and immutable query selection."""

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from mnemonic_api.activity_query_schemas import QueryActivityRequest
from mnemonic_api.errors import ApplicationError, conflict


def invalid_activity_query_cursor() -> ApplicationError:
    return ApplicationError(422, "invalid_activity_query_cursor",
                            "Activity position is invalid for this project or query.")


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def query_scope(query: QueryActivityRequest, *, polling: bool = False) -> str:
    omitted = {"cursor", "start", "limit"}
    if polling:
        omitted |= {"since", "until", "after"}
    values = query.model_dump(mode="json", exclude=omitted)
    return hashlib.sha256(_canonical(values)).hexdigest()


def encode_position(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(_canonical(value)).decode().rstrip("=")


def decode_position(
    token: str, project_id: UUID, stream_id: UUID, scope: str, kind: str, head: int,
) -> dict[str, Any]:
    keys = {"v", "kind", "project_id", "stream_id", "scope"}
    keys |= {"after"} if kind == "checkpoint" else {"lower", "upper", "last"}
    try:
        if len(token) > 2048 or not token.isascii():
            raise ValueError
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != keys or encode_position(value) != token:
            raise ValueError
        if type(value["v"]) is not int or value["v"] != 1 or value["kind"] != kind:
            raise ValueError
        if value["project_id"] != str(project_id) or value["scope"] != scope:
            raise ValueError
        _validate_numbers(value, keys, kind)
        UUID(value["stream_id"])
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise invalid_activity_query_cursor() from None
    if value["stream_id"] != str(stream_id):
        raise conflict("activity_stream_changed", "The project stream changed. Reload a snapshot.")
    numbers = {"after"} if kind == "checkpoint" else {"lower", "upper", "last"}
    if any(int(value[key]) > head for key in numbers):
        raise invalid_activity_query_cursor()
    return value


def _validate_numbers(value: dict[str, Any], keys: set[str], kind: str) -> None:
    for key in keys - {"v", "kind", "project_id", "stream_id", "scope"}:
        number = value[key]
        if not isinstance(number, str) or str(int(number)) != number:
            raise ValueError
        if not 0 <= int(number) <= 9223372036854775807:
            raise ValueError
    if kind == "page" and not (
        int(value["lower"]) <= int(value["last"]) <= int(value["upper"])
    ):
        raise ValueError


@dataclass(frozen=True)
class ActivityWindow:
    project_id: UUID
    stream_id: UUID
    lower: int
    upper: int
    last: int

    def _base(self, query: QueryActivityRequest, kind: str) -> dict[str, Any]:
        return {"v": 1, "kind": kind, "project_id": str(self.project_id),
                "stream_id": str(self.stream_id),
                "scope": query_scope(query, polling=kind == "checkpoint")}

    def page_cursor(self, query: QueryActivityRequest, last: int) -> str:
        return encode_position({**self._base(query, "page"), "lower": str(self.lower),
                                "upper": str(self.upper), "last": str(last)})

    def checkpoint(self, query: QueryActivityRequest) -> str:
        return encode_position({**self._base(query, "checkpoint"), "after": str(self.upper)})


def activity_window(
    query: QueryActivityRequest, project_id: UUID, stream_id: UUID, head: int,
) -> ActivityWindow:
    lower = head if query.start == "now" else 0
    if query.after is not None:
        previous = decode_position(query.after, project_id, stream_id,
                                   query_scope(query, polling=True), "checkpoint", head)
        lower = int(previous["after"])
    if query.cursor is not None:
        page = decode_position(query.cursor, project_id, stream_id,
                               query_scope(query), "page", head)
        if int(page["lower"]) != lower:
            raise invalid_activity_query_cursor()
        return ActivityWindow(project_id, stream_id, lower, int(page["upper"]), int(page["last"]))
    return ActivityWindow(project_id, stream_id, lower, head, lower)
