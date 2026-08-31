"""Validated service configuration, with secrets kept out of repr/log output."""

from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_url: SecretStr = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    api_key: SecretStr = Field(validation_alias=AliasChoices("MNEMONIC_API_KEY", "api_key"))
    dashboard_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("MNEMONIC_DASHBOARD_ORIGINS", "dashboard_origins"),
    )

    @field_validator("api_key")
    @classmethod
    def strong_enough_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("MNEMONIC_API_KEY must contain at least 32 characters")
        if value.get_secret_value().strip() != value.get_secret_value():
            raise ValueError("MNEMONIC_API_KEY must not start or end with whitespace")
        return value

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if raw.startswith("postgres://"):
            raw = "postgresql://" + raw[len("postgres://") :]
        try:
            url = make_url(raw)
        except Exception as exc:
            raise ValueError("DATABASE_URL must be a PostgreSQL connection URL") from exc
        if url.get_backend_name() != "postgresql":
            raise ValueError("DATABASE_URL must use PostgreSQL")
        if url.drivername not in {"postgresql", "postgresql+psycopg"}:
            raise ValueError("DATABASE_URL must use the psycopg driver")
        url = url.set(drivername="postgresql+psycopg")
        return SecretStr(url.render_as_string(hide_password=False))

    @field_validator("dashboard_origins")
    @classmethod
    def canonical_dashboard_origins(cls, value: str) -> str:
        origins: list[str] = []
        for entry in value.split(","):
            candidate = entry.strip()
            if not candidate:
                raise ValueError("MNEMONIC_DASHBOARD_ORIGINS must not contain blank entries")
            parsed = urlsplit(candidate)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ValueError("MNEMONIC_DASHBOARD_ORIGINS contains an invalid port") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or "*" in parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("MNEMONIC_DASHBOARD_ORIGINS must contain exact HTTP origins")
            host = parsed.hostname.lower()
            if ":" in host:
                host = f"[{host}]"
            default_port = 80 if parsed.scheme == "http" else 443
            port_suffix = f":{port}" if port is not None and port != default_port else ""
            origin = f"{parsed.scheme}://{host}{port_suffix}"
            if origin not in origins:
                origins.append(origin)
        return ",".join(origins)

    @property
    def allowed_dashboard_origins(self) -> frozenset[str]:
        return frozenset(self.dashboard_origins.split(","))
