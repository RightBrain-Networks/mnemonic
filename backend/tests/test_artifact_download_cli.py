"""Real local HTTP coverage for the standalone, credential-provisioned download client."""

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "download_artifact.py"
PROJECT_ID = "c1b684d5-aa2a-450a-80e3-e1296f806ceb"
ARTIFACT_ID = "79ec5f12-d1eb-42bb-bd65-867be242aaee"
API_KEY = "local-fixture-artifact-key"
BASE_PATH = f"/api/v1/projects/{PROJECT_ID}/artifacts/{ARTIFACT_ID}"


class DownloadServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), DownloadHandler)
        self.content = bytes(range(256)) * 700
        self.requests: list[tuple[str, dict[str, str]]] = []
        self.metadata_changes: dict[str, Any] = {}
        self.metadata_status = 200
        self.metadata_body: bytes | None = None
        self.content_status = 200
        self.content_headers: dict[str, str] = {}
        self.content_body: bytes | None = None
        self.create_destination: Path | None = None
        self.slow_phase: str | None = None
        self.stream_closed = threading.Event()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def metadata(self) -> dict[str, Any]:
        return {"id": ARTIFACT_ID, "project_id": PROJECT_ID, "revision": 3,
                "size_bytes": len(self.content), "sha256": hashlib.sha256(self.content).hexdigest(),
                "content_available": True, "deleted_at": None, **self.metadata_changes}


class DownloadHandler(BaseHTTPRequestHandler):
    server: DownloadServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        self.server.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
        if self.path == BASE_PATH:
            body = self.server.metadata_body
            if body is None:
                body = json.dumps(self.server.metadata()).encode()
            self.respond(self.server.metadata_status, body, {"Content-Type": "application/json"})
        elif self.path == f"{BASE_PATH}/content?expected_revision=3":
            self.send_content()
        else:
            self.respond(404, b"Unexpected test path", {})

    def send_content(self) -> None:
        if self.server.create_destination is not None:
            self.server.create_destination.write_bytes(b"created concurrently")
        headers = {"Content-Type": "application/octet-stream", "X-Artifact-Revision": "3",
                   "ETag": f'"{hashlib.sha256(self.server.content).hexdigest()}"',
                   "Content-Length": str(len(self.server.content)), **self.server.content_headers}
        content = self.server.content_body
        self.respond(self.server.content_status,
                     self.server.content if content is None else content, headers)

    def respond(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        if self.server.slow_phase == "headers" and self.path == BASE_PATH:
            self.slow_headers()
            return
        self.send_response(status)
        for name, value in {"Content-Length": str(len(body)), **headers}.items():
            self.send_header(name, value)
        self.send_header("Location", f"{self.server.origin}/must-not-follow")
        self.end_headers()
        try:
            if self.server.slow_phase == "content" and "/content?" in self.path:
                for byte in body[:40]:
                    self.wfile.write(bytes([byte]))
                    time.sleep(0.05)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.server.stream_closed.set()

    def slow_headers(self) -> None:
        try:
            self.wfile.write(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            for _ in range(40):
                self.wfile.write(b"x")
                time.sleep(0.05)
            self.wfile.write(b"\r\nContent-Length: 0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            self.server.stream_closed.set()


@pytest.fixture
def server() -> Iterator[DownloadServer]:
    with DownloadServer() as instance:
        worker = threading.Thread(target=instance.serve_forever, daemon=True)
        worker.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            worker.join()


def run_client(
    server: DownloadServer, dest: Path, *args: str, api_key: str = API_KEY, script: Path = SCRIPT,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MNEMONIC_API_URL": server.origin, "MNEMONIC_API_KEY": api_key,
           "HTTP_PROXY": "http://127.0.0.1:1", "HTTPS_PROXY": "http://127.0.0.1:1",
           "http_proxy": "http://127.0.0.1:1", "https_proxy": "http://127.0.0.1:1",
           "NO_PROXY": "", "no_proxy": ""}
    return subprocess.run(
        [sys.executable, str(script), "--project-id", PROJECT_ID, "--artifact-id", ARTIFACT_ID,
         "--dest", str(dest), "--agent-session-id", "fixture-session-é",
         "--actor-client", "pytest", *args],
        env=env, text=True, capture_output=True, check=False, timeout=15,
    )


def assert_failed(result: subprocess.CompletedProcess[str], dest: Path) -> None:
    assert result.returncode == 1
    assert not result.stdout
    assert "Download failed:" in result.stderr
    assert API_KEY not in result.stderr
    assert not dest.exists()
    assert not list(dest.parent.glob(".mnemonic-download-*"))


def test_streams_binary_with_pinned_revision_auth_and_truthful_provenance(
    server: DownloadServer, tmp_path: Path,
) -> None:
    dest = tmp_path / "download.bin"
    result = run_client(server, dest)
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert dest.read_bytes() == server.content
    assert dest.stat().st_mode & 0o777 == 0o600
    assert json.loads(result.stdout) == {
        "path": str(dest), "revision": 3, "size_bytes": len(server.content),
        "sha256": hashlib.sha256(server.content).hexdigest(),
    }
    assert [path for path, _ in server.requests] == [
        BASE_PATH, f"{BASE_PATH}/content?expected_revision=3",
    ]
    for _, headers in server.requests:
        assert headers["authorization"] == f"Bearer {API_KEY}"
        assert headers["accept-encoding"] == "identity"
    provenance = server.requests[1][1]["x-artifact-metadata"]
    assert provenance.isascii()
    assert json.loads(provenance) == {
        "agent_session_id": "fixture-session-é", "actor_client": "pytest",
    }
    assert not list(tmp_path.glob(".mnemonic-download-*"))


@pytest.mark.parametrize("symlink", [False, True])
def test_existing_destination_including_dangling_symlink_is_never_overwritten(
    server: DownloadServer, tmp_path: Path, symlink: bool,
) -> None:
    dest = tmp_path / "existing.bin"
    if symlink:
        dest.symlink_to(tmp_path / "missing-target")
    else:
        dest.write_bytes(b"original")
    result = run_client(server, dest)
    assert result.returncode == 1
    assert "Destination already exists" in result.stderr
    assert not server.requests
    if symlink:
        assert dest.is_symlink()
    else:
        assert dest.read_bytes() == b"original"


def test_concurrent_destination_creation_is_not_clobbered(
    server: DownloadServer, tmp_path: Path,
) -> None:
    dest = tmp_path / "concurrent.bin"
    server.create_destination = dest
    result = run_client(server, dest)
    assert result.returncode == 1
    assert "Destination already exists" in result.stderr
    assert dest.read_bytes() == b"created concurrently"
    assert not list(tmp_path.glob(".mnemonic-download-*"))


@pytest.mark.parametrize("header,value", [
    ("Content-Encoding", "gzip"), ("Content-Length", "1073741825"),
    ("Content-Length", "-1"), ("Content-Length", "10"),
    ("X-Artifact-Revision", "4"), ("ETag", '"wrong-digest"'),
    ("Transfer-Encoding", "chunked"), ("Content-Type", "text/html"),
])
def test_rejects_content_header_mismatch_without_publishing(
    server: DownloadServer, tmp_path: Path, header: str, value: str,
) -> None:
    server.content_headers[header] = value
    dest = tmp_path / "rejected.bin"
    assert_failed(run_client(server, dest), dest)


@pytest.mark.parametrize("content", [b"truncated", bytes(256 * 700)], ids=["short", "corrupt"])
def test_short_or_corrupt_bytes_leave_no_partial_file(
    server: DownloadServer, tmp_path: Path, content: bytes,
) -> None:
    server.content_body = content
    dest = tmp_path / "rejected.bin"
    assert_failed(run_client(server, dest), dest)


@pytest.mark.parametrize("stage", ["metadata", "content"])
def test_redirects_are_refused_without_forwarding_credentials(
    server: DownloadServer, tmp_path: Path, stage: str,
) -> None:
    setattr(server, f"{stage}_status", 302)
    dest = tmp_path / "redirect.bin"
    result = run_client(server, dest)
    assert_failed(result, dest)
    assert "redirects are refused" in result.stderr
    assert all("must-not-follow" not in path for path, _ in server.requests)


def test_revision_replacement_between_metadata_and_content_fails_cleanly(
    server: DownloadServer, tmp_path: Path,
) -> None:
    server.content_status = 409
    dest = tmp_path / "race.bin"
    result = run_client(server, dest)
    assert_failed(result, dest)
    assert "revision changed" in result.stderr


def test_expected_revision_prevents_downloading_a_newer_revision(
    server: DownloadServer, tmp_path: Path,
) -> None:
    dest = tmp_path / "revision.bin"
    assert_failed(run_client(server, dest, "--expected-revision", "2"), dest)
    assert len(server.requests) == 1


@pytest.mark.parametrize("changes", [
    {"revision": True}, {"size_bytes": 1073741825}, {"sha256": "invalid"},
    {"id": PROJECT_ID}, {"content_available": False}, {"deleted_at": "2026-09-09"},
])
def test_invalid_or_unavailable_metadata_prevents_content_request(
    server: DownloadServer, tmp_path: Path, changes: dict[str, Any],
) -> None:
    server.metadata_changes.update(changes)
    dest = tmp_path / "metadata.bin"
    assert_failed(run_client(server, dest), dest)
    assert len(server.requests) == 1


@pytest.mark.parametrize("body", [b"x" * (65536 + 1), b"not json"], ids=["large", "non-json"])
def test_metadata_body_is_bounded_and_must_be_json(
    server: DownloadServer, tmp_path: Path, body: bytes,
) -> None:
    server.metadata_body = body
    dest = tmp_path / "metadata.bin"
    assert_failed(run_client(server, dest), dest)


@pytest.mark.parametrize("status", [401, 503])
def test_authentication_or_disabled_library_failure_does_not_print_server_body(
    server: DownloadServer, tmp_path: Path, status: int,
) -> None:
    server.metadata_status = status
    server.metadata_body = f"{API_KEY} private diagnostic".encode()
    dest = tmp_path / "refused.bin"
    result = run_client(server, dest)
    assert_failed(result, dest)
    assert "private diagnostic" not in result.stderr
    assert f"HTTP {status}" in result.stderr


def test_credential_must_be_provisioned_in_environment(
    server: DownloadServer, tmp_path: Path,
) -> None:
    dest = tmp_path / "missing-key.bin"
    result = run_client(server, dest, api_key="")
    assert_failed(result, dest)
    assert "Provision MNEMONIC_API_KEY" in result.stderr
    assert not server.requests


def test_empty_artifact_is_verified_and_written(server: DownloadServer, tmp_path: Path) -> None:
    server.content = b""
    dest = tmp_path / "empty.bin"
    result = run_client(server, dest, "--expected-revision", "3")
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == b""


@pytest.mark.parametrize("origin", [
    "https://user:password@example.com", "https://example.com/api/v1",
    "https://example.com?key=secret", "file:///tmp/artifact", "http://example.com:bad",
])
def test_api_url_rejects_credentials_and_non_origin_addresses(
    server: DownloadServer, tmp_path: Path, origin: str,
) -> None:
    dest = tmp_path / "invalid-origin.bin"
    result = run_client(server, dest, "--api-url", origin)
    assert_failed(result, dest)
    assert origin not in result.stderr
    assert not server.requests


def test_provenance_cannot_echo_api_credential(server: DownloadServer, tmp_path: Path) -> None:
    dest = tmp_path / "invalid-provenance.bin"
    result = run_client(server, dest, "--actor-client", API_KEY)
    assert_failed(result, dest)
    assert "provenance must not contain" in result.stderr
    assert not server.requests


def deadline_client(tmp_path: Path, *, setup: str = "") -> Path:
    wrapper = tmp_path / "deadline_client.py"
    wrapper.write_text(
        "import runpy\n"
        f"main = runpy.run_path({str(SCRIPT)!r})['main']\n"
        "main.__globals__['REQUEST_SECONDS'] = 0.25\n"
        "main.__globals__['SOCKET_SECONDS'] = 1.0\n"
        + setup + "raise SystemExit(main())\n"
    )
    return wrapper


@pytest.mark.parametrize("phase", ["headers", "content"])
def test_wall_clock_deadline_interrupts_trickle_and_cleans_staging(
    server: DownloadServer, tmp_path: Path, phase: str,
) -> None:
    server.slow_phase = phase
    dest = tmp_path / "timeout.bin"
    started = time.monotonic()
    result = run_client(server, dest, script=deadline_client(tmp_path))
    elapsed = time.monotonic() - started
    assert_failed(result, dest)
    assert "exceeded its time limit" in result.stderr
    assert elapsed < 1.5, f"Request deadline did not interrupt the slow {phase}: {elapsed:.2f}s"
    assert server.stream_closed.wait(timeout=1), "The timed-out client left its socket open"
    assert not dest.exists()
    assert not list(tmp_path.glob(".mnemonic-download-*"))


@pytest.mark.parametrize("setup,diagnostic", [
    ("del main.__globals__['signal'].setitimer\n", "requires POSIX interval timers"),
    ("main.__globals__['signal'].setitimer(0, 60)\n", "without an inherited interval timer"),
], ids=["unsupported-platform", "inherited-timer"])
def test_unsupported_deadline_configuration_fails_before_authenticated_request(
    server: DownloadServer, tmp_path: Path, setup: str, diagnostic: str,
) -> None:
    dest = tmp_path / "unsupported.bin"
    result = run_client(server, dest, script=deadline_client(tmp_path, setup=setup))
    assert_failed(result, dest)
    assert diagnostic in result.stderr
    assert not server.requests


def test_request_timer_is_cancelled_before_file_publication(
    server: DownloadServer, tmp_path: Path,
) -> None:
    setup = (
        "import time\n"
        "original_link = main.__globals__['os'].link\n"
        "def slow_link(*args, **kwargs):\n"
        "    time.sleep(0.4)\n"
        "    return original_link(*args, **kwargs)\n"
        "main.__globals__['os'].link = slow_link\n"
    )
    dest = tmp_path / "published.bin"
    result = run_client(server, dest, script=deadline_client(tmp_path, setup=setup))
    assert result.returncode == 0, result.stderr
    assert dest.read_bytes() == server.content
    assert not list(tmp_path.glob(".mnemonic-download-*"))
