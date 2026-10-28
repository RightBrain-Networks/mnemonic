"""Create a private local .env without printing or replacing existing secrets."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


def transcript_options() -> tuple[dict[str, Path], int, int]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-source", type=Path)
    parser.add_argument("--codex-source", type=Path)
    parser.add_argument("--codex-archived-source", type=Path)
    parser.add_argument("--service-uid", type=int)
    parser.add_argument("--service-gid", type=int)
    args = parser.parse_args()
    sources = {key: value for key, value in {
        "MNEMONIC_TRANSCRIPT_SOURCE_DIR": args.transcript_source,
        "MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR": args.codex_source,
        "MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR": args.codex_archived_source,
    }.items() if value is not None}
    owners = set()
    for source in sources.values():
        if (not source.is_absolute() or not source.is_dir() or source.resolve() != source
                or source == Path("/") or any(c in str(source) for c in "\n\r'")):
            parser.error("Sources must be existing absolute dedicated directories without symlinks")
        owners.add((source.stat().st_uid, source.stat().st_gid))
    if len({uid for uid, _ in owners}) > 1 and args.service_uid is None:
        parser.error("Source owners differ. Choose --service-uid and arrange private access "
                     "for both services; one UID cannot read multiple owners' 0600 files.")
    owner_uid, owner_gid = next(iter(sorted(owners)), (10001, 10001))
    uid = owner_uid if args.service_uid is None else args.service_uid
    gid = owner_gid if args.service_gid is None else args.service_gid
    if uid <= 0 or gid <= 0:
        parser.error("Use positive non-root service UID/GID values")
    return sources, uid, gid


def main() -> None:
    sources, uid, gid = transcript_options()
    directory = Path(__file__).resolve().parent
    root = directory if (directory / ".env.example").exists() else directory.parent
    target = root / ".env"
    if target.exists():
        print(".env already exists; left it unchanged.")
        print("Existing bind installs: use compose.bind-mounts.yaml and read docs/operations.md.")
        return
    template = (root / ".env.example").read_text(encoding="utf-8")
    content = template.replace(
        "POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
    ).replace("MNEMONIC_API_KEY=\n", f"MNEMONIC_API_KEY={secrets.token_hex(32)}\n").replace(
        "MNEMONIC_BACKUP_TOKEN=\n", f"MNEMONIC_BACKUP_TOKEN={secrets.token_hex(32)}\n"
    ).replace(
        "MNEMONIC_RABBITMQ_PASSWORD=\n", f"MNEMONIC_RABBITMQ_PASSWORD={secrets.token_hex(32)}\n"
    )
    content = content.replace("MNEMONIC_API_UID=10001", f"MNEMONIC_API_UID={uid}")
    content = content.replace("MNEMONIC_API_GID=10001", f"MNEMONIC_API_GID={gid}")
    content += "\n" + "".join(f"{key}='{value}'\n" for key, value in sources.items())
    # O_EXCL also protects against another initializer creating the file meanwhile.
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(".env already exists; left it unchanged.")
        print("Existing bind installs: use compose.bind-mounts.yaml and read docs/operations.md.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    print("Created .env with new local secrets. Do not commit or share it.")
    print("Docker will initialize private named volumes for the configured service identity.")
    print("Start Mnemonic: docker compose pull && docker compose up -d --wait")
    print("Contributors: docker compose -f compose.yaml -f compose.build.yaml up --build -d --wait")
    print("Check transcript access after startup: python scripts/check_transcript_access.py")


if __name__ == "__main__":
    main()
