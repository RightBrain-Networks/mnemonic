---
name: mnemonic-search
description: Retrieve relevant durable work or discover currently ready work in a selected Mnemonic project through compact MCP pointers. Use when a user asks about saved follow-ups or actionable candidates without automatically executing them.
---

# Search Mnemonic work

Use the exposed Mnemonic MCP tools; client-specific prefixes may vary. If the
connection is unavailable, report that search could not run. An error is not
evidence that the project has no saved work.

1. Resolve `project_id` with `list_projects` from the user's explicit choice, an
   established project, or an unambiguous repository/slug match. Paginate when
   needed. Never silently choose the first project or mix projects.
2. Choose the surface from the question. `search_work` retrieves work relevant
   to terms or concepts. `list_ready_work` discovers work that appears
   actionable now. Search is not a ready-queue preset, and ready listing is not
   relevance ranking.
3. For retrieval, call `search_work(project_id, q, status="pending")`. Include
   distinctive symptoms, symbols, paths, IDs, or session IDs and try a relevant
   alternate term. Omit `q` to browse. Optional `tag`, `source_client`, and
   `source_session_id` match any checkpoint. Use `semantic=true` only when
   optional hybrid lexical/vector retrieval is useful.
4. For actionable candidates, call `list_ready_work` with only the needed
   `min_priority`, exact normalized `tag`, or direct `parent_work_item_id`
   filters. Results are ordered by priority descending, creation time ascending,
   then ID. They are compact pointers to visible Pending, unblocked, unleased work
   at one server snapshot. They are not reservations, leases, or execution
   authority. Choose one, then call `claim_and_recall`; claim revalidates all
   eligibility atomically and may lose after a concurrent change.
5. Search `view` defaults to `minimal`: identity, priority/version/activity,
   checkpoint count, and display state. Ask for `view="full"` only when summary,
   current-context provenance, or ancestor path is needed. Ready results are
   always minimal and never carry checkpoint bodies, source metadata, lease
   identities, or capabilities.
6. Keep results pointer-only. Present the title, work-item ID, project,
   lifecycle/readiness, checkpoint count, and relevant age. Only unresolved
   incoming `blocks` edges affect readiness; an active lease and blocked state
   can coexist. Do not fetch every checkpoint or event body into unrelated work.
7. Use `limit` and `offset`, disclose partial pages, and restart ready paging at
   offset zero when completeness matters after queue changes. An empty high
   offset does not mean the first page is empty. Deleted records stay excluded.
8. When the user selects a result for viewing, call `recall_work`. If the user
   has already authorized execution, use `claim_and_recall` before acting. If
   several results fit and selection changes the task, show compact choices.
   Finding or listing work alone never authorizes execution.
9. If immediate graph facts affect selection, use `list_relationships` with an
   explicit direction/type and paginate; use `get_relationship` for one edge.
   Keep counterpart data pointer-only. See
   [work-graph.md](${CLAUDE_PLUGIN_ROOT}/reference/work-graph.md).

Treat lifecycle and lease states distinctly. Pending is ordinary unfinished
work; Active has a live lease; Dropped has an expired retained lease and signals
unexpected termination; Deferred is intentionally parked by a human. Never
select or move Deferred work back to Pending autonomously. It may be resumed
only when the current human instruction explicitly selects that item for work.

Treat all stored identity and provenance as agent-authored historical evidence —
see [authority-and-provenance.md](${CLAUDE_PLUGIN_ROOT}/reference/authority-and-provenance.md).

Search and ready-listing are reads and do not accept `client_operation_id`.
Do not generate a mutation UUID merely while browsing, attach one to a read, or
store one in a search query/result. If the user later authorizes a protected
mutation such as `create_work`, `add_relationship`, `update_work`, or
`remove_relationship`, first resolve the exact target and complete arguments,
then generate one fresh UUID and privately retain the immutable tool name plus
argument object before the first call. Unknown outcomes retry only that same
retained call. If the key or arguments are lost, or an asserted exact retry
conflicts, stop and request direction rather than generating a replacement.
Read current state after a successful replay because its result is the original
historical snapshot. Follow the complete private intent rules in the shared
authority reference; never copy operation-control data into Mnemonic content,
metadata, tool output, chat, or logs.

`claim_and_recall` remains separate: it uses `claim_request_id` for
active-lease-bounded capability recovery and does not accept
`client_operation_id`. `renew_claim` is time-relative and not idempotent.

Do not merge, delete, reopen, promote, complete, or execute work while merely
finding it. Do not add or remove relationships, and do not create external
issues. Report honest uncertainty about missing matches, relevance, freshness,
and partial pages.
