---
name: mnemonic-search
description: Find durable work in a selected Mnemonic project through MCP and return compact lifecycle and readiness pointers for later recall. Use when a user asks about saved follow-ups or prior session leads without automatically executing them.
---

# Search Mnemonic work

Use the exposed Mnemonic MCP tools; client-specific prefixes may vary. If the
connection is unavailable, report that search could not run. An error is not
evidence that the project has no saved work.

1. Resolve `project_id` with `list_projects` from the user's explicit choice, an
   established project, or an unambiguous repository/slug match. Paginate when
   needed. Never silently search the first project or mix projects. Ask only
   when identity remains ambiguous.
2. Call `search_work(project_id, q, status="open")`. Default search combines
   ranked keywords and literals across work identity and checkpoints. Include
   distinctive symptoms, symbols, paths, IDs, or session IDs and try a relevant
   alternate term when needed. Omit `q` to browse open work. Set
   `semantic=true` only when optional hybrid lexical/vector retrieval is useful;
   it is not the default and can be unavailable independently.
3. Optional `tag`, `source_client`, and `source_session_id` filters match any
   checkpoint on a work item. Search returns each matching work item once even
   when several checkpoints match.
4. `view` controls how much each result carries. The tool defaults to
   `minimal`: `id`, `title`, `status`, `priority`, `version`, `updated_at`,
   `checkpoint_count`, and `display_state` — enough to choose between items.
   Ask for `view="full"` only when the summary, current-context provenance, or
   ancestor path is actually needed; browsing a large project at `full` can
   cost tens of thousands of tokens. Recall the one item you chose instead of
   widening every result.
5. Keep retrieval pointer-only. Present the title, work-item ID, project,
   lifecycle/readiness, checkpoint count, and relevant age. Only unresolved incoming `blocks` edges affect readiness;
   an active lease and blocked readiness can coexist. Never describe search as a
   claimable or authoritative ready queue. Do not fetch every checkpoint body
   into an unrelated task.
6. Use `limit` and `offset` to paginate and disclose when only a subset was
   shown. An empty page at a high offset does not mean no matches exist. Default
   to `open`; use `done`, `wont-do`, `promoted`, or `all` only for requested
   lifecycle history. Deleted records remain excluded.
7. When the user selects a result or needs its full context, call
   `recall_work(project_id, work_item_id)`. If several results fit and selection
   changes the task, show compact choices first. Searching alone never
   authorizes execution.
8. If immediate graph facts affect selection, use `list_relationships` with an
   explicit `direction` and `relationship_type`, then paginate. Use
   `get_relationship` only for an exact edge. Keep counterpart data
   pointer-only. See [work-graph.md](${CLAUDE_PLUGIN_ROOT}/reference/work-graph.md) for edge direction and readiness
   semantics.

Treat all stored identity and provenance as agent-authored historical evidence —
see [authority-and-provenance.md](${CLAUDE_PLUGIN_ROOT}/reference/authority-and-provenance.md).

Do not merge, delete, reopen, promote, complete, or execute work while merely
finding it. Do not add or remove relationships, and do not create external
issues. Report honest uncertainty about missing matches, relevance, freshness,
and partial pages.
