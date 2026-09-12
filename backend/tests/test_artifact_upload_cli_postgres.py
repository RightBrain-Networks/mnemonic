"""Round-trip the standalone binary client through the real API and receipt journal."""

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mnemonic_api.artifact_storage import ArtifactStorage

from .conftest import TEST_API_KEY
from .test_artifact_download_cli import SCRIPT as DOWNLOAD_SCRIPT
from .test_artifact_upload_cli import cli

pytestmark = pytest.mark.postgres


class APIBridge(ThreadingHTTPServer):
    def __init__(self, api: TestClient) -> None:
        super().__init__(("127.0.0.1", 0), APIHandler)
        self.api = api


class APIHandler(BaseHTTPRequestHandler):
    server: APIBridge

    def log_message(self, format: str, *args: object) -> None:
        pass

    def relay(self, content: bytes | None = None) -> None:
        response = self.server.api.request(
            self.command,
            self.path,
            content=content,
            headers=dict(self.headers),
        )
        self.send_response(response.status_code)
        for key, value in response.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(response.content)

    def do_POST(self) -> None:
        self.relay(self.rfile.read(int(self.headers["Content-Length"])))

    def do_GET(self) -> None:
        self.relay()

    do_PUT = do_POST


def read_result(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    assert TEST_API_KEY not in result.stdout + result.stderr
    return json.loads(result.stdout)


def test_upload_replay_replace_and_scratchpad_download_with_real_receipts(
    api, project, tmp_path: Path
) -> None:
    api.app.state.artifact_storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024)
    api.app.state.settings.artifact_max_bytes = 1024
    with APIBridge(api) as bridge:
        worker = threading.Thread(target=bridge.serve_forever, daemon=True)
        worker.start()
        try:
            exercise_client(api, project, tmp_path, bridge.server_port)
        finally:
            bridge.shutdown()
            worker.join()


def exercise_client(api, project, tmp_path: Path, port: int) -> None:
    source = tmp_path / "report.bin"
    source.write_bytes(b"first\x00\xff")
    origin = f"http://127.0.0.1:{port}"
    common = [
        "--api-url",
        origin,
        "--project-id",
        project["id"],
        "--source",
        str(source),
        "--agent-session-id",
        "real-http-session",
        "--actor-client",
        "pytest",
    ]
    prepared = tmp_path / "upload"
    first = read_result(
        cli(
            "prepare",
            "--request-dir",
            str(prepared),
            *common,
            "--description",
            "Résumé",
            api_key=TEST_API_KEY,
        )
    )
    assert first["status"] == "prepared"
    created = read_result(cli("send", "--request-dir", str(prepared), api_key=TEST_API_KEY))
    assert created["artifact_id"] == first["artifact_id"]
    assert created["revision"] == 1 and not created["replayed"]
    # Historical receipt replay must work even if today's upload maximum is lower.
    api.app.state.settings.artifact_max_bytes = 1
    replay = read_result(cli("send", "--request-dir", str(prepared), api_key=TEST_API_KEY))
    assert replay == {**created, "replayed": True}
    api.app.state.settings.artifact_max_bytes = 1024
    source.write_bytes(b"second\x00\xfe")
    replacement = tmp_path / "replace"
    read_result(
        cli(
            "prepare",
            "--request-dir",
            str(replacement),
            *common,
            "--artifact-id",
            created["artifact_id"],
            "--expected-revision",
            "1",
            api_key=TEST_API_KEY,
        )
    )
    replaced = read_result(cli("send", "--request-dir", str(replacement), api_key=TEST_API_KEY))
    assert replaced["revision"] == 2 and not replaced["replayed"]
    path = f"/api/v1/projects/{project['id']}/artifacts/{created['artifact_id']}"
    current = api.get(path).json()
    assert current["description"] == "Résumé"
    assert current["sha256"] == replaced["sha256"]
    scratchpad = tmp_path / "agent scratchpad"
    scratchpad.mkdir(mode=0o700)
    destination = scratchpad / "download.bin"
    downloaded = read_result(
        cli(
            "--api-url",
            origin,
            "--project-id",
            project["id"],
            "--artifact-id",
            created["artifact_id"],
            "--expected-revision",
            "2",
            "--agent-session-id",
            "downloading-session",
            "--actor-client",
            "pytest",
            "--dest",
            str(destination),
            script=DOWNLOAD_SCRIPT,
            api_key=TEST_API_KEY,
        )
    )
    assert downloaded["path"] == str(destination)
    assert downloaded["sha256"] == replaced["sha256"]
    assert destination.read_bytes() == b"second\x00\xfe"
    history = api.get(path + "/history").json()
    assert history["revisions"]["total"] == 2
    assert [item["action"] for item in history["audit"]["items"]].count("uploaded") == 1
