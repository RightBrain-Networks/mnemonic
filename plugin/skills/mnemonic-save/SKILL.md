---
name: mnemonic-save
description: Save requested Mnemonic work, progress, artifacts, decisions, or a question for a person.
---

# Save Mnemonic records

If the current conversation lacks a `session_init` response, call `session_init()`
and follow its guidance. For a newly selected project, read
`get_project(project_id, settings_only=true)` for its current configuration.

Read `help(topic="create_work usage")`, or the chosen write command's `usage` help,
before preparing its arguments. Use command help for checkpoints, decisions,
human questions, and closeouts rather than inferring a workflow from this skill.

For local files or evidence, read the relevant
[artifact transfer](${CLAUDE_PLUGIN_ROOT}/reference/artifact-transfers.md),
[native transcript](${CLAUDE_PLUGIN_ROOT}/reference/transcripts.md), or
[decision provenance](${CLAUDE_PLUGIN_ROOT}/reference/knowledge-graph.md) resource.
Resolve linked resources from this installed skill, not the repository directory.
