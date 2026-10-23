# Prompt library

Mnemonic 0.49.0 provides a project prompt library at `/settings/prompts`.
Choose a prompt name to open the editor in a drawer on the right. The library
uses the transcript file-list pattern and shows each prompt’s UTF-8 size,
creation time, and last update. Copy copies the exact editable template;
Save writes the Markdown file for the selected project.

## Inventory

Each shipped task prompt has one source document in [`prompts/`](../prompts/):

| Prompt | Source | Used by |
| --- | --- | --- |
| Recall pointer | [recall-pointer.md](../prompts/recall-pointer.md) | Dashboard Copy recall pointer |
| Job completion report | [job-completion-report.md](../prompts/job-completion-report.md) | `get_prompt` before Done, Won’t do, or Promoted closeout |
| Cold code review | [cold-code-review.md](../prompts/cold-code-review.md) | Dashboard Cold review |
| Warm code review | [warm-code-review.md](../prompts/warm-code-review.md) | Dashboard review recall pointer |
| Review recommendation | [review-recommendation.md](../prompts/review-recommendation.md) | Optional review follow-up after completion |
| Review remediation | [review-remediation.md](../prompts/review-remediation.md) | Initial checkpoint for the one remediation created from a review |
| Decision impact review | [decision-impact-review.md](../prompts/decision-impact-review.md) | Initial checkpoint for automatic decision-change investigations |
| Resume work | [resume-work.md](../prompts/resume-work.md) | MCP `resume_work` prompt |
| Request human review | [request-human-review.md](../prompts/request-human-review.md) | `get_prompt` before creating or revising a Needs Attention question |

## Agent retrieval

In 0.83.0 the MCP tool `get_prompt` is the shared instruction read:

```json
{
  "project_id": "<project UUID>",
  "prompt_id": "request-human-review",
  "work_item_id": "<work UUID>"
}
```

`project_id` and `prompt_id` are required. Optional `work_item_id` provides work
macros; optional `code_review_id` selects an exact review episode and its scope.
Supplied IDs must belong to the project and to each other. The tool accepts the
nine library IDs above plus `priority-rubric`, which reads the existing rubric
in Settings > Workspace, preserving its PostgreSQL storage and customizations.
The former `get_priority_rubric` MCP tool is removed. Its human REST editor remains.

The response contains `project_id`, `prompt_id`, the requested `work_item_id`
and `code_review_id` (null when omitted), rendered `content`, the source-template
SHA-256 `revision`, and `prompt_revision`. Only `job-completion-report` returns
a nonnull `prompt_revision`: this is the decimal settings revision required by
closeout writes. Never submit the template hash as a report's revision.
Report retrieval synchronizes external file edits and returns matching text and
revision; stale fresh closeouts are rejected, while exact receipt retries retain
their original text and revision.

MCP `get_project_settings(project_id)` returns lease durations, code-review policy, and automatic decision-impact-review settings
without authored text. The existing REST settings representation continues to
serve dashboard editors. The MCP `resume_work` prompt uses the same fetch path as
`get_prompt`, then appends bounded retained work context. The underlying safe
read is `POST /projects/{project_id}/prompts/{prompt_id}/render`, with the optional
context IDs in its body and the response fields described above.

Skills route to current prompts and retain fixed coordination, schema, authority,
and retry rules. Writing style belongs in editable templates. Retrieving a prompt
does not claim work, create a question, or authorize execution. Needs Attention
questions are still submitted with `request_human_input`; `request-human-review`
names its authoring instructions, not a second gate mutation. Cold reviewers
must preserve their existing context boundary before findings freeze.

Upgrade API, MCP, dashboard, and plugin together (plugin 0.49.0). No new database
migration is required; the head stays `0051_priority_rubrics`. Existing project
templates are preserved. Review existing project templates, especially
`resume-work`, for references to the retired `get_priority_rubric` call or report
instructions in `get_project_settings`; update those references to `get_prompt`.
Changing a shipped default does not overwrite an already initialized template.
The new human-review file is initialized from the
shipped default on first access, including a library listing. Missing previously
registered files remain errors and are never silently replaced with defaults.

The inventory covers authored task instructions previously embedded in the
backend, dashboard, and MCP adapter. MCP tool schemas, tool descriptions,
projectless initialization instructions, validation errors, and sensitive-file
approval protocol describe fixed API behavior. They are protocol documentation,
not project task templates. Agent-authored checkpoints, questions, findings,
and follow-ups remain authored records rather than library templates.

## Files and deployment

The API reads shipped defaults from the source `prompts/` directory, also
included in its package and container image. A project receives its own files;
changing one project never edits another project or the shipped defaults.

`MNEMONIC_PROMPT_ROOT` is the native API storage path. Compose sets it to
`/var/lib/mnemonic/prompts` and mounts the host `MNEMONIC_PROMPT_DIR` there.
The default host location is `./prompts/runtime`. Create the bind directory
before starting the updated stack, using the API’s configured UID and GID:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./prompts/runtime
```

The directory contains:

- `projects/<project UUID>/<prompt ID>.md`: current editable templates.
- Hidden creation metadata and staging files managed by the storage layer.

An external editor can change project Markdown files directly. Use the same
filesystem owner as the API and write UTF-8 regular files; symlinks and hardlinks
are rejected. Coordinate external writes with the project’s `.lock` file using an exclusive
`flock`, then check the current content and atomically replace the complete file.
The API uses that same lock. Uncoordinated writers can race with any editor;
an atomic rename by itself prevents partial reads, but cannot prevent a lost update. Content hashes detect edits made outside Mnemonic,
including edits that do not change file size. Keep this directory in a private
Git repository if you want version history; Mnemonic does not create commits.

Back up the prompt directory together with the database. Database and project
archives do not include editable template files. Restore the matching files
alongside a database restore. Completed reports retain their immutable
authoring-prompt text and hash in PostgreSQL, so their history remains included
in database and project backups.

## Migration

Upgrade API, MCP, and dashboard together. Migration `0035_prompt_library`
exports existing customized recall pointers and report templates to files
before removing their editable database text columns. Historical report
authoring-prompt snapshots remain immutable database records.
Existing bytes are preserved. An export conflict or storage failure stops the
migration instead of discarding text. Keep the configured prompt volume mounted
while migrating, and include it in the upgrade backup. Older application
processes must not run against the new schema. A downgrade restores the two
legacy editable settings from their exact files and retains the files. Read current
settings first to synchronize any external edits. It refuses
to proceed if any expanded historical prompt exceeds the predecessor’s bounds;
restore an upgrade backup in that case.

Historical work checkpoints, review questions, and permanent operation receipts
retain their original authored outcomes. Changing a template affects future
rendering and newly created records; it does not rewrite prior work history.

## Macros

The Available macros glossary at the bottom of the page lists the current
supported names and their definitions. All templates use the same single-pass
renderer. Existing `$PROJECT_ID`, `$PROJECT_NAME`, `$PROJECT_SLUG`,
`$WORK_ITEM_ID`, `$WORK_ITEM_TITLE`, `$WORK_ITEM_SUMMARY`, `$WORK_ITEM_STATUS`,
and `$WORK_ITEM_PRIORITY` remain supported. The library also defines project
repository and description, work version, lease duration, review routing/scope,
and generated review context values.

Values are literal text: a macro inside a work title is not expanded again.
Unknown macros and context-specific macros whose data is unavailable remain
unchanged. To receive work macros, supply `work_item_id` to `get_prompt`. A
project-only prompt read has no selected work context. Review-generated values
such as finding checklists remain unavailable until their originating workflow
supplies them; the tool accepts IDs, not caller-authored macro overrides.

Cold review defaults expose only the pinned routing and repository scope.
Explicitly inserting authored work macros into a customized cold-review template
reveals that context to the reviewer. Review the template accordingly when you
intend to preserve a cold review.

## Concurrency and errors

Each drawer loads a SHA-256 revision and saves with that `expected_revision`.
A changed file returns `prompt_changed`; the drawer keeps the draft so the user
can review the latest file before attempting another save. A storage read or
write failure returns `prompt_unavailable`. Missing content must not become an
empty prompt.

Legacy settings PATCH requests save one template at a time; mixed template and
scalar settings changes are rejected. Use the individual prompt PUT endpoint
for template editing.

Report settings retain their existing decimal revision contract. External report
template changes invalidate stale fresh closeouts. Exact retries of a permanent
receipt still replay the original result before current prompt checks. Retain
all report fields, the revision, and operation UUID unchanged across uncertain
closeout outcomes.

## Automatic decision impact reviews (0.89.0)

The `decision-impact-review` template is initialized for existing and new projects on first
access. Exact untouched copies of the 0.89 and 0.90 shipped templates are refreshed to
the improved default on their next read, under the existing prompt lock. Customized
bytes, including whitespace changes, are preserved. Its creation time remains;
the new content has a new SHA-256 revision, so stale saves must reload.
Editing it in Prompts changes future automatically created investigations.
Each generated work item retains its rendered template, source hash, and exact
decision/assessment references in the initial checkpoint. Later template edits do
not rewrite existing investigations. The default instructs agents to investigate
without changing code and to create separate standard work items for remediation
or further investigation. It classifies rule/scope/reference changes before
candidate review, requires dispositions for incoming related citations of unknown
causality, preserves historical assessment requirements, separates required verdicts
from optional contextual contradiction notes, and gives fallback repository searches
for missing commits.
See [automatic decision impact reviews](knowledge-graph.md#automatic-decision-impact-reviews).
