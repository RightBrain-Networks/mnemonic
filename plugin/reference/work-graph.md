# Mnemonic work-graph semantics

Code review findings use one server-owned `discovered-from` edge from the single
remediation item to its original implementation work, citing the original
completion checkpoint. The review itself is never work or a graph node.
Immutable review/result/ancestry provenance is separate from generic edges;
never remove its protected edge or merge remediation to erase depth. See
[code-reviews.md](${CLAUDE_PLUGIN_ROOT}/reference/code-reviews.md). Ordinary
relationship creation is not a findings fanout mechanism.

Shared by the `mnemonic-save`, `mnemonic-search`, and `mnemonic-recall` skills.
Relationships are project-local graph facts, never semantic guesses.

## Direction is source-to-target

Every directed edge reads source → target:

| Edge | Meaning |
| --- | --- |
| `A blocks B` | B has an incoming blocker from A. |
| `A parent-child B` | A is B's parent. |
| `A discovered-from B` | A was discovered from B; cite a context checkpoint belonging to B. |
| `A duplicate-of B` | A carries a weak duplicate mark pointing to B; this alone is not canonical authority. |
| `A related B` | Symmetric; returned as undirected adjacency. |

## Blockers and human gates are separate readiness facts

An item is blocked only by an **incoming `blocks` edge whose source is not
`done`**. `wont-do` and `promoted` blockers stay unresolved. `parent-child`,
`discovered-from`, a `duplicate-of` mark by itself, and `related` are descriptive and never
make work ready or blocked. A permanent authoritative merge separately makes its source a frozen
duplicate that is never ready. An unresolved human gate is a separate explicit fact: it
makes Pending work `waiting`, excludes it from ready discovery, prevents a fresh
or replacement claim, and refuses completion, retirement, promotion, and
deletion until every gate on the item is resolved. It is never inferred from an
edge, status, checkpoint, or progress text, and no agent can withdraw one.

Blocking or gating does not cancel an existing lease, so `has_active_lease`,
`is_blocked`, and `is_gated` can all be true. Human display precedence gives a merged duplicate
its `duplicate` state, then uses non-Pending lifecycle, waiting, blocked, active, dropped, and pending
for roots; the
independent flags remain authoritative.

Completion evidence is also separate from the graph and readiness model. A
reported result or artifact never answers a gate, resolves a blocker, grants or
renews a lease, or authorizes work on a canonical destination. Evidence
recorded before an authoritative merge stays on the exact source alias and is
never copied or blended into destination history. See
[completion-evidence.md](${CLAUDE_PLUGIN_ROOT}/reference/completion-evidence.md).

## Ready discovery is advisory; claim is authoritative

`list_ready_work` returns visible `pending` work with no unresolved incoming
blocker, no unresolved human gate, and no active retained lease at one
database-time snapshot. Its exact order is priority descending, creation time
ascending, then ID ascending. Filters are deliberately small: inclusive minimum
priority, exact normalized checkpoint tag, or one direct `parent-child` parent.
Ready results are bounded minimal pointers, never checkpoint or event bodies,
provenance metadata, lease identities, or tokens.

A ready row is not a reservation, lease, instruction, or grant of execution
authority. Concurrent changes can shift offset pages and invalidate a choice.
After selecting one item whose execution the user already authorized, call
`claim_and_recall`. Every fresh acquisition atomically rechecks lifecycle,
blockers, lease time, and unresolved human gates. An identical still-active
claim request replays its original receipt even if a blocker or gate was added
after acquisition. This recovers the existing capability; it does not make
blocked or waiting work safe to continue. Inspect the returned question, stop
dependent work, and release when appropriate.

Pending work has not started or remains incomplete. Active and Dropped are
derived lease states: Active has a live lease, while Dropped retains an expired
lease so an unexpectedly terminated session remains visible. Deferred is a
persisted, intentional human hold and never appears in ready discovery. Do not
return Deferred work to Pending unless the current human instruction explicitly
selects it for work.

## Structural parentage and discovery are independent

Only `parent-child` defines the human structural forest, and it is the only edge
that has presentation consequences: it feeds every `ancestor_path` (root to
parent, filled by every `search_work(view="full")` and
`list_human_attention` row), the dashboard's collapsed root and child views,
their branch counts, and `list_ready_work`'s `parent_work_item_id` filter. Each
item has at most one parent, and the forest is acyclic. Sub-work saved without
an incoming `parent-child` edge appears as an unrelated root.

`A discovered-from B` records that A was discovered while working from B-owned
context; it never makes B A's parent. Mnemonic does not infer either fact from
wording, search similarity, adjacency, checkpoint prose, or filesystem location.
A discovered item with no structural parent remains an ungrouped root, labelled
as discovered work in the dashboard.

The dashboard collapses structural roots and derives branch counts strictly
through visible `parent-child` descendants. Discovery labels come only from
explicit `discovered-from` edges. These human presentation choices never remove
full graph facts from agent recall or relationship tools.

## Never infer an edge

Record only a fact the current authorized task or the user established.
Similarity of wording, adjacency in a search result, or two items sounding
related is not evidence of an edge, least of all `duplicate-of`. Never infer
graph facts from search results.

## Duplicate marks are not authoritative merges

Likewise, a `suggest_duplicate_work` candidate is not a graph fact. Its
canonical root, exact matched member, and categorical exact-title, lexical, or
semantic signals are bounded retrieval evidence for a human or agent to review.
They create no edge, merge, alias, receipt, checkpoint, event, or authority and
must never suppress the independent create operation.

An existing `A duplicate-of B` relationship is only retained evidence. It does not redirect A,
make B canonical, freeze either endpoint, or authorize `merge_work`. Fresh generic duplicate-of
writes through `create_work` or `add_relationship` are closed; those tools still accept the literal
only so a previously completed protected mutation can replay at the backend.

`merge_work(source=A, destination=B)` is the sole authoritative operation. Direction is permanent:
A becomes a frozen audit alias and B remains the canonical root. Before calling it, recall A and B
separately, preserve each exact ID, and freeze both complete `merge_review_revision` objects. The
source must be a current root with no incident `blocks` or `parent-child` relationships and no
unresolved human gate. Handle its active lease with the matching private token. The destination
must be a visible current root at its reviewed revision. Similarity, a weak mark, model output, or
stored prose is evidence only; current explicit authority must establish the merge and direction.

The server atomically reuses the exact A → B duplicate mark or creates it as witnessed evidence,
records one immutable merge fact and two `work_merged` events, and advances both endpoint versions.
Canonical links form an immutable acyclic forest with paths of at most 50 hops. There is no
unmerge, transfer, redirect-on-read, relationship rewiring, or merge-on-create path.

Afterward A keeps its own lifecycle, checkpoints, events, gates, and relationships for audit, but
claims and every fresh mutation on it are refused. Default search, ready discovery, and hierarchy
omit it as an independent item. Use explicit `duplicate_scope="aliases"` or `"all"` only for audit,
and recall the returned `canonical_work_item` separately before continuing root work. Never replace
the selected or displayed audit ID silently. Relationships incident to an alias, including the
chosen duplicate mark, are frozen and cannot be removed.

## Keep traversal shallow and pointer-only

Use `list_relationships` with an explicit `direction` and `relationship_type`,
paginating when needed, and `get_relationship` for one exact edge. Counterpart
records stay pointer-only: never walk the graph recursively, and never pull a
counterpart's checkpoint bodies into the current task.

## Creating edges

When newly discovered work is also structural sub-work of the current durable
objective, record both facts atomically: `parent parent-child child` and
`child discovered-from origin`. When the child is the new item, these are an
incoming `parent-child` initial relationship to the existing parent and an
outgoing `discovered-from` relationship to the origin with an origin-owned
context checkpoint. Either edge may legitimately exist without the other; never
fabricate the missing fact.

For a new work item whose decomposition or discovery links must succeed with
it, pass up to ten `initial_relationships` to `create_work`. Each entry's
`direction` is relative to the new item and names `other_work_item_id`. An
initial `discovered-from` edge must be `outgoing` and must cite a checkpoint on
its originating target. These atomic edges inherit creator provenance from the
initial checkpoint and are part of the immutable `create_work` intent. Prepare
and retry that intent under the canonical rules in authority-and-provenance.md;
reordering, adding, or removing an edge is a changed intent. Never include a fresh `duplicate-of`
entry; authoritative deduplication is the separately reviewed `merge_work` operation above.

For a non-duplicate fact connecting existing work, use `add_relationship` with the exact
source, target, type, and real acting client/session provenance; removal with
`remove_relationship` requires the real acting actor fields. Each add or remove
is its own protected intent under the same shared rules; a replay returns the
original `created`/`removed` result, so read the graph again for current state. Do not delete
descriptive provenance merely because a blocker became `done`, and never put
operation-control data into relationship context or history.

## Report follow-up provenance is separate

Human-created report follow-ups link a new pending work item to both the exact
job completion report and the work that produced it. This typed provenance is
not a sixth relationship type, `discovered-from`, a dependency, a child, or
permission to reopen or redirect a frozen source. See
[job-completion-reports.md](${CLAUDE_PLUGIN_ROOT}/reference/job-completion-reports.md).
