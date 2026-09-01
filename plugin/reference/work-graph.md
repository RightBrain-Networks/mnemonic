# Mnemonic work-graph semantics

Shared by the `mnemonic-save`, `mnemonic-search`, and `mnemonic-recall` skills.
Relationships are project-local graph facts, never semantic guesses.

## Direction is source-to-target

Every directed edge reads source → target:

| Edge | Meaning |
| --- | --- |
| `A blocks B` | B has an incoming blocker from A. |
| `A parent-child B` | A is B's parent. |
| `A discovered-from B` | A was discovered from B; cite a context checkpoint belonging to B. |
| `A duplicate-of B` | B is the canonical counterpart. |
| `A related B` | Symmetric; returned as undirected adjacency. |

## Only `blocks` changes readiness

An item is blocked only by an **incoming `blocks` edge whose source is not
`done`**. `wont-do` and `promoted` blockers stay unresolved. `parent-child`,
`discovered-from`, `duplicate-of`, and `related` are descriptive and never make
work ready or blocked.

Blocking does not cancel an existing lease, so `has_active_lease` and
`is_blocked` can both be true.


## Ready discovery is advisory; claim is authoritative

`list_ready_work` returns visible `pending` work with no unresolved incoming
blocker and no active retained lease at one database-time snapshot. Future
gates will extend the same predicate. Its exact order is priority descending,
creation time ascending, then ID ascending. Filters are deliberately small:
inclusive minimum priority, exact normalized checkpoint tag, or one direct
project-local parent. Ready results are bounded minimal pointers, never
checkpoint/event bodies, provenance metadata, lease identities, or tokens.

A ready row is not a reservation, lease, instruction, or grant of execution
authority. Concurrent changes can shift offset pages and invalidate a choice.
After selecting one item whose execution the user already authorized, call
`claim_and_recall`. Every fresh acquisition atomically rechecks lifecycle,
blockers, lease time, and future gates. An identical still-active claim request
replays its original receipt even if a blocker was added after acquisition;
this recovers the existing capability and does not make newly blocked work safe
to continue.

Pending work has not started or remains incomplete. Active and Dropped are
derived lease states: Active has a live lease, while Dropped retains an expired
lease so an unexpectedly terminated session remains visible. Deferred is a
persisted, intentional human hold and never appears in ready discovery. Do not
return Deferred work to Pending unless the current human instruction explicitly
selects it for work.
## Never infer an edge

Record only a fact the current authorized task or the user established.
Similarity of wording, adjacency in a search result, or two items sounding
related is not evidence of an edge — least of all `duplicate-of`. Never infer
graph facts from search results.

## Keep traversal shallow and pointer-only

Use `list_relationships` with an explicit `direction` and `relationship_type`,
paginating when needed, and `get_relationship` for one exact edge. Counterpart
records stay pointer-only: never walk the graph recursively, and never pull a
counterpart's checkpoint bodies into the current task.

## Creating edges

For a new work item whose decomposition or discovery links must succeed with
it, pass up to ten `initial_relationships` to `create_work`. Each entry's
`direction` is relative to the new item and names `other_work_item_id`. An
initial `discovered-from` edge must be `outgoing` and must cite a checkpoint on
its originating target. These atomic edges inherit creator provenance from the
initial checkpoint. They are part of the one immutable `create_work` argument
object retained with that call's `client_operation_id`; never retry the create
under the same UUID with a reordered, added, or removed relationship.

For a fact connecting existing work, use `add_relationship` with the exact
source, target, type, and real acting client/session provenance. Removal with
`remove_relationship` requires the real acting `actor_client`,
`actor_session_id`, and optional known `actor_model`. Before each separate add
or remove intent, generate its own `client_operation_id` and privately retain
it with the complete immutable arguments as described in
[authority-and-provenance.md](${CLAUDE_PLUGIN_ROOT}/reference/authority-and-provenance.md).
An unknown result is retried only with that same UUID and exact argument object;
an add/remove replay returns the original historical `created`/`removed` result
even if the edge later changed. Read the graph again for current state. A new
UUID with the same edge is a new intent and may instead bind a natural no-op.
Do not delete descriptive provenance merely because a blocker became `done`,
and never put operation-control data into relationship context or history.
