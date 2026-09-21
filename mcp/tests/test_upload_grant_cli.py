"""Exercise the installed helper through actual HTTP MCP authorization and byte ingress."""

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from conftest import API_KEY, PROJECT_ID
from starlette.testclient import TestClient
from test_upload_grants import receipt

from mnemonic_mcp.api import MnemonicAPI
from mnemonic_mcp.config import Settings
from mnemonic_mcp.server import create_app
from mnemonic_mcp.upload_grants import UploadIntent

REPO = Path(__file__).resolve().parents[2]


class MCPBridge(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), MCPHandler)
        self.client = None
        self.drop_next_upload = False

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server_port}/mcp"


class MCPHandler(BaseHTTPRequestHandler):
    server: MCPBridge

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        response = self.server.client.post(self.path, content=raw, headers=dict(self.headers))
        if self.server.drop_next_upload and self.headers["Authorization"].startswith(
            "MnemonicUpload "
        ):
            self.server.drop_next_upload = False
            self.close_connection = True
            return
        self.send_response(response.status_code)
        for name, value in response.headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.content)


def cli(script, *args):
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"MNEMONIC_API_KEY", "MNEMONIC_API_URL"}
    }
    result = subprocess.run(
        [sys.executable, str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert API_KEY not in result.stdout + result.stderr
    assert "private-upload-content" not in result.stdout + result.stderr
    return result


def result_json(result):
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def authorize(bridge, prepared):
    import urllib.request

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "authorize_artifact_upload",
            "arguments": {
                "intent": prepared["upload_intent"],
            },
        },
    }
    request = urllib.request.Request(
        bridge.endpoint,
        json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        document = json.loads(response.read())
    assert not document["result"].get("isError"), document
    return document["result"]["structuredContent"]


@pytest.mark.parametrize(
    "installed,size,slow",
    [(False, 37, False), (True, 64 * 1024 * 1024 + 1, False), (True, 37, True)],
)
def test_no_client_credentials_upload_and_lost_response_replay(tmp_path, installed, size, slow):
    script = REPO / "scripts/upload_artifact.py"
    if installed:
        script = tmp_path / "installed-helper.py"
        shutil.copyfile(REPO / "plugin/scripts/upload_artifact.py", script)
    if slow:
        launcher = tmp_path / "slow-network-client.py"
        launcher.write_text(
            "import runpy, sys\n"
            f"module = runpy.run_path({str(script)!r})\n"
            "main = module['main']\n"
            "main.__globals__['SOCKET_SECONDS'] = 0.05\n"
            "main.__globals__['GRANTED_SOCKET_SECONDS'] = 2\n"
            "main.__globals__['GRANTED_REQUEST_SECONDS'] = 3\n"
            "raise SystemExit(main())\n"
        )
        script = launcher
    source = tmp_path / "report.bin"
    with source.open("wb") as output:
        output.write(b"private-upload-content")
        output.truncate(size)
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    prepared_dir = tmp_path / "prepared"
    prepared = result_json(
        cli(
            script,
            "prepare",
            "--project-id",
            PROJECT_ID,
            "--source",
            str(source),
            "--request-dir",
            str(prepared_dir),
            "--actor-client",
            "pytest",
            "--agent-session-id",
            "upload-session",
        )
    )
    assert prepared["expected_artifact"]["sha256"] == original_sha
    assert json.loads((prepared_dir / "request.json").read_text())["api_origin"] is None
    # Preparation survives later changes to the source.
    source.write_bytes(b"changed after preparation")
    calls, ledger = [], {}
    with MCPBridge() as bridge:

        async def backend(request):
            if request.url.path == "/api/v1/artifacts/status":
                body = {"enabled": True, "max_bytes": 1024 * 1024 * 1024, "message": "configured"}
                return httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    stream=httpx.ByteStream(json.dumps(body).encode()),
                )
            content = await request.aread()
            if slow:
                # The gateway forwards after reception: waiting must exceed the direct timeout.
                await asyncio.sleep(0.25)
            calls.append(request)
            metadata = json.loads(request.headers["x-artifact-metadata"])
            uploaded = UploadIntent.model_validate(
                {**prepared["upload_intent"], "metadata": metadata}
            )
            assert len(content) == size and hashlib.sha256(content).hexdigest() == original_sha
            assert request.headers["authorization"] == f"Bearer {API_KEY}"
            assert "x-artifact-upload-intent" not in request.headers
            response = receipt(request, uploaded)
            # The real journal is exercised separately against PostgreSQL.
            response.headers["X-Artifact-Operation-Replayed"] = "true" if ledger else "false"
            ledger[prepared["expected_artifact"]["client_operation_id"]] = original_sha
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=httpx.ByteStream(response.content),
            )

        settings = Settings(api_key=API_KEY, port=bridge.server_port)
        with TestClient(
            create_app(settings, MnemonicAPI(settings, httpx.MockTransport(backend))),
            base_url=bridge.endpoint.removesuffix("/mcp"),
        ) as client:
            bridge.client = client
            worker = threading.Thread(target=bridge.serve_forever, daemon=True)
            worker.start()
            try:
                grant = authorize(bridge, prepared)
                assert grant["upload_url"] == bridge.endpoint
                grant_file = tmp_path / "grant.json"
                grant_file.write_text(json.dumps(grant))
                grant_file.chmod(0o600)
                bridge.drop_next_upload = True
                failed = cli(
                    script,
                    "send",
                    "--request-dir",
                    str(prepared_dir),
                    "--grant-file",
                    str(grant_file),
                )
                assert failed.returncode == 1 and not failed.stdout
                assert "may have committed" in failed.stderr or "unknown" in failed.stderr
                replay = result_json(
                    cli(
                        script,
                        "send",
                        "--request-dir",
                        str(prepared_dir),
                        "--grant-file",
                        str(grant_file),
                    )
                )
                assert replay["replayed"] and replay["sha256"] == original_sha
                assert len(calls) == 2 and len(ledger) == 1
                assert grant["upload_token"] not in failed.stderr
                assert grant["upload_token"] not in json.dumps(replay)
            finally:
                bridge.shutdown()
                worker.join()
