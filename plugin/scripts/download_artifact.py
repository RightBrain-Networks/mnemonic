#!/usr/bin/env python3
"""Stream a revision-verified artifact to the caller's filesystem using only stdlib."""

import argparse
import hashlib
import json
import os
import re
import signal
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from email.message import Message
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from threading import current_thread, main_thread
from types import FrameType
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    ProxyHandler,
    Request,
    build_opener,
)
from uuid import UUID

MAX_METADATA_BYTES = 64 * 1024
MAX_CONTENT_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
REQUEST_SECONDS = 120
SOCKET_SECONDS = 30
HUMAN_APPROVAL_REQUIRED = (
    "HUMAN APPROVAL REQUIRED. STOP and ask the actual human user to explicitly approve this "
    "exact sensitive artifact download. Do not infer consent from the task, a token, prior "
    "approval, or an automated classifier. Never clear sensitivity or switch routes to bypass "
    "this requirement. Only after the human answers yes, repeat the same download with "
    "--approval-token TOKEN --human-approved. Tokens expire in five minutes and are consumed "
    "once; every subsequent access or retry requires a new human approval."
)


class DownloadError(Exception):
    """A safe diagnostic that contains no credentials, server body or artifact content."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: HTTPResponse, code: int, msg: str, headers: Message, newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class Artifact:
    revision: int
    size_bytes: int
    sha256: str


def api_origin(value: str | None) -> str:
    if not value or any(ord(char) <= 32 for char in value):
        raise DownloadError("Provide --api-url or MNEMONIC_API_URL with the API origin.")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and (
            parsed.username is None and parsed.password is None and parsed.port != 0
        )
    except ValueError:
        valid = False
    if not valid or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise DownloadError("The API URL must be an HTTP(S) origin without credentials or a path.")
    return value.rstrip("/")


def actor_metadata(session_id: str, actor_client: str, api_key: str) -> str:
    values = {"agent_session_id": session_id, "actor_client": actor_client}
    for field, limit in (("agent_session_id", 200), ("actor_client", 80)):
        value = values[field]
        if not value.strip() or len(value) > limit or any(ord(char) < 32 for char in value):
            raise DownloadError(f"{field} must be nonblank, control-free, at most {limit} chars.")
        if value == api_key:
            raise DownloadError("Caller provenance must not contain the API credential.")
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def api_credential() -> str:
    key = os.environ.get("MNEMONIC_API_KEY", "")
    if not key or any(ord(char) <= 32 or ord(char) > 126 for char in key):
        raise DownloadError("Provision MNEMONIC_API_KEY in the client process environment.")
    return key


def header(response: HTTPResponse, name: str) -> str | None:
    values = response.headers.get_all(name, [])
    if len(values) > 1:
        raise DownloadError("The API returned ambiguous response headers.")
    return values[0] if values else None


def content_length(response: HTTPResponse, maximum: int) -> int | None:
    if header(response, "Content-Encoding") not in {None, "identity"}:
        raise DownloadError("The API returned encoded content instead of original bytes.")
    value = header(response, "Content-Length")
    if value is None:
        return None
    if not re.fullmatch(r"[0-9]{1,12}", value) or int(value) > maximum:
        raise DownloadError("The API response exceeds the download limit or has invalid length.")
    if header(response, "Transfer-Encoding") is not None:
        raise DownloadError("The API returned ambiguous response framing.")
    return int(value)


def request(opener: OpenerDirector, url: str, headers: dict[str, str]) -> HTTPResponse:
    try:
        response = opener.open(Request(url, headers=headers), timeout=SOCKET_SECONDS)
    except HTTPError as error:
        status = error.code
        if status == 428:
            try:
                message = approval_challenge_message(error)
            finally:
                error.close()
            raise DownloadError(message) from None
        error.close()
        if 300 <= status < 400:
            raise DownloadError("The API redirected the request; redirects are refused.") from None
        if status == 409:
            raise DownloadError("Artifact revision changed; read its metadata and retry.") from None
        raise DownloadError(f"The API refused the download (HTTP {status}).") from None
    if response.status != 200:
        response.close()
        raise DownloadError("The API returned an unexpected response status.")
    return response


def approval_challenge_message(error: HTTPError) -> str:
    try:
        raw = error.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            raise ValueError("Oversized challenge")
        detail = json.loads(raw)["detail"]
        if detail["code"] != "artifact_human_approval_required":
            raise ValueError("Unexpected challenge")
        context = detail["context"]
        safe = validate_approval_challenge(context)
    except (ValueError, KeyError, TypeError, RecursionError):
        return HUMAN_APPROVAL_REQUIRED + " No valid approval challenge was returned."
    return HUMAN_APPROVAL_REQUIRED + " Challenge: " + json.dumps(safe, ensure_ascii=True)


def validate_approval_challenge(context: dict[str, object]) -> dict[str, object]:
    token = context["approval_token"]
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Invalid approval token")
    expiry = context["expires_at"]
    if not isinstance(expiry, str) or len(expiry) > 64 or (
        datetime.fromisoformat(expiry).tzinfo is None
    ):
        raise ValueError("Invalid approval expiry")
    revision = context["revision"]
    if type(revision) is not int or revision < 1 or context["action"] != "download" or (
        context["human_approval_required"] is not True
    ):
        raise ValueError("Invalid approval scope")
    artifact_id = str(UUID(str(context["artifact_id"])))
    return {"approval_token": token, "expires_at": expiry, "artifact_id": artifact_id,
            "action": "download", "revision": revision, "human_approval_required": True}


def approval_provenance(args: argparse.Namespace, provenance: str) -> str:
    token = args.approval_token
    if args.human_approved and token is None:
        raise DownloadError("--human-approved requires the token from the exact access challenge.")
    if token is None:
        return provenance
    if not args.human_approved:
        raise DownloadError(HUMAN_APPROVAL_REQUIRED)
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise DownloadError("Invalid approval token; request a new challenge and human approval.")
    values = json.loads(provenance)
    values.update(approval_token=token, human_approved=True)
    return json.dumps(values, ensure_ascii=True, separators=(",", ":"))


def require_deadline_support() -> None:
    if os.name != "posix" or not hasattr(signal, "setitimer"):
        raise DownloadError("This client requires POSIX interval timers (Linux or macOS).")
    if current_thread() is not main_thread():
        raise DownloadError("Run the download client in the main thread.")
    if any(signal.getitimer(signal.ITIMER_REAL)):
        raise DownloadError("Run the download client without an inherited interval timer.")


def deadline_expired(signum: int, frame: FrameType | None) -> None:
    raise DownloadError("The artifact request exceeded its time limit.")


@contextmanager
def request_deadline() -> Iterator[None]:
    # The signal interrupts connection/header/framing reads as well as payload reads.
    # Keep it inside staging's lifetime and cancel before publishing a verified file.
    require_deadline_support()
    previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, REQUEST_SECONDS)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def chunks(response: HTTPResponse, maximum: int) -> Iterator[bytes]:
    count = 0
    while True:
        chunk = response.read1(min(CHUNK_BYTES, maximum - count + 1))
        if not chunk:
            break
        count += len(chunk)
        if count > maximum:
            raise DownloadError("The API response exceeds the download limit.")
        yield chunk


def artifact_metadata(raw: bytes, args: argparse.Namespace) -> Artifact:
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        raise DownloadError("The API returned invalid artifact metadata.") from None
    if not isinstance(value, dict) or value.get("id") != str(args.artifact_id) or (
        value.get("project_id") != str(args.project_id)
    ):
        raise DownloadError("The API returned metadata for another artifact.")
    revision, size, digest = value.get("revision"), value.get("size_bytes"), value.get("sha256")
    if type(revision) is not int or revision < 1 or type(size) is not int or (
        not 0 <= size <= MAX_CONTENT_BYTES
    ) or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise DownloadError("The API returned invalid artifact revision, size or SHA256 metadata.")
    if value.get("content_available") is not True or value.get("deleted_at") is not None:
        raise DownloadError("Artifact bytes are unavailable; only retained metadata can be read.")
    if args.expected_revision is not None and revision != args.expected_revision:
        raise DownloadError("Artifact revision changed; read its metadata and retry.")
    return Artifact(revision, size, digest)


def fetch_metadata(
    opener: OpenerDirector, url: str, headers: dict[str, str], args: argparse.Namespace,
) -> Artifact:
    with request_deadline(), request(opener, url, headers) as response:
        declared = content_length(response, MAX_METADATA_BYTES)
        json_types = {"application/json", "application/json; charset=utf-8"}
        if header(response, "Content-Type") not in json_types:
            raise DownloadError("The API returned non-JSON artifact metadata.")
        raw = b"".join(chunks(response, MAX_METADATA_BYTES))
        if declared is not None and declared != len(raw):
            raise DownloadError("The API returned incomplete artifact metadata.")
    return artifact_metadata(raw, args)


def verify_headers(response: HTTPResponse, artifact: Artifact) -> None:
    if content_length(response, MAX_CONTENT_BYTES) != artifact.size_bytes or (
        header(response, "X-Artifact-Revision") != str(artifact.revision)
        or header(response, "ETag") != f'"{artifact.sha256}"'
        or header(response, "Content-Type") != "application/octet-stream"
    ):
        raise DownloadError("Artifact response headers did not match the requested revision.")


def save_content(
    opener: OpenerDirector, url: str, headers: dict[str, str], artifact: Artifact, dest: Path,
) -> None:
    content_url = f"{url}/content?expected_revision={artifact.revision}"
    with tempfile.NamedTemporaryFile(prefix=".mnemonic-download-", dir=dest.parent) as staging:
        with request_deadline(), request(opener, content_url, headers) as response:
            verify_headers(response, artifact)
            digest, size = hashlib.sha256(), 0
            for chunk in chunks(response, artifact.size_bytes):
                staging.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            if size != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
                raise DownloadError("Artifact bytes did not match the requested revision.")
        # The request timer is cancelled before publication; it cannot interrupt a
        # successful link and turn a completed download into a reported timeout.
        staging.flush()
        os.fsync(staging.fileno())
        try:
            os.link(staging.name, dest, follow_symlinks=False)
        except FileExistsError:
            raise DownloadError(
                "Destination already exists; choose a new --dest path."
            ) from None


def download(args: argparse.Namespace) -> dict[str, str | int]:
    require_deadline_support()
    origin = api_origin(args.api_url)
    key = api_credential()
    provenance = actor_metadata(args.agent_session_id, args.actor_client, key)
    provenance = approval_provenance(args, provenance)
    dest = Path(os.path.abspath(args.dest))
    if os.path.lexists(dest):
        raise DownloadError("Destination already exists; choose a new --dest path.")
    if not dest.parent.is_dir():
        raise DownloadError("The destination directory must already exist.")
    if args.expected_revision is not None and args.expected_revision < 1:
        raise DownloadError("Expected revision must be a positive integer.")
    # Ignore proxy environment: the credential is sent only to the explicit API origin.
    opener = build_opener(ProxyHandler({}), NoRedirects())
    url = f"{origin}/api/v1/projects/{args.project_id}/artifacts/{args.artifact_id}"
    headers = {"Authorization": f"Bearer {key}", "Accept-Encoding": "identity"}
    artifact = fetch_metadata(opener, url, headers, args)
    headers["X-Artifact-Metadata"] = provenance
    save_content(opener, url, headers, artifact, dest)
    return {"path": str(dest), "revision": artifact.revision,
            "sha256": artifact.sha256, "size_bytes": artifact.size_bytes}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("MNEMONIC_API_URL"),
                        help="Client-reachable API origin; defaults to MNEMONIC_API_URL")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--artifact-id", type=UUID, required=True)
    parser.add_argument("--dest", type=Path, required=True, help="New file; never overwritten")
    parser.add_argument("--agent-session-id", required=True, help="Current caller's session ID")
    parser.add_argument("--actor-client", required=True, help="Current caller's client name")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--approval-token", help="One-use token from this exact download challenge")
    parser.add_argument("--human-approved", action="store_true",
                        help="Assert the actual human explicitly approved this exact access")
    args = parser.parse_args()
    try:
        result = download(args)
    except DownloadError as error:
        print(f"Download failed: {error}", file=sys.stderr)
        if args.approval_token and "HUMAN APPROVAL REQUIRED" not in str(error):
            print("The token may already be consumed. " + HUMAN_APPROVAL_REQUIRED, file=sys.stderr)
        return 1
    except (OSError, URLError, HTTPException, ValueError):
        print("Download failed: connection or filesystem error; "
              "check the destination before retrying.", file=sys.stderr)
        if args.approval_token:
            print("The token may already be consumed. " + HUMAN_APPROVAL_REQUIRED, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
