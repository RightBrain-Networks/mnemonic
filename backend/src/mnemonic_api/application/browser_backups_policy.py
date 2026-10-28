"""Closed browser backup inventory and admission before worker contact."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.requests import Request

from mnemonic_api.application.browser_file_transport import combined_headers, declared_length
from mnemonic_api.application.browser_policy import forbidden_control_transport, trusted_request
from mnemonic_api.application.browser_policy.values import MAX_SAFE_INTEGER, uuid
from mnemonic_api.application.state import settings_of

BACKUP_BROWSER_PREFIX = "/api/backups"
BACKUP_FILENAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.bz2")
BACKUP_SELECTION_MAX_BYTES = 128 * 1024


class BackupRejection(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class BackupAdmission:
    action: str
    path: str
    project_id: str
    maximum: int
    url: str
    token: str


def backup_route(path: str, method: str) -> str | None:
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "projects" or not uuid(parts[1]):
        return None
    if (len(parts) == 5 and parts[2] == "backups" and BACKUP_FILENAME.fullmatch(parts[3])
            and parts[4] == "restore" and method == "POST"):
        return "restore_stored"
    if len(parts) == 3:
        return {("backups", "GET"): "list", ("backups", "POST"): "create",
                ("backup-settings", "PUT"): "settings", ("exports", "POST"): "export",
                ("restore", "POST"): "restore"}.get((parts[2], method))
    if len(parts) != 4:
        return None
    if parts[2] == "backups" and BACKUP_FILENAME.fullmatch(parts[3]):
        return {"GET": "download", "DELETE": "delete"}.get(method)
    if parts[2] == "backup-jobs" and uuid(parts[3]) and method == "GET":
        return "job"
    return None


def _worker_connection(request: Request) -> tuple[str, str]:
    settings = settings_of(request)
    token = settings.backup_token.get_secret_value() if settings.backup_token else ""
    if len(token) < 32:
        raise BackupRejection(503, "The dashboard backup connection is not configured.")
    try:
        base = urlsplit(settings.backup_url)
        if (base.scheme not in {"http", "https"} or not base.hostname or base.username
                or base.password or base.path not in {"", "/"} or base.query or base.fragment):
            raise ValueError("Invalid worker address")
        _ = base.port
    except ValueError:
        raise BackupRejection(
            503, "The backup service address is not configured correctly.") from None
    return settings.backup_url.rstrip("/"), token


def admit_backup(request: Request) -> BackupAdmission:
    settings = settings_of(request)
    headers = combined_headers(request.headers)
    if not trusted_request(headers, request.method, set(settings.allowed_dashboard_origins)):
        raise BackupRejection(403, "This dashboard request is not from a trusted origin.")
    path = request.scope["path"].removeprefix(BACKUP_BROWSER_PREFIX + "/")
    action = backup_route(path, request.method)
    if action is None or b"%2f" in request.scope.get("raw_path", b"").lower():
        raise BackupRejection(404, "Route not found.")
    if request.scope.get("query_string"):
        raise BackupRejection(400, "Backup queries are not supported.")
    if forbidden_control_transport(headers):
        raise BackupRejection(400, "The request contains a forbidden control header.")
    url, token = _worker_connection(request)
    return BackupAdmission(action, path, path.split("/")[1], settings.backup_max_bytes, url, token)


def admit_backup_body(request: Request, admission: BackupAdmission) -> None:
    headers = combined_headers(request.headers)
    encoding = headers.get("content-encoding")
    if encoding and encoding.lower() != "identity":
        raise BackupRejection(415, "Encoded backup request bodies are not supported.")
    try:
        length = declared_length(headers, MAX_SAFE_INTEGER)
    except ValueError:
        raise BackupRejection(400, "Invalid backup content length.") from None
    if admission.action in {"settings", "export"}:
        if headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise BackupRejection(415, "Send backup settings or export selections as JSON.")
        if length is not None and length > BACKUP_SELECTION_MAX_BYTES:
            raise BackupRejection(413, "The selection is too large.")
        return
    if admission.action != "restore":
        if (admission.action == "restore_stored"
                and headers.get("x-confirm-project") != admission.project_id):
            raise BackupRejection(400, "Confirm the selected project before restoring.")
        if length not in (None, 0):
            raise BackupRejection(400, "This backup action does not accept a request body.")
        return
    _admit_restore(headers, admission, length)


def _admit_restore(headers, admission: BackupAdmission, length: int | None) -> None:
    if headers.get("x-confirm-project") != admission.project_id:
        raise BackupRejection(400, "Confirm the selected project before restoring.")
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/octet-stream":
        raise BackupRejection(415, "Send the compressed archive as application/octet-stream.")
    if length == 0:
        raise BackupRejection(400, "A backup archive is required.")
    if length is not None and length > admission.maximum:
        raise BackupRejection(413, "Backup archive exceeds the configured size limit.")
