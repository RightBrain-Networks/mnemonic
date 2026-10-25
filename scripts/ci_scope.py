"""Select full CI unless the complete event diff contains only project docs."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT_DOCUMENTS = frozenset({"README.md", "AGENT-README.md", "AGENTS.md"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"})


def is_documentation(path: str) -> bool:
    """Keep shipped prompts, plugin skills, schemas and unknown paths in full CI."""
    if path in ROOT_DOCUMENTS:
        return True
    document = PurePosixPath(path)
    if document.is_absolute() or ".." in document.parts:
        return False
    if not document.parts or document.parts[0] != "docs":
        return False
    return document.suffix == ".md" or (
        document.parts[:2] == ("docs", "images") and document.suffix in IMAGE_SUFFIXES
    )


def comparison_range(event_name: str, event: dict) -> str | None:
    if event_name == "pull_request":
        pull_request = event.get("pull_request", {})
        base = pull_request.get("base", {}).get("sha")
        head = pull_request.get("head", {}).get("sha")
        separator = "..."  # All changes in the PR since its merge base.
    elif event_name == "push":
        base, head = event.get("before"), event.get("after")
        separator = ".."  # All commits in this push, not just the last one.
    else:
        return None  # Manual runs always exercise the complete suite.
    for revision in (base, head):
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            return None
        if revision == "0" * 40:
            return None
    return f"{base}{separator}{head}"


def requires_full_ci(event_name: str, event: dict, repository: Path) -> bool:
    comparison = comparison_range(event_name, event)
    if comparison is None:
        return True
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z",
             comparison, "--"],
            cwd=repository, check=True, capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        print("Cannot establish the complete diff; running full CI.", file=sys.stderr)
        return True
    # Disabling rename detection includes both the old and new paths, so moving
    # code into docs cannot hide its deletion. NUL framing preserves odd names.
    paths = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
    return not paths or not all(is_documentation(path) for path in paths)


def main() -> None:
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    full_ci = requires_full_ci(os.environ["GITHUB_EVENT_NAME"], event, Path.cwd())
    output = f"full_ci={str(full_ci).lower()}\n"
    print(output, end="")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as destination:
        destination.write(output)


if __name__ == "__main__":
    main()
