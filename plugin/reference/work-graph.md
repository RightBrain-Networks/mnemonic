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
initial checkpoint.

For a fact connecting existing work, use `add_relationship` with the exact
source, target, type, and real acting client/session provenance. Removal with
`remove_relationship` is idempotent and is for the exact edge the user asked to
remove, or that the authorized work established is wrong. Do not delete
descriptive provenance merely because a blocker later became `done`.
