"""Selective export honors the same artifact availability switch as the dashboard."""

import bz2
from tempfile import TemporaryFile
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from mnemonic_backup.config import BackupSettings
from mnemonic_backup.service import create_app


def test_disabled_artifacts_cannot_be_read_via_export(tmp_path, monkeypatch):
    token = "export-policy-test-token-at-least-32-characters"
    settings = BackupSettings(DATABASE_URL="sqlite://", token=token, root=tmp_path,
                              MNEMONIC_ARTIFACT_MAX_BYTES=0)
    engine = create_engine("sqlite://")
    app = create_app(settings, engine=engine)
    monkeypatch.setattr(app.state.backup_service, "require_project", lambda _project: None)

    def unexpected_export(*_args):
        raise AssertionError("Disabled artifact export must not touch content")

    monkeypatch.setattr("mnemonic_backup.export_routes.export_file", unexpected_export)
    try:
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.post(f"/projects/{uuid4()}/exports",
                                   json={"artifact_ids": [str(uuid4())]})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "artifact_library_disabled"
    finally:
        engine.dispose()


def test_disabled_artifacts_still_allow_domain_only_export(tmp_path, monkeypatch):
    token = "export-policy-test-token-at-least-32-characters"
    settings = BackupSettings(DATABASE_URL="sqlite://", token=token, root=tmp_path,
                              MNEMONIC_ARTIFACT_MAX_BYTES=0)
    engine = create_engine("sqlite://")
    app = create_app(settings, engine=engine)
    project_id, domain_id = uuid4(), uuid4()
    monkeypatch.setattr(app.state.backup_service, "require_project", lambda _project: None)
    compressed = bz2.compress(b"selected domain content")
    outputs = []

    def export_domain_only(_engine, _storage, project, selection):
        assert project == project_id
        assert selection.domain_ids == [domain_id]
        assert selection.artifact_ids == []
        output = TemporaryFile(mode="w+b")
        output.write(compressed)
        output.seek(0)
        outputs.append(output)
        return output

    monkeypatch.setattr("mnemonic_backup.export_routes.export_file", export_domain_only)
    try:
        with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
            response = client.post(f"/projects/{project_id}/exports",
                                   json={"domain_ids": [str(domain_id)]})
        assert response.status_code == 200
        assert response.content == compressed
        assert response.headers["content-length"] == str(len(compressed))
        assert outputs[0].closed
    finally:
        engine.dispose()
