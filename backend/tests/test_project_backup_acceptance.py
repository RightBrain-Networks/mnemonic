"""The backup acceptance harness retries only explicitly rolled-back restores."""

import json
import runpy
import socket
import threading
from http.client import HTTPConnection, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/test_project_backups.py"
ARCHIVE = b"BZh identical immutable archive fixture"
BUSY = (409, b'{"error":{"code":"backup_database_busy"}}')
SUCCESS = (200, b'{"restored":true}')


@pytest.fixture
def early_rejection_server(monkeypatch):
    state = {"status": 413, "requests": [], "broken_pipes": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append((self.path, int(self.headers["Content-Length"])))
            self.close_connection = True
            if state["status"] is None:
                return
            # Reject from the headers without consuming the upload, like the worker.
            self.send_response(state["status"])
            self.send_header("Content-Length", "8")
            self.end_headers()
            self.wfile.write(b"rejected")
            self.wfile.flush()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    connect = socket.create_connection
    send = HTTPConnection.send

    def local_connection(address, *args, **kwargs):
        assert address == ("127.0.0.1", 8002)
        connection = connect(server.server_address, *args, **kwargs)
        # Force the large upload to overlap the server's early response.
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        return connection

    def tracked_send(connection, data):
        try:
            return send(connection, data)
        except BrokenPipeError:
            state["broken_pipes"] += 1
            raise

    monkeypatch.setattr(socket, "create_connection", local_connection)
    monkeypatch.setattr(HTTPConnection, "send", tracked_send)
    monkeypatch.setenv("MNEMONIC_BACKUP_TOKEN", "acceptance-client-test")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("status", [401, 413])
def test_acceptance_client_reads_early_rejection_without_retry(early_rejection_server, status):
    early_rejection_server["status"] = status
    request = runpy.run_path(str(SCRIPT))["request"]
    body = b"x" * (8 * 1024 * 1024)

    assert request("/restore", method="POST", body=body) == (status, b"rejected")

    assert early_rejection_server["requests"] == [("/restore", len(body))]
    assert early_rejection_server["broken_pipes"] == 1


def test_acceptance_client_requires_a_response_after_upload_disconnect(early_rejection_server):
    early_rejection_server["status"] = None
    request = runpy.run_path(str(SCRIPT))["request"]
    body = b"x" * (8 * 1024 * 1024)

    with pytest.raises((RemoteDisconnected, ConnectionResetError)):
        request("/restore", method="POST", body=body)

    assert early_rejection_server["requests"] == [("/restore", len(body))]


class RestoreServer:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.projects = {"target": {"name": "Archived project"}, "other": {"name": "Other"}}
        self.restores = []
        self.change_after_rejection = None

    def request(self, path, *, method="GET", body=None, api=False, headers=None):
        project = path.split("/")[2]
        if api:
            if method == "PATCH":
                self.projects[project].update(body)
            return 200, json.dumps(self.projects[project]).encode()
        assert path == "/projects/target/restore" and method == "POST"
        self.restores.append((path, body, headers))
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        if result[0] == 200:
            self.projects["target"] = {"name": "Archived project"}
        elif self.change_after_rejection is not None:
            self.projects[self.change_after_rejection] = {"name": "Unexpected change"}
        return result


@pytest.fixture
def harness(monkeypatch):
    loaded = runpy.run_path(str(SCRIPT))
    namespace = loaded["check_restore_success"].__globals__
    sleeps = []
    monkeypatch.setattr(namespace["time"], "sleep", sleeps.append)

    def configure(outcomes):
        server = RestoreServer(outcomes)
        monkeypatch.setitem(namespace, "request", server.request)
        return server, namespace["check_restore_success"], sleeps

    return configure


def test_restore_retries_identical_bytes_only_after_unchanged_rolled_back_attempts(harness):
    server, check, sleeps = harness([BUSY, BUSY, SUCCESS])

    check("target", "other", ARCHIVE)

    assert len(server.restores) == 3
    assert all(body is ARCHIVE for _, body, _ in server.restores)
    assert all(headers == {"X-Confirm-Project": "target"}
               for _, _, headers in server.restores)
    assert server.projects == {"target": {"name": "Archived project"}, "other": {"name": "Other"}}
    assert sleeps == [0.2, 0.4]


def test_restore_success_without_contention_does_not_repeat(harness):
    server, check, sleeps = harness([SUCCESS])

    check("target", "other", ARCHIVE)

    assert len(server.restores) == 1
    assert sleeps == []


def test_restore_contention_has_a_three_attempt_limit(harness):
    server, check, sleeps = harness([BUSY] * 4)

    with pytest.raises(AssertionError, match="three rolled-back attempts"):
        check("target", "other", ARCHIVE)

    assert len(server.restores) == 3
    assert sleeps == [0.2, 0.4]
    assert server.projects["target"] == {"name": "Changed after backup"}


@pytest.mark.parametrize("response", [
    (409, b'{"error":{"code":"backup_integrity_conflict"}}'),
    (409, b'{"error":{"code":"backup_schema_mismatch"}}'),
    (409, b'{"error":null}'),
    (409, b'[]'),
    (503, b'{"error":{"code":"backup_database_busy"}}'),
    (500, b'internal error'),
])
def test_restore_does_not_retry_other_http_failures(harness, response):
    server, check, sleeps = harness([response, SUCCESS])

    with pytest.raises(AssertionError):
        check("target", "other", ARCHIVE)

    assert len(server.restores) == 1
    assert sleeps == []


def test_restore_does_not_retry_malformed_busy_response(harness):
    server, check, sleeps = harness([(409, b'not JSON'), SUCCESS])

    with pytest.raises(json.JSONDecodeError):
        check("target", "other", ARCHIVE)

    assert len(server.restores) == 1
    assert sleeps == []


@pytest.mark.parametrize("error", [
    TimeoutError("uncertain"), URLError("disconnected"), RemoteDisconnected("no response"),
])
def test_restore_does_not_retry_transport_uncertainty(harness, error):
    server, check, sleeps = harness([error, SUCCESS])

    with pytest.raises(type(error)):
        check("target", "other", ARCHIVE)

    assert len(server.restores) == 1
    assert sleeps == []


@pytest.mark.parametrize("changed_project", ["target", "other"])
def test_restore_does_not_retry_if_either_project_changed(harness, changed_project):
    server, check, sleeps = harness([BUSY, SUCCESS])
    server.change_after_rejection = changed_project

    with pytest.raises(AssertionError, match="preserve both projects before retry"):
        check("target", "other", ARCHIVE)

    assert len(server.restores) == 1
    assert sleeps == []
