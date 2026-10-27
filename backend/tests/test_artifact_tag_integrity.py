"""Audit current artifact labels without treating retained historical names as dangling tags."""

import pytest
from sqlalchemy import text

from mnemonic_api.artifact_tag_integrity import ARTIFACT_TAG_FINDINGS

from .test_artifacts_postgres import artifact_storage as artifact_storage
from .test_artifacts_postgres import upload
from .test_project_tags import browser

pytestmark = pytest.mark.postgres


def findings(engine):
    with engine.begin() as connection:
        return {key: connection.scalar(text(query))
                for key, query in ARTIFACT_TAG_FINDINGS.items()}


def test_audit_accepts_historical_names_after_catalog_removal(api, project, postgres_engine):
    rows = api.get(f"/api/v1/projects/{project['id']}/tags").json()["items"]
    tag = next(row for row in rows if row["name"] == "security")
    upload(api, project, metadata={"tags": ["security"]})
    assert browser(api, project, "DELETE", "/" + tag["id"]).status_code == 200
    assert not any(findings(postgres_engine).values())


def test_audit_detects_current_assignment_drift(api, project, postgres_engine):
    artifact = upload(api, project, metadata={"tags": ["security"]})
    with postgres_engine.begin() as connection:
        connection.execute(text("UPDATE artifacts SET tags=ARRAY['missing-tag'] WHERE id=:id"),
                           {"id": artifact["id"]})
    assert findings(postgres_engine) == {
        "artifact_current_tags_mismatch": 1,
        "artifact_unknown_current_tags": 1,
        "artifact_invalid_tag_history": 0,
    }
