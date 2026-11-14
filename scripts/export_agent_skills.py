"""Export relocatable Mnemonic skills without modifying client configuration."""

from __future__ import annotations

import argparse
import posixpath
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugin"
PLUGIN_RESOURCE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_./-]+)")
MARKDOWN_LINK = re.compile(r"(\[[^\]]+\]\()([^\s)]+)(\))")


def _local_resource(source: Path, link: str, plugin_root: Path) -> Path | None:
    target = link.split("#", 1)[0]
    if not target or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
        return None
    prefix = "${CLAUDE_PLUGIN_ROOT}/"
    candidate = (plugin_root / target[len(prefix):] if target.startswith(prefix)
                 else source.parent / target)
    if any(path.is_symlink() for path in (candidate, *candidate.parents)):
        raise ValueError(f"Plugin resource must not contain symlinks: {link}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(plugin_root) or not resolved.is_file():
        raise ValueError(f"Plugin resource must be a file inside the plugin: {link}")
    return resolved


def _linked_resources(source: Path, plugin_root: Path) -> set[Path]:
    if source.suffix != ".md":
        return set()
    content = source.read_text(encoding="utf-8")
    links = [match[0] for match in PLUGIN_RESOURCE.finditer(content)]
    links.extend(match[2] for match in MARKDOWN_LINK.finditer(content))
    return {
        resource for link in links
        if (resource := _local_resource(source, link, plugin_root)) is not None
    }


def _render_markdown(source: Path, relative: Path, plugin_root: Path) -> bytes:
    content = source.read_text(encoding="utf-8")

    def render_link(match: re.Match[str]) -> str:
        resource = _local_resource(source, match[2], plugin_root)
        if resource is None:
            return match[0]
        path = posixpath.relpath(resource.relative_to(plugin_root), relative.parent.as_posix())
        _, marker, anchor = match[2].partition("#")
        return match[1] + path + marker + anchor + match[3]

    content = MARKDOWN_LINK.sub(render_link, content)
    content = PLUGIN_RESOURCE.sub(
        lambda match: posixpath.relpath(match[1], relative.parent.as_posix()), content
    )
    if "${CLAUDE_PLUGIN_ROOT}" in content:
        raise ValueError(f"Unsupported plugin resource in {source.name}")
    if relative.name == "SKILL.md" and (
        not content.startswith("---\n") or "\n---\n" not in content
    ):
        raise ValueError(f"Missing skill frontmatter in {source}")
    return content.encode("utf-8")


def _payload(plugin_root: Path) -> dict[Path, tuple[bytes, int]]:
    payload: dict[Path, tuple[bytes, int]] = {}
    for skill in sorted((plugin_root / "skills").glob("*/SKILL.md")):
        pending, visited = [skill], set()
        while pending:
            source = pending.pop()
            if source in visited:
                continue
            _local_resource(
                source, "${CLAUDE_PLUGIN_ROOT}/" + source.relative_to(plugin_root).as_posix(),
                plugin_root,
            )
            visited.add(source)
            pending.extend(sorted(_linked_resources(source, plugin_root) - visited))
            relative = Path("SKILL.md") if source == skill else source.relative_to(plugin_root)
            content = (
                _render_markdown(source, relative, plugin_root)
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
    payload = _payload(plugin_root.resolve())
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
