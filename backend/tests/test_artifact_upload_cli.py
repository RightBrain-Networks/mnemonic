"""Exercise installed upload clients against real HTTP without exposing file bytes."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/upload_artifact.py"
PLUGIN_SCRIPT = REPO / "plugin/scripts/upload_artifact.py"
PROJECT = "c1b684d5-aa2a-450a-80e3-e1296f806ceb"
OPERATION = "5c94d2c6-3f06-4610-87a9-3d9a4894e96c"
ARTIFACT = str(uuid5(UUID(PROJECT), f"artifact:{OPERATION}"))
RELATED = "79ec5f12-d1eb-42bb-bd65-867be242aaee"
API_KEY = "local-fixture-upload-key"
CONTENT = b"private upload sentinel\x00\xff" * 18000


class UploadServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), UploadHandler)
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        self.status: int | None = None
        self.body: bytes | None = None
        self.changes: dict[str, Any] = {}
        self.headers: dict[str, str] = {}
        self.duplicate_header: str | None = None
        self.disconnect = False

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"


class UploadHandler(BaseHTTPRequestHandler):
    server: UploadServer

    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        headers = {key.lower(): value for key, value in self.headers.items()}
        content = self.rfile.read(int(headers["content-length"]))
        self.server.requests.append((self.command, self.path, headers, content))
        if self.server.disconnect:
            return
        metadata = json.loads(headers["x-artifact-metadata"])
        replacing = self.command == "PUT"
        receipt = {
            "id": ARTIFACT,
            "project_id": PROJECT,
            "filename": metadata["filename"],
            "revision": int(headers.get("x-artifact-expected-revision", "0")) + 1,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "created_by_agent_session_id": metadata["agent_session_id"],
            "created_by_client": metadata["actor_client"],
            "content_available": True,
            "deleted_at": None,
            "description": metadata.get("description", ""),
            "originating_work_item_id": metadata.get("work_item_id"),
            "related_work_item_ids": metadata.get("related_work_item_ids", []),
            "related_artifact_ids": metadata.get("related_artifact_ids", []),
            "sensitive": metadata.get("sensitive", False),
            **self.server.changes,
        }
        body = self.server.body if self.server.body is not None else json.dumps(receipt).encode()
        response_headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Client-Operation-ID": headers["x-client-operation-id"],
            "X-Artifact-Operation-Replayed": "false",
            "Location": f"{self.server.origin}/must-not-follow",
            **self.server.headers,
        }
        self.send_response(self.server.status or (200 if replacing else 201))
        for name, value in response_headers.items():
            self.send_header(name, value)
        if self.server.duplicate_header:
            self.send_header(
                self.server.duplicate_header, response_headers[self.server.duplicate_header]
            )
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError, ConnectionResetError:
            pass

    do_PUT = do_POST


@pytest.fixture
def server() -> Iterator[UploadServer]:
    with UploadServer() as instance:
        worker = threading.Thread(target=instance.serve_forever, daemon=True)
        worker.start()
        try:
            yield instance
        finally:
            instance.shutdown()
            worker.join()


def cli(
    *args: str, script: Path = SCRIPT, api_key: str = API_KEY
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "MNEMONIC_API_KEY": api_key,
        "HTTP_PROXY": "http://127.0.0.1:1",
        "HTTPS_PROXY": "http://127.0.0.1:1",
        "http_proxy": "http://127.0.0.1:1",
        "https_proxy": "http://127.0.0.1:1",
        "NO_PROXY": "",
        "no_proxy": "",
        "MNEMONIC_API_URL": "http://127.0.0.1:1",
    }
    result = subprocess.run(
        [sys.executable, str(script), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=15,
    )
    output = result.stdout + result.stderr
    assert API_KEY not in output
    assert "private upload sentinel" not in output
    assert base64.b64encode(CONTENT).decode() not in output
    assert "Traceback" not in output
    return result


def prepare(server: UploadServer, tmp_path: Path, *args: str, script: Path = SCRIPT) -> Path:
    source, request_dir = tmp_path / "résumé.bin", tmp_path / "private request"
    source.write_bytes(CONTENT)
    result = cli(
        "prepare",
        "--api-url",
        server.origin,
        "--project-id",
        PROJECT,
        "--client-operation-id",
        OPERATION,
        "--source",
        str(source),
        "--request-dir",
        str(request_dir),
        "--agent-session-id",
        "session-é",
        "--actor-client",
        "pytest",
        *args,
        script=script,
    )
    assert result.returncode == 0, result.stderr
    assert not server.requests
    assert len(result.stdout) < 800
    return request_dir


@pytest.mark.parametrize("client", ["checkout", "installed", "exported"])
def test_streams_original_bytes_from_standalone_clients(
    server: UploadServer,
    tmp_path: Path,
    client: str,
) -> None:
    script = SCRIPT
    if client == "installed":
        script = tmp_path / "plugin install/scripts/upload_artifact.py"
        script.parent.mkdir(parents=True)
        shutil.copy2(PLUGIN_SCRIPT, script)
    elif client == "exported":
        destination = tmp_path / "portable skills"
        subprocess.run(
            [sys.executable, str(REPO / "scripts/export_agent_skills.py"), str(destination)],
            check=True,
            capture_output=True,
        )
        script = destination / "mnemonic-save/scripts/upload_artifact.py"
    request_dir = prepare(
        server,
        tmp_path,
        "--description",
        "Résumé",
        "--work-item-id",
        RELATED,
        "--related-work-item-id",
        RELATED,
        "--related-artifact-id",
        RELATED,
        "--sensitive",
        script=script,
    )
    assert request_dir.stat().st_mode & 0o777 == 0o700
    for name in ("request.json", "content"):
        assert (request_dir / name).stat().st_mode & 0o777 == 0o600
    # Sending and replaying must never reread the caller's now-edited original file.
    (tmp_path / "résumé.bin").write_bytes(b"later version")
    result = cli("send", "--request-dir", str(request_dir), script=script)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt == {
        "project_id": PROJECT,
        "artifact_id": ARTIFACT,
        "client_operation_id": OPERATION,
        "revision": 1,
        "size_bytes": len(CONTENT),
        "sha256": hashlib.sha256(CONTENT).hexdigest(),
        "replayed": False,
    }
    method, path, headers, content = server.requests[0]
    assert (method, path) == ("POST", f"/api/v1/projects/{PROJECT}/artifacts")
    assert content == CONTENT
    assert headers["authorization"] == f"Bearer {API_KEY}"
    assert headers["content-type"] == "application/octet-stream"
    assert "transfer-encoding" not in headers
    assert headers["x-artifact-metadata"].isascii()
    assert json.loads(headers["x-artifact-metadata"])["agent_session_id"] == "session-é"
    assert len(result.stdout) < 600


def test_unknown_outcome_retries_exact_snapshot_and_receipt(server: UploadServer, tmp_path: Path):
    request_dir = prepare(server, tmp_path)
    server.disconnect = True
    first = cli("send", "--request-dir", str(request_dir))
    assert first.returncode == 1 and not first.stdout
    assert "at most once more" in first.stderr
    assert len(server.requests) == 1
    (tmp_path / "résumé.bin").unlink()
    server.disconnect = False
    server.headers["X-Artifact-Operation-Replayed"] = "true"
    replay = cli("send", "--request-dir", str(request_dir))
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout)["replayed"] is True
    assert len(server.requests) == 2
    assert server.requests[0] == server.requests[1]


def test_replacement_preserves_omission_and_pins_original_revision(
    server: UploadServer, tmp_path: Path
):
    request_dir = prepare(
        server,
        tmp_path,
        "--artifact-id",
        ARTIFACT,
        "--expected-revision",
        "7",
        "--filename",
        "original.bin",
    )
    result = cli("send", "--request-dir", str(request_dir))
    assert result.returncode == 0, result.stderr
    method, path, headers, _ = server.requests[0]
    assert (method, path) == ("PUT", f"/api/v1/projects/{PROJECT}/artifacts/{ARTIFACT}/content")
    assert headers["x-artifact-expected-revision"] == "7"
    assert json.loads(headers["x-artifact-metadata"]) == {
        "filename": "original.bin",
        "agent_session_id": "session-é",
        "actor_client": "pytest",
    }
    assert json.loads(result.stdout)["revision"] == 8


@pytest.mark.parametrize(
    "change",
    [
        {"id": RELATED},
        {"project_id": RELATED},
        {"sha256": "0" * 64},
        {"size_bytes": 0},
        {"revision": True},
        {"revision": 2},
        {"filename": "wrong.bin"},
        {"content_available": False},
        {"created_by_client": "wrong-client"},
        {"related_artifact_ids": []},
        {"sensitive": False},
    ],
)
def test_rejects_mismatched_receipts(server: UploadServer, tmp_path: Path, change: dict):
    request_dir = prepare(server, tmp_path, "--sensitive", "--related-artifact-id", RELATED)
    server.changes = change
    result = cli("send", "--request-dir", str(request_dir))
    assert result.returncode == 1 and not result.stdout
    assert "may have committed" in result.stderr
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "failure", ["operation", "replayed", "duplicate", "json", "large", "encoded"]
)
def test_rejects_unverifiable_responses(server: UploadServer, tmp_path: Path, failure: str):
    request_dir = prepare(server, tmp_path)
    if failure == "operation":
        server.headers["X-Client-Operation-ID"] = RELATED
    elif failure == "replayed":
        server.headers["X-Artifact-Operation-Replayed"] = "maybe"
    elif failure == "duplicate":
        server.duplicate_header = "X-Client-Operation-ID"
    elif failure == "encoded":
        server.headers["Content-Encoding"] = "gzip"
    else:
        server.body = b"{" if failure == "json" else b"x" * (64 * 1024 + 1)
    result = cli("send", "--request-dir", str(request_dir))
    assert result.returncode == 1 and not result.stdout
    assert "unchanged" in result.stderr
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (503, "artifact_storage_unavailable", "operator repair"),
        (409, "artifact_revision_conflict", "Read current metadata"),
        (409, "artifact_operation_conflict", "Stop this intent"),
        (413, "artifact_too_large", "size/availability policy"),
        (503, "artifact_library_disabled", "size/availability policy"),
        (307, "redirect", "redirects are refused"),
        (500, "unknown", "HTTP 500"),
    ],
)
def test_errors_are_bounded_redacted_and_never_retried(
    server: UploadServer,
    tmp_path: Path,
    status: int,
    code: str,
    message: str,
):
    request_dir = prepare(server, tmp_path)
    server.status = status
    server.body = json.dumps(
        {"detail": {"code": code, "message": API_KEY, "context": {"attempt_not_committed": True}}}
    ).encode()
    result = cli("send", "--request-dir", str(request_dir))
    assert result.returncode == 1 and not result.stdout
    assert message in result.stderr
    assert len(server.requests) == 1


@pytest.mark.parametrize("failure", ["changed", "symlink", "fifo", "no-key", "existing"])
def test_local_failures_send_nothing(server: UploadServer, tmp_path: Path, failure: str):
    request_dir = prepare(server, tmp_path)
    content = request_dir / "content"
    if failure in {"symlink", "fifo"}:
        content.unlink()
        if failure == "symlink":
            content.symlink_to(tmp_path / "résumé.bin")
        else:
            os.mkfifo(content)
    elif failure == "changed":
        content.write_bytes(b"tampered")
    if failure == "existing":
        result = cli(
            "prepare",
            "--request-dir",
            str(request_dir),
            "--source",
            str(content),
            "--project-id",
            PROJECT,
            "--api-url",
            server.origin,
            "--agent-session-id",
            "session",
            "--actor-client",
            "pytest",
        )
    else:
        result = cli(
            "send",
            "--request-dir",
            str(request_dir),
            api_key="" if failure == "no-key" else API_KEY,
        )
    assert result.returncode == 1 and not result.stdout
    assert not server.requests


@pytest.mark.parametrize("size", [0, 65 * 1024 * 1024])
def test_empty_and_above_mcp_limit_files_stay_out_of_output(
    server: UploadServer,
    tmp_path: Path,
    size: int,
) -> None:
    source, request_dir = tmp_path / "binary.bin", tmp_path / "request"
    with source.open("wb") as file:
        file.truncate(size)
    result = cli(
        "prepare",
        "--api-url",
        server.origin,
        "--project-id",
        PROJECT,
        "--client-operation-id",
        OPERATION,
        "--source",
        str(source),
        "--request-dir",
        str(request_dir),
        "--agent-session-id",
        "session",
        "--actor-client",
        "pytest",
    )
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) < 800
    source.unlink()
    result = cli("send", "--request-dir", str(request_dir))
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) < 600
    assert json.loads(result.stdout)["size_bytes"] == size
    assert len(server.requests[0][3]) == size


@pytest.mark.parametrize(
    "arguments",
    [
        ["--artifact-id", ARTIFACT],
        ["--expected-revision", "1"],
        ["--artifact-id", ARTIFACT, "--expected-revision", "0"],
        ["--api-url", "https://user:password@example.com"],
        ["--api-url", "https://example.com/api/v1"],
        ["--filename", "../escape.bin"],
        ["--actor-client", API_KEY],
        ["--description", "雪" * 4000],
    ],
)
def test_invalid_preparation_never_creates_a_request(
    server: UploadServer,
    tmp_path: Path,
    arguments: list[str],
) -> None:
    source, request_dir = tmp_path / "source.bin", tmp_path / "request"
    source.write_bytes(b"data")
    result = cli(
        "prepare",
        "--api-url",
        server.origin,
        "--project-id",
        PROJECT,
        "--source",
        str(source),
        "--request-dir",
        str(request_dir),
        "--agent-session-id",
        "session",
        "--actor-client",
        "pytest",
        *arguments,
    )
    assert result.returncode == 1 and not result.stdout
    assert not request_dir.exists()
    assert not server.requests
