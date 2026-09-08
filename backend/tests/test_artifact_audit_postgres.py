"""Aggregate integrity audit covers populated artifact history and current metadata drift."""

import pytest
from sqlalchemy import text

from mnemonic_api.artifact_extraction import extract_next_artifact

from .test_artifact_extraction_postgres import Parser
from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import collection, headers, upload
from .test_project_activity_audit_postgres import _audit

pytestmark = pytest.mark.postgres


def test_artifact_audit_accepts_replaced_and_deleted_history(api, project, postgres_engine):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"]
    response = api.put(path + "/content", content=b"new bytes", headers=headers(
        {"filename": artifact["filename"]}, revision=1,
    ))
    assert response.status_code == 200
    assert api.delete(path, headers=headers(revision=2)).status_code == 200
    report = _audit(postgres_engine)
    assert report["result"] == "pass", report
    assert report["inventory"]["artifacts"] == 1
    assert report["inventory"]["artifact_pending_operations"] == 0


def test_artifact_audit_detects_current_metadata_drift(api, project, postgres_engine):
    artifact = upload(api, project)
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE artifacts SET description='changed' WHERE id=:id"),
                           {"id": artifact["id"]})
    report = _audit(postgres_engine)
    assert report["result"] == "blocked"
    assert report["blocking_findings"]["artifact_current_revision_mismatch"] == 1


def test_artifact_audit_accepts_extracted_then_deleted_content(
    api, project, postgres_engine, artifact_storage,
):
    artifact = upload(api, project, filename="audit.txt", body=b"synthetic text")
    assert extract_next_artifact(api.app.state.session_factory, artifact_storage, Parser())
    assert _audit(postgres_engine)["result"] == "pass"
    path = collection(project) + "/" + artifact["id"]
    assert api.delete(path, headers=headers(revision=1)).status_code == 200
    assert _audit(postgres_engine)["result"] == "pass"


@pytest.mark.parametrize("damage, finding", [
    ("DELETE FROM artifact_extractions", "artifact_missing_extraction_revision"),
    (
        "UPDATE artifact_extractions SET status='ready', normalized_text='retained'",
        "artifact_extracted_text_retention_violation",
    ),
    (
        "UPDATE artifact_extractions SET status='pending'",
        "artifact_extraction_lifecycle_mismatch",
    ),
])
def test_artifact_audit_detects_privileged_restore_extraction_drift(
    api, project, postgres_engine, damage, finding,
):
    artifact = upload(api, project)
    path = collection(project) + "/" + artifact["id"]
    assert api.delete(path, headers=headers(revision=1)).status_code == 200
    # Restore disables triggers but still must pass the aggregate semantic audit.
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text(damage))
    result = _audit(postgres_engine)
    assert result["result"] == "blocked"
    assert result["blocking_findings"][finding] == 1
