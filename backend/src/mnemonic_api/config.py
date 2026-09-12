"""Validated service configuration, with secrets kept out of repr/log output."""

from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from mnemonic_api.summary_limits import DEFAULT_WORK_SUMMARY_MAX_CHARS

DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES = 536_870_912


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_url: SecretStr = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    api_key: SecretStr = Field(validation_alias=AliasChoices("MNEMONIC_API_KEY", "api_key"))
    work_summary_max_chars: int = Field(
        default=DEFAULT_WORK_SUMMARY_MAX_CHARS,
        ge=1,
        le=2**53 - 1,
        validation_alias=AliasChoices(
            "MNEMONIC_WORK_SUMMARY_MAX_CHARS", "work_summary_max_chars"
        ),
    )
    prompt_root: Path = Field(
        default=Path("/var/lib/mnemonic/prompts"),
        validation_alias=AliasChoices("MNEMONIC_PROMPT_ROOT", "prompt_root"),
    )

    artifact_root: Path = Field(
        default=Path("/var/lib/mnemonic/artifacts"),
        validation_alias=AliasChoices("MNEMONIC_ARTIFACT_ROOT", "artifact_root"),
    )
    artifact_max_bytes: int = Field(
        default=67_108_864,
        ge=0,
        le=1_073_741_824,
        validation_alias=AliasChoices("MNEMONIC_ARTIFACT_MAX_BYTES", "artifact_max_bytes"),
    )
    artifact_tika_url: str = Field(
        default="http://tika:9998",
        validation_alias=AliasChoices("MNEMONIC_ARTIFACT_TIKA_URL", "artifact_tika_url"),
    )
    artifact_extraction_timeout_seconds: int = Field(
        default=60,
        ge=5,
        le=300,
        validation_alias=AliasChoices(
            "MNEMONIC_ARTIFACT_EXTRACTION_TIMEOUT_SECONDS", "artifact_extraction_timeout_seconds"
        ),
    )
    artifact_extraction_max_chars: int = Field(
        default=2_000_000,
        ge=1,
        le=8_000_000,
        validation_alias=AliasChoices(
            "MNEMONIC_ARTIFACT_EXTRACTION_MAX_CHARS", "artifact_extraction_max_chars"
        ),
    )
    transcript_allowed_roots: list[Path] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS", "transcript_allowed_roots"),
    )
    transcript_source_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("MNEMONIC_TRANSCRIPT_SOURCE_DIR", "transcript_source_dir"),
    )

    codex_transcript_source_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR", "codex_transcript_source_dir"),
    )
    codex_archived_transcript_source_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR", "codex_archived_transcript_source_dir",
        ),
    )

    @property
    def transcript_source_dirs(self) -> list[Path]:
        return list(dict.fromkeys(source for source in (
            self.transcript_source_dir, self.codex_transcript_source_dir,
            self.codex_archived_transcript_source_dir,
        ) if source is not None))

    @model_validator(mode="after")
    def transcript_source_default_allowlist(self) -> Self:
        sources = self.transcript_source_dirs
        if not self.transcript_allowed_roots:
            self.transcript_allowed_roots = sources
        elif any(source not in self.transcript_allowed_roots for source in sources):
            raise ValueError(
                "MNEMONIC_TRANSCRIPT_ALLOWED_ROOTS must include every configured transcript "
                "source directory (MNEMONIC_TRANSCRIPT_SOURCE_DIR, "
                "MNEMONIC_CODEX_TRANSCRIPT_SOURCE_DIR, "
                "MNEMONIC_CODEX_ARCHIVED_TRANSCRIPT_SOURCE_DIR)"
            )
        return self

    transcript_max_bytes: int = Field(
        default=67_108_864, ge=1, le=268_435_456,
        validation_alias=AliasChoices("MNEMONIC_TRANSCRIPT_MAX_BYTES", "transcript_max_bytes"),
    )
    transcript_index_dir: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("MNEMONIC_TRANSCRIPT_INDEX_DIR", "transcript_index_dir"),
    )

    @field_validator(
        "prompt_root", "transcript_index_dir", "transcript_source_dir",
        "codex_transcript_source_dir", "codex_archived_transcript_source_dir", mode="before",
    )
    @classmethod
    def transcript_directory(cls, value):
        if value is None or value == "":
            return None
        directory = Path(value)
        if (not directory.is_absolute() or directory == Path("/")
                or ".." in directory.parts or str(directory).startswith("//")
                or "\x00" in str(directory)):
            raise ValueError("Storage directories must be absolute dedicated paths")
        return directory

    transcript_search_max_bytes: int = Field(
        default=DEFAULT_TRANSCRIPT_SEARCH_MAX_BYTES, ge=1, le=2_147_483_648,
        validation_alias=AliasChoices(
            "MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES", "transcript_search_max_bytes"),
    )

    @field_validator("transcript_allowed_roots")
    @classmethod
    def transcript_roots_absolute(cls, roots: list[Path]) -> list[Path]:
        if any(not root.is_absolute() or root == Path("/") or ".." in root.parts for root in roots):
            raise ValueError("Transcript roots must be explicit absolute directories other than /")
        return list(dict.fromkeys(roots))

    dashboard_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("MNEMONIC_DASHBOARD_ORIGINS", "dashboard_origins"),
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

    @field_validator("artifact_tika_url")
    @classmethod
    def tika_origin_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("MNEMONIC_ARTIFACT_TIKA_URL must be an HTTP origin") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(char.isspace() for char in value)
        ):
            raise ValueError(
                "MNEMONIC_ARTIFACT_TIKA_URL must be an HTTP origin without credentials"
            )
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        return f"{parsed.scheme}://{host}" + (f":{port}" if port is not None else "")

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
