"""Private human-dashboard export transport; never mounted on machine API/MCP."""

import os
from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import FastAPI
from starlette.background import BackgroundTask

from mnemonic_api.application.browser_file_transport import ClosingStreamingResponse
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_backup.archive import BackupError
from mnemonic_backup.exports import ExportSelection, export_file

if TYPE_CHECKING:
    from mnemonic_backup.service import BackupService


def install_export_routes(app: FastAPI, service: BackupService) -> None:
    @app.post("/projects/{project_id}/exports")
    def export_selected(project_id: UUID, selection: ExportSelection):
        service.require_project(project_id)
        if (selection.include_artifacts or selection.artifact_ids) \
                and service.settings.artifact_max_bytes == 0:
            raise BackupError(503, "artifact_library_disabled",
                              "Artifact library is disabled (MNEMONIC_ARTIFACT_MAX_BYTES=0).")
        storage = ArtifactStorage(service.settings.artifact_root,
                                  max(1, service.settings.artifact_max_bytes))
        content = export_file(service.engine, storage, project_id, selection)
        return ClosingStreamingResponse(
            iter(lambda: content.read(64 * 1024), b""),
            background=BackgroundTask(content.close), media_type="application/x-bzip2",
            headers={"Content-Length": str(os.fstat(content.fileno()).st_size),
                     "Content-Disposition": 'attachment; filename="selected-assets.tar.bz2"'},
        )
