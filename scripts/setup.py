"""Create a private local .env without printing or replacing existing secrets."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".env"
    if target.exists():
        print(".env already exists; left it unchanged.")
        return
    template = (root / ".env.example").read_text(encoding="utf-8")
    content = template.replace(
        "POSTGRES_PASSWORD=\n", f"POSTGRES_PASSWORD={secrets.token_hex(32)}\n"
    ).replace("MNEMONIC_API_KEY=\n", f"MNEMONIC_API_KEY={secrets.token_hex(32)}\n")
    # O_EXCL also protects against another initializer creating the file meanwhile.
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(".env already exists; left it unchanged.")
        return
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    print("Created .env with new local secrets. Do not commit or share it.")
    print("Start Mnemonic: docker compose up --build -d --wait")


if __name__ == "__main__":
    main()
