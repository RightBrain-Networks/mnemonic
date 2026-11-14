# Mnemonic in Codex

When Mnemonic orientation is absent from the conversation, call `session_init()`.
Resolve the project if needed and read `get_project(project_id, settings_only=true)`.
Read `help(topic="COMMAND usage")` for the task and follow its detail links. The
optional `mnemonic-recall`, `mnemonic-save`, and `mnemonic-search` skills provide
shortcuts and local helpers. Use `client=codex` and the actual native thread ID.

For a fresh transcript declaration, verify this thread's exact native
`rollout-*.jsonl` beneath an operator-approved source root. Use the client-provided
thread ID (including `CODEX_THREAD_ID` when available) and confirm the initial
`session_meta.payload.id` matches. Verify a readable regular file without symlink
components. Never infer a repository-based path, choose the newest file, or use
`history.jsonl`. Verify each child's own rollout independently. Read the selected
skill's transcript reference when local verification is needed.

Use claim and closeout help for transcript arguments and explicit null when a
fresh path cannot be established; report that limitation. Preserve original
assertions and every argument on uncertain retries. Connection and orientation
do not enroll transcripts. Never import a mixed-project sessions directory into
one project. Use transcript command help to check copy/index coverage.
