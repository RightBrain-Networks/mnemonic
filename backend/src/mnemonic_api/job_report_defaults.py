"""Frozen Phase 12 migration data, independent of editable current prompt templates."""

from pathlib import Path

DEFAULT_JOB_COMPLETION_REPORT_PROMPT = (
    Path(__file__).parent / "migration_data" / "0021-job-completion-report.md"
).read_text(encoding="utf-8")
