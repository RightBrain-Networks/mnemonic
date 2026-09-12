"""Current project lease policy shared by settings, claims, renewals and safe reads."""

from uuid import UUID

from sqlalchemy.orm import Session

from mnemonic_api.errors import ApplicationError
from mnemonic_api.models import ProjectSettings
from mnemonic_api.schemas import LeaseSettingsRead, ProjectSettingsPatch


def lease_settings(database: Session, project_id: UUID) -> LeaseSettingsRead:
    settings = database.get(ProjectSettings, project_id)
    if settings is None:
        raise ApplicationError(503, "project_settings_unavailable", "Project settings unavailable.")
    return LeaseSettingsRead(
        default_minutes=settings.lease_default_minutes,
        minimum_minutes=settings.lease_minimum_minutes,
        maximum_minutes=settings.lease_maximum_minutes,
    )


def requested_lease_minutes(database: Session, project_id: UUID, requested: int | None) -> int:
    policy = lease_settings(database, project_id)
    minutes = policy.default_minutes if requested is None else requested
    if not policy.minimum_minutes <= minutes <= policy.maximum_minutes:
        raise ApplicationError(
            422, "lease_minutes_out_of_range",
            f"Request between {policy.minimum_minutes} and {policy.maximum_minutes} lease minutes.",
        )
    return minutes


def validate_settings_patch(settings: ProjectSettings, payload: ProjectSettingsPatch) -> None:
    values = {
        name: getattr(payload, f"lease_{name}_minutes")
        if f"lease_{name}_minutes" in payload.model_fields_set
        else getattr(settings, f"lease_{name}_minutes")
        for name in ("default", "minimum", "maximum")
    }
    if not values["minimum"] <= values["default"] <= values["maximum"]:
        raise ApplicationError(
            422, "invalid_lease_settings",
            "Lease durations must satisfy minimum <= default <= maximum.",
        )
