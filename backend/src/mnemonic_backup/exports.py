"""Selective, disk-spooled portable exports, distinct from restorable database backups."""

import hashlib
import io
import json
import os
import tarfile
from datetime import UTC, datetime
from tempfile import TemporaryFile
from typing import BinaryIO
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, undefer

from mnemonic_api.artifact_schemas import ArtifactActor
from mnemonic_api.artifact_storage import ArtifactStorage
from mnemonic_api.database import begin_coherent_read
from mnemonic_api.models import DecisionDocument, DecisionRevision
from mnemonic_api.services import artifacts, knowledge
from mnemonic_backup.archive import BackupError


class ExportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    artifact_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def selected(self) -> ExportSelection:
        if not self.domain_ids and not self.artifact_ids:
            raise ValueError("Select at least one knowledge domain or artifact.")
        for values in (self.domain_ids, self.artifact_ids):
            if len(set(values)) != len(values):
                raise ValueError("Selections must be unique.")
        return self


def _file(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    entry = tarfile.TarInfo(name)
    entry.mode = 0o600
    entry.size = len(content)
    archive.addfile(entry, io.BytesIO(content))


def _json(archive: tarfile.TarFile, name: str, value: dict) -> None:
    _file(archive, name, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def _domains(archive: tarfile.TarFile, database: Session, project: UUID,
             selected: list[UUID]) -> None:
    begin_coherent_read(database)
    for domain_id in selected:
        domain = knowledge.require_domain(database, project, domain_id)
        folder = f"domains/{domain.id}"
        _json(archive, f"{folder}/domain.json",
              knowledge.domain_read(database, domain).model_dump(mode="json"))
        statement = select(DecisionDocument, DecisionRevision).join(
            DecisionRevision,
            (DecisionRevision.document_id == DecisionDocument.id)
            & (DecisionRevision.revision == DecisionDocument.current_revision),
        ).where(DecisionDocument.domain_id == domain.id,
                DecisionDocument.deleted_at.is_(None)).options(
            undefer(DecisionRevision.content),
        ).order_by(DecisionDocument.ordinal).execution_options(yield_per=25)
        for document, revision in database.execute(statement):
            name = knowledge.decision_identifier(domain, document)
            suffix = ".md" if revision.media_type == "text/markdown" else ".txt"
            _file(archive, f"{folder}/{name}{suffix}", revision.content.encode("utf-8"))
            _json(archive, f"{folder}/{name}.json",
                  knowledge.decision_read(database, document, revision, domain).model_dump(
                      mode="json"))


def _artifact(archive: tarfile.TarFile, database: Session, storage: ArtifactStorage,
              project: UUID, artifact_id: UUID) -> None:
    metadata, content = artifacts.open_artifact(
        database, storage, project, artifact_id, None,
        ArtifactActor(actor_client="dashboard"), human_dashboard=True,
    )
    # An open descriptor is stable across atomic replacement. Verify the entire
    # file before publication so missing, changed or corrupt assets fail together.
    with content:
        hasher = hashlib.sha256()
        while chunk := content.read(1024 * 1024):
            hasher.update(chunk)
        digest = hasher.hexdigest()
        size = os.fstat(content.fileno()).st_size
        if digest != metadata.sha256 or size != metadata.size_bytes:
            raise BackupError(409, "export_content_changed",
                              "An artifact changed or is damaged. Refresh and try exporting again.")
        content.seek(0)
        entry = tarfile.TarInfo(f"artifacts/{artifact_id}/files/{metadata.filename}")
        entry.mode = 0o600
        entry.size = size
        archive.addfile(entry, content)
    _json(archive, f"artifacts/{artifact_id}/metadata.json", metadata.model_dump(mode="json"))


def export_assets(engine: Engine, storage: ArtifactStorage, project: UUID,
                  selection: ExportSelection, output: BinaryIO) -> None:
    with tarfile.open(fileobj=output, mode="w:bz2") as archive:
        _json(archive, "manifest.json", {
            "format": "mnemonic-selected-assets-v1", "project_id": str(project),
            "created_at": datetime.now(UTC).isoformat(),
            "selection": selection.model_dump(mode="json"),
            "contents": "Current decision revisions and current artifact files with metadata.",
            "restore": "Portable export; not a restorable project database backup.",
        })
        with Session(engine) as database:
            _domains(archive, database, project, selection.domain_ids)
        for artifact_id in selection.artifact_ids:
            with Session(engine) as database:
                _artifact(archive, database, storage, project, artifact_id)
    output.seek(0)


def export_file(engine: Engine, storage: ArtifactStorage, project: UUID,
                selection: ExportSelection) -> BinaryIO:
    output = TemporaryFile(mode="w+b")
    try:
        export_assets(engine, storage, project, selection, output)
        return output
    except BaseException:
        output.close()
        raise
