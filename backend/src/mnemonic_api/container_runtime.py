"""Explicit container initialization, migration, and startup guards."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text

from mnemonic_api.container_backup import backup_database
from mnemonic_api.container_storage import require_existing_storage as _require_existing_storage

STORES = {
    "artifacts": Path("/var/lib/mnemonic/artifacts"),
    "prompts": Path("/var/lib/mnemonic/prompts"),
    "transcripts": Path("/var/lib/mnemonic/transcripts"),
    "index": Path("/var/lib/mnemonic/transcript-index"),
    "backups": Path("/backups"),
}
MIGRATION_LOCK = 1869440355


def initialize_storage() -> None:
    if os.environ.get("MNEMONIC_STORAGE_MODE") not in {"volumes", "bind"}:
        raise RuntimeError(
            "Existing installation: add compose.bind-mounts.yaml to preserve your data paths. "
            "For a new installation, run scripts/setup.py to select named volumes."
        )
    uid = int(os.environ.get("MNEMONIC_API_UID", "10001"))
    gid = int(os.environ.get("MNEMONIC_API_GID", "10001"))
    if uid <= 0 or gid <= 0:
        raise RuntimeError("Service UID and GID must be positive non-root values")
    for path in STORES.values():
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise RuntimeError("Storage mount must not be a symlink")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Only the mount root: changing ownership of existing content is an
        # explicit operator migration, never a recursive startup side effect.
        os.chown(path, uid, gid)
        path.chmod(0o700)


def require_existing_storage(connection: Connection) -> None:
    _require_existing_storage(connection, STORES)


def schema_heads(connection: Connection, config: Config) -> tuple[set[str], set[str]]:
    return (set(MigrationContext.configure(connection).get_current_heads()),
            set(ScriptDirectory.from_config(config).get_heads()))


def require_schema_head(connection: Connection, config: Config) -> None:
    current, expected = schema_heads(connection, config)
    if current != expected:
        raise RuntimeError("Database schema differs from this image; run the migrate service first")
    require_existing_storage(connection)


def require_upgrade_path(connection: Connection, config: Config, current: set[str]) -> None:
    script = ScriptDirectory.from_config(config)
    available = {revision.revision for revision in script.walk_revisions()}
    if current - available:
        raise RuntimeError("Database schema is newer than or incompatible with this image")
    if not current and set(inspect(connection).get_table_names()) - {"alembic_version"}:
        raise RuntimeError("Unversioned database contains tables; refusing an automatic migration")


def pre_migration_backup(connection: Connection, database_url: str, current: set[str]) -> None:
    backup = os.environ.get("MNEMONIC_PRE_MIGRATION_BACKUP", "true").lower()
    if backup not in {"true", "false"}:
        raise RuntimeError("MNEMONIC_PRE_MIGRATION_BACKUP must be true or false")
    if current and backup == "true":
        backup_database(connection, database_url, STORES["backups"] / "pre-migration")
    elif current:
        print("Pre-migration backup explicitly disabled by the operator", flush=True)


def migrate(connection: Connection, config: Config, database_url: str) -> None:
    if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK}):
        raise RuntimeError("Another schema migration is already running")
    connection.commit()
    try:
        require_existing_storage(connection)
        current, expected = schema_heads(connection, config)
        if current == expected:
            print("Database schema is already current", flush=True)
            return
        require_upgrade_path(connection, config, current)
        pre_migration_backup(connection, database_url, current)
        connection.commit()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        require_schema_head(connection, config)
    finally:
        connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
        connection.commit()


def main() -> None:
    args = sys.argv[1:]
    action = args[0] if args else "serve-api"
    if action == "init-storage":
        initialize_storage()
        return
    if action not in {"migrate", "serve-api", "serve-worker"}:
        os.execvp(action, args)
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url, hide_parameters=True, connect_args={"connect_timeout": 10})
    config = Config("alembic.ini")
    try:
        with engine.connect() as connection:
            if action == "migrate":
                migrate(connection, config, database_url)
                return
            require_schema_head(connection, config)
    finally:
        engine.dispose()
    target = ("mnemonic_api.main:create_app" if action == "serve-api"
              else "mnemonic_api.job_worker:create_app")
    port = "8000" if action == "serve-api" else "8002"
    argv = ["uvicorn", target, "--factory", "--host", "0.0.0.0", "--port", port,
            "--no-access-log", "--log-config", "logging.json"]
    if action == "serve-api":
        argv += ["--h11-max-incomplete-event-size", "65536"]
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
