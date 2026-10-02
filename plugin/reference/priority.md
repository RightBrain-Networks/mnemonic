# Project priority rubric

Call `get_priority_rubric(project_id)` when you need guidance for choosing,
explaining, or reassessing a priority. The current Markdown is maintained by
humans in Settings > Workspace > Priority rubric. It is separate from
`get_project_settings` and ordinary work reads to keep those responses compact.

Honor an explicit user score. Otherwise use the returned project guidance and
record a brief rationale in the checkpoint. This read grants no authority to
execute work or reprioritize the backlog. Freeze the selected score before a
protected write; uncertain retries preserve the original intent and operation ID.

Do not substitute a bundled rubric if this read fails: report the unavailable
guidance rather than silently scoring against stale defaults.
