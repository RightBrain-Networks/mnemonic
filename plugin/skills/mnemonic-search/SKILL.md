---
name: mnemonic-search
description: Find Mnemonic work, artifacts, transcripts, decisions, or bounded project activity updates.
---

# Search Mnemonic

If the current conversation lacks a `session_init` response, call `session_init()`
and follow its guidance. For a newly selected project, read
`get_project(project_id, settings_only=true)` for its current configuration.

Read `help(topic="search usage")` for discovery, or
`help(topic="query_activity usage")` for bounded status updates. Follow that help
to choose filters, page results, and inspect coverage. Use the selected read
command's help when opening a result.

For a local artifact download, read the
[download helper guide](${CLAUDE_PLUGIN_ROOT}/reference/artifacts.md).
[Native transcript verification](${CLAUDE_PLUGIN_ROOT}/reference/transcripts.md)
applies when a later operation requests a local session path.
Resolve resources from this installed skill, not the repository directory.
