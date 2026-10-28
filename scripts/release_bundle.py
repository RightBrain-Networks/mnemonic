"""Assemble a source-free install bundle and version-pinned release metadata."""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

REPOSITORY = "https://github.com/RightBrain-Networks/mnemonic"
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)")
INSTALL_FILES = (
    "compose.yaml", "compose.bind-mounts.yaml", "compose.tls.yaml",
    "compose.transcripts.yaml", ".env.example", "scripts/setup.py",
    "scripts/check-stack.py", "scripts/check_transcript_access.py", "LICENSE",
    "SECURITY.md", "CHANGELOG.md", "README.md", "AGENT-README.md", "AGENTS.md",
    "docs/operations.md", "docs/releases.md",
    "docs/upgrade-history.md",
)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def version(root: Path) -> str:
    value = tomllib.loads((root / "backend/pyproject.toml").read_text())["project"]["version"]
    if not SEMVER.fullmatch(value):
        raise ValueError("Release version must be a stable MAJOR.MINOR.PATCH")
    return value


def migration_metadata(source: str) -> tuple[str, tuple[str, ...], str]:
    tree = ast.parse(source)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {"revision", "down_revision"}:
                    values[target.id] = ast.literal_eval(node.value)
    revision = values["revision"]
    parent = values["down_revision"]
    parents = () if parent is None else (parent,) if isinstance(parent, str) else tuple(parent)
    return revision, parents, (ast.get_docstring(tree) or revision).splitlines()[0]


def migrations(root: Path, ref: str | None = None) -> dict[str, tuple[str, tuple[str, ...], str]]:
    if ref is None:
        return {p.name: migration_metadata(p.read_text())
                for p in (root / "backend/alembic/versions").glob("*.py")}
    if not re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", ref):
        raise ValueError("Previous release must be a stable version tag")
    directory = "backend/alembic/versions"
    names = git(root, "ls-tree", "--name-only", f"{ref}:{directory}").splitlines()
    return {name: migration_metadata(git(root, "show", f"{ref}:{directory}/{name}"))
            for name in names if name.endswith(".py")}


def head(items: dict) -> str:
    revisions = {value[0] for value in items.values()}
    parents = {parent for value in items.values() for parent in value[1]}
    heads = revisions - parents
    if len(heads) != 1:
        raise ValueError("Release needs exactly one Alembic head")
    return next(iter(heads))


def env_keys(source: str) -> dict[str, str]:
    return {match[1]: match[2] for line in source.splitlines()
            if (match := re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line))}


def upgrade_notes(root: Path, release: str, plugin: str, previous: str | None) -> str:
    current = migrations(root)
    old = migrations(root, previous) if previous else {}
    before = head(old) if old else "none (first published release)"
    added = [current[name] for name in sorted(current.keys() - old.keys())]
    previous_env = env_keys(git(root, "show", f"{previous}:.env.example")) if previous else {}
    current_env = env_keys((root / ".env.example").read_text())
    changed = sorted(key for key in previous_env.keys() | current_env.keys()
                     if previous_env.get(key) != current_env.get(key))
    migration_lines = "\n".join(f"- `{row[0]}`: {row[2]}" for row in added) or "- None."
    config_lines = ", ".join(f"`{key}`" for key in changed) or "None."
    bootstrap = ("This is the first published release; no previous published-image upgrade "
                 "has been validated.\n\n") if not previous else ""
    return (
        f"# Mnemonic {release}\n\n## Upgrade notes\n\n{bootstrap}"
        f"- Previous release: {previous or 'none'}.\n"
        f"- Alembic head before: `{before}`.\n"
        f"- Alembic head after: `{head(current)}`.\n"
        f"- Compatible Claude plugin: `{plugin}` (distributed by this release).\n"
        "- Reconnect agent clients after upgrading to refresh their tool contracts.\n"
        "- Database downgrade is not a supported rollback procedure. Restore the "
        "pre-migration database backup and matching filesystem backup with the previous images.\n\n"
        "### Migrations added\n\n" + migration_lines + "\n\n"
        "### Configuration keys added, changed or removed\n\n" + config_lines + "\n\n"
        "Review `docs/operations.md` before upgrading. Existing installations must preserve "
        "their storage bindings with `compose.bind-mounts.yaml`; changing storage defaults "
        "does not move existing data. Migrations require a successful automatic backup "
        "unless the operator explicitly opts out.\n"
    )


def marketplace(root: Path, release: str, commit: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Marketplace requires the full release commit SHA")
    document = json.loads((root / ".claude-plugin/marketplace.json").read_text())
    for plugin in document["plugins"]:
        if plugin["name"] != "mnemonic":
            raise ValueError("Unexpected plugin in release marketplace")
        plugin["source"] = {"source": "git-subdir", "url": f"{REPOSITORY}.git",
                            "path": "plugin", "ref": f"v{release}", "sha": commit}
    return (json.dumps(document, indent=2) + "\n").encode()


def payload(root: Path) -> dict[str, bytes]:
    files = {}
    for name in INSTALL_FILES:
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Bundle inputs must be regular files: {name}")
        files[name] = path.read_bytes()
    for directory in ("deploy/nginx", "deploy/empty-transcripts", "docs"):
        for name in git(root, "ls-files", "--", directory).splitlines():
            if directory == "docs" and (not name.endswith(".md")
                                        or name.startswith("docs/images/")):
                continue
            if "/test/" not in name:
                path = root / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"Bundle inputs must be regular files: {name}")
                files[name] = path.read_bytes()
    files["setup.py"] = (
        b'"""Configure this extracted Mnemonic release."""\n'
        b"import runpy\nfrom pathlib import Path\n\n"
        b'runpy.run_path(str(Path(__file__).parent / "scripts/setup.py"), run_name="__main__")\n'
    )
    return files


def checksums(files: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name, data in sorted(files.items())).encode()


def archive(files: dict[str, bytes], release: str) -> bytes:
    result = io.BytesIO()
    with gzip.GzipFile(fileobj=result, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for name, data in sorted(files.items()):
                entry = tarfile.TarInfo(f"mnemonic-{release}/{name}")
                entry.size = len(data)
                entry.mode = 0o644
                tar.addfile(entry, io.BytesIO(data))
    return result.getvalue()


def build(root: Path, output: Path, previous: str | None = None) -> list[Path]:
    release = version(root)
    plugin = json.loads((root / "plugin/.claude-plugin/plugin.json").read_text())["version"]
    commit = git(root, "rev-parse", "HEAD")
    notes = upgrade_notes(root, release, plugin, previous).encode()
    compatibility = ("# Release compatibility\n\n| Server | Claude plugin | Source |\n"
                     "| --- | --- | --- |\n"
                     f"| {release} | {plugin} | `{commit}` |\n").encode()
    catalog = marketplace(root, release, commit)
    files = payload(root)
    files.update({"UPGRADE.md": notes, "COMPATIBILITY.md": compatibility,
                  "marketplace.json": catalog})
    files["SHA256SUMS"] = checksums(files)
    artifacts = {f"mnemonic-{release}.tar.gz": archive(files, release),
                 "marketplace.json": catalog, "UPGRADE.md": notes,
                 "COMPATIBILITY.md": compatibility}
    artifacts["SHA256SUMS"] = checksums(artifacts)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        (output / name).write_bytes(data)
    return [output / name for name in artifacts]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous-tag")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for path in build(root, args.output, args.previous_tag):
        print(path)


if __name__ == "__main__":
    main()
