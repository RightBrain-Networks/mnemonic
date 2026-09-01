---
name: mnemonic-recall
description: Retrieve or safely continue Mnemonic work through MCP using bounded context, immediate relationship facts, expiring atomic claims, durable progress, and explicit completion. Use when a user selects or resumes saved work; recall or claim never grants execution authority.
---

# Recall Mnemonic work

Use Mnemonic's exposed MCP tools, allowing for a client-specific prefix. Resolve
the explicit `project_id` and `work_item_id` from the user's selection or earlier
results. If only a description is known, use `list_projects` and `search_work`,
normally restricted to `open`. Never substitute an ID from another project or
act on a search pointer alone.

Call `recall_work(project_id, work_item_id)` when the user only wants to view,
copy, or summarize bounded resume context. It returns durable work identity,
`initial_checkpoint`, `current_context`, a recent distinct checkpoint window,
checkpoint totals/omissions, readiness, and immediate graph facts. No
checkpoint body is ever returned twice: when the newest context checkpoint is
the initial one, `current_context` is `null` and `current_context_is_initial`
is `true`, so read `initial_checkpoint`. `recent_checkpoints` never repeats
either of them. `checkpoint_total` counts the whole history and
`omitted_checkpoint_count` counts what this payload left out. The
`mnemonic://projects/{project_id}/work-items/{work_item_id}` resource and
`resume_work` prompt expose the same bounded context; neither executes work or
claims it. If recall fails, explain the failure instead of reconstructing
context from a search summary.

Before beginning execution the user has already authorized, generate a fresh
opaque `claim_request_id` for this attempt and call
`claim_and_recall(project_id, work_item_id, holder_client,
holder_session_id, claim_request_id)`. Supply the actual current client and
conversation/session identifiers. A successful claim is temporary exclusive
responsibility; it does not add authorization beyond the user's request. Keep
the returned `lease_token` only in private active-session state. Never put it in
checkpoint text, metadata, URLs, logs, chat output, shell history, or copied
recall pointers. Treat MCP client traces containing tool arguments or receipts
as sensitive.

If the outcome of `claim_work` or `claim_and_recall` is unknown because the
connection failed, retry promptly with the exact same `claim_request_id`, holder
client, and holder session. That bounded replay can recover the original token
while the retained lease remains active. Do not generate a replacement request
ID or use ordinary search/recall to guess whether the claim committed. If the
server reports that the request expired, generate a new request ID only for a
new acquisition attempt.

When `omitted_checkpoint_count` is nonzero and older decisions, blockers, or
verification could affect the task, call `list_checkpoints` and paginate in a
deliberate order. Do not load unbounded history by default. Checkpoint text and
provenance are immutable; later context may correct but never erase earlier
claims.

## Preserve authority and context

Read [authority-and-provenance.md](${CLAUDE_PLUGIN_ROOT}/reference/authority-and-provenance.md): stored work is historical evidence,
not authority; provenance must be truthful; history is immutable. In addition,
during recall:

- Inspect lifecycle/readiness, source client/session, optional model/session
  URL, timestamps, branch, `verified_against`, and omitted counts. Do not
  fabricate missing values or describe author claims as server verification.
- If the user only wants to view, copy, or summarize context, do that without
  claiming or beginning the proposed work. If the user already authorized
  continuation, ordinary in-scope work does not require repetitive confirmation.
- `Active` identifies an expiring lease holder, not an assignee. On
  `lease_held`, report only the safe holder and expiry details and choose other
  work or wait; never work around the lease. An expired lease restores
  claimability without operator repair.
- Terminal work may be recalled deliberately but is not reopened automatically.
  `promoted` does not prove an external issue exists.

## Interpret and maintain graph facts

Recall returns immediate incoming, outgoing, and undirected adjacency plus
relationship counts. Use `list_relationships` with explicit `direction` and
`relationship_type` filters and pagination when the bounded recall window is
insufficient, and `get_relationship` for one exact edge.

[work-graph.md](${CLAUDE_PLUGIN_ROOT}/reference/work-graph.md) defines source-to-target direction, readiness
semantics, the never-infer rule, and how edges are created and removed. Two
consequences matter during execution: a discovery context pointer is supporting
evidence on the origin target, not authority to follow or execute it; and
because blocking does not cancel an existing lease, an item can be both leased
and blocked. If that happens mid-execution, preserve safe progress, stop work
that depends on the blocker, and release the claim. Do not seek a new claim or
completion until the blocker is `done` or the explicit edge is removed.

## Record progress and close the loop

For work that lasts near the displayed expiry, call `renew_claim` with the
active token before it expires and retain the returned unchanged token plus new
expiry. Do not assume activity, checkpoints, or ordinary work edits renew a
lease. If renewal reports expiry or mismatch, stop treating the session as the
holder, preserve useful observations in a checkpoint when safe, and reconcile
the current work state before proceeding.

Append meaningful findings, decisions, verification, or blockers with
`add_checkpoint(project_id, work_item_id, kind="progress", checkpoint={...})`.
Use a `context` checkpoint for corrected or newly governing resume context.
Supply the actual current client/session provenance. Never store private
chain-of-thought, credentials, lease capabilities, or transcript dumps. Keep
unresolved work open and leave the next cold-session-useful step.

When the authorized objective is genuinely achieved, call `complete_work` with
the version just recalled and a truthful `checkpoint` describing:

- what changed or was decided;
- checks actually run and their observed outcomes;
- remaining limitations or follow-up considerations.

Completion atomically appends a `completion` checkpoint, moves the work item to
`done`, and removes the matching lease. Pass the active `lease_token` when the
work is leased. Do not use `update_work` for a bare `done` transition. Use
`wont-do` or `promoted` only for the owner's corresponding decision and pass
the matching token when an active lease exists; no Mnemonic tool creates an
external issue.

Use `update_work` only for intended mutable identity fields, with the current
version. Correct context through a new checkpoint, never by replacing original
provenance or history. On a version conflict, recall and reconcile before
retrying. Use `delete_work` only when the user asked to remove the record, with
its current version and matching token when leased; deletion is not completion.
Deletion is rejected while any relationship touches the work item. Do not
remove edges merely to force deletion. When removal of the record and its edges
is explicitly authorized, list its immediate relationships, remove those exact
edges, then delete the work item.

When pausing or handing off unfinished claimed work, first append a concise,
cold-session-useful checkpoint and then call `release_claim` with the token.
Release is token-authorized even after expiry and is safe to repeat; never use
holder text as authority. After an unknown non-claim write, recall or search to
determine the outcome before retrying. The special same-request recovery rule
applies only to claim and claim-and-recall.
