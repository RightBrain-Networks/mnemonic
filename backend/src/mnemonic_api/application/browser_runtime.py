"""Public runtime values needed to initialize a dashboard browser."""

from fastapi import Request
from fastapi.responses import JSONResponse

from mnemonic_api.application.state import settings_of


async def runtime_config(request: Request) -> JSONResponse:
    """Expose only presentation settings, never serialize the service settings."""
    settings = settings_of(request)
    return JSONResponse(
        {
            "timezone": settings.timezone,
            "work_summary_max_chars": settings.work_summary_max_chars,
            "artifact_max_bytes": settings.artifact_max_bytes,
            "backup_max_bytes": settings.backup_max_bytes,
        },
        headers={
            "Cache-Control": "no-store, max-age=0, no-transform",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )
