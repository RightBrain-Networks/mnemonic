"""Select published baselines and manage draft releases without editing repository history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

REPOSITORY = "RightBrain-Networks/mnemonic"
TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True).strip()


def stable_releases() -> list[dict]:
    pages = json.loads(gh("api", f"repos/{REPOSITORY}/releases?per_page=100",
                         "--paginate", "--slurp"))
    return [release for page in pages for release in page
            if not release["draft"] and not release["prerelease"]
            and TAG.fullmatch(release["tag_name"])]


def version_tuple(tag: str) -> tuple[int, ...]:
    if not TAG.fullmatch(tag):
        raise ValueError("Expected a stable vMAJOR.MINOR.PATCH tag")
    return tuple(map(int, tag[1:].split(".")))


def previous(releases: list[dict], tag: str, release: bool) -> str:
    target = version_tuple(tag)
    newer = [r for r in releases if version_tuple(r["tag_name"]) >= target]
    if release and newer:
        raise ValueError("Release version must be newer than every published stable release")
    candidates = [r for r in releases if version_tuple(r["tag_name"]) <= target]
    return max((r["tag_name"] for r in candidates), key=version_tuple, default="")


def select(tag: str, release: bool) -> None:
    prior = previous(stable_releases(), tag, release)
    outputs = {"version": tag[1:], "previous_tag": prior,
               "previous_version": prior[1:] if prior else ""}
    if destination := os.environ.get("GITHUB_OUTPUT"):
        with Path(destination).open("a") as output:
            for key, value in outputs.items():
                output.write(f"{key}={value}\n")
    print(json.dumps(outputs))
    if not prior:
        message = "First published release: no prior stable-image upgrade can be validated."
        print(message)
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a") as output:
                output.write(message + "\n")


def verify_ci(commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full commit SHA")
    pages = json.loads(gh("api", f"repos/{REPOSITORY}/commits/{commit}/check-runs?per_page=100",
                         "--paginate", "--slurp"))
    checks = [check for page in pages for check in page["check_runs"]
              if check["name"] == "Required checks" and check["head_sha"] == commit
              and check["app"]["slug"] == "github-actions"]
    if not checks or max(checks, key=lambda check: check["id"])["conclusion"] != "success":
        raise ValueError("The release commit has not passed Required checks")


def draft(tag: str, directory: Path, previous_tag: str | None) -> None:
    version_tuple(tag)
    parameters = ["-f", f"tag_name={tag}"]
    if previous_tag:
        version_tuple(previous_tag)
        parameters += ["-f", f"previous_tag_name={previous_tag}"]
    generated = json.loads(gh("api", f"repos/{REPOSITORY}/releases/generate-notes",
                             "--method", "POST", *parameters))
    notes = directory / "RELEASE_NOTES.md"
    notes.write_text((directory / "UPGRADE.md").read_text() + "\n" + generated["body"] + "\n")
    assets = sorted(path for path in directory.iterdir() if path.is_file()
                    and path.name != "SHA256SUMS")
    sums = directory / "SHA256SUMS"
    sums.write_text("".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                            for path in assets))
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", REPOSITORY,
                               "--json", "isDraft"], capture_output=True, text=True)
    if existing.returncode == 0:
        if not json.loads(existing.stdout)["isDraft"]:
            raise ValueError("Refusing to replace a published release")
        gh("release", "edit", tag, "--repo", REPOSITORY, "--notes-file", str(notes))
    else:
        if "release not found" not in existing.stderr.lower():
            raise RuntimeError("Could not establish whether the release already exists")
        gh("release", "create", tag, "--repo", REPOSITORY, "--verify-tag", "--draft",
           "--title", f"Mnemonic {tag[1:]}", "--notes-file", str(notes))
    gh("release", "upload", tag, "--repo", REPOSITORY, "--clobber",
       *map(str, [*assets, sums]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    select_parser = sub.add_parser("select")
    select_parser.add_argument("--tag", required=True)
    select_parser.add_argument("--release", action="store_true")
    verify = sub.add_parser("verify-ci")
    verify.add_argument("--commit", required=True)
    draft_parser = sub.add_parser("draft")
    draft_parser.add_argument("--tag", required=True)
    draft_parser.add_argument("--directory", required=True, type=Path)
    draft_parser.add_argument("--previous-tag")
    args = vars(parser.parse_args())
    command = args.pop("command")
    {"select": select, "verify-ci": verify_ci, "draft": draft}[command](**args)


if __name__ == "__main__":
    main()
