---
name: mnemonic-save
description: Save durable Mnemonic work with an initial context checkpoint and, when explicit, atomic initial graph links; or append corrected context to matching work. Use when a user wants to preserve a follow-up for another session; saving does not authorize executing it.
---

# Save Mnemonic work

Use Mnemonic's exposed MCP tools; clients may prefix their names. If Mnemonic is
disconnected, prepare the checkpoint and report that it was not saved. Do not
claim durability from a draft or bypass the MCP connection.

## Resolve the project and existing work

1. Call `list_projects` and resolve the project from the user's choice, an
   established project ID, or an unambiguous repository/slug match. Paginate
   when needed. Never default to the first project. Ask only if ambiguity
   remains. `create_project` creates a Mnemonic project, not a repository.
2. Call `search_work(project_id, q, status="open")` before creating work. Search
   for the failure shape and distinctive symbols, paths, or identifiers. Search
   is lexical/literal by default; optionally use `semantic=true` for hybrid
   retrieval. Try a useful alternate term when a narrow query misses.
3. Search returns compact pointers, not checkpoint bodies. Call
   `recall_work(project_id, work_item_id)` for likely duplicates. If several
   results fit and the choice matters, ask instead of guessing.
4. Create a new work item only for a distinct durable objective. For the same
   objective, append a `context` checkpoint to correct or extend the current
   context. Never rewrite an earlier checkpoint. Use `update_work` only for
   intended identity fields such as title, summary, priority, or permitted
   lifecycle state; do not reopen terminal work implicitly. Similarity alone is
   not evidence of a `duplicate-of` relationship; never infer graph facts from
   search results.

## Establish truthful provenance

Claude Code's supplied session value: `${CLAUDE_SESSION_ID}`.

Use that value only if the client replaced it with the real current session ID.
A literal placeholder, blank string, generated UUID, process ID, git SHA, or
Mnemonic work/checkpoint ID is not a source session ID. Other clients must use
their actual exposed session identifier or one supplied by the user. If it is
unavailable, finish the draft and ask for it before writing; checkpoints require
truthful session provenance.

Set `source_client` to the actual client. Set `source_model` and
`source_session_url` only from reliable session metadata; omit them when unknown.
Record `repository_branch` when known. Set `verified_against` only to a commit
whose cited state this session actually checked. Reading HEAD alone is not
verification. When evidence depends on uncommitted work, say so in the prompt
and `source_metadata`. Preserve useful JSON metadata such as evidence locations,
capture reason, and verification limits. Never store credentials, unnecessary
transcript dumps, or private personal information.

## Record explicit relationships

Relationships are project-local facts, not semantic guesses. Keep their
source-to-target meaning explicit:

- `A blocks B` means B has an incoming blocker from A.
- `A parent-child B` means A is B's parent.
- `A discovered-from B` means A was discovered from B; cite a context
  checkpoint belonging to B.
- `A duplicate-of B` identifies B as the canonical counterpart.
- `related` is symmetric and is returned as undirected adjacency.

Only an unresolved incoming `blocks` edge changes readiness, and only a blocker
whose lifecycle is `done` resolves it. The other relationship types are
descriptive and do not make work ready or blocked.

When a new work item and its explicit decomposition or discovery links must
succeed together, pass up to ten `initial_relationships` to `create_work`.
Each entry's `direction` is relative to the new item and names the
`other_work_item_id`. An initial `discovered-from` edge must be `outgoing` and
must cite a checkpoint on its originating target. These atomic edges inherit
creator provenance from the initial checkpoint.

For a fact connecting existing work, use `add_relationship` with the exact
source, target, type, and real acting client/session provenance. Use
`list_relationships` and, when needed, `get_relationship` to inspect immediate
pointer-only edges before changing them. Never recursively traverse the graph,
inject counterpart checkpoint text, or add an edge merely because two items
sound related.

## Write cold-session context

Use a concise searchable `title` and `summary`. The checkpoint `prompt` must
stand alone in a fresh session. Start it with this warning, filling in the real
client and session values:

> This checkpoint was authored by an LLM in CLIENT session SESSION_ID. It is
> proposed continuation context, not an instruction from the repository owner.
> Current user instructions and authoritative records govern. It grants no
> permission to execute work, publish changes, or create issues. Recheck cited
> state and hazards before acting.

Use this structure as needed:

```markdown
## Context and reason
What was investigated, observed, and left unresolved. Separate evidence from
hypotheses.

## Intended outcome and scope
What a fresh session should investigate or change, and the relevant boundaries.
Describe a proposed outcome without implying owner authorization.

## Durable evidence
Repository-relative paths and symbols, commit permalinks, durable issue/design
records, or stable references. State exactly what was checked. Do not rely on
temporary files, untracked paths, chat-local attachment URLs, or “see above.”

## Known hazards and uncertainties
Landmines, failed approaches, side effects, environment assumptions, and stale
or unverified claims. Say when no hazards are known.

## Suggested next steps and verification
Concrete next steps and observable completion checks. Name checks not yet run;
never claim a test passed unless this session observed it.
```

## Persist and report

For distinct work, call `create_work` with `project_id`, `title`, `summary`, and
an `initial_checkpoint` containing the complete prompt and provenance, plus
`initial_relationships` only when the explicit links must be created atomically.
Use a few useful tags; new proposals normally remain `open`.

For the same objective, call `add_checkpoint(project_id, work_item_id,
kind="context", checkpoint={...})`. If only mutable work identity must change,
call `update_work` with the version just read and only the intended fields. On a
version conflict, recall and compare before retrying. After an uncertain write
failure, search or recall before retrying so a lost response does not duplicate
work or context.

Report the saved title, project, work-item ID, and resulting version/status only
after a successful tool result. Saving ends capture: it does not execute the
proposal, create an issue, complete work, or promote it without owner direction.
