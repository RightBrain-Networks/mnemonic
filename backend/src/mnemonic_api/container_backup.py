"""Fail-closed PostgreSQL recovery dumps before container schema upgrades."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from psycopg import Connection as PsycopgConnection
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url


def dump_environment(database_url: str, connection: Connection | None = None) -> dict[str, str]:
    url = make_url(database_url)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    env.update({"PGHOST": url.host or "postgres", "PGPORT": str(url.port or 5432),
                "PGUSER": url.username or "", "PGPASSWORD": url.password or "",
                "PGDATABASE": url.database or "", "PGCONNECT_TIMEOUT": "10"})
    options = {"sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT",
               "sslkey": "PGSSLKEY", "sslcrl": "PGSSLCRL", "options": "PGOPTIONS",
               "connect_timeout": "PGCONNECT_TIMEOUT", "application_name": "PGAPPNAME"}
    for name, value in url.query.items():
        if name not in options or not isinstance(value, str):
            raise RuntimeError(f"Unsupported pre-migration backup connection option: {name}")
        env[options[name]] = value
    _live_connection_environment(connection, env, options)
    return env


def _live_connection_environment(
    connection: Connection | None, env: dict[str, str], options: dict[str, str],
) -> None:
    if connection is None:
        return
    driver = connection.connection.driver_connection
    if not isinstance(driver, PsycopgConnection):
        return
    # Use the actual server and identity, including resolved host address and any
    # inherited libpq configuration, rather than reconnecting to guessed defaults.
    info = driver.info
    env.update({"PGHOST": info.host, "PGPORT": str(info.port), "PGUSER": info.user,
                "PGPASSWORD": info.password, "PGDATABASE": info.dbname})
    if info.hostaddr:
        env["PGHOSTADDR"] = info.hostaddr
    for name, value in info.get_parameters().items():
        if name in options:
            env[options[name]] = value


def _sync_directory(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _dump(output: BinaryIO, env: dict[str, str]) -> int:
    # Diagnostic output can be large and can contain connection details. Retain
    # neither it nor a password-bearing command line in application logs.
    with tempfile.TemporaryFile() as errors:
        result = subprocess.run(["pg_dump", "--format=custom", "--no-password"],
                                env=env, stdout=output, stderr=errors, check=False)
    output.flush()
    os.fsync(output.fileno())
    return result.returncode


def backup_database(connection: Connection, database_url: str, root: Path) -> Path:
    """Publish a private custom-format dump only after pg_dump succeeds."""
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise RuntimeError("Pre-migration backup storage must not contain symlinks")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database_bytes = connection.scalar(text("SELECT pg_database_size(current_database())"))
    if database_bytes is None:
        raise RuntimeError("Could not estimate pre-migration database backup space")
    if shutil.disk_usage(root).free < int(database_bytes) + 64 * 1024 * 1024:
        raise RuntimeError("Insufficient free space for the pre-migration database backup")
    env = dump_environment(database_url, connection)
    descriptor, temporary = tempfile.mkstemp(prefix=".incomplete-", dir=root)
    pending = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            result = _dump(output, env)
        if result or pending.stat().st_size == 0:
            raise RuntimeError("Pre-migration pg_dump failed; migration was not started")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = root / f"before-upgrade-{stamp}-{pending.name[12:]}.dump"
        os.link(pending, destination)
        _sync_directory(root)
        print(f"Pre-migration backup completed: {destination.name}", flush=True)
        return destination
    finally:
        pending.unlink(missing_ok=True)
        _sync_directory(root)
