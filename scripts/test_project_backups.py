"""HTTP/fault acceptance checks, executed only inside the disposable backup container.

The runner creates the database and backup bind, supplies an ephemeral API key,
and destroys both afterwards. No host or production service URL is accepted.
"""

import bz2
import fcntl
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import create_engine, text


def request(
    path: str,
    *,
    method: str = "GET",
    body: bytes | dict | None = None,
    api: bool = False,
    token: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    base = "http://api:8000/api/v1" if api else "http://127.0.0.1:8002"
    secret = os.environ["MNEMONIC_TEST_API_KEY" if api else "MNEMONIC_BACKUP_TOKEN"]
    outgoing = {"Authorization": f"Bearer {secret if token is None else token}"}
    outgoing.update(headers or {})
    if isinstance(body, dict):
        outgoing["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    elif body is not None:
        outgoing.setdefault("Content-Type", "application/x-bzip2")
    call = Request(base + path, data=body, method=method, headers=outgoing)
    try:
        with urlopen(call, timeout=60) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def expect(status: int | tuple[int, ...], response: tuple[int, bytes]) -> bytes:
    allowed = (status,) if isinstance(status, int) else status
    assert response[0] in allowed, f"Expected HTTP {allowed}; got {response!r}"
    return response[1]


def create_project(label: str) -> str:
    data = {"name": f"Backup acceptance {label}", "slug": f"backup-{label}-{uuid4().hex}"}
    return json.loads(expect(201, request("/projects", method="POST", body=data, api=True)))["id"]


def listed(project: str) -> list[dict]:
    payload = json.loads(expect(200, request(f"/projects/{project}/backups")))
    assert payload["project_id"] == project
    assert payload["retention_count"] == 2
    return payload["backups"]


def backup(project: str) -> dict:
    return json.loads(expect((200, 201), request(f"/projects/{project}/backups", method="POST")))


def check_authentication_and_missing(project: str) -> None:
    path = f"/projects/{project}/backups"
    expect((401, 403), request(path, token="wrong-token"))
    expect((401, 403), request(path, method="POST", token=os.environ["MNEMONIC_TEST_API_KEY"]))
    expect(404, request(f"/projects/{uuid4()}/backups"))
    expect(404, request(f"/projects/{project}/backups/missing.json.bz2"))
    expect((400, 404, 422), request(f"/projects/{project}/backups/%2e%2e%2fetc%2fpasswd"))
    expect(404, request(f"/projects/{project}/backups", api=True))
    print(
        "PASS separate authentication, missing resources, traversal and API isolation", flush=True
    )


def check_retention_and_concurrency(project: str, other: str) -> bytes:
    assert listed(project) == []
    first = backup(project)
    filename = first["filename"]
    raw = expect(200, request(f"/projects/{project}/backups/{filename}"))
    assert raw.startswith(b"BZh"), "Archives must be bzip2 before filesystem publication"
    assert bz2.decompress(raw)
    assert first["size_bytes"] == len(raw)
    created = datetime.fromisoformat(first["created_at"].replace("Z", "+00:00"))
    assert abs((datetime.now(UTC) - created).total_seconds()) < 120
    second = backup(project)
    third = backup(project)
    current = listed(project)
    assert {item["filename"] for item in current} == {second["filename"], third["filename"]}
    expect(404, request(f"/projects/{project}/backups/{filename}"))
    assert listed(other) == [], "Project retention must not create or prune another project"
    expect(404, request(f"/projects/{other}/backups/{third['filename']}"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: request(f"/projects/{project}/backups", method="POST"), range(4))
        )
    successes = [json.loads(body) for status, body in responses if status in (200, 201)]
    assert successes, responses
    assert len({item["filename"] for item in successes}) == len(successes)
    assert all(status in (200, 201, 409) for status, _ in responses), responses
    assert len(listed(project)) == 2
    for item in listed(project):
        content = expect(200, request(f"/projects/{project}/backups/{item['filename']}"))
        assert bz2.decompress(content)
    print(
        "PASS bzip2 download, accurate age/size, retention, project isolation, concurrent backups",
        flush=True,
    )
    return raw


def check_unwritable_destination(project: str) -> None:
    directory = Path("/backups") / project
    before = {
        item["filename"]: (directory / item["filename"]).read_bytes() for item in listed(project)
    }
    owner = directory.stat()
    assert owner.st_uid == 10001
    try:
        # Simulate a bind mount restored by a root-owned host backup program.
        # The service remains UID 10001, even though this fault harness is root.
        os.chown(directory, 0, 0)
        expect((500, 503, 507), request(f"/projects/{project}/backups", method="POST"))
    finally:
        os.chown(directory, owner.st_uid, owner.st_gid)
    after = {
        item["filename"]: (directory / item["filename"]).read_bytes() for item in listed(project)
    }
    assert before == after, "A failed backup must preserve all prior archives"
    files = [
        item for item in directory.iterdir() if item.is_file() and not item.name.startswith(".")
    ]
    assert {item.name for item in files} == set(after), "Failed backup left a partial archive"
    backup(project)
    print(
        "PASS unwritable storage preserves previous backups and recovers after permissions return",
        flush=True,
    )


def check_restore_rejections(project: str, other: str, archive: bytes) -> None:
    path = f"/projects/{project}/restore"
    confirmation = {"X-Confirm-Project": project}
    before = [expect(200, request(f"/projects/{item}", api=True)) for item in (project, other)]
    expect((400, 409, 422), request(path, method="POST", body=archive))
    expect(
        (400, 409, 422),
        request(path, method="POST", body=archive, headers={"X-Confirm-Project": other}),
    )
    expect(
        (400, 409, 422), request(path, method="POST", body=b"not an archive", headers=confirmation)
    )
    expect(
        (400, 409, 422),
        request(path, method="POST", body=archive[: len(archive) // 2], headers=confirmation),
    )
    expect(
        (400, 409, 422),
        request(
            f"/projects/{other}/restore",
            method="POST",
            body=archive,
            headers={"X-Confirm-Project": other},
        ),
    )
    expect(
        413, request(path, method="POST", body=b"x" * (2 * 1024 * 1024 + 1), headers=confirmation)
    )
    after = [expect(200, request(f"/projects/{item}", api=True)) for item in (project, other)]
    assert before == after, "Rejected restores must leave both projects unchanged"
    print(
        "PASS restore confirmation, corrupt/truncated input, project mismatch and upload limit",
        flush=True,
    )


def check_restore_success(project: str, other: str, archive: bytes) -> None:
    original = json.loads(expect(200, request(f"/projects/{project}", api=True)))
    untouched = expect(200, request(f"/projects/{other}", api=True))
    expect(
        200,
        request(
            f"/projects/{project}", api=True, method="PATCH", body={"name": "Changed after backup"}
        ),
    )
    expect(
        200,
        request(
            f"/projects/{project}/restore",
            method="POST",
            body=archive,
            headers={"X-Confirm-Project": project},
        ),
    )
    restored = json.loads(expect(200, request(f"/projects/{project}", api=True)))
    assert original == restored, "Restore must recover exactly the archived project data"
    assert expect(200, request(f"/projects/{other}", api=True)) == untouched
    assert not Path("/var/lib/mnemonic/artifacts").exists()
    print("PASS HTTP upload restores PostgreSQL data and preserves another project", flush=True)


def wait_for_service_lock() -> None:
    deadline = time.monotonic() + 5
    with (Path("/backups") / ".operation-lock").open("rb") as lock:
        while time.monotonic() < deadline:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            fcntl.flock(lock, fcntl.LOCK_UN)
            time.sleep(0.02)
    raise AssertionError("Expected the backup request to acquire its operation lock")


def check_database_contention(project: str) -> None:
    before = listed(project)
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        with ThreadPoolExecutor(max_workers=1) as pool, engine.begin() as connection:
            connection.execute(text("LOCK TABLE projects IN ACCESS EXCLUSIVE MODE"))
            pending = pool.submit(request, f"/projects/{project}/backups", method="POST")
            wait_for_service_lock()
            expect(409, request(f"/projects/{project}/backups", method="POST"))
            expect(409, pending.result(timeout=30))
    finally:
        engine.dispose()
    assert listed(project) == before, "A busy database must not prune successful archives"
    backup(project)
    print("PASS database lock timeout, concurrent operation rejection and recovery", flush=True)


def main() -> None:
    project_name = os.environ.get("MNEMONIC_E2E_COMPOSE_PROJECT", "")
    if not re.fullmatch(r"mnemonic-e2e-[a-z0-9-]+", project_name):
        raise SystemExit("Only scripts/test-e2e.sh may invoke destructive acceptance fixtures")
    assert os.geteuid() == 0, "The isolated fault harness needs root to alter fixture ownership"
    assert os.environ["MNEMONIC_BACKUP_ROOT"] == "/backups"
    assert os.environ["MNEMONIC_BACKUP_RETENTION_COUNT"] == "2"
    assert os.environ["MNEMONIC_BACKUP_MAX_BYTES"] == "2097152"
    assert not Path("/var/lib/mnemonic/artifacts").exists(), (
        "Backup service must not mount artifacts"
    )
    project, other = create_project("faults"), create_project("untouched")
    check_authentication_and_missing(project)
    archive = check_retention_and_concurrency(project, other)
    check_unwritable_destination(project)
    check_restore_rejections(project, other, archive)
    check_restore_success(project, other, archive)
    check_database_contention(project)


if __name__ == "__main__":
    main()
