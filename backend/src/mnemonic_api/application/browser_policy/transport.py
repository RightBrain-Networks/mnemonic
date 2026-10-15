"""Browser origin and control-transport checks; forwarding headers grant no trust."""

from collections.abc import Collection, Mapping
from urllib.parse import urlsplit

CONTROL_NAMES = frozenset(
    {
        "client_operation_id",
        "client-operation-id",
        "idempotency-key",
        "x-idempotency-key",
        "x-client-operation-id",
        "gate_id",
        "gate-id",
        "human-gate-id",
        "x-gate-id",
        "x-human-gate-id",
        "lease_token",
        "lease-token",
        "x-lease-token",
    }
)


def forbidden_control_transport(headers: Mapping[str, str]) -> bool:
    return any(
        key.lower() in CONTROL_NAMES
        or key.lower() == "cookie"
        and any(
            entry.split("=", 1)[0].strip().lower() in CONTROL_NAMES for entry in value.split(";")
        )
        for key, value in headers.items()
    )


def trusted_request(headers: Mapping[str, str], method: str, origins: Collection[str]) -> bool:
    trust_fields = [
        key.lower()
        for key, _ in headers.items()
        if key.lower() in {"host", "origin", "sec-fetch-site"}
    ]
    if len(trust_fields) != len(set(trust_fields)):
        return False
    normalized = {key.lower(): value for key, value in headers.items()}
    host = normalized.get("host", "").lower()
    if not host or host not in {urlsplit(origin).netloc.lower() for origin in origins}:
        return False
    if normalized.get("sec-fetch-site", "") not in {"", "same-origin", "none"}:
        return False
    origin = normalized.get("origin")
    if not origin:
        return method == "GET"
    return origin in origins and urlsplit(origin).netloc.lower() == host
