"""Expose artifact availability without touching its journal or filesystem."""

from fastapi import Request

from mnemonic_api.application.state import settings_of
from mnemonic_api.artifact_schemas import ArtifactLibraryStatus
from mnemonic_api.errors import ApplicationError


def artifact_status(request: Request) -> ArtifactLibraryStatus:
    maximum = settings_of(request).artifact_max_bytes
    message = (
        f"Artifact library is enabled. Maximum upload size is {maximum} bytes "
        "(MNEMONIC_ARTIFACT_MAX_BYTES)."
        if maximum > 0
        else "Artifact library is disabled (MNEMONIC_ARTIFACT_MAX_BYTES=0)."
    )
    return ArtifactLibraryStatus(enabled=maximum > 0, max_bytes=maximum, message=message)


def require_artifacts_enabled(request: Request) -> None:
    status = artifact_status(request)
    if not status.enabled:
        raise ApplicationError(
            503,
            "artifact_library_disabled",
            status.message,
            context={"max_bytes": 0},
            headers={"Cache-Control": "no-store"},
        )
