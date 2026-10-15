"""Canonical domain paths for transport-independent bookkeeping, never routing."""


def domain_path(path: str) -> str:
    if path.startswith("/api/mnemonic/"):
        return "/api/v1/" + path.removeprefix("/api/mnemonic/")
    return path
