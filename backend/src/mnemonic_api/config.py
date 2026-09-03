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
    lease_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        validation_alias=AliasChoices("MNEMONIC_LEASE_TTL_SECONDS", "lease_ttl_seconds"),
    )
    client_operation_wait_seconds: int = Field(
        default=10,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS",
            "client_operation_wait_seconds",
        ),
    )
    duplicate_suggestion_body_max_bytes: int = Field(
        default=2_097_152,
        ge=2_097_152,
        le=2_097_152,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_BODY_MAX_BYTES",
            "duplicate_suggestion_body_max_bytes",
        ),
    )
    duplicate_suggestion_request_slots: int = Field(
        default=4,
        ge=1,
        le=4,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_SLOTS",
            "duplicate_suggestion_request_slots",
        ),
    )
    duplicate_suggestion_request_wait_ms: int = Field(
        default=250,
        ge=1,
        le=250,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_WAIT_MS",
            "duplicate_suggestion_request_wait_ms",
        ),
    )
    duplicate_suggestion_inference_slots: int = Field(
        default=1,
        ge=1,
        le=1,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_SLOTS",
            "duplicate_suggestion_inference_slots",
        ),
    )
    duplicate_suggestion_inference_wait_ms: int = Field(
        default=50,
        ge=1,
        le=50,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_WAIT_MS",
            "duplicate_suggestion_inference_wait_ms",
        ),
    )
    duplicate_suggestion_lexical_shortlist: int = Field(
        default=200,
        ge=1,
        le=200,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_LEXICAL_SHORTLIST",
            "duplicate_suggestion_lexical_shortlist",
        ),
    )
    duplicate_suggestion_missing_vector_limit: int = Field(
        default=128,
        ge=1,
        le=128,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_MISSING_VECTOR_LIMIT",
            "duplicate_suggestion_missing_vector_limit",
        ),
    )
    duplicate_suggestion_full_population_ceiling: int = Field(
        default=10_000,
        ge=1,
        le=10_000,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_FULL_POPULATION_CEILING",
            "duplicate_suggestion_full_population_ceiling",
        ),
    )
    duplicate_suggestion_timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=60,
        validation_alias=AliasChoices(
            "MNEMONIC_DUPLICATE_SUGGESTION_TIMEOUT_SECONDS",
            "duplicate_suggestion_timeout_seconds",
        ),
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
