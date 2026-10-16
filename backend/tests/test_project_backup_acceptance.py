"""The backup acceptance harness retries only explicitly rolled-back restores."""

import json
import runpy
from pathlib import Path
from urllib.error import URLError

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/test_project_backups.py"
ARCHIVE = b"BZh identical immutable archive fixture"
BUSY = (409, b'{"error":{"code":"backup_database_busy"}}')
SUCCESS = (200, b'{"restored":true}')


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


@pytest.mark.parametrize("error", [TimeoutError("uncertain"), URLError("disconnected")])
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
