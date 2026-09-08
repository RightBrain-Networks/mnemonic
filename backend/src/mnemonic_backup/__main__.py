"""Container entry point for dashboard backups and explicit operator commands."""

import argparse
import logging
from pathlib import Path
from uuid import UUID

import uvicorn
from sqlalchemy import create_engine

from mnemonic_backup.archive import BackupError, restore_project
from mnemonic_backup.config import BackupSettings
from mnemonic_backup.service import BackupService


def main() -> None:
    parser = argparse.ArgumentParser(description="Private PostgreSQL project backup service")
    parser.add_argument("command", choices=("serve", "once", "restore"), nargs="?", default="serve")
    parser.add_argument("--project", type=UUID)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--confirm-project", type=UUID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.command == "serve":
        uvicorn.run("mnemonic_backup.service:create_app", factory=True, host="0.0.0.0", port=8002,
                    access_log=False)
        return
    settings = BackupSettings()  # type: ignore[call-arg]
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    service = BackupService(settings, engine)
    try:
        if args.command == "once":
            result = service.create(args.project) if args.project else service.cycle()
            print(result or "Project backups completed.")
        else:
            if not args.project or args.confirm_project != args.project or not args.file:
                parser.error("restore requires --project UUID --confirm-project UUID --file PATH")
            with service.store.operation(), args.file.open("rb") as source:
                if args.file.stat().st_size > settings.max_bytes:
                    parser.error("The compressed backup exceeds the configured byte limit.")
                restore_project(engine, args.project, source, max_bytes=settings.expanded_max_bytes)
            print("Project database records restored; artifact files were not changed.")
    except BackupError as error:
        parser.exit(1, error.message + "\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
