"""Configuration for the private backup process only."""

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackupSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MNEMONIC_BACKUP_", extra="ignore")

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    token: SecretStr = Field(min_length=32)
    root: Path = Path("/backups")
    retention_count: int = Field(default=7, ge=1, le=10000)
    interval_seconds: int = Field(default=86400, ge=60, le=31536000)
    max_bytes: int = Field(default=67108864, ge=1024, le=1073741824)
    expanded_max_bytes: int = Field(default=268435456, ge=1024, le=1073741824)

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute() or value == Path("/"):
            raise ValueError("Storage roots must be absolute, dedicated directories.")
        return value
