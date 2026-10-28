"""Release and legacy overlays must preserve the intended storage identities."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker CLI required")


def config(*overlays: str, base: str = "compose.yaml", **settings: str) -> dict:
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("COMPOSE_", "MNEMONIC_"))
                   and key != "POSTGRES_PASSWORD"}
    environment.update({"POSTGRES_PASSWORD": "disposable-test-password",
                        "MNEMONIC_RABBITMQ_PASSWORD": "disposable-test-password",
                        "MNEMONIC_API_KEY": "a" * 32,
                        "MNEMONIC_BACKUP_TOKEN": "b" * 32, **settings})
    command = ["docker", "compose", "--env-file", "/dev/null", "-f", base]
    for overlay in overlays:
        command += ["-f", overlay]
    result = subprocess.run([*command, "config", "--format", "json"], cwd=ROOT,
                            env=environment, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_release_uses_images_and_explicit_storage_selection():
    services = config()["services"]
    assert all("build" not in service for service in services.values())
    assert services["init-storage"]["environment"]["MNEMONIC_STORAGE_MODE"] == "legacy"
    for name in ("api", "worker", "migrate"):
        assert services[name]["user"] == "10001:10001"
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_legacy_overlay_preserves_custom_paths_in_every_service(tmp_path):
    roots = {"MNEMONIC_ARTIFACT_DIR": str(tmp_path / "existing-artifacts"),
             "MNEMONIC_PROMPT_DIR": str(tmp_path / "existing-prompts"),
             "MNEMONIC_TRANSCRIPT_DIR": str(tmp_path / "existing-transcripts"),
             "MNEMONIC_TRANSCRIPT_INDEX_DIR": str(tmp_path / "existing-index"),
             "MNEMONIC_BACKUP_DIR": str(tmp_path / "existing-backups")}
    services = config("compose.bind-mounts.yaml", **roots,
                      MNEMONIC_API_UID="1026", MNEMONIC_API_GID="1000")["services"]
    assert services["init-storage"]["environment"]["MNEMONIC_STORAGE_MODE"] == "bind"
    expected = dict(zip(("/var/lib/mnemonic/artifacts", "/var/lib/mnemonic/prompts",
                        "/var/lib/mnemonic/transcripts", "/var/lib/mnemonic/transcript-index",
                        "/backups"), roots.values(), strict=True))
    for name in ("init-storage", "migrate", "api", "worker"):
        for mount in services[name]["volumes"]:
            if mount["target"] in expected:
                assert mount["type"] == "bind"
                assert mount["source"] == expected[mount["target"]]
                assert not mount["bind"].get("create_host_path", False)
    assert services["api"]["user"] == services["worker"]["user"] == "1026:1000"


def test_contributor_builds_cannot_overwrite_published_image_names():
    services = config("compose.build.yaml", MNEMONIC_STORAGE_MODE="volumes")["services"]
    for name in ("api", "worker", "migrate", "init-storage", "mcp", "web", "tika"):
        assert "build" in services[name]
        assert services[name]["image"].endswith(":local")
        assert not services[name]["image"].startswith("ghcr.io/")


def test_acceptance_projects_cannot_replace_each_others_built_images(tmp_path):
    settings = {"MNEMONIC_E2E_API_KEY": "c" * 32, "MNEMONIC_E2E_BACKUP_TOKEN": "d" * 32,
                "MNEMONIC_DASHBOARD_ORIGINS": "http://127.0.0.1:39001",
                "MNEMONIC_E2E_WEB_PORT": "39001", "MNEMONIC_E2E_API_PORT": "39002"}
    for store in ("ARTIFACT", "BACKUP", "PROMPT", "TRANSCRIPT_INDEX", "TRANSCRIPT"):
        settings[f"MNEMONIC_E2E_{store}_DIR"] = str(tmp_path / store.lower())
    images = []
    for project in ("mnemonic-e2e-first", "mnemonic-e2e-second"):
        services = config("compose.e2e-build.yaml", base="compose.e2e.yaml", **settings,
                          MNEMONIC_E2E_COMPOSE_PROJECT=project)["services"]
        images.append({service["image"] for service in services.values() if "build" in service})
        assert all(image.startswith(project + ":") for image in images[-1])
    assert images[0].isdisjoint(images[1])
