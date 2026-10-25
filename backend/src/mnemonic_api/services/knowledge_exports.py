"""Exact current decision bytes and a bounded-memory domain archive."""

import io
import json
import tarfile
from tempfile import SpooledTemporaryFile
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, undefer

from mnemonic_api.models import DecisionDocument, DecisionRevision, KnowledgeDomain
from mnemonic_api.services import knowledge


def _add_file(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = 0o600
    archive.addfile(info, io.BytesIO(content))


def export_domain(database: Session, project_id: UUID,
                  domain_id: UUID) -> tuple[KnowledgeDomain, SpooledTemporaryFile[bytes]]:
    domain = knowledge.require_domain(database, project_id, domain_id)
    output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            _add_file(archive, "domain.json", json.dumps(
                knowledge.domain_read(database, domain).model_dump(mode="json"),
                ensure_ascii=False, indent=2,
            ).encode("utf-8"))
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
                _add_file(archive, name + ".md", revision.content.encode("utf-8"))
                metadata = knowledge.decision_read(database, document, revision, domain)
                _add_file(archive, name + ".json", json.dumps(
                    metadata.model_dump(mode="json"), ensure_ascii=False, indent=2,
                ).encode("utf-8"))
        output.seek(0)
        return domain, output
    except BaseException:
        output.close()
        raise
