"""Export relocatable Mnemonic skills without modifying client configuration."""

from __future__ import annotations

import argparse
import posixpath
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugin"
PLUGIN_RESOURCE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
PORTABLE_PATH_GUIDANCE = """\
Resolve resource paths relative to the file containing the link, never the
repository working directory. Before running the bundled helper, resolve
`bin/mnemonic-repository-freshness` against the directory containing this loaded
`SKILL.md` and pass its absolute path as one quoted command argument. Keep the
working directory at the user-selected repository. Relative helper paths shown
in examples are resource locators to resolve first. This bundle requires no
client-specific path or session-variable expansion.

"""


def _render_markdown(source: Path, relative: Path) -> bytes:
    content = source.read_text(encoding="utf-8")
    content = PLUGIN_RESOURCE.sub(
        lambda match: posixpath.relpath(match[1], relative.parent.as_posix()), content
    )
    if "${CLAUDE_PLUGIN_ROOT}" in content:
        raise ValueError(f"Unsupported plugin resource in {source.name}")
    if relative.name == "SKILL.md":
        frontmatter, separator, body = content.partition("\n---\n")
        if not content.startswith("---\n") or not separator:
            raise ValueError(f"Missing skill frontmatter in {source}")
        content = frontmatter + separator + "\n" + PORTABLE_PATH_GUIDANCE + body.lstrip("\n")
    return content.encode("utf-8")


def _payload(plugin_root: Path) -> dict[Path, tuple[bytes, int]]:
    payload: dict[Path, tuple[bytes, int]] = {}
    resources = sorted((plugin_root / "reference").glob("*.md"))
    helper = plugin_root / "bin" / "mnemonic-repository-freshness"
    for skill in sorted((plugin_root / "skills").glob("*/SKILL.md")):
        for source in (skill, *resources, helper):
            relative = Path("SKILL.md") if source == skill else source.relative_to(plugin_root)
            content = (
                _render_markdown(source, relative)
                if source.suffix == ".md"
                else source.read_bytes()
            )
            payload[Path(skill.parent.name) / relative] = (content, source.stat().st_mode & 0o777)
    if not payload:
        raise ValueError("No Mnemonic skills found")
    return payload


def export_skills(destination: Path, *, plugin_root: Path = PLUGIN_ROOT) -> Path:
    """Create a new export directory; reject existing targets and symlink ancestors."""
    destination = destination.absolute()
    for ancestor in (destination, *destination.parents):
        if ancestor.is_symlink():
            raise ValueError(f"Export paths must not contain symlinks: {ancestor}")
    if destination.exists():
        raise FileExistsError(f"Export destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise ValueError(f"Export parent directory must already exist: {destination.parent}")
    destination = destination.resolve()
    payload = _payload(plugin_root)
    destination.mkdir()
    for relative, (content, mode) in payload.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(content)
        target.chmod(mode)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new output directory in an existing parent")
    arguments = parser.parse_args()
    try:
        destination = export_skills(arguments.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Skill export failed: {error}\n")
    print(f"Exported Mnemonic skills to {destination}")


if __name__ == "__main__":
    main()
