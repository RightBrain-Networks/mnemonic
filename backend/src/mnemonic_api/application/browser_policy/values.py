"""Small JSON predicates with the browser's non-coercing scalar semantics."""

import json
import math
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeGuard

from pydantic import AnyHttpUrl, BaseModel, TypeAdapter, ValidationError

from mnemonic_api.application.browser_policy.routes import UUID

JsonObject = dict[str, Any]
MAX_SAFE_INTEGER = 2**53 - 1
_HTTP_URL = TypeAdapter(AnyHttpUrl)
# ECMAScript trim differs from Python str.strip for FEFF and U+001C..001F/U+0085.
JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
    "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
RESERVED_METADATA_KEYS = frozenset(
    {
        "lease_token",
        "claim_request_id",
        "client_operation_id",
        "api_key",
        "authorization",
        "cookie",
        "secret",
        "gate_id",
        "gate_type",
        "affected_paths",
        "repository_root",
        "repo_root",
        "assessment",
        "assessment_result",
        "freshness_assessment",
        "freshness_state",
    }
)


def allowed_keys(value: JsonObject, keys: Iterable[str]) -> bool:
    return value.keys() <= set(keys)


def exact_keys(value: JsonObject, keys: Iterable[str]) -> bool:
    return value.keys() == set(keys)


def optional(body: JsonObject, key: str, predicate: Callable[[Any], bool]) -> bool:
    return key not in body or predicate(body[key])


def integer(value: Any, minimum: int = 0, maximum: int = MAX_SAFE_INTEGER) -> bool:
    return (
        type(value) in {int, float}
        and minimum <= value <= min(maximum, MAX_SAFE_INTEGER)
        and int(value) == value
    )


def valid_unicode(value: str) -> bool:
    return not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def text(value: Any, maximum: int | float) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and valid_unicode(value)
        and "\0" not in value
        and bool(value.strip(JS_WHITESPACE))
        and len(value) <= maximum
    )


def nullable_text(value: Any, maximum: int) -> bool:
    return value is None or text(value, maximum)


def uuid(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(UUID, value) is not None


def browser_url(value: str) -> AnyHttpUrl | None:
    # Pydantic's URL parser follows browser URL syntax (including special-scheme
    # slash handling); urllib.urlsplit accepts invalid hosts and rejects repairs.
    try:
        return _HTTP_URL.validate_python(value)
    except ValidationError:
        return None


def string_array(value: Any, maximum_items: int, maximum_length: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= maximum_items
        and all(text(item, maximum_length) for item in value)
    )


def timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value, re.ASCII
    ):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def decimal(value: Any, positive: bool = True) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"0|[1-9][0-9]{0,18}", value) is not None
        and int(value) <= 2**63 - 1
        and (not positive or value != "0")
    )


def valid_model(model: type[BaseModel], value: Any) -> bool:
    try:
        model.model_validate(value)
    except ValidationError, ValueError, TypeError, OverflowError:
        return False
    return True


def encoded_size(value: Any, *, spaced: bool = False) -> int:
    return len(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=None if spaced else (",", ":")
        ).encode("utf-8")
    )


def _metadata_value(value: Any, stack: set[int]) -> bool:
    if value is None or type(value) is bool:
        return True
    if type(value) in {int, float}:
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if isinstance(value, str):
        return valid_unicode(value) and "\0" not in value
    if not isinstance(value, (list, dict)) or id(value) in stack:
        return False
    stack.add(id(value))
    result = _metadata_container(value, stack)
    stack.remove(id(value))
    return result


def _metadata_container(value: list | dict, stack: set[int]) -> bool:
    if isinstance(value, list):
        return all(_metadata_value(entry, stack) for entry in value)
    return all(
        isinstance(key, str)
        and valid_unicode(key)
        and "\0" not in key
        and key.lower() not in RESERVED_METADATA_KEYS
        and _metadata_value(entry, stack)
        for key, entry in value.items()
    )


def metadata(value: Any) -> bool:
    if not isinstance(value, dict) or not _metadata_value(value, set()):
        return False
    try:
        return _metadata_size(value) <= 16_384
    except ValueError, UnicodeError, OverflowError:
        return False


def _number_size(value: int | float) -> int:
    # JSON.parse represents every number as binary64. JSON.stringify uses fixed
    # notation in [1e-6, 1e21), drops .0 and negative zero, and unpads exponents.
    number = float(value)
    if number == 0:
        return 1
    spelling = repr(number)
    if 1e-6 <= abs(number) < 1e21:
        fixed = format(Decimal(spelling), "f")
        return len(fixed.rstrip("0").rstrip(".") if "." in fixed else fixed)
    coefficient, exponent = spelling.split("e")
    coefficient = coefficient.removesuffix(".0")
    return len(coefficient) + 2 + len(str(abs(int(exponent))))


def _metadata_size(value: Any) -> int:
    if type(value) in {int, float}:
        return _number_size(value)
    if isinstance(value, list):
        return 2 + sum(_metadata_size(item) for item in value) + 2 * max(0, len(value) - 1)
    if isinstance(value, dict):
        return (
            2
            + sum(encoded_size(key) + 2 + _metadata_size(item) for key, item in value.items())
            + 2 * max(0, len(value) - 1)
        )
    return encoded_size(value)


def actor(value: Any) -> TypeGuard[JsonObject]:
    return (
        isinstance(value, dict)
        and allowed_keys(value, ["actor_client", "actor_session_id", "actor_model"])
        and text(value.get("actor_client"), 80)
        and text(value.get("actor_session_id"), 200)
        and optional(value, "actor_model", lambda item: nullable_text(item, 120))
    )


def dashboard_actor(value: Any, *, no_model: bool = False) -> TypeGuard[JsonObject]:
    return (
        actor(value)
        and value.get("actor_client") == "dashboard"
        and (not no_model or value.get("actor_model") is None)
    )


def affected_paths(value: Any) -> bool:
    if not isinstance(value, list) or len(value) > 64:
        return False
    if not all(
        isinstance(path, str) and path.isascii() and 1 <= len(path) <= 512 for path in value
    ):
        return False
    if len(set(value)) != len(value) or sum(map(len, value)) > 16_384:
        return False
    return all(
        component not in {".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._@+=,~*\-]+", component) is not None
        and (component == "**" or "**" not in component)
        for path in value
        for component in path.split("/")
    )


def checkpoint(value: Any, *, include_kind: bool = False) -> TypeGuard[JsonObject]:
    if not isinstance(value, dict):
        return False
    keys = [
        "prompt",
        "source_client",
        "source_session_id",
        "source_model",
        "source_session_url",
        "repository_branch",
        "verified_against",
        "affected_paths",
        "tags",
        "source_metadata",
        "client_operation_id",
    ] + (["kind"] if include_kind else [])
    return (
        allowed_keys(value, keys)
        and (not include_kind or value.get("kind") in ("context", "progress"))
        and text(value.get("prompt"), 100_000)
        and text(value.get("source_client"), 80)
        and text(value.get("source_session_id"), 200)
        and _checkpoint_optional(value)
    )


def _checkpoint_optional(value: JsonObject) -> bool:
    bounds = {"source_model": 120, "source_session_url": 2_000, "repository_branch": 200}
    commit = value.get("verified_against")
    valid_commit = isinstance(commit, str) and re.fullmatch(r"[a-fA-F0-9]{7,64}", commit)
    return (
        all(
            optional(value, key, lambda item, maximum=maximum: nullable_text(item, maximum))
            for key, maximum in bounds.items()
        )
        and (commit is None or bool(valid_commit))
        and optional(value, "affected_paths", affected_paths)
        and (not value.get("affected_paths") or bool(valid_commit))
        and optional(value, "tags", lambda item: string_array(item, 50, 50))
        and optional(value, "source_metadata", metadata)
    )


def revision(value: Any, *, merge: bool = False) -> bool:
    count = "work_event_count" if merge else "relationship_event_count"
    return (
        isinstance(value, dict)
        and exact_keys(value, ["work_version", "context_checkpoint_id", count])
        and integer(value.get("work_version"), 1)
        and uuid(value.get("context_checkpoint_id"))
        and integer(value.get(count), 1 if merge else 0)
    )
