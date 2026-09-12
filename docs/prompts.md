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
| Job completion report | [job-completion-report.md](../prompts/job-completion-report.md) | Settings reads before Done, Won’t do, or Promoted closeout |
| Cold code review | [cold-code-review.md](../prompts/cold-code-review.md) | Dashboard Cold review |
| Warm code review | [warm-code-review.md](../prompts/warm-code-review.md) | Dashboard review recall pointer |
| Review recommendation | [review-recommendation.md](../prompts/review-recommendation.md) | Optional review follow-up after completion |
| Review remediation | [review-remediation.md](../prompts/review-remediation.md) | Initial checkpoint for the one remediation created from a review |
| Resume work | [resume-work.md](../prompts/resume-work.md) | MCP `resume_work` prompt |

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
unchanged. To receive work macros in the report prompt, call
`get_project_settings(project_id, work_item_id)` or add `work_item_id` to the
REST settings query. Project-only settings reads have no selected work context.

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
