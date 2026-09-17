"""Operator-only cache reset; the durable scheduler regenerates current passages."""

import argparse
import json
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from mnemonic_api import artifact_passages as passages
from mnemonic_api.config import Settings
from mnemonic_api.database import build_engine, build_session_factory
from mnemonic_api.models import Artifact
from mnemonic_api.services.project_mutations import project_mutation
from mnemonic_api.services.work_items import require_project


def reset_artifact_passages(database: Session, project_id: UUID) -> int:
    # Deleting the generation invalidates in-flight publication and cascades to
    # its vectors. Future manifests have fresh job dedupe IDs, even after restore.
    with project_mutation(database, project_id):
        require_project(database, project_id)
        result = database.execute(delete(passages.INDEXES).where(
            passages.INDEXES.c.artifact_id.in_(select(Artifact.id).where(
                Artifact.project_id == project_id))))
        return cast(CursorResult, result).rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", type=UUID, required=True)
    arguments = parser.parse_args()
    engine = build_engine(Settings())
    try:
        with build_session_factory(engine).begin() as database:
            count = reset_artifact_passages(database, arguments.project_id)
        print(json.dumps({"project_id": str(arguments.project_id), "generations_reset": count}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
