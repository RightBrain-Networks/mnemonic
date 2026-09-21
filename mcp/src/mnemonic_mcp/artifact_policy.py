"""Discover authoritative artifact configuration without touching artifact data."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from mcp.server.fastmcp.exceptions import ToolError

from .api import MnemonicAPI, TransportEffect
from .artifact_models import MCP_ARTIFACT_MAX_BYTES, ArtifactLibraryStatus, ArtifactToolStatus

DISABLED_MESSAGE = (
    "Artifact library is disabled (MNEMONIC_ARTIFACT_MAX_BYTES=0; max_bytes=0). "
    "Existing files and history are retained. No artifact operation is available until enabled. "
    "For any earlier unknown write, preserve its original operation UUID and exact arguments "
    "and bytes; disabling does not establish that write's outcome."
)


async def _status(api: MnemonicAPI) -> ArtifactToolStatus:
    try:
        status = cast(ArtifactLibraryStatus, await api.request(
            "GET", "artifacts/status", response_model=ArtifactLibraryStatus,
            effect=TransportEffect.SAFE_READ, expected_status_code=200,
            strict_wire_response=True, bounded_identity_response=True, response_max_bytes=4096,
        ))
    except ToolError as exc:
        raise ToolError(
            "Could not determine artifact-library status or configured size limit; "
            "no artifact operation was sent by this attempt. Preserve the original operation "
            "UUID, arguments and bytes for any earlier unknown write. " + str(exc)
        ) from None
    if not status.enabled:
        raise ToolError(DISABLED_MESSAGE)
    effective = min(status.max_bytes, MCP_ARTIFACT_MAX_BYTES)
    return ArtifactToolStatus(
        enabled=True, max_bytes=status.max_bytes, effective_upload_max_bytes=effective,
        message=(f"Upload limit: {status.max_bytes} bytes; MCP base64 limit: "
                 f"{MCP_ARTIFACT_MAX_BYTES} bytes. Raw helpers use the configured limit."),
    )


@asynccontextmanager
async def artifact_access(api: MnemonicAPI) -> AsyncIterator[ArtifactToolStatus]:
    status = await _status(api)
    yield status
