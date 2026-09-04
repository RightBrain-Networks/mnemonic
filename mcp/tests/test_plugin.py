import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPOSITORY_ROOT / "plugin"
SKILL_FILES = {
    "mnemonic-save": PLUGIN_ROOT / "skills" / "mnemonic-save" / "SKILL.md",
    "mnemonic-search": PLUGIN_ROOT / "skills" / "mnemonic-search" / "SKILL.md",
    "mnemonic-recall": PLUGIN_ROOT / "skills" / "mnemonic-recall" / "SKILL.md",
}
REFERENCE_FILES = {
    "authority-and-provenance.md",
    "completion-evidence.md",
    "repository-freshness.md",
    "work-graph.md",
}
BIN_FILES = {"mnemonic-repository-freshness"}
PLUGIN_PAYLOAD_FILES = {
    ".claude-plugin/plugin.json",
    "bin/mnemonic-repository-freshness",
    "reference/authority-and-provenance.md",
    "reference/completion-evidence.md",
    "reference/repository-freshness.md",
    "reference/work-graph.md",
    "skills/mnemonic-recall/SKILL.md",
    "skills/mnemonic-save/SKILL.md",
    "skills/mnemonic-search/SKILL.md",
    "tests/test_repository_freshness.py",
    "tests/test_repository_freshness_authentic_runtime.py",
}
_CLAUDE_ISOLATED_PATH_KEYS = {
    "CLAUDE_CONFIG_DIR",
    "HOME",
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
}


def _intended_plugin_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def _copy_exact_plugin_payload(destination: Path) -> None:
    assert _intended_plugin_files(PLUGIN_ROOT) == PLUGIN_PAYLOAD_FILES
    for relative in sorted(PLUGIN_PAYLOAD_FILES):
        source = PLUGIN_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _payload_snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    assert _intended_plugin_files(root) == PLUGIN_PAYLOAD_FILES
    return {
        relative: (
            (root / relative).read_bytes(),
            stat.S_IMODE((root / relative).stat().st_mode),
        )
        for relative in sorted(PLUGIN_PAYLOAD_FILES)
    }


def _assert_exact_payload(
    installed_root: Path,
    expected: dict[str, tuple[bytes, int]],
) -> None:
    assert _intended_plugin_files(installed_root) == PLUGIN_PAYLOAD_FILES
    actual = _payload_snapshot(installed_root)
    for relative, (expected_bytes, expected_mode) in expected.items():
        actual_bytes, actual_mode = actual[relative]
        if actual_bytes != expected_bytes:
            pytest.fail(f"installed plugin bytes differ: {relative}")
        assert actual_mode == expected_mode, relative


def _isolated_claude_environment(config_directory: Path) -> dict[str, str]:
    """Return a minimal, state-isolated, offline CLI environment for lifecycle proof."""
    runtime_root = config_directory.parent / f".{config_directory.name}-runtime"
    locations = {
        "CLAUDE_CONFIG_DIR": config_directory,
        "HOME": runtime_root / "home",
        "TMPDIR": runtime_root / "tmp",
        "XDG_CACHE_HOME": runtime_root / "xdg-cache",
        "XDG_CONFIG_HOME": runtime_root / "xdg-config",
        "XDG_DATA_HOME": runtime_root / "xdg-data",
    }
    for location in locations.values():
        location.mkdir(parents=True, exist_ok=True)

    return {
        **{name: str(location) for name, location in locations.items()},
        "ALL_PROXY": "http://127.0.0.1:9",
        "CI": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "GIT_CEILING_DIRECTORIES": str(config_directory.parent),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "PAGER": "cat",
        "PATH": os.defpath,
        "SHELL": "/bin/sh",
        "TERM": "dumb",
        "TZ": "UTC",
    }


def _run_claude(
    executable: str,
    config_directory: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [executable, *arguments],
        cwd=config_directory.parent,
        env=_isolated_claude_environment(config_directory),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"isolated Claude CLI command failed with exit {result.returncode}: "
            f"{' '.join(arguments)}"
        )
    return result


def _active_plugin_root(executable: str, config_directory: Path, version: str) -> Path:
    listing = json.loads(
        _run_claude(executable, config_directory, "plugin", "list", "--json").stdout
    )
    assert len(listing) == 1
    assert listing[0]["id"] == "mnemonic@mnemonic"
    assert listing[0]["version"] == version
    assert listing[0]["scope"] == "user"
    assert listing[0]["enabled"] is True

    installed_metadata = json.loads(
        (config_directory / "plugins" / "installed_plugins.json").read_text()
    )
    assert installed_metadata["version"] == 2
    records = installed_metadata["plugins"]
    assert set(records) == {"mnemonic@mnemonic"}
    assert len(records["mnemonic@mnemonic"]) == 1
    record = records["mnemonic@mnemonic"][0]
    assert record["scope"] == "user"
    assert record["version"] == version

    install_path = Path(listing[0]["installPath"])
    assert install_path == Path(record["installPath"])
    assert install_path.parent.name == "mnemonic"
    assert install_path.name == version
    install_path.relative_to(config_directory)
    return install_path


def _assert_installed_details(executable: str, config_directory: Path) -> None:
    output = _run_claude(
        executable,
        config_directory,
        "plugin",
        "details",
        "mnemonic@mnemonic",
    ).stdout
    assert "Mnemonic (mnemonic) 0.10.0" in output
    assert "Source: mnemonic@mnemonic" in output
    assert "Component inventory" in output
    assert re.search(
        r"Skills \(3\)\s+mnemonic-recall, mnemonic-save, mnemonic-search",
        output,
    )
    for empty_kind in ("Agents", "Hooks", "MCP servers", "LSP servers"):
        assert f"{empty_kind} (0)" in output


def _assert_installed_component_inventory(root: Path) -> None:
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "mnemonic"
    assert manifest["version"] == "0.10.0"
    assert {
        path.parent.name for path in (root / "skills").glob("*/SKILL.md")
    } == set(SKILL_FILES)
    assert {path.name for path in (root / "reference").glob("*.md")} == REFERENCE_FILES
    assert {path.name for path in (root / "bin").iterdir()} == BIN_FILES
    helper = root / "bin" / "mnemonic-repository-freshness"
    assert stat.S_IMODE(helper.stat().st_mode) & 0o111 == 0o111


def _add_marketplace_and_install(
    executable: str,
    config_directory: Path,
    marketplace_root: Path,
) -> None:
    config_directory.mkdir(parents=True)
    _run_claude(
        executable,
        config_directory,
        "plugin",
        "marketplace",
        "add",
        str(marketplace_root),
        "--scope",
        "user",
    )
    _run_claude(
        executable,
        config_directory,
        "plugin",
        "install",
        "mnemonic@mnemonic",
        "--scope",
        "user",
    )


def test_plugin_manifest_and_inventory_are_exact():
    inner = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").read_text()
    )

    assert inner["name"] == "mnemonic"
    assert inner["version"] == "0.10.0"
    assert "duplicate merges" in inner["description"]
    assert "declared repository scope" in inner["description"]
    assert marketplace["plugins"] == [
        {
            "name": "mnemonic",
            "source": "./plugin",
            "description": inner["description"],
        }
    ]
    assert {path.parent.name for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md")} == (
        set(SKILL_FILES)
    )
    assert {path.name for path in (PLUGIN_ROOT / "reference").glob("*.md")} == (
        REFERENCE_FILES
    )
    assert {path.name for path in (PLUGIN_ROOT / "bin").iterdir()} == BIN_FILES
    helper = PLUGIN_ROOT / "bin" / "mnemonic-repository-freshness"
    assert helper.stat().st_mode & 0o111 == 0o111


def test_authentic_cli_environment_is_minimal_offline_and_state_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    marker = "must-not-reach-authentic-cli"
    for name in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "SSH_AUTH_SOCK",
        "AWS_ACCESS_KEY_ID",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.setenv(name, marker)

    config_directory = tmp_path / "isolated-config"
    environment = _isolated_claude_environment(config_directory)

    assert marker not in environment
    assert marker not in environment.values()
    assert set(environment) == _CLAUDE_ISOLATED_PATH_KEYS | {
        "ALL_PROXY",
        "CI",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
        "DISABLE_AUTOUPDATER",
        "DISABLE_TELEMETRY",
        "GIT_CEILING_DIRECTORIES",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "NO_PROXY",
        "PAGER",
        "PATH",
        "SHELL",
        "TERM",
        "TZ",
    }
    assert environment["PATH"] == os.defpath
    assert environment["GIT_CONFIG_GLOBAL"] == os.devnull
    assert environment["NO_PROXY"] == "127.0.0.1,localhost"
    for name in _CLAUDE_ISOLATED_PATH_KEYS:
        location = Path(environment[name])
        location.relative_to(tmp_path)
        assert location.is_dir()


def test_authentic_sequential_and_fresh_plugin_installs_are_exact(tmp_path: Path):
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("Claude CLI is unavailable for the authentic isolated install proof")

    marketplace_root = tmp_path / "marketplace"
    marketplace_manifest = marketplace_root / ".claude-plugin" / "marketplace.json"
    marketplace_manifest.parent.mkdir(parents=True)
    shutil.copy2(
        REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json",
        marketplace_manifest,
    )
    marketplace_plugin = marketplace_root / "plugin"
    _copy_exact_plugin_payload(marketplace_plugin)

    synthetic_manifest_path = marketplace_plugin / ".claude-plugin" / "plugin.json"
    synthetic_manifest = json.loads(synthetic_manifest_path.read_text())
    assert synthetic_manifest["version"] == "0.10.0"
    synthetic_manifest["version"] = "0.9.0"
    synthetic_manifest_path.write_text(json.dumps(synthetic_manifest, indent=2) + "\n")
    stale_relative = Path("reference") / "obsolete-phase-10-reference.md"
    stale_path = marketplace_plugin / stale_relative
    stale_path.write_bytes(b"synthetic obsolete plugin payload\n")

    sequential_config = tmp_path / "sequential-config"
    _add_marketplace_and_install(executable, sequential_config, marketplace_root)
    old_root = _active_plugin_root(executable, sequential_config, "0.9.0")
    assert (old_root / stale_relative).is_file()
    assert json.loads(
        (old_root / ".claude-plugin" / "plugin.json").read_text()
    )["version"] == "0.9.0"

    shutil.rmtree(marketplace_plugin)
    _copy_exact_plugin_payload(marketplace_plugin)
    expected_current = _payload_snapshot(marketplace_plugin)
    _run_claude(
        executable,
        sequential_config,
        "plugin",
        "marketplace",
        "update",
        "mnemonic",
    )
    refreshed_only_root = _active_plugin_root(executable, sequential_config, "0.9.0")
    assert refreshed_only_root == old_root
    assert (refreshed_only_root / stale_relative).is_file()
    _run_claude(
        executable,
        sequential_config,
        "plugin",
        "update",
        "mnemonic@mnemonic",
        "--scope",
        "user",
    )

    upgraded_root = _active_plugin_root(executable, sequential_config, "0.10.0")
    _assert_exact_payload(upgraded_root, expected_current)
    _assert_installed_component_inventory(upgraded_root)
    assert not (upgraded_root / stale_relative).exists()
    _assert_installed_details(executable, sequential_config)

    fresh_config = tmp_path / "fresh-config"
    _add_marketplace_and_install(executable, fresh_config, marketplace_root)
    fresh_root = _active_plugin_root(executable, fresh_config, "0.10.0")
    _assert_exact_payload(fresh_root, expected_current)
    _assert_installed_component_inventory(fresh_root)
    assert not (fresh_root / stale_relative).exists()
    _assert_installed_details(executable, fresh_config)

    fresh_snapshot = _payload_snapshot(fresh_root)
    upgraded_snapshot = _payload_snapshot(upgraded_root)
    assert fresh_snapshot.keys() == upgraded_snapshot.keys()
    for relative in fresh_snapshot:
        if fresh_snapshot[relative][0] != upgraded_snapshot[relative][0]:
            pytest.fail(f"fresh and upgraded plugin bytes differ: {relative}")
        assert fresh_snapshot[relative][1] == upgraded_snapshot[relative][1], relative


def test_every_skill_agrees_on_gate_authority_and_dual_graph_facts():
    for name, path in SKILL_FILES.items():
        content = path.read_text()
        lowered = content.lower()
        assert "request_human_input" in content, name
        assert "authority-and-provenance.md" in content, name
        assert "parent-child" in content, name
        assert "discovered-from" in content, name
        assert "never infer" in lowered, name
        assert "dashboard" in lowered, name
        assert "execution authority" in lowered or "automatic execution" in lowered, name
        assert "resolve_human_input" not in content, name

        for relative in re.findall(
            r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)", content
        ):
            assert (PLUGIN_ROOT / relative).is_file(), (name, relative)


def test_shared_references_freeze_eleven_writes_and_permanent_merge():
    authority = (PLUGIN_ROOT / "reference" / "authority-and-provenance.md").read_text()
    graph = (PLUGIN_ROOT / "reference" / "work-graph.md").read_text()

    assert "These eleven canonical mutations" in authority
    assert "`merge_work`" in authority
    assert "permanent" in authority
    assert "typed `503 duplicate_graph_invalid`" in authority
    assert "not an unknown" in authority
    assert "do not retry" in authority
    assert "duplicate-handling aggregate audit" in authority
    assert "No canonical MCP tool resolves a gate" in authority
    assert "request_human_input" in authority
    assert "list_work_gates" in authority
    assert "No longer needed" in authority
    assert "cannot withdraw" in authority
    assert "`requested_context_revision`" in authority
    assert "backend-computed drift flags" in authority
    assert "resolve_human_input" not in authority
    assert "unresolved human gate" in graph
    assert "Only `parent-child` defines" in graph
    assert "record both facts atomically" in graph
    assert "Duplicate marks are not authoritative merges" in graph
    assert "sole authoritative operation" in graph
    assert "never replace" in graph.lower()
    assert "Fresh generic duplicate-of" in graph


def test_advisory_guidance_preserves_create_anyway_and_categorical_evidence():
    save = SKILL_FILES["mnemonic-save"].read_text()
    authority = (PLUGIN_ROOT / "reference" / "authority-and-provenance.md").read_text()

    assert "suggest_duplicate_work" in save
    assert "exact_title" in save
    assert "lexical" in save
    assert "semantic" in save
    assert re.search(r"create\s+anyway", save, re.IGNORECASE)
    assert "suggest_duplicate_work" in authority
    assert "evidence-only" in authority


def test_repository_freshness_guidance_uses_only_the_packaged_fixed_helper():
    recall = SKILL_FILES["mnemonic-recall"].read_text()
    freshness = (PLUGIN_ROOT / "reference" / "repository-freshness.md").read_text()
    executable = '"${CLAUDE_PLUGIN_ROOT}/bin/mnemonic-repository-freshness"'

    for content in (recall, freshness):
        assert executable in content
        assert "--baseline" in content
        assert "--path" in content
    assert "explicitly select the workspace" in freshness
    assert "15-second" in freshness
    assert "unchanged" in freshness
    assert "changed" in freshness
    assert "indeterminate" in freshness
    assert "semantic freshness" in freshness
    assert "correctness" in freshness
    assert "--repo" not in recall
    assert "--root" not in recall
