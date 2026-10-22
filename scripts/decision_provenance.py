"""Prepare decision provenance from locally verified Git commits; never send or fetch."""

import argparse
import json
import re
import subprocess
from pathlib import Path


def commit_id(directory: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--verify", "--end-of-options",
         f"{revision}^{{commit}}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", result):
        raise ValueError("Git did not return one full commit identity")
    return result


def citation(reference: str) -> dict:
    match = re.fullmatch(r"([A-Z]{1,5}-[0-9]{5,19})-([0-9]{1,10})", reference)
    if not match or int(match[2]) > 2_147_483_647:
        raise ValueError("Citations require an exact reference such as ADR-00194-0")
    return {"decision_id": match[1], "revision": int(match[2])}


def identity(value: str) -> str:
    if (not value or len(value) > 500 or "?" in value or "#" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or ("://" in value and "@" in value.split("://", 1)[1])):
        raise ValueError("Use a stable repository identity without credentials or URL parameters")
    return value


def prepare(arguments: argparse.Namespace) -> dict:
    repository = identity(arguments.repository)
    if arguments.original is not None:
        if not arguments.merged or arguments.decision or arguments.commit:
            raise ValueError("Reconciliation requires --original and --merged only")
        return {
            "repository": repository,
            "original_commit_hash": commit_id(arguments.directory, arguments.original),
            "merged_commit_hash": commit_id(arguments.directory, arguments.merged),
        }
    if arguments.merged or not arguments.decision or not arguments.commit:
        raise ValueError("Supply --decision and --commit for a cited completion declaration")
    citations = [citation(reference) for reference in arguments.decision]
    commits = [{"repository": repository, "commit_hash": commit_id(arguments.directory, ref)}
               for ref in arguments.commit]
    if len(citations) > 100 or len(commits) > 100 or len(citations) * len(commits) > 500:
        raise ValueError("A declaration supports at most 100 decisions, 100 commits, and 500 pairs")
    if (len({json.dumps(item, sort_keys=True) for item in citations}) != len(citations)
        or len({item["commit_hash"] for item in commits}) != len(commits)):
        raise ValueError("Duplicate citations or commits are not allowed")
    return {"disposition": "cited", "reason": None, "citations": citations, "commits": commits}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument("--repository", required=True, help="stable credential-free repository ID")
    parser.add_argument("--decision", action="append", default=[], help="exact revision consulted")
    parser.add_argument("--commit", action="append", default=[], help="local revision, e.g. HEAD")
    parser.add_argument("--original", help="original local commit for merge reconciliation")
    parser.add_argument("--merged", help="final local merged commit")
    arguments = parser.parse_args()
    try:
        result = prepare(arguments)
    except (ValueError, OSError, subprocess.CalledProcessError):
        parser.exit(1, "Unable to prepare provenance: check arguments and local commit availability.\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
