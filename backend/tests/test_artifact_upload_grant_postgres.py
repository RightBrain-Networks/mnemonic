"""Exercise grant-only client uploads through HTTP MCP, the real API and PostgreSQL."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest

from mnemonic_api.artifact_storage import ArtifactStorage

from .conftest import TEST_API_KEY
from .test_artifact_upload_cli import REPO, SCRIPT
from .test_artifact_upload_cli_postgres import APIBridge, read_result

pytestmark = pytest.mark.postgres


def grant_cli(*args: str) -> dict:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name not in {"MNEMONIC_API_URL", "MNEMONIC_API_KEY"}
    }
    return read_result(
        subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    )


@contextmanager
def running_mcp(api_port: int, tmp_path: Path):
    python = REPO / "mcp/.venv/bin/python"
    assert python.is_file(), "Install the separate MCP environment: uv sync --project mcp --frozen"
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("MNEMONIC_")
    }
    with socket.socket() as listener, (tmp_path / "mcp.log").open("w+") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        environment.update(
            {
                "MNEMONIC_API_KEY": TEST_API_KEY,
                "MNEMONIC_API_URL": f"http://127.0.0.1:{api_port}",
                "MNEMONIC_MCP_HOST": "127.0.0.1",
                "MNEMONIC_MCP_PORT": str(port),
            }
        )
        # Keep the environments separate and reserve the port throughout startup.
        command = (
            "import sys, uvicorn; from mnemonic_mcp.server import create_app; "
            "uvicorn.run(create_app(), fd=int(sys.argv[1]), proxy_headers=False, "
            "access_log=False, h11_max_incomplete_event_size=64*1024)"
        )
        process = subprocess.Popen(
            [str(python), "-c", command, str(listener.fileno())],
            env=environment,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=log,
        )
        endpoint = f"http://127.0.0.1:{port}/mcp"
        try:
            wait_for_mcp(process, endpoint)
            yield endpoint
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def wait_for_mcp(process, endpoint: str) -> None:
    request = urllib.request.Request(
        endpoint.removesuffix("/mcp") + "/healthz",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    )
    for _ in range(100):
        assert process.poll() is None, "MCP subprocess exited before becoming ready"
        try:
            with urllib.request.urlopen(request, timeout=0.2) as response:
                assert response.status == 200
                return
        except urllib.error.URLError, TimeoutError:
            time.sleep(0.1)
    pytest.fail("MCP subprocess did not become ready")


def save_grant(endpoint: str, prepared: dict, destination: Path) -> Path:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "authorize_artifact_upload",
            "arguments": {"intent": prepared["upload_intent"]},
        },
    }
    request = urllib.request.Request(
        endpoint,
        json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TEST_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        result = json.loads(response.read())["result"]
    assert not result.get("isError"), result
    grant = result["structuredContent"]
    assert grant["intent"] == prepared["upload_intent"]
    assert grant["upload_url"] == endpoint
    assert TEST_API_KEY not in json.dumps(grant)
    destination.write_text(json.dumps(grant))
    destination.chmod(0o600)
    return destination


def test_granted_upload_replay_and_replacement_with_real_journal(api, project, tmp_path):
    api.app.state.artifact_storage = ArtifactStorage(tmp_path / "artifacts", max_bytes=1024 * 1024)
    api.app.state.settings.artifact_max_bytes = 1024 * 1024
    with APIBridge(api) as bridge:
        worker = threading.Thread(target=bridge.serve_forever, daemon=True)
        worker.start()
        try:
            with running_mcp(bridge.server_port, tmp_path) as endpoint:
                exercise_grants(api, project, tmp_path, endpoint)
        finally:
            bridge.shutdown()
            worker.join()


def exercise_grants(api, project, tmp_path, endpoint):
    source = tmp_path / "report.bin"
    source.write_bytes(b"first\x00\xff" * 12000)
    common = [
        "--project-id",
        project["id"],
        "--source",
        str(source),
        "--agent-session-id",
        "grant-session",
        "--actor-client",
        "pytest",
    ]
    prepared_dir = tmp_path / "prepared"
    prepared = grant_cli(
        "prepare", "--request-dir", str(prepared_dir), *common, "--description", "Résumé"
    )
    grant_file = save_grant(endpoint, prepared, tmp_path / "grant.json")
    source.write_bytes(b"replacement\x00\xfe")
    uploaded = grant_cli(
        "send", "--request-dir", str(prepared_dir), "--grant-file", str(grant_file)
    )
    assert uploaded["revision"] == 1 and not uploaded["replayed"]
    assert uploaded["sha256"] == prepared["sha256"]
    # Reauthorizing a frozen intent after the positive limit falls still reaches its old receipt.
    api.app.state.settings.artifact_max_bytes = 1
    fresh_grant = save_grant(endpoint, prepared, tmp_path / "refreshed-grant.json")
    replay = grant_cli("send", "--request-dir", str(prepared_dir), "--grant-file", str(fresh_grant))
    assert replay == {**uploaded, "replayed": True}
    api.app.state.settings.artifact_max_bytes = 1024 * 1024
    replacement_dir = tmp_path / "replacement"
    replacement = grant_cli(
        "prepare",
        "--request-dir",
        str(replacement_dir),
        *common,
        "--artifact-id",
        uploaded["artifact_id"],
        "--expected-revision",
        "1",
    )
    assert "description" not in replacement["upload_intent"]["metadata"]
    replacement_grant = save_grant(endpoint, replacement, tmp_path / "replacement-grant.json")
    replaced = grant_cli(
        "send", "--request-dir", str(replacement_dir), "--grant-file", str(replacement_grant)
    )
    assert replaced["revision"] == 2 and not replaced["replayed"]
    path = f"/api/v1/projects/{project['id']}/artifacts/{uploaded['artifact_id']}"
    current = api.get(path).json()
    assert current["description"] == "Résumé"
    assert current["sha256"] == replaced["sha256"]
    history = api.get(path + "/history").json()
    assert history["revisions"]["total"] == 2
    actions = [item["action"] for item in history["audit"]["items"]]
    assert actions.count("uploaded") == 1 and actions.count("replaced") == 1
