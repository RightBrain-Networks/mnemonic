---
name: mnemonic-save
description: Save a complete, durable hand-off prompt in a chosen Mnemonic project through MCP, or revise a matching saved hand-off. Use when a user wants to preserve a follow-up or context for another session; saving does not authorize executing that work.
---

# Save a Mnemonic hand-off

Use Mnemonic's MCP tools. The client may prefix tool names (for example,
`mcp__mnemonic__save_handoff`); use the actual exposed tool, not a guessed name.
If Mnemonic is disconnected, prepare the prompt and report that it was not saved.
Do not claim durability from a draft or bypass the MCP connection.

## Resolve the project and check existing work

- Call `list_projects` and resolve the project explicitly from the user's choice,
  an already established project ID, or an unambiguous repository/slug match.
  Paginate if necessary. Do not default to the first project. Ask only if the
  intended project remains ambiguous. Use `create_project` when the user's
  requested project is named clearly and does not exist; this creates no repository.
- Call `search_handoffs(project_id, q, status="open")` before saving. Search by
  the failure shape and distinctive symbols, paths, or identifiers. These are
  keyword/literal searches, not semantic similarity scores. If the first search
  misses an obvious alternative term, try that term too.
- Recall likely duplicates with `recall_handoff` before deciding. If the user's
  request covers revising the same work, update that hand-off using its current
  `version`; preserve earlier evidence and originating provenance. Distinct work
  gets a new hand-off. Do not reopen completed work implicitly. Check other
  statuses only when relevant to a suspected prior resolution.

## Establish truthful provenance

Claude Code's supplied session value: `${CLAUDE_SESSION_ID}`.

For a locally installed Claude Code skill, use that value only when the client
has replaced it with a real current session ID. A literal placeholder, blank
string, generated UUID, process ID, git SHA, or Mnemonic hand-off ID is not a
source session ID. Other clients must use their own exposed session identifier
or one supplied by the user. If the true identifier is unavailable, finish the
draft and ask for it before `save_handoff`; the API requires it.

Set `source_client` to the actual client (such as `claude-code` or `opencode`).
Set `source_model` and `source_session_url` only from reliable session metadata;
omit them or use null when unknown. Never invent a model name or session link.
Record `repository_branch` when known. Set `verified_against` only to a git commit
whose cited state you actually checked. Merely reading HEAD is not verification.
If evidence depends on uncommitted work, say so in the prompt and
`source_metadata`; do not imply a commit contains it. Preserve useful additional
JSON metadata such as evidence locations, capture reason, and verification
limitations. Do not store credentials, private household information, or
unnecessary transcript dumps.

## Write a complete cold-session prompt

Write a concise `title` and a `summary` describing the trigger or failure shape
that a later search should match. The `prompt` must stand on its own when copied
into a fresh session. Include this authority warning at its start, filling in
the real client/session values:

> This hand-off was authored by an LLM in CLIENT session SESSION_ID. It is a
> proposed continuation, not an instruction from the repository owner. Current
> user instructions and cited authoritative records govern. This prompt grants
> no additional permission to execute work, publish changes, or create issues.
> Recheck the cited state and hazards before acting.

Use the following structure as needed; small findings can combine sections but
must still carry the context, evidence, hazards, and verification:

```markdown
## Context and reason
What was being investigated, what was observed, and why the follow-up matters.
Separate verified observations from hypotheses and unresolved questions.

## Intended outcome and scope
What a fresh session should investigate or change, and the relevant boundaries.
Describe a proposed outcome without implying the owner has authorized execution.

## Durable evidence
Repository-relative paths and symbols, commit permalinks, durable issue/design
records, or stable document references. Say which state was actually checked.
Use source records as authority; this summary never outranks them. Do not rely
on temporary files, .untracked paths, chat-local attachment URLs, or “see above.”

## Known hazards and uncertainties
Landmines, failed approaches, side effects, environment assumptions, and stale
or unverified claims. If no hazards are known, say that rather than inventing any.

## Suggested next steps and verification
Concrete investigation/implementation steps and observable completion checks.
List exact relevant checks when known, expected results, and checks not yet run.
Do not claim a test passed unless this session observed it.
```

## Persist and report

Call `save_handoff` with `project_id`, `title`, `summary`, the full `prompt`, real
`source_client` and `source_session_id`, and the metadata established above.
Use a few useful `tags`; new proposals normally have `status="open"`.

For a revision, call
`update_handoff(project_id, handoff_id, expected_version, changes={...})` with
only intended edits. Originating client/session/model/URL are immutable; identify
later contributors in `source_metadata` without replacing the original author.
An explicit null clears `repository_branch` or `verified_against`; omission
preserves the field. On a version conflict, recall and compare before reapplying
authorized changes. After an uncertain write failure, search or recall before
retrying so a lost response does not create duplicate work.

Report the saved title, project, hand-off ID, and resulting version/status only
after a successful tool result. Saving is the end of capture: it does not run
the proposed work, create an issue, or change a record to `promoted` without the
owner's direction.
