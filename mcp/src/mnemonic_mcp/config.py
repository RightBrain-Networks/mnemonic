"""Environment configuration, deliberately independent of the API package."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str = field(repr=False)
    api_url: str = "http://api:8000"
    host: str = "0.0.0.0"
    port: int = 8001
    allowed_hosts: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            len(self.api_key) < 32
            or not self.api_key.isascii()
            or any(character.isspace() for character in self.api_key)
        ):
            raise ValueError("MNEMONIC_API_KEY must contain at least 32 ASCII characters without spaces.")
        try:
            parsed = urlsplit(self.api_url)
            parsed_port = parsed.port
            valid_url = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
                and parsed.path in {"", "/"}
                and (parsed_port is None or 1 <= parsed_port <= 65535)
            )
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("MNEMONIC_API_URL must be an HTTP(S) origin without credentials or a path.")
        if not 1 <= self.port <= 65535:
            raise ValueError("MNEMONIC_MCP_PORT must be between 1 and 65535.")
        if not self.allowed_hosts:
            object.__setattr__(self, "allowed_hosts", (
                f"localhost:{self.port}", f"127.0.0.1:{self.port}",
                f"[::1]:{self.port}", f"mcp:{self.port}",
            ))
        if not self.allowed_origins:
            object.__setattr__(self, "allowed_origins", (
                f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}",
                f"http://[::1]:{self.port}",
            ))
        if any("*" in entry for entry in self.allowed_hosts + self.allowed_origins):
            raise ValueError("MCP allowed hosts and origins must be explicit; wildcards are not supported.")
        for origin in self.allowed_origins:
            parsed_origin = urlsplit(origin)
            if (
                parsed_origin.scheme not in {"http", "https"}
                or not parsed_origin.hostname
                or parsed_origin.username is not None
                or parsed_origin.password is not None
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise ValueError("MNEMONIC_MCP_ALLOWED_ORIGINS must contain HTTP(S) origins without paths.")

    @classmethod
    def from_env(cls) -> "Settings":
        def entries(name: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in os.getenv(name, "").split(",") if part.strip())

        try:
            port = int(os.getenv("MNEMONIC_MCP_PORT", "8001"))
        except ValueError:
            raise ValueError("MNEMONIC_MCP_PORT must be an integer.") from None
        return cls(
            api_key=os.getenv("MNEMONIC_API_KEY", ""),
            api_url=os.getenv("MNEMONIC_API_URL", "http://api:8000").rstrip("/"),
            host=os.getenv("MNEMONIC_MCP_HOST", "0.0.0.0"),
            port=port,
            allowed_hosts=entries("MNEMONIC_MCP_ALLOWED_HOSTS"),
            allowed_origins=entries("MNEMONIC_MCP_ALLOWED_ORIGINS"),
        )
