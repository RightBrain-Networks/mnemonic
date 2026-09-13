"""Locally constructed gateway errors; never expose upstream diagnostics."""

import errno
import json

import httpx
from starlette.responses import JSONResponse

from .api import _invalid_response_constant, _response_object_without_duplicate_keys
from .artifact_errors import _STORAGE_FAILURES

_KNOWN_ERRORS = {
    404: {
        "project_not_found",
        "artifact_not_found",
        "artifact_work_item_not_found",
        "artifact_related_artifact_not_found",
    },
    409: {
        "artifact_revision_conflict",
        "artifact_filename_immutable",
        "artifact_origin_immutable",
        "client_operation_conflict",
    },
    410: {"artifact_deleted"},
    415: {"artifact_encoding_unsupported"},
    422: {
        "artifact_filename_unsafe",
        "artifact_header_invalid",
        "artifact_metadata_invalid",
        "artifact_operation_id_invalid",
        "artifact_revision_invalid",
        "artifact_query_forbidden",
        "artifact_length_invalid",
        "artifact_body_forbidden",
        "artifact_link_limit",
        "artifact_self_link",
        "client_operation_secret_echo",
    },
}
_STORAGE_CAUSES = {
    errno.EACCES: "storage_permission_denied",
    errno.EPERM: "storage_permission_denied",
    errno.ENOSPC: "storage_full",
    errno.EDQUOT: "storage_full",
    errno.EROFS: "storage_read_only",
}


def rejection(status: int, code: str, context: dict | None = None) -> JSONResponse:
    detail: dict = {"code": code}
    if context is not None:
        detail["context"] = context
    return JSONResponse(
        {"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"}
    )


def staging_failure(error: OSError) -> JSONResponse:
    return rejection(
        503,
        "artifact_storage_unavailable",
        {
            "cause": _STORAGE_CAUSES.get(error.errno, "storage_unavailable"),
            "attempt_not_committed": True,
            "storage_boundary": "mcp_upload_staging",
        },
    )


def _context(status: int, code: str, context: dict) -> dict | None:
    if status == 503 and code == "artifact_storage_unavailable":
        cause, uncommitted = context.get("cause"), context.get("attempt_not_committed")
        if isinstance(cause, str) and cause in _STORAGE_FAILURES and type(uncommitted) is bool:
            return {"cause": cause, "attempt_not_committed": uncommitted}
    maximum = context.get("max_bytes")
    if (
        type(maximum) is int
        and 0 <= maximum <= 1024 * 1024 * 1024
        and (
            (status, code, maximum) == (503, "artifact_library_disabled", 0)
            or (status == 413 and code == "artifact_too_large" and maximum > 0)
        )
    ):
        return {"max_bytes": maximum}
    if code in _KNOWN_ERRORS.get(status, set()):
        return {}
    return None


def upstream_failure(response: httpx.Response) -> JSONResponse:
    try:
        if response.headers.get_list("content-type") not in (
            ["application/json"],
            ["application/json; charset=utf-8"],
        ):
            raise ValueError("Invalid error encoding")
        body = json.loads(
            response.content,
            object_pairs_hook=_response_object_without_duplicate_keys,
            parse_constant=_invalid_response_constant,
        )
        detail = body["detail"]
        code, context = detail["code"], detail.get("context", {})
        if not isinstance(code, str) or not isinstance(context, dict):
            raise TypeError("Invalid error envelope")
        safe = _context(response.status_code, code, context)
        if safe is not None:
            return rejection(response.status_code, code, safe or None)
    except ValueError, TypeError, KeyError, AttributeError, RecursionError:
        pass
    return rejection(502, "artifact_upload_outcome_unknown")
