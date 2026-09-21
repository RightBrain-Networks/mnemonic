#!/usr/bin/env python3
"""Prepare a private file snapshot, then stream it using an MCP upload grant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from email.message import Message
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from threading import current_thread, main_thread
from types import FrameType
from typing import Any, BinaryIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4, uuid5

CHUNK_BYTES = 64 * 1024
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
REQUEST_SECONDS = 180
SOCKET_SECONDS = 30
GRANTED_REQUEST_SECONDS = 330
GRANTED_SOCKET_SECONDS = 310
PRESERVE_REQUEST = (
    "Keep the prepared request directory unchanged; never generate a new operation UUID "
    "for this intent. After an unknown outcome, send that same directory at most once more, "
    "then reconcile with get_artifact/list_artifact_history."
)


class UploadError(Exception):
    """A local diagnostic that never includes file bytes, credentials or server prose."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        return None


def api_origin(value: str | None) -> str:
    if not value or any(ord(char) <= 32 for char in value):
        raise UploadError("Provide --api-url or MNEMONIC_API_URL with the API origin.")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and (
                parsed.username is None and parsed.password is None and parsed.port != 0
            )
        )
    except ValueError:
        valid = False
    if not valid or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise UploadError(
            "The API URL must be an HTTP(S) origin without credentials or a path."
        )
    return value.rstrip("/")


def credential() -> str:
    key = os.environ.get("MNEMONIC_API_KEY", "")
    if not key or any(ord(char) <= 32 or ord(char) > 126 for char in key):
        raise UploadError(
            "Provision MNEMONIC_API_KEY in the client process environment."
        )
    return key


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def decode_json(raw: bytes) -> dict[str, Any]:
    result = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise TypeError("Expected a JSON object")
    return result


def metadata_of(args: argparse.Namespace) -> dict[str, Any]:
    values = {
        "filename": args.filename or args.source.name,
        "agent_session_id": args.agent_session_id,
        "actor_client": args.actor_client,
    }
    for field, limit in (
        ("filename", 255),
        ("agent_session_id", 200),
        ("actor_client", 80),
    ):
        value = values[field]
        if (
            not value.strip()
            or len(value) > limit
            or any(ord(char) < 32 for char in value)
        ):
            raise UploadError(
                f"{field} must be nonblank, control-free, at most {limit} chars."
            )
    if "/" in values["filename"] or "\\" in values["filename"]:
        raise UploadError(
            "Filename must be a basename; the API validates its storage policy."
        )
    if args.description is not None:
        if len(args.description) > 4000 or "\x00" in args.description:
            raise UploadError(
                "Description must be at most 4000 characters without NUL."
            )
        values["description"] = args.description
    for field in (
        "work_item_id",
        "sensitive",
        "related_work_item_ids",
        "related_artifact_ids",
    ):
        value = getattr(args, field)
        if value is not None:
            values[field] = (
                [str(item) for item in value]
                if isinstance(value, list)
                else (str(value) if isinstance(value, UUID) else value)
            )
    return values


def validate_metadata(values: dict[str, Any]) -> str:
    for field in ("related_work_item_ids", "related_artifact_ids"):
        if len(values.get(field, [])) > 50:
            raise UploadError(
                "At most 50 related work IDs and 50 related artifact IDs are allowed."
            )
    encoded = encode_json(values)
    if len(encoded) > 16 * 1024:
        raise UploadError("Artifact metadata exceeds the 16 KiB header limit.")
    return encoded


@contextmanager
def regular_file(path: Path) -> Iterator[BinaryIO]:
    # Nonblocking open prevents FIFOs/devices from hanging before the regular-file check.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise UploadError(
                "Source and prepared request files must be regular files."
            )
        yield source


def copy_content(source: BinaryIO, destination: BinaryIO) -> tuple[int, str]:
    size, digest = 0, hashlib.sha256()
    while chunk := source.read(CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_CONTENT_BYTES:
            raise UploadError(
                "This client supports files up to 1 GiB; API limits may be lower."
            )
        destination.write(chunk)
        digest.update(chunk)
    destination.flush()
    return size, digest.hexdigest()


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    origin = api_origin(args.api_url) if args.api_url else None
    if (args.artifact_id is None) != (args.expected_revision is None) or (
        args.expected_revision is not None and args.expected_revision < 1
    ):
        raise UploadError(
            "Replacement requires both --artifact-id and positive --expected-revision."
        )
    metadata = metadata_of(args)
    validate_metadata(metadata)
    key = os.environ.get("MNEMONIC_API_KEY")
    if key and key in encode_json(metadata):
        raise UploadError("Request metadata must not contain the API credential.")
    operation = args.client_operation_id or uuid4()
    request_dir = args.request_dir.absolute()
    # An existing directory is never repurposed for another operation or retry.
    request_dir.mkdir(mode=0o700)
    with (
        regular_file(args.source) as source,
        (request_dir / "content").open("xb") as content,
    ):
        os.fchmod(content.fileno(), 0o600)
        size, digest = copy_content(source, content)
        os.fsync(content.fileno())
    manifest = {
        "api_origin": origin,
        "project_id": str(args.project_id),
        "client_operation_id": str(operation),
        "metadata": metadata,
        "artifact_id": str(args.artifact_id) if args.artifact_id else None,
        "expected_revision": args.expected_revision,
        "size_bytes": size,
        "sha256": digest,
    }
    with (request_dir / "request.json").open("x", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.write(encode_json({"api_origin": origin, "upload_intent": upload_intent(manifest)}))
        output.flush()
        os.fsync(output.fileno())
    for directory in (request_dir, request_dir.parent):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {
        "status": "prepared",
        "request_dir": str(request_dir),
        "upload_intent": upload_intent(manifest),
        "expected_artifact": summary(manifest),
    }


def summary(manifest: dict[str, Any]) -> dict[str, Any]:
    target = manifest["artifact_id"] or str(
        uuid5(
            UUID(manifest["project_id"]),
            f"artifact:{manifest['client_operation_id']}",
        )
    )
    return {
        "project_id": manifest["project_id"],
        "artifact_id": target,
        "client_operation_id": manifest["client_operation_id"],
        "revision": (manifest["expected_revision"] or 0) + 1,
        "size_bytes": manifest["size_bytes"],
        "sha256": manifest["sha256"],
    }


def load_request(request_dir: Path) -> dict[str, Any]:
    with regular_file(request_dir / "request.json") as source:
        raw = source.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise UploadError("Prepared request metadata exceeds its limit.")
    manifest = decode_json(raw)
    if "upload_intent" in manifest:
        manifest = {**manifest["upload_intent"], "api_origin": manifest["api_origin"]}
    if manifest["api_origin"] is not None:
        api_origin(manifest["api_origin"])
    for field in ("project_id", "client_operation_id"):
        if str(UUID(manifest[field])) != manifest[field]:
            raise ValueError("Noncanonical request identity")
    revision, target = manifest["expected_revision"], manifest["artifact_id"]
    if target is not None:
        if str(UUID(target)) != target or type(revision) is not int or revision < 1:
            raise ValueError("Invalid replacement")
    elif revision is not None:
        raise ValueError("Unexpected revision")
    size, digest = manifest["size_bytes"], manifest["sha256"]
    if (
        type(size) is not int
        or not 0 <= size <= MAX_CONTENT_BYTES
        or (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest))
    ):
        raise ValueError("Invalid content identity")
    validate_metadata(manifest["metadata"])
    return manifest


def header(response: HTTPResponse, name: str) -> str | None:
    values = response.headers.get_all(name, [])
    if len(values) > 1:
        raise UploadError(
            "The API returned ambiguous response headers. " + PRESERVE_REQUEST
        )
    return values[0] if values else None


def response_json(response: HTTPResponse) -> dict[str, Any]:
    if header(response, "Content-Encoding") not in {None, "identity"} or (
        header(response, "Content-Type")
        not in {
            "application/json",
            "application/json; charset=utf-8",
        }
    ):
        raise ValueError("Invalid JSON response encoding")
    length = header(response, "Content-Length")
    if length is not None and (
        not re.fullmatch(r"[0-9]{1,12}", length)
        or (
            int(length) > MAX_JSON_BYTES
            or header(response, "Transfer-Encoding") is not None
        )
    ):
        raise ValueError("Invalid response framing")
    raw = response.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES or length is not None and int(length) != len(raw):
        raise ValueError("Invalid response length")
    return decode_json(raw)


def verify_metadata(result: dict[str, Any], manifest: dict[str, Any]) -> None:
    metadata = manifest["metadata"]
    fields = {
        "filename": "filename",
        "description": "description",
        "work_item_id": "originating_work_item_id",
        "sensitive": "sensitive",
    }
    if manifest["artifact_id"] is None:
        fields.update(
            agent_session_id="created_by_agent_session_id",
            actor_client="created_by_client",
        )
    for requested, returned in fields.items():
        if requested in metadata and (
            type(result.get(returned)) is not type(metadata[requested])
            or result[returned] != metadata[requested]
        ):
            raise ValueError("Receipt metadata mismatch")
    for field in ("related_work_item_ids", "related_artifact_ids"):
        links = set(result.get(field, []))
        if field == "related_work_item_ids":
            links.add(result.get("originating_work_item_id"))
        if not set(metadata.get(field, [])) <= links:
            raise ValueError("Receipt links mismatch")


def verify_receipt(response: HTTPResponse, manifest: dict[str, Any]) -> dict[str, Any]:
    expected = summary(manifest)
    if response.status != (201 if manifest["artifact_id"] is None else 200) or (
        header(response, "X-Client-Operation-ID") != manifest["client_operation_id"]
        or header(response, "X-Artifact-Operation-Replayed") not in {"true", "false"}
    ):
        raise ValueError("Unexpected receipt headers")
    result = response_json(response)
    for field in ("project_id", "revision", "size_bytes", "sha256"):
        if (
            type(result.get(field)) is not type(expected[field])
            or result[field] != expected[field]
        ):
            raise ValueError("Receipt identity mismatch")
    if (
        result.get("id") != expected["artifact_id"]
        or result.get("content_available") is not True
        or ("deleted_at" not in result or result["deleted_at"] is not None)
    ):
        raise ValueError("Receipt artifact mismatch")
    verify_metadata(result, manifest)
    return {
        **expected,
        "replayed": header(response, "X-Artifact-Operation-Replayed") == "true",
    }


def http_failure(error: HTTPError) -> UploadError:
    try:
        detail = response_json(error).get("detail", {})
        code = detail.get("code")
    except (ValueError, TypeError, AttributeError, RecursionError, UploadError):
        code = None
    if error.code == 401 and code == "artifact_upload_grant_invalid":
        message = "Upload grant expired or invalid. Reauthorize the exact upload_intent via MCP. "
    elif code == "artifact_upload_content_mismatch":
        message = "Transferred bytes did not match the grant. Stop and reconcile. "
    elif error.code == 503 and code == "artifact_storage_unavailable":
        boundary = detail.get("context", {})
        local = (isinstance(boundary, dict)
                 and boundary.get("storage_boundary") == "mcp_upload_staging")
        location = "MCP temporary upload storage" if local else "Artifact storage"
        message = f"{location} needs operator repair. Stop retries until it is repaired. "
    elif error.code == 409 and code == "artifact_revision_conflict":
        message = "Artifact revision changed. Read current metadata before a new intent. "
    elif code in {"artifact_origin_immutable", "artifact_filename_immutable"}:
        message = {
            "artifact_origin_immutable": "The originating work_item_id cannot change. ",
            "artifact_filename_immutable": "Replacement must keep the original filename. ",
        }[code]
    elif error.code == 409 and code in {"artifact_operation_conflict", "client_operation_conflict"}:
        message = (
            "Operation UUID conflicts with a different request. Stop this intent. "
        )
    elif error.code in {413, 503} and code in {
        "artifact_too_large",
        "artifact_library_disabled",
    }:
        message = "Upload refused by size/availability policy. Read /api/v1/artifacts/status. "
    elif 300 <= error.code < 400:
        message = "The API redirected the request; redirects are refused. "
    else:
        message = f"The API refused the upload (HTTP {error.code}). "
    return UploadError(message + PRESERVE_REQUEST)


def deadline_expired(signum: int, frame: FrameType | None) -> None:
    raise UploadError(
        "Upload outcome is unknown: request time limit exceeded. " + PRESERVE_REQUEST
    )


@contextmanager
def request_deadline(seconds: int = REQUEST_SECONDS) -> Iterator[None]:
    if (
        os.name != "posix"
        or not hasattr(signal, "setitimer")
        or (
            current_thread() is not main_thread()
            or any(signal.getitimer(signal.ITIMER_REAL))
        )
    ):
        raise UploadError(
            "Run this client on Linux/macOS in the main thread without an active timer."
        )
    previous = signal.signal(signal.SIGALRM, deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def transmit(manifest: dict[str, Any], content: BinaryIO, key: str) -> dict[str, Any]:
    metadata = validate_metadata(manifest["metadata"])
    if key in encode_json(manifest):
        raise UploadError(
            "Prepared request metadata must not contain the API credential."
        )
    url = f"{manifest['api_origin']}/api/v1/projects/{manifest['project_id']}/artifacts"
    headers = {
        "Authorization": f"Bearer {key}",
        "Accept-Encoding": "identity",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(manifest["size_bytes"]),
        "X-Client-Operation-ID": manifest["client_operation_id"],
        "X-Artifact-Metadata": metadata,
    }
    method = "POST"
    if manifest["artifact_id"] is not None:
        url += f"/{manifest['artifact_id']}/content"
        method = "PUT"
        headers["X-Artifact-Expected-Revision"] = str(manifest["expected_revision"])
    request = Request(url, data=content, headers=headers, method=method)
    return transmit_request(request, manifest)


def upload_intent(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        field: manifest[field]
        for field in (
            "project_id",
            "client_operation_id",
            "artifact_id",
            "expected_revision",
            "size_bytes",
            "sha256",
            "metadata",
        )
    }


def grant_bytes(path: Path) -> bytes:
    if str(path) == "-":
        raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
    else:
        with regular_file(path) as source:
            if os.fstat(source.fileno()).st_mode & 0o077:
                raise UploadError("Grant file must have owner-only permissions (chmod 600).")
            raw = source.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise UploadError("Upload grant exceeds its limit.")
    return raw


def load_grant(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    grant = decode_json(grant_bytes(path))
    if set(grant) != {
        "upload_url",
        "upload_token",
        "expires_at",
        "intent",
        "artifact_library",
    }:
        raise UploadError(
            "Save the exact structured authorize_artifact_upload result as the grant."
        )
    requested = json.dumps(upload_intent(manifest), sort_keys=True, ensure_ascii=True)
    granted = json.dumps(grant["intent"], sort_keys=True, ensure_ascii=True)
    if requested != granted:
        raise UploadError(
            "Grant does not match the frozen upload intent. " + PRESERVE_REQUEST
        )
    url = urlsplit(grant["upload_url"])
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.port == 0
        or url.username is not None
        or url.password is not None
        or not url.path
        or url.query
        or url.fragment
        or any(ord(char) <= 32 or ord(char) >= 127 for char in grant["upload_url"])
    ):
        raise UploadError(
            "Grant must supply an HTTP(S) MCP endpoint without credentials or query."
        )
    token = grant["upload_token"]
    if not isinstance(token, str) or not re.fullmatch(
        r"v1\.[0-9]{1,12}\.[0-9a-f]{64}", token
    ):
        raise UploadError("Invalid upload grant token.")
    expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
    if expires.tzinfo is None or expires.timestamp() != int(token.split(".")[1]):
        raise UploadError("Invalid upload grant expiry.")
    if expires.timestamp() <= time.time():
        raise UploadError(
            "Upload grant expired. Reauthorize the exact upload_intent through MCP; "
            "keep the prepared directory and operation UUID unchanged."
        )
    if token in encode_json(manifest):
        raise UploadError("Frozen request metadata must not contain the upload token.")
    return grant


def transmit_granted(
    manifest: dict[str, Any],
    content: BinaryIO,
    grant: dict[str, Any],
) -> dict[str, Any]:
    headers = {
        "Authorization": f"MnemonicUpload {grant['upload_token']}",
        "X-Artifact-Upload-Intent": encode_json(upload_intent(manifest)),
        "Accept-Encoding": "identity",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(manifest["size_bytes"]),
    }
    request = Request(grant["upload_url"], data=content, headers=headers, method="POST")
    return transmit_request(request, manifest, granted=True)


def transmit_request(
    request: Request,
    manifest: dict[str, Any],
    *,
    granted: bool = False,
) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({}), NoRedirects())
    deadline = GRANTED_REQUEST_SECONDS if granted else REQUEST_SECONDS
    socket_timeout = GRANTED_SOCKET_SECONDS if granted else SOCKET_SECONDS
    with request_deadline(deadline):
        try:
            with opener.open(request, timeout=socket_timeout) as response:
                return verify_receipt(response, manifest)
        except HTTPError as error:
            try:
                raise http_failure(error) from None
            finally:
                error.close()


def send(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_request(args.request_dir)
    grant = load_grant(args.grant_file, manifest) if args.grant_file else None
    if grant is None and manifest["api_origin"] is None:
        raise UploadError(
            "Authorize the prepared upload_intent with authorize_artifact_upload "
            "and supply its private --grant-file. No API key is needed."
        )
    key = credential() if grant is None else None
    # Verify and send a private per-attempt copy. The source path is never revisited,
    # and changing the retained snapshot during HTTP cannot change transmitted bytes.
    with (
        regular_file(args.request_dir / "content") as source,
        tempfile.TemporaryFile() as content,
    ):
        size, digest = copy_content(source, content)
        if size != manifest["size_bytes"] or digest != manifest["sha256"]:
            raise UploadError(
                "Prepared content changed. Stop; do not regenerate this request."
            )
        content.seek(0)
        if grant is not None:
            return transmit_granted(manifest, content, grant)
        assert key is not None
        return transmit(manifest, content, key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser(
        "prepare", help="Freeze bytes and metadata locally; no HTTP calls"
    )
    staging.add_argument("--api-url", default=os.environ.get("MNEMONIC_API_URL"))
    staging.add_argument(
        "--request-dir", type=Path, required=True, help="New private directory"
    )
    staging.add_argument(
        "--source", type=Path, required=True, help="Local regular file; never printed"
    )
    staging.add_argument("--project-id", type=UUID, required=True)
    staging.add_argument(
        "--client-operation-id", type=UUID, help="Default: generate once locally"
    )
    staging.add_argument("--agent-session-id", required=True)
    staging.add_argument("--actor-client", required=True)
    staging.add_argument(
        "--filename", help="Default: source basename; replacement needs original name"
    )
    staging.add_argument("--description")
    staging.add_argument("--work-item-id", type=UUID)
    staging.add_argument(
        "--related-work-item-id",
        dest="related_work_item_ids",
        type=UUID,
        action="append",
    )
    staging.add_argument(
        "--related-artifact-id", dest="related_artifact_ids", type=UUID, action="append"
    )
    staging.add_argument(
        "--sensitive", action=argparse.BooleanOptionalAction, default=None
    )
    staging.add_argument(
        "--artifact-id", type=UUID, help="Replace this existing artifact"
    )
    staging.add_argument(
        "--expected-revision", type=int, help="Revision just read for replacement"
    )
    sending = commands.add_parser(
        "send", help="Send prepared bytes once; never retries automatically"
    )
    sending.add_argument("--request-dir", type=Path, required=True)
    sending.add_argument(
        "--grant-file", type=Path, help="Private MCP grant JSON; - reads bounded JSON from stdin"
    )
    args = parser.parse_args()
    try:
        result = prepare(args) if args.command == "prepare" else send(args)
    except UploadError as error:
        print(f"Upload failed: {error}", file=sys.stderr)
        return 1
    except (
        OSError,
        URLError,
        HTTPException,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
    ):
        print(
            "Upload failed: invalid request, connection, receipt or filesystem error. "
            "A send may have committed. " + PRESERVE_REQUEST,
            file=sys.stderr,
        )
        return 1
    print(encode_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
