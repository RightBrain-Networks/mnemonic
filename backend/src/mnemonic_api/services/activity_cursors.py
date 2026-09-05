"""Canonical, bounded cursors scoped to one project stream and query."""

import base64
import json
from typing import Any
from uuid import UUID

from mnemonic_api.errors import ApplicationError, conflict
from mnemonic_api.models import ProjectActivityHead
from mnemonic_api.phase12_schemas import decimal_sequence


def invalid_cursor(kind: str) -> ApplicationError:
    code = "invalid_activity_cursor" if kind == "activity" else "invalid_report_cursor"
    return ApplicationError(422, code, "The cursor is invalid for this request.")


def encode_cursor(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(
    token: str,
    head: ProjectActivityHead,
    kind: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    try:
        if not token or len(token) > 512 or not token.isascii():
            raise ValueError
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        value = json.loads(raw)
        expected_keys = {"v", "kind", "project_id", "stream_id", *scope}
        expected_keys |= {"after"} if kind == "activity" else {"upper", "last"}
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise ValueError
        if encode_cursor(value) != token or type(value["v"]) is not int or value["v"] != 1:
            raise ValueError
        if value["kind"] != kind or value["project_id"] != str(head.project_id):
            raise ValueError
        if any(value[key] != expected for key, expected in scope.items()):
            raise ValueError
        stream_id = _cursor_numbers(value, kind)
    except ValueError, TypeError, KeyError, UnicodeError:
        raise invalid_cursor(kind) from None
    if stream_id != head.stream_id:
        raise conflict("activity_stream_changed", "The project stream changed. Reload a snapshot.")
    high = value["after"] if kind == "activity" else value["upper"]
    if int(high) > head.last_sequence:
        raise invalid_cursor(kind)
    return value


def cursor_base(head: ProjectActivityHead, kind: str, **scope: Any) -> dict[str, Any]:
    return {
        "v": 1,
        "kind": kind,
        "project_id": str(head.project_id),
        "stream_id": str(head.stream_id),
        **scope,
    }


def _cursor_numbers(value: dict[str, Any], kind: str) -> UUID:
    if not isinstance(value["stream_id"], str):
        raise ValueError
    stream_id = UUID(value["stream_id"])
    if str(stream_id) != value["stream_id"]:
        raise ValueError
    for field in {"after"} if kind == "activity" else {"upper", "last"}:
        number = value[field]
        if not isinstance(number, str) or not 1 <= len(number) <= 19:
            raise ValueError
        decimal_sequence(number)
    if kind != "activity" and int(value["last"]) > int(value["upper"]):
        raise ValueError
    return stream_id
