"""Canonical identities for explicit GitHub locators; unknown values stay unknown."""

import re

_GITHUB = re.compile(
    r"^(?:(?:https?://)?github\.com/)?"
    r"(?P<owner>[a-z0-9][a-z0-9-]{0,38})/(?P<name>[a-z0-9_.-]{1,100})/?$",
    re.IGNORECASE | re.ASCII,
)


def _github_slug(value: str) -> str | None:
    if (match := _GITHUB.fullmatch(value)) is None:
        return None
    # Repeated clone suffixes are ambiguous; keep them exact and idempotent.
    raw_name = match["name"].lower()
    name = raw_name.removesuffix(".git")
    if not name or name in {".", ".."} or raw_name.endswith(".git.git"):
        return None
    return f"{match['owner'].lower()}/{name}"


def canonical_repository(value: str | None) -> str | None:
    if value is None or (slug := _github_slug(value)) is None:
        return value
    return f"https://github.com/{slug}"


def repository_aliases(value: str) -> set[str]:
    if (slug := _github_slug(value)) is None:
        return {value}
    return {
        prefix + slug + suffix
        for prefix in ("", "github.com/", "https://github.com/", "http://github.com/")
        for suffix in ("", "/", ".git", ".git/")
    }


def canonical_provenance(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {
        **value,
        "commits": [
            {**commit, "repository": canonical_repository(commit["repository"])}
            for commit in value.get("commits", [])
        ],
    }
