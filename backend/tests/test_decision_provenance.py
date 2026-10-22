"""Declaration validation and the offline helper produce precise commit citations."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from mnemonic_api.provenance_schemas import DecisionProvenance


@pytest.mark.parametrize("payload", [
    {"disposition": "cited"},
    {"disposition": "no-applicable-decisions", "reason": " "},
    {"disposition": "not-code", "reason": "Research", "commits": [
        {"repository": "example/repo", "commit_hash": "a" * 40}]},
    {"disposition": "cited", "citations": [{"decision_id": "ADR-00001", "revision": True}],
     "commits": [{"repository": "example/repo", "commit_hash": "a" * 40}]},
])
def test_invalid_declarations_are_rejected(payload):
    with pytest.raises(ValidationError):
        DecisionProvenance.model_validate(payload)


def test_local_helper_resolves_commits_and_preserves_repository(tmp_path):
    def git(*arguments):
        return subprocess.run(["git", "-C", str(tmp_path), *arguments], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit",
        "--allow-empty", "-m", "Initial")
    script = Path(__file__).resolve().parents[2] / "scripts/decision_provenance.py"
    arguments = [sys.executable, str(script), "--directory", str(tmp_path),
                 "--repository", "github.com/example/repo", "--decision", "ADR-00194-0",
                 "--commit", "HEAD"]
    result = subprocess.run(arguments, capture_output=True, text=True, check=True)
    declaration = DecisionProvenance.model_validate(json.loads(result.stdout))
    assert declaration.commits[0].commit_hash == git("rev-parse", "HEAD")
    assert declaration.citations[0].revision == 0
    arguments[-1] = "missing-commit"
    assert subprocess.run(arguments, capture_output=True).returncode == 1
