# Project priority rubric

Call `get_prompt(project_id, prompt_id="priority-rubric")` when you need guidance for choosing,
explaining, or reassessing a priority. The current Markdown is maintained by
humans in Settings > Workspace > Priority rubric. It is separate from
`get_project_settings` and ordinary work reads to keep those responses compact.

Honor an explicit user score. Otherwise use the returned project guidance and
record a brief rationale in the checkpoint. This read grants no authority to
execute work or reprioritize the backlog. Freeze the selected score before a
protected write; uncertain retries preserve the original intent and operation ID.

Do not substitute a bundled rubric if this read fails: report the unavailable
guidance rather than silently scoring against stale defaults.

See [prompts.md](${CLAUDE_PLUGIN_ROOT}/reference/prompts.md) for optional work
context and the shared macro and retrieval contract.
