"""Executable expected-error checks for completion-evidence model constructors."""

import subprocess
import sys
from pathlib import Path


def test_completion_evidence_models_statically_reject_extra_fields() -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture = project_root / "tests" / "typecheck" / "completion_evidence_extra_fields.py"
    ty = Path(sys.executable).with_name("ty")

    completed = subprocess.run(
        [
            ty,
            "check",
            "--project",
            project_root,
            "--error-on-warning",
            "--output-format",
            "concise",
            fixture,
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
