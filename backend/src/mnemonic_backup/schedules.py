"""Validated, durable project backup scheduling preferences."""

from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.engine import Engine


class BackupSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = False
    time: str = Field(default="02:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    retention_days: int = Field(default=7, ge=0, le=36500)

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA timezone.") from None
        return value


def read_schedule(engine: Engine, project: UUID) -> BackupSchedule:
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT enabled, time, timezone, retention_days FROM backup_settings "
            "WHERE project_id = :project"), {"project": str(project)}).mappings().first()
    return BackupSchedule(**dict(row)) if row else BackupSchedule()


def save_schedule(engine: Engine, project: UUID, schedule: BackupSchedule) -> None:
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO backup_settings
                (project_id, enabled, time, timezone, retention_days, updated_at)
            VALUES (:project, :enabled, :time, :timezone, :retention_days, :updated_at)
            ON CONFLICT (project_id) DO UPDATE SET enabled = EXCLUDED.enabled,
                time = EXCLUDED.time, timezone = EXCLUDED.timezone,
                retention_days = EXCLUDED.retention_days,
                updated_at = CASE WHEN backup_settings.enabled = EXCLUDED.enabled
                    AND backup_settings.time = EXCLUDED.time
                    AND backup_settings.timezone = EXCLUDED.timezone
                    THEN backup_settings.updated_at ELSE EXCLUDED.updated_at END
        """), {"project": str(project), **schedule.model_dump(), "updated_at": datetime.now(UTC)})
