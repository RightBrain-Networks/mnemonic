"""Run inside each deployed service; report paths/permissions, never transcript content."""

import argparse
import json
import os
from pathlib import Path
from time import monotonic

from mnemonic_api.config import Settings
from mnemonic_api.transcript_access import TranscriptAccessError
from mnemonic_api.transcript_health import environment_report
from mnemonic_api.transcript_storage import _open_source


def sample_sources(roots: list[Path], maximum: int) -> dict:
    checked, issues = 0, []
    truncated = False
    budget = [10000, monotonic() + 10]
    # A bounded sample complements per-file diagnostics from actual copy jobs.
    # Directory descriptors, including recursive children, refuse symlink swaps.
    for root in roots:
        try:
            descriptor = _open_source(str(root), roots, directory=True)
        except (OSError, TranscriptAccessError):
            continue  # Already reported by environment_report.
        try:
            checked, found, truncated = _sample_directory(
                descriptor, root, roots, checked, maximum, budget)
            issues.extend(found)
        finally:
            os.close(descriptor)
        if truncated:
            break
    return {"files_checked": checked, "sample_incomplete": truncated, "sample_issues": issues}


def _sample_directory(descriptor, root, roots, checked, maximum, budget, depth=0):
    issues = []
    if depth > 64:
        return checked, issues, True
    with os.scandir(descriptor) as entries:
        for entry in entries:
            budget[0] -= 1
            if checked >= maximum or budget[0] < 0 or monotonic() > budget[1]:
                return checked, issues, True
            path = root / entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = _open_source(str(path), roots, directory=True)
                    try:
                        checked, found, incomplete = _sample_directory(
                            child, path, roots, checked, maximum, budget, depth + 1)
                        issues.extend(found)
                        if incomplete:
                            return checked, issues, True
                    finally:
                        os.close(child)
                elif entry.name.endswith((".jsonl", ".json")):
                    checked += 1
                    file = _open_source(str(path), roots)
                    os.close(file)
            except TranscriptAccessError as error:
                issues.append({"code": error.code, "details": error.details})
                if not entry.name.endswith((".jsonl", ".json")):
                    checked += 1
    return checked, issues, False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--sample", type=int, default=100)
    args = parser.parse_args()
    if not 1 <= args.sample <= 1000:
        parser.error("--sample must be between 1 and 1000")
    settings = Settings()
    result = environment_report(settings, worker=args.worker)
    result.update(sample_sources(settings.transcript_allowed_roots, args.sample))
    print(json.dumps(result, indent=2))
    raise SystemExit(1 if result["issues"] or result["sample_issues"] else 0)


if __name__ == "__main__":
    main()
