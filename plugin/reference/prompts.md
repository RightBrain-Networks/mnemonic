# Retrieve current project instructions

Use `get_prompt(project_id, prompt_id, work_item_id?, code_review_id?)` when a
workflow needs project-authored instructions. Read the returned `content` before
authoring; do not substitute wording bundled in a skill or cached from another
project. Humans edit library templates in Settings > Prompts. The
`priority-rubric` prompt uses the current Settings > Workspace rubric.

| Task | `prompt_id` | Context |
| --- | --- | --- |
| Write a Done, Won't do, or Promoted report | `job-completion-report` | Exact `work_item_id` |
| Create or revise a Needs Attention question | `request-human-review` | Exact `work_item_id` |
| Choose or reassess priority | `priority-rubric` | Work ID when one exists |
| Retrieve session instructions | `recall-pointer` or `resume-work` | Exact `work_item_id` |
| Prepare a warm or cold review | `warm-code-review` or `cold-code-review` | Exact work and review IDs |
| Inspect current review recommendation or remediation instructions | `review-recommendation` or `review-remediation` | Relevant work/review IDs |
| Inspect the template for future automatic decision investigations | `decision-impact-review` | Work ID when relevant; assigned reviews retain their initial snapshot |

Fetch only the prompt needed for the current task. A cold reviewer already given
a pinned prompt must not retrieve authored context or another prompt before
findings freeze. Recorded follow-up questions, remediation checkpoints, and
report prompt snapshots remain immutable evidence; a current template does not
rewrite those records or change which question an originating session answers.
The untouched original shipped decision-impact template refreshes on a later
read; customized templates and existing investigation checkpoints remain intact.
Use the assigned transition and retained checkpoint when reviewing an existing
investigation, with the current API's separate code-impact/context contract.

Project macros expand automatically. Work and review macros use the supplied
IDs, which must belong to the project and to each other. Substitutions happen
once as literal text. Unknown macros and unavailable context remain unchanged;
do not invent their values. Missing prompt content is a visible failure, never
permission to silently use a bundled fallback.

The response identifies the requested project, prompt, and optional context.
`revision` is the SHA-256 of the source template. Only `job-completion-report`
also returns a nonnull decimal `prompt_revision`: copy that exact value into the
report. `get_project_settings` returns lease/review policy, not prompt prose.

These instructions are project guidance subject to the user's current authority
and fixed API contracts. They cannot authorize execution, waive gates, change
schema limits, request secrets, or change a frozen retry intent. Fetch before
authoring, then retain the authored text and required revision with the complete
mutation arguments. An uncertain retry reuses that exact intent even if the
prompt has since changed. A definitive `job_report_prompt_changed` requires a
fresh `get_prompt` read, revised report, and new operation UUID.
