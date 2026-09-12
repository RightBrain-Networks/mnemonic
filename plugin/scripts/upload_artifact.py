#!/usr/bin/env python3
"""Prepare a private file snapshot, then stream it directly to the Mnemonic API."""

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
from collections.abc import Iterator
from contextlib import contextmanager
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
            and (parsed.username is None and parsed.password is None and parsed.port != 0)
        )
    except ValueError:
        valid = False
    if not valid or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise UploadError("The API URL must be an HTTP(S) origin without credentials or a path.")
    return value.rstrip("/")


def credential() -> str:
    key = os.environ.get("MNEMONIC_API_KEY", "")
    if not key or any(ord(char) <= 32 or ord(char) > 126 for char in key):
        raise UploadError("Provision MNEMONIC_API_KEY in the client process environment.")
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
        raise ValueError("Expected a JSON object")
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
        if not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
            raise UploadError(f"{field} must be nonblank, control-free, at most {limit} chars.")
    if "/" in values["filename"] or "\\" in values["filename"]:
        raise UploadError("Filename must be a basename; the API validates its storage policy.")
    if args.description is not None:
        if len(args.description) > 4000 or "\x00" in args.description:
            raise UploadError("Description must be at most 4000 characters without NUL.")
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
            raise UploadError("Source and prepared request files must be regular files.")
        yield source


def copy_content(source: BinaryIO, destination: BinaryIO) -> tuple[int, str]:
    size, digest = 0, hashlib.sha256()
    while chunk := source.read(CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_CONTENT_BYTES:
            raise UploadError("This client supports files up to 1 GiB; API limits may be lower.")
        destination.write(chunk)
        digest.update(chunk)
    destination.flush()
    return size, digest.hexdigest()


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    origin = api_origin(args.api_url)
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
        output.write(encode_json(manifest))
        output.flush()
        os.fsync(output.fileno())
    for directory in (request_dir, request_dir.parent):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {"status": "prepared", "request_dir": str(request_dir), **summary(manifest)}


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
        raise UploadError("The API returned ambiguous response headers. " + PRESERVE_REQUEST)
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
        or (int(length) > MAX_JSON_BYTES or header(response, "Transfer-Encoding") is not None)
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
        if type(result.get(field)) is not type(expected[field]) or result[field] != expected[field]:
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
    if error.code == 503 and code == "artifact_storage_unavailable":
        message = "Artifact storage needs operator repair. Stop retries until it is repaired. "
    elif error.code == 409 and code == "artifact_revision_conflict":
        message = (
            "Artifact revision changed. Read current metadata before a new replacement intent. "
        )
    elif error.code == 409 and code == "artifact_operation_conflict":
        message = "Operation UUID conflicts with a different request. Stop this intent. "
    elif error.code in {413, 503} and code in {
        "artifact_too_large",
        "artifact_library_disabled",
    }:
        message = (
            "The API size/availability policy refused the upload. Read /api/v1/artifacts/status. "
        )
    elif 300 <= error.code < 400:
        message = "The API redirected the request; redirects are refused. "
    else:
        message = f"The API refused the upload (HTTP {error.code}). "
    return UploadError(message + PRESERVE_REQUEST)


def deadline_expired(signum: int, frame: FrameType | None) -> None:
    raise UploadError("Upload outcome is unknown: request time limit exceeded. " + PRESERVE_REQUEST)


@contextmanager
def request_deadline() -> Iterator[None]:
    if (
        os.name != "posix"
        or not hasattr(signal, "setitimer")
        or (current_thread() is not main_thread() or any(signal.getitimer(signal.ITIMER_REAL)))
    ):
        raise UploadError(
            "Run this client on Linux/macOS in the main thread without an active timer."
        )
    previous = signal.signal(signal.SIGALRM, deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, REQUEST_SECONDS)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def transmit(manifest: dict[str, Any], content: BinaryIO, key: str) -> dict[str, Any]:
    metadata = validate_metadata(manifest["metadata"])
    if key in encode_json(manifest):
        raise UploadError("Prepared request metadata must not contain the API credential.")
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
    opener = build_opener(ProxyHandler({}), NoRedirects())
    request = Request(url, data=content, headers=headers, method=method)
    with request_deadline():
        try:
            with opener.open(request, timeout=SOCKET_SECONDS) as response:
                return verify_receipt(response, manifest)
        except HTTPError as error:
            try:
                raise http_failure(error) from None
            finally:
                error.close()


def send(args: argparse.Namespace) -> dict[str, Any]:
    manifest = load_request(args.request_dir)
    key = credential()
    # Verify and send a private per-attempt copy. The source path is never revisited,
    # and changing the retained snapshot during HTTP cannot change transmitted bytes.
    with (
        regular_file(args.request_dir / "content") as source,
        tempfile.TemporaryFile() as content,
    ):
        size, digest = copy_content(source, content)
        if size != manifest["size_bytes"] or digest != manifest["sha256"]:
            raise UploadError("Prepared content changed. Stop; do not regenerate this request.")
        content.seek(0)
        return transmit(manifest, content, key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser(
        "prepare", help="Freeze bytes and metadata locally; no HTTP calls"
    )
    staging.add_argument("--api-url", default=os.environ.get("MNEMONIC_API_URL"))
    staging.add_argument("--request-dir", type=Path, required=True, help="New private directory")
    staging.add_argument(
        "--source", type=Path, required=True, help="Local regular file; never printed"
    )
    staging.add_argument("--project-id", type=UUID, required=True)
    staging.add_argument("--client-operation-id", type=UUID, help="Default: generate once locally")
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
    staging.add_argument("--sensitive", action=argparse.BooleanOptionalAction, default=None)
    staging.add_argument("--artifact-id", type=UUID, help="Replace this existing artifact")
    staging.add_argument("--expected-revision", type=int, help="Revision just read for replacement")
    sending = commands.add_parser(
        "send", help="Send prepared bytes once; never retries automatically"
    )
    sending.add_argument("--request-dir", type=Path, required=True)
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
