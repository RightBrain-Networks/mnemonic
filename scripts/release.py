#!/usr/bin/env python3
"""Check coordinated release versions or bump them using the native generators.

The backend project version is canonical for API, MCP and dashboard releases.
The Claude plugin has its own canonical version in its plugin manifest.
Historical release notes are deliberately not rewritten.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
CAPTURE = rf"(?P<version>{VERSION})"
PLUGIN_MANIFEST = "plugin/.claude-plugin/plugin.json"
# Exact current-version declarations; historical release notes remain untouched.
FIELDS = (
    ("backend/pyproject.toml", rf'^version = "{CAPTURE}"$', "app"),
    ("mcp/pyproject.toml", rf'^version = "{CAPTURE}"$', "app"),
    ("mcp/src/mnemonic_mcp/__init__.py", rf'^__version__ = "{CAPTURE}"$', "app"),
    ("backend/src/mnemonic_api/application/__init__.py", rf'version="{CAPTURE}"', "app"),
    ("AGENTS.md", rf"current application/API/MCP/dashboard release is `{CAPTURE}`", "app"),
    ("AGENTS.md", rf"Claude plugin\s+`{CAPTURE}`", "plugin"),
    ("AGENT-README.md", rf"Application/API/MCP/dashboard {CAPTURE}, plugin", "app"),
    ("AGENT-README.md", rf"dashboard {VERSION}, plugin {CAPTURE} and Alembic", "plugin"),
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def version_tuple(value: str) -> tuple[int, ...]:
    if re.fullmatch(VERSION, value) is None:
        raise ValueError(f"Expected MAJOR.MINOR.PATCH without leading zeroes, got {value!r}")
    return tuple(int(part) for part in value.split("."))


def current_versions(root: Path) -> dict[str, str]:
    versions = {
        "app": read_toml(root / "backend/pyproject.toml")["project"]["version"],
        "plugin": read_json(root / PLUGIN_MANIFEST)["version"],
    }
    for value in versions.values():
        version_tuple(value)
    return versions


def field_match(text: str, pattern: str, path: str) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(
            f"{path}: expected exactly one current-version declaration, got {len(matches)}")
    return matches[0]


def lock_version(root: Path, package: str, name: str) -> str:
    matches = [item["version"] for item in read_toml(root / package / "uv.lock")["package"]
               if item["name"] == name]
    if len(matches) != 1:
        raise ValueError(f"{package}/uv.lock: expected one root package {name}")
    return matches[0]


def image_defaults(root: Path) -> list[tuple[str, str, str]]:
    """Recognize release defaults when image distribution is present."""
    values = []
    for path in sorted(root.glob("compose*.yaml")):
        for match in re.finditer(r"\$\{MNEMONIC_VERSION:-([^}]+)\}", path.read_text()):
            values.append((path.name, match[1], "app"))
    example = root / ".env.example"
    if example.exists():
        values.extend((".env.example", match[1], "app") for match in re.finditer(
            r"^MNEMONIC_VERSION=(.+)$", example.read_text(), re.MULTILINE))
    return values


def declared_versions(root: Path) -> list[tuple[str, str, str]]:
    values = []
    for path, pattern, kind in FIELDS:
        match = field_match((root / path).read_text(encoding="utf-8"), pattern, path)
        values.append((path, match["version"], kind))
    lock = read_json(root / "frontend/package-lock.json")
    values.extend([
        ("frontend/package.json", read_json(root / "frontend/package.json")["version"], "app"),
        ("frontend/package-lock.json", lock["version"], "app"),
        ("frontend/package-lock.json packages.root", lock["packages"][""]["version"], "app"),
        ("backend/uv.lock", lock_version(root, "backend", "mnemonic-api"), "app"),
        ("mcp/uv.lock", lock_version(root, "mcp", "mnemonic-mcp"), "app"),
        ("docs/openapi.json", read_json(root / "docs/openapi.json")["info"]["version"], "app"),
    ])
    return values + image_defaults(root)


def check(root: Path, tag: str | None = None) -> dict[str, str]:
    versions = current_versions(root)
    errors = [f"{path}: {actual!r}, expected {versions[kind]!r}"
              for path, actual, kind in declared_versions(root) if actual != versions[kind]]
    if tag is not None and tag != f"v{versions['app']}":
        errors.append(f"Tag {tag!r} does not match v{versions['app']}")
    if errors:
        raise ValueError("Release versions disagree:\n" + "\n".join(errors))
    return versions


def replace_field(text: str, pattern: str, path: str, version: str) -> str:
    match = field_match(text, pattern, path)
    start, end = match.span("version")
    return text[:start] + version + text[end:]


def authored_updates(root: Path, versions: dict[str, str]) -> dict[Path, str]:
    updates: dict[Path, str] = {}
    for relative, pattern, kind in FIELDS:
        path = root / relative
        text = updates.get(path, path.read_text(encoding="utf-8"))
        updates[path] = replace_field(text, pattern, relative, versions[kind])
    for relative, kind in (("frontend/package.json", "app"), (PLUGIN_MANIFEST, "plugin")):
        path = root / relative
        document = read_json(path)
        document["version"] = versions[kind]
        updates[path] = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    for path in sorted(root.glob("compose*.yaml")):
        text = path.read_text(encoding="utf-8")
        updated = re.sub(r"(?<=\$\{MNEMONIC_VERSION:-)[^}]+", versions["app"], text)
        if updated != text:
            updates[path] = updated
    example = root / ".env.example"
    if example.exists():
        text = example.read_text(encoding="utf-8")
        updated = re.sub(r"(?<=\nMNEMONIC_VERSION=)[^\n]+", versions["app"], "\n" + text)[1:]
        if updated != text:
            updates[example] = updated
    return updates


def regenerate(root: Path) -> None:
    for package in ("backend", "mcp"):
        subprocess.run(["uv", "lock"], cwd=root / package, check=True)
    subprocess.run(["npm", "install", "--package-lock-only", "--ignore-scripts",
                    "--no-audit", "--no-fund"], cwd=root / "frontend", check=True)
    subprocess.run(["uv", "run", "--frozen", "python", "tests/test_openapi_snapshot.py", "--write"],
                   cwd=root / "backend", check=True)


def bump(root: Path, app: str, plugin: str | None, allow_major: bool = False) -> dict[str, str]:
    # An interrupted generator can leave derived files stale. Repeating the same
    # bump repairs them without requiring manual lockfile or snapshot edits.
    old = current_versions(root)
    versions = {"app": app, "plugin": plugin or old["plugin"]}
    for kind, version in versions.items():
        previous, proposed = version_tuple(old[kind]), version_tuple(version)
        if proposed < previous:
            raise ValueError(f"Refusing to decrease {kind} version {old[kind]} to {version}")
        if proposed[0] > previous[0] and not allow_major:
            raise ValueError(
                "Major bumps require owner approval; pass --allow-major after approval")
    # Validate every authored location before changing any files.
    for path, text in authored_updates(root, versions).items():
        path.write_text(text, encoding="utf-8")
    regenerate(root)
    return check(root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    checks = commands.add_parser("check", help="Verify all current release version declarations")
    checks.add_argument("--tag", help="Also require an exact vMAJOR.MINOR.PATCH tag")
    bumps = commands.add_parser("bump", help="Update versions and regenerate lockfiles and OpenAPI")
    bumps.add_argument("version")
    bumps.add_argument("--plugin", help="New independent Claude plugin version")
    bumps.add_argument("--allow-major", action="store_true",
                       help="Acknowledge prior owner approval")
    args = parser.parse_args()
    try:
        versions = (check(ROOT, args.tag) if args.command == "check" else
                    bump(ROOT, args.version, args.plugin, args.allow_major))
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Release check failed: {error}", file=sys.stderr)
        return 1
    print(f"Application {versions['app']}; Claude plugin {versions['plugin']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
