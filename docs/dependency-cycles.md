# Dependency cycles

Release 0.84.0, plugin 0.50.0, migration `0052_dependency_cycles`.

The existing contract makes parent-child links descriptive. They do not require
children to close before their parent. Treating every existing link as a closeout
constraint would silently change recorded plans. This release adds an explicit
`requires_child_closeout` flag, default false, to parent-child edges and initial
relationships. It is never inferred from prose or from a parent's title.

A required child must be terminal (Done, Won't do, or Promoted) before the parent
can close. A parent can still be claimed and worked on with open children. Blocks
continues to require its source to be Done; retirement does not satisfy it.

## Write guard

`services/relationships.py:stage_relationship_locked` checks the final proposed
edge through `services/precedence.py:require_acyclic_relationship`. This covers
explicit adds, initial relationship batches, and parent replacements. Blocks
runs source to target; required parent-child closure runs child to parent. The
parent hierarchy also retains its independent forest check. The graph spans
projects and includes terminal work, so reopening cannot evade a new-edge check.
Self edges, second parents, and existing duplicate restrictions remain enforced.

Adds and removals hold the schema-wide transaction advisory lock, with projects
and endpoint rows locked in the established order. Migration 0052 installs a
matching lock trigger and deferred constraint for direct inserts; immutable-edge
triggers already prohibit UPDATE. Relationship writes require READ COMMITTED so
a waiter cannot validate an obsolete transaction snapshot. The constraint checks
the committed topology after all edges in the transaction, not a truncated path.

A 409 refusal contains an actionable sentence and validated metadata describing
the ordered loop. MCP renders only fixed labels and UUIDs from that evidence,
never arbitrary backend prose. The response includes the full cycle length,
deterministic cycle hash and at most 32 steps. Traversal has no depth bound.

`add_relationship` accepts `replace_relationship_id` for an exact existing
parent-child edge of the same child. It deletes the old edge and stages the new
edge, events and receipt atomically. Failure preserves the old link. This supports
reparenting and changing the closure flag without exposing an intermediate state.
An old ID cannot replace a later link. Existing operation receipts replay before
fresh checks with unchanged arguments, including defaults and omissions.

There is no force flag that accepts an enforced cycle. Atomic replacement handles
the supported restructuring operation. Bulk restructuring is not introduced by
this release; remove obsolete constraints before adding replacements, or use a
future atomic batch API. Duplicate merges already refuse incident structural
edges and do not transfer them.

## Read guard

`services/graph_cycles.py` computes strongly connected components iteratively.
`services/precedence.py` uses current unsatisfied relationships to derive two
kinds of issue:

- `deadlock`: a cycle in blocks plus explicit required child closure.
- `planning-cycle`: a cycle if all parent links were closure constraints, with
  at least one descriptive link. This is a warning, not proof of an execution
  deadlock under the recorded semantics.

Cycle members and their downstream dependents carry an issue. Acyclic upstream
prerequisites do not. Multiple cycles produce deterministic representative
witnesses; a witness is not an exhaustive enumeration of every possible cycle.
Terminal work and immutable duplicate aliases have no active issue.

An enforced deadlock sets `display_state=deadlocked`, `is_ready=false`, prevents
fresh or forced implementation claims and prevents Done. It takes display
precedence over pending/deferred, gates, blockers and leases. Independent blocker,
gate and lease facts stay intact. Existing leases are not revoked; exact claim
receipt replay still recovers a capability and does not establish eligibility.
Human retirement remains a possible explicit plan decision, subject to child
closeout and the existing human gates. No automatic repair occurs.

Full/status/context reads, relationship counterpart pointers, flat/compact
search, hierarchy and ready discovery share this interpretation. Ready pages
exclude deadlocks before pagination and report nonzero project-wide pending
issue counts plus at most ten work IDs for inspection. Counts precede ready
filters; consumers must not mistake an empty ready list for an empty backlog.
The dashboard labels deadlocks, shows the path in work detail, and marks planning
warnings separately. False closeout flags and absent issues remain omitted from
canonical responses and operation fingerprints.

## Existing state and limits

Migration 0052 leaves all existing parent edges descriptive and does not edit
customer plans. Existing blocks cycles, including corrupt restores, remain
readable and are diagnosed independently of write enforcement. Existing mixed
cycles are planning warnings until the owner explicitly establishes closure
requirements. A requirement that would complete a deadlock is refused.

The FishFood reproduction is a planning cycle under its actual recorded
semantics: child `94a122ce-8de0-4b89-a04f-ef8b1e3d4c19` precedes parent
`0d03e65b-8875-4202-8ecf-5a7640b86162` only under a child-closure interpretation;
the four blocks edges then run through `37bcf872-743f-450b-9d34-1c265aad4de7`,
`9761e837-fdb4-4e2f-a742-66aa29516f2e`, and
`a3df29a0-1e2f-45c1-847d-7b2cdefb4826` back to that child. This release surfaces
that ambiguity without relabeling a descriptive edge as a historical mandate.
No FishFood relationship is repaired or migrated to required closure.

Read-only verification on 2026-10-17 used `list_relationships(direction="both")`
against project `de3b0903-7eae-49a2-9eb6-2fdcde186807`. The recorded loop was:

| Relationship ID | Stored relationship |
| --- | --- |
| `c0b426a8-649b-4a3f-83b3-7438a13108dc` | Umbrella → child, parent-child |
| `3cb3d325-f88a-43f1-9e62-815ecfeda18e` | Umbrella → ingress, blocks |
| `d97b50e6-2833-46f0-8e07-dc0fc4371fd6` | Ingress → cutover A, blocks |
| `86397408-919f-4f4e-8a8c-20765e94b29c` | Cutover A → cutover B, blocks |
| `0274499f-87ac-42b8-b97a-ab5a07701e7f` | Cutover B → child, blocks |

Cutover B's actual ID above corrects the malformed ID in the original hand-off.
Against source `054c754` (0.83.9), isolated PostgreSQL probes confirmed that
either write order and an initial relationship batch admitted this mixed loop.
They also confirmed the parent could complete with its child pending, releasing
the next blocks target. That behavior belongs to the domain service, not an API
projection. A separate restored blocks cycle appeared as ordinary blocked work.
The new regressions retain the descriptive-parent behavior, refuse the mixed loop
when closure is required, and display restored enforced cycles as deadlocked.

This guard sees topology, not unstated intent: circular prose prerequisites,
external approvals or resources, contradictory acceptance criteria, an abandoned
prerequisite, and other non-topological deadlocks remain outside it. In particular
a Won't do source of a blocks edge remains unresolved without becoming a graph
cycle. Acyclic does not guarantee that a plan is feasible or that work is allowed.

Upgrade API, worker, MCP and dashboard together; older writers are unsupported.
The migration adds one column, a type check and database graph guards; no tools,
receipt kinds or work-event types are added. Backup schema signatures and audit
catalogs advance to 0052. Downgrade refuses retained required-child edges or their
immutable event history instead of erasing their meaning. Fix forward or restore
a pre-upgrade backup when such history exists. Production restart is a separate
operator-authorized operation.
