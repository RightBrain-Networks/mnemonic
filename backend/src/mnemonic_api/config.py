"""Validated service configuration, with secrets kept out of repr/log output."""

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    database_url: SecretStr = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    api_key: SecretStr = Field(validation_alias=AliasChoices("MNEMONIC_API_KEY", "api_key"))

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
