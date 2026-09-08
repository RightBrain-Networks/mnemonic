"""Artifact configuration and disabled responses never require storage or a database."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import mnemonic_api.application as application
from mnemonic_api.config import Settings

API_KEY = "artifact-policy-test-key-32-characters"


def settings(**changes):
    return Settings(
        database_url="postgresql://localhost:1/unavailable", api_key=API_KEY, **changes
    )


@pytest.mark.parametrize("maximum", [0, 1, 67_108_864, 1_073_741_824])
def test_artifact_maximum_reads_environment_including_disabled(monkeypatch, maximum):
    monkeypatch.setenv("MNEMONIC_ARTIFACT_MAX_BYTES", str(maximum))
    assert settings().artifact_max_bytes == maximum


@pytest.mark.parametrize("maximum", [-1, 1_073_741_825])
def test_artifact_maximum_rejects_out_of_range_values(maximum):
    with pytest.raises(ValidationError):
        settings(artifact_max_bytes=maximum)


def test_artifact_maximum_defaults_to_64_mib(monkeypatch):
    monkeypatch.delenv("MNEMONIC_ARTIFACT_MAX_BYTES", raising=False)
    assert settings().artifact_max_bytes == 67_108_864


@pytest.mark.parametrize("maximum", [0, 8, 67_108_864, 1_073_741_824])
def test_authenticated_status_is_explicit_without_database_or_storage(tmp_path, maximum):
    root = tmp_path / "nonexistent-artifacts"
    app = application.create_app(settings(artifact_root=root, artifact_max_bytes=maximum))
    with TestClient(app) as client:
        assert client.get("/api/v1/artifacts/status").status_code == 401
        result = client.get(
            "/api/v1/artifacts/status", headers={"Authorization": f"Bearer {API_KEY}"}
        )
        assert result.status_code == 200, result.text
        assert result.headers["cache-control"] == "no-store"
        status = result.json()
        assert status["enabled"] is (maximum > 0)
        assert status["max_bytes"] == maximum
        assert str(maximum) in status["message"]
        assert ("enabled" if maximum else "disabled") in status["message"]
        assert "MNEMONIC_ARTIFACT_MAX_BYTES" in status["message"]
        assert client.get("/healthz").json() == {"status": "ok"}
    assert not root.exists()


def test_disabled_routes_never_open_storage_database_or_consume_content(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled artifact code must not run")

    def forbidden_body():
        forbidden()
        yield b"unreachable"

    monkeypatch.setattr(application, "ArtifactStorage", forbidden)
    monkeypatch.setattr(application, "artifact_maintenance_loop", forbidden)
    root = tmp_path / "nonexistent-artifacts"
    app = application.create_app(settings(artifact_root=root, artifact_max_bytes=0))
    app.state.session_factory = forbidden
    collection = f"/api/v1/projects/{uuid4()}/artifacts"
    item = collection + f"/{uuid4()}"
    endpoints = [
        ("GET", collection),
        ("POST", collection),
        ("GET", item),
        ("DELETE", item),
        ("GET", item + "/history"),
        ("GET", item + "/content"),
        ("PUT", item + "/content"),
        ("POST", collection + "/search-content"),
    ]
    with TestClient(app) as client:
        for method, endpoint in endpoints:
            result = client.request(
                method,
                endpoint,
                content=forbidden_body(),
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "X-Artifact-Metadata": "invalid-json-never-parsed",
                    "Content-Length": "100",
                },
            )
            assert result.status_code == 503, result.text
            assert result.json() == {"detail": {
                "code": "artifact_library_disabled",
                "message": "Artifact library is disabled (MNEMONIC_ARTIFACT_MAX_BYTES=0).",
                "context": {"max_bytes": 0},
            }}
            assert result.headers["cache-control"] == "no-store"
        assert client.get("/healthz").status_code == 200
    assert app.state.artifact_storage is None
    assert not root.exists()
