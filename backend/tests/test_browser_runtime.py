"""Runtime browser configuration retains presentation defaults without exposing secrets."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from mnemonic_api.application.browser_runtime import runtime_config
from mnemonic_api.config import Settings


def settings(**changes) -> Settings:
    return Settings(
        database_url="postgresql://private.example/mnemonic",
        api_key="runtime-test-secret-with-at-least-32-characters",
        **changes,
    )


def test_runtime_config_exposes_only_public_runtime_values():
    app = FastAPI()
    app.state.settings = settings(
        timezone=" America/Detroit ",
        work_summary_max_chars=3456,
        artifact_max_bytes=0,
        backup_max_bytes=123456,
    )
    app.get("/api/mnemonic/runtime-config")(runtime_config)
    with TestClient(app) as client:
        response = client.get("/api/mnemonic/runtime-config")
    assert response.status_code == 200
    assert response.json() == {
        "timezone": "America/Detroit",
        "work_summary_max_chars": 3456,
        "artifact_max_bytes": 0,
        "backup_max_bytes": 123456,
    }
    assert response.headers["cache-control"] == "no-store, max-age=0, no-transform"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert "private.example" not in response.text
    assert "runtime-test-secret" not in response.text


def test_browser_runtime_defaults(monkeypatch):
    monkeypatch.delenv("TIMEZONE", raising=False)
    monkeypatch.delenv("MNEMONIC_BACKUP_MAX_BYTES", raising=False)
    config = settings()
    assert config.timezone == "UTC"
    assert config.backup_max_bytes == 67_108_864


@pytest.mark.parametrize("timezone", [
    "", " ", "not/a-timezone", "/etc/passwd", "../UTC", "Factory", "localtime",
    "+24:00", "-12:60", "+5", "posix/America/Detroit",
])
def test_invalid_display_timezone_falls_back_to_utc(timezone):
    assert settings(timezone=timezone).timezone == "UTC"


@pytest.mark.parametrize("timezone", ["america/detroit", "utc", "+05:30", "+0530", "-05"])
def test_display_timezone_retains_browser_supported_spellings(timezone):
    assert settings(timezone=timezone).timezone == timezone


def test_browser_runtime_settings_use_runtime_environment(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "Europe/Paris")
    monkeypatch.setenv("MNEMONIC_BACKUP_MAX_BYTES", "123456")
    config = settings()
    assert config.timezone == "Europe/Paris"
    assert config.backup_max_bytes == 123456


@pytest.mark.parametrize("size", ["", 0, -1, "NaN", 2**53, 1.5])
def test_backup_maximum_rejects_invalid_browser_limits(size):
    with pytest.raises(ValidationError):
        settings(backup_max_bytes=size)


@pytest.mark.parametrize("size", [1, 2**53 - 1])
def test_backup_maximum_accepts_browser_safe_integer_boundaries(size):
    assert settings(backup_max_bytes=size).backup_max_bytes == size
