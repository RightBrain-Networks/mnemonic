---
name: mnemonic-recall
description: Retrieve selected Mnemonic work or continue an authorized work item, code review, or decision impact review.
---

# Recall Mnemonic work

If the current conversation lacks a `session_init` response, call `session_init()`
and follow its guidance. For a newly selected project, read
`get_project(project_id, settings_only=true)` for its current configuration.

Start with `help(topic="recall_work usage")`. For execution or a code review, read
`help(topic="claim_work usage")` before loading work context; follow the selected
mode's command help through closeout.

When local mechanics apply, read only the relevant resource:
[native transcript verification](${CLAUDE_PLUGIN_ROOT}/reference/transcripts.md),
[repository check helper](${CLAUDE_PLUGIN_ROOT}/reference/repository-freshness.md),
or [decision provenance helper](${CLAUDE_PLUGIN_ROOT}/reference/knowledge-graph.md).
Resolve linked resources from this installed skill, not the repository directory.
