"""Aggregate integrity audit covers populated artifact history and current metadata drift."""

import pytest
from sqlalchemy import text

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
