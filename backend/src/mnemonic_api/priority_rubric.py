"""The shipped seed for project-owned priority guidance."""

from pathlib import Path

DEFAULT_PRIORITY_RUBRIC = Path(__file__).with_suffix(".md").read_bytes().decode("utf-8")
PRIORITY_RUBRIC_DEFAULT_SQL = "'" + DEFAULT_PRIORITY_RUBRIC.replace("'", "''") + "'"
PRIORITY_RUBRIC_CHECK = (
    "length(priority_rubric) BETWEEN 1 AND 100000 "
    "AND mnemonic_has_non_whitespace(priority_rubric)"
)
