# Mnemonic Product Roadmap

## Purpose

Mnemonic is evolving from a durable LLM hand-off store into a coordination substrate for multiple LLM agents working on shared software projects.

The design goal is **not** to recreate GitHub Issues for machines. GitHub's issue model is centered on a human-readable ticket and the conversation around it. Mnemonic should instead optimize for:

- durable work state across ephemeral agent sessions,
- clean hand-offs between agents,
- safe concurrent work,
- provenance and verification,
- dependency-aware work discovery,
- low-noise human oversight,
- strong recovery behavior after failures or interrupted sessions.

The core architectural idea is:

> **A durable work graph in which ephemeral agents leave immutable checkpoints for one another.**

Humans should see a relatively small number of meaningful workstreams. Agents may operate over a much richer underlying graph containing checkpoints, discovered sub-work, dependencies, leases, verification evidence, and historical attempts.

This asymmetry is intentional. Mnemonic should absorb machine-generated coordination noise rather than push that noise back into GitHub Issues or the human-facing control surface.

## Delivery Snapshot

As of 2026-09-03, Phases 1–10 are shipped in the repository at
application/API/MCP/dashboard `0.5.0`, plugin `0.9.0`, and migration
`0018_repository_freshness`. Production-target preflight and cutover remain
explicit operator gates. Phases 11–13 remain planned.

| Roadmap element | Status | Implemented functionality |
| --- | --- | --- |
| Phase 1 — Work items and checkpoints | Shipped | Canonical durable work items, immutable checkpoints, bounded recall, and a provenance-preserving migration from legacy hand-offs. |
| Phase 2 — Atomic work leases | Shipped | Server-timed TTL claims with atomic claim-and-recall, renewal, release, active-claim replay, expiry, and takeover. |
| Phase 3 — Typed relationships | Shipped | Five project-local edge types, blocker and parent-cycle protection, one-parent enforcement, atomic linked creation, graph-aware recall, and hierarchy browsing. |
| Phase 4 — Ready-work discovery | Shipped | A deterministic, filtered REST/MCP ready queue derived from lifecycle, blockers, leases, and human gates, with claim-time revalidation. |
| Phase 5 — Work event timeline | Shipped | Immutable typed per-work events, conservative historical backfill, atomic mutation events, explicit progress events, bounded recall, and a paged dashboard timeline. |
| Phase 6 — Idempotent mutations | Shipped | Durable project-scoped success receipts established exact unknown-outcome recovery; Phase 9 extends the registry to 13 REST kinds, 11 canonical MCP writes, and 11 dashboard actions. |
| Phase 7 — Human gates | Shipped | Immutable question/answer history, drift-aware review, readiness and lifecycle guards, bounded recall, and a dedicated Needs Attention queue. |
| Phase 8 — Hierarchical presentation | Shipped | Collapsed root workstreams, lazy child paging, subtree-aware filtering, breadcrumbs, discovery labels, and exact branch aggregates. |
| Phase 9 — Duplicate handling | Shipped | Immutable authoritative merges, retained non-actionable aliases, canonical-aware reads/search/hierarchy, explicit draft duplicate suggestions, resource controls, and coordinated 0.4.0/0.8.0 clients. Production cutover and recovery gates remain explicit. |
| Phase 10 — Repository freshness verification | Shipped | Immutable ordered checkpoint dependency declarations plus a local, repository-selected, three-state Git assessment with fail-closed runtime, index, filter, normalization, race, privacy, and authority boundaries. |
| Phase 11 — Completion evidence | Planned | Completion checkpoints and completion events exist; structured verification results and artifact references have not shipped. |
| Phase 12 — Project activity feed | Planned | Per-work timelines and data-free dashboard invalidations exist; a durable project-wide cursor/feed, SSE, and webhooks have not shipped. |
| Phase 13 — Resource reservations | Planned | Work-item leases exist; arbitrary resource-key reservations have not shipped. |

---

# Guiding Principles

## 1. Separate durable work from session-specific context

A durable unit of work should survive many agent sessions. A hand-off should describe the state of that work at a particular point in time.

Do not make every new agent session create another top-level unit of work.

## 2. Prefer immutable history over mutable narrative

Agent-authored checkpoints, work events, verification results, and provenance should generally be append-only.

Mutable state should be kept small and explicit.

## 3. Avoid conventional human-ticket semantics unless they fit agents

Do not automatically adopt familiar issue-tracker concepts such as permanent assignees, proliferating workflow statuses, comment threads, or notification streams.

Agent sessions are temporary and failure-prone. The data model should reflect that.

## 4. Make coordination primitives atomic

Any workflow where two agents could independently observe the same state and race should have a server-side atomic operation.

## 5. Treat semantic similarity as advisory, not authoritative

Embeddings are useful for retrieval and duplicate suggestions, but should never silently decide identity, merge work, or infer dependencies.

## 6. Keep the human queue intentionally small

Agent-generated work should not automatically become human-visible top-level noise.

Human attention should be requested explicitly through first-class mechanisms.

---

# Target Conceptual Model

```text
Project
  |
  +-- WorkItem
        |
        +-- Handoff / Checkpoint
        +-- Handoff / Checkpoint
        |
        +-- WorkEvent
        +-- WorkEvent
        |
        +-- Relationships
        |     +-- blocks (directed acyclic dependency graph)
        |     +-- parent-child
        |     +-- discovered-from
        |     +-- duplicate-of
        |     +-- related
        |
        +-- Authoritative Duplicate Merge (optional outgoing, immutable)
        |
        +-- Active Lease (optional, ephemeral)
        |
        +-- Gates
        |
        +-- Verification Results
        |
        +-- Artifact References
```

The `WorkItem` represents the durable objective.

The existing hand-off concept should evolve into an immutable checkpoint representing the context left by a particular agent/session while working on that objective.

---

# Phase 1 - Separate Work Items from Hand-offs

**Status: Shipped.**

## Objective

Introduce a stable durable unit of work and stop treating each hand-off as a top-level task.

This is the foundational architectural change and should be completed before substantial coordination features are added.

## Shipped implementation

Migrations `0004_work_graph_expand` through `0006_work_graph_contract` created
canonical `WorkItem` and immutable `Checkpoint` records, backfilled every
legacy hand-off and comment while preserving IDs, timestamps, text, lifecycle,
and recorded provenance, and then removed the legacy storage after cutover.
Work creation atomically includes its initial checkpoint; REST, MCP, and the
dashboard support work-item browsing and search, bounded recall, versioned
identity edits, checkpoint append, completion, and soft deletion.

## Proposed `WorkItem`

Example fields:

```text
id
project_id
title
summary
status
priority
created_at
updated_at
version
```

Keep the mutable object intentionally small.

Possible persistent statuses:

```text
pending
deferred
done
wont-do
promoted
```

Avoid adding `in_progress`, `blocked`, `waiting`, or similar states if they can
be derived from other first-class objects. `deferred` is intentionally stored:
it records a human decision to park the item outside the work queue.

## Proposed `Handoff` / `Checkpoint`

Example fields:

```text
id
work_item_id
prompt
source_client
source_session_id
source_model
source_session_url
repository_branch
verified_against
affected_paths
tags
source_metadata
created_at
```

A checkpoint should be immutable after creation.

If an agent needs to correct or supersede prior context, it should create another checkpoint rather than rewriting history.

## Why

The current hand-off object conflates:

- work identity,
- work state,
- context packet,
- provenance,
- lifecycle,
- retrieval.

Splitting these concepts allows many agent sessions to work on the same durable objective without generating many top-level records.

It also improves provenance because the text attributed to an originating agent/session remains the text that session actually wrote.

## Deliverables

- New `WorkItem` domain model.
- Existing hand-offs linked to a `work_item_id`.
- Immutable checkpoint semantics.
- API/MCP operations for:
  - creating work,
  - retrieving work,
  - adding checkpoints,
  - recalling current work context.
- Migration strategy for existing hand-offs.

## Acceptance Criteria

- Multiple sessions can add checkpoints to one work item.
- Checkpoints cannot be edited after creation.
- A human-facing project view can show one work item even if it has many checkpoints.
- Existing Mnemonic data can be migrated without losing provenance.

---

# Phase 2 - Atomic Work Leases

**Status: Shipped.**

## Objective

Allow multiple agents to safely select and coordinate work without introducing conventional permanent assignees.

## Shipped implementation

Migration `0007_work_leases` and the lease service provide `claim_work`,
`claim_and_recall`, `renew_claim`, and `release_claim`. At most one server-timed
lease exists per work item; a request ID can replay the same active claim, an
opaque token protects renewal and release, and expiry permits safe takeover
without a cleanup worker. Every new or replacement claim rechecks current
blockers and human gates in the acquisition transaction, while ordinary reads
and the dashboard expose only the non-capability lease projection.

## Proposed Model

```text
WorkLease
  work_item_id
  holder_client
  holder_session_id
  acquired_at
  renewed_at
  expires_at
  lease_token
```

A lease is ephemeral ownership, not durable assignment.

## Core Operations

```text
claim_work
renew_claim
release_claim
claim_and_recall
```

`claim_and_recall` is especially important.

It should atomically:

1. select or validate the work item,
2. ensure no unexpired lease already exists,
3. acquire the lease,
4. return the current resume context.

This eliminates races where two agents independently discover the same ready work and both begin executing it.

Phase 2 does not require dependency relationships to exist. Until Phase 3 lands,
"unblocked" is vacuously true for every Pending work item. Once `blocks`
relationships are writable, every claim operation must re-evaluate unresolved
blockers inside the same transaction that acquires the lease. A prior search or
ready-work response is never sufficient authority to claim work.

## Lease Behavior

- Claims have a TTL.
- Agents may renew an active claim.
- Crashed or abandoned sessions naturally lose ownership when the lease expires.
- Explicit release should immediately return the work to the ready pool.
- Lease operations should use a server-generated opaque token to avoid one session accidentally releasing another session's claim.

## Derived State

Do not persist workflow states that can be derived:

```text
ready      = pending + unblocked + no active lease + no unresolved gate
active     = pending + active lease
dropped    = pending + expired retained lease
blocked    = pending + unresolved blocking dependency
waiting    = pending + unresolved gate
deferred   = persisted human hold outside the queue
```

## Acceptance Criteria

- Two agents cannot simultaneously acquire the same exclusive work lease.
- An expired lease does not permanently strand work.
- An agent can resume and renew its own lease.
- Work state displayed to users is derived consistently from underlying facts.

---

# Phase 3 - Typed Work Relationships

**Status: Shipped.**

## Objective

Represent how work relates to other work without forcing every discovered item into a flat issue queue.

## Shipped implementation

Migration `0008_work_relationships` and the relationship service implement all
five edge types with project-local database constraints, normalized identity,
indexed incoming/outgoing traversal, and transactional add/remove operations.
Serialized checks reject self-links, cross-project edges, duplicate parents,
and `blocks` or `parent-child` cycles; linked work and its initial relationships
can be created atomically. Recall, search, MCP, and the dashboard expose graph
context, while unresolved incoming blockers are enforced by readiness and every
claim path. Only `done` resolves a blocker automatically.

## Initial Relationship Types

```text
blocks
parent-child
discovered-from
duplicate-of
related
```

Only `blocks` should affect scheduler readiness initially.

### `blocks`

Represents a true execution dependency.

Example:

```text
WORK-18 blocks WORK-27
```

WORK-27 is not ready until WORK-18 is resolved.

### `parent-child`

Represents decomposition of a larger objective.

### `discovered-from`

Represents work discovered while performing another task.

This is particularly important for agents because they frequently encounter latent bugs, cleanup opportunities, missing tests, documentation gaps, or architectural problems while executing unrelated work.

### `duplicate-of`

Records a directional descriptive assertion that the source duplicates the
target. Phase 9 deliberately does not infer canonical identity from this weak
fact; only an authoritative `merge_work` ledger row makes the source an alias.

### `related`

Descriptive only.

## Cycle Protection

The relationship model as a whole is a typed graph, not a DAG. Only the
project-local `blocks` subgraph is the execution-dependency DAG.

For a canonical edge `A -> B`, read the direction as "A blocks B." Adding an
edge must atomically reject:

- self-links;
- duplicate links;
- cross-project links;
- any edge for which `B` can already reach `A` through `blocks` edges.

Cycle protection must ship in the same increment that first makes `blocks`
relationships writable. Do not accept cyclic data temporarily and defer
validation to ready-work scheduling or a later milestone.

PostgreSQL recursive queries are sufficient for cycle detection and traversal;
this phase does not require a graph database or a general-purpose workflow
engine. Add indexes supporting both incoming and outgoing traversal, and expose
direct and transitive blocker queries where recall or diagnostics need them.

`parent-child` should also reject cycles. If each child has at most one parent,
the result is a project-local forest rather than a general DAG. The remaining
relationship types do not participate in execution ordering:

- `discovered-from` records provenance;
- `duplicate-of` records descriptive duplicate evidence;
- `related` is symmetric and descriptive.

Blocker resolution must be explicit. Initially, only `done` automatically
resolves an outgoing blocker. `wont-do`, `promoted`, and soft deletion must not
silently make dependent work ready; an operator or authorized agent must remove
the `blocks` relationship, or a later first-class waiver mechanism must record
why the dependency no longer applies.

## Deliverables

- Relationship storage.
- Relationship CRUD operations.
- Transactional cycle detection for blockers from the first writable release.
- Acyclic parent hierarchy enforcement.
- Incoming/outgoing traversal indexes and blocker queries.
- Documented blocker-resolution semantics.
- Relationship-aware recall.
- Hierarchical display support.

## Acceptance Criteria

- Blocked work never appears in the ready queue.
- Dependency cycles cannot be created.
- Parent/child cycles cannot be created.
- `wont-do`, `promoted`, or deleted blockers do not silently unblock dependents.
- Discovered work retains a durable link to the context in which it was found.
- Parent/child work can be collapsed in the human UI.

---

# Phase 4 - Ready-Work Discovery

**Status: Shipped.**

## Objective

Give agents a purpose-built way to discover actionable work.

Search and recall are not sufficient coordination primitives.

## Shipped implementation

Migration `0009_ready_work_indexes` and the shared readiness service expose
`GET /projects/{project_id}/ready-work` and the MCP `list_ready_work` tool.
Results are computed at one database-time snapshot, ordered by priority,
creation time, and UUID, and can be filtered by minimum priority, normalized
exact tag, or direct parent. Exact totals and bounded pages exclude non-Pending,
deleted, blocked, actively leased, or gated work. Discovery remains advisory:
claims revalidate eligibility atomically, and `claim_next_ready_work` has not
shipped.

## Proposed Operation

```text
list_ready_work(project_id, ...)
```

The result should contain work satisfying approximately:

```text
status = pending
AND no unresolved blockers
AND no active lease
AND no unresolved gate
```

The blocker predicate is evaluated from the Phase 3 `blocks` DAG. A Pending work
item is dependency-ready when it has no incoming `blocks` edge from unresolved
work. Implementations may use recursive PostgreSQL queries for explanations and
transitive diagnostics, but ordinary readiness should remain a bounded,
indexed query rather than loading the full graph into application memory.

Optional filtering may include:

```text
priority
tags
parent
capability requirements
repository/path scope
creation age
```

Avoid overbuilding scheduling policy initially.

## Future Extension

Potential operation:

```text
claim_next_ready_work(...)
```

This could atomically choose and lease a ready work item according to deterministic server-side ordering.

## Acceptance Criteria

- An agent does not need to search all Pending work to determine what it may safely execute.
- Blocked, leased, gated, or completed items are excluded.
- Ordering is deterministic and documented.

---

# Phase 5 - Append-only Work Event Timeline

**Status: Shipped.**

## Objective

Move collaboration history out of mutable work records.

## Shipped implementation

Migration `0010_work_events` adds immutable, actor-attributed, typed per-work
events with event-specific metadata and references. Supported domain mutations
write their authoritative events in the same transaction; callers may append
only bounded `progress` events directly. The migration conservatively
reconstructs provable earlier facts and labels that history as backfilled.
Recall includes a bounded recent slice, and the dashboard pages the complete
per-work timeline. A durable project-wide feed remains Phase 12 work.

## Proposed Model

```text
WorkEvent
  id
  work_item_id
  actor_client
  actor_session_id
  type
  body
  metadata
  created_at
```

## Candidate Event Types

```text
work_created
work_claimed
work_released
lease_expired
checkpoint_added
progress
blocker_discovered
dependency_added
dependency_removed
verification_run
human_attention_requested
human_attention_resolved
work_completed
work_reopened
promotion_requested
work_merged
```

Phase 9 implements `work_merged` as a paired server event backed by an
immutable merge, rather than treating a generic relationship mark as identity.

Not every event needs a free-form body.

Prefer structured metadata where practical.

## Benefits

- Strong audit history.
- Fewer optimistic-concurrency conflicts.
- Easier reconstruction of "what happened?"
- Better LLM recall.
- Natural future support for streaming activity.

## Human UI

A work item should expose an ordered timeline:

```text
10:14  Claude session A claimed work
10:26  Checkpoint added
10:31  New blocking work discovered: WORK-94
10:32  Lease released
11:08  Codex session B claimed work
11:45  Verification passed
11:46  Work completed
```

## Acceptance Criteria

- Progress and historical facts do not require editing the main work item.
- Events are immutable.
- Event order is deterministic.
- The UI can reconstruct a meaningful work history.

---

# Phase 6 - Idempotent Mutations

**Status: Shipped.**

## Objective

Make covered agent and dashboard retries safe after an ambiguous network or
process boundary. A caller that retains one immutable intent can recover the
original successful result without executing the mutation twice.

## Shipped mechanism

Migration `0013_idempotent_mutations`, directly after
`0012_pending_deferred_statuses`, adds a private durable receipt ledger while
preserving existing production work, event, and metadata content. A covered
REST request may carry one caller-generated top-level UUID:

```text
client_operation_id
```

Direct REST callers may omit it and retain the earlier unprotected behavior.
Canonical MCP tools require it, and the dashboard generates it before sending
any of its covered actions. Neither the server nor MCP adapter invents a key for
the caller.

The shipped uniqueness scope deliberately strengthens the roadmap's earlier
session-scoped suggestion:

```text
project_id
client_operation_id
```

Operation kind, target IDs, complete validated provenance/actor data, expected
version, metadata, and other semantic arguments are part of a salted canonical
fingerprint. They do not widen the unique namespace. The ledger stores the
salted digest and bounded successful response snapshot, not the raw request,
bearer credential, or lease token.

A fully serialized successful `2xx` result binds the UUID indefinitely,
including a natural no-op such as `created=false`, `removed=false`, or
`released=false`. Definite validation/domain failures and pre-commit failures
roll the pending receipt and domain effects back together. A matching completed
receipt is validated and replayed before current work, relationship, lifecycle,
version, or lease guards. Replay returns the original status and parsed JSON
snapshot without recreating an edge, changing current work, duplicating an
event, or releasing a replacement lease. It is historical outcome evidence,
not a current-state read or live capability.

The server fails closed with sanitized stable errors:

- `client_operation_conflict` (`409`) when a project/key is already bound to a
  different successful semantic request;
- `client_operation_unavailable` (`503`) when receipt safety or the transaction
  outcome cannot be proven; and
- `client_operation_secret_echo` (`422`) when operation/control/capability
  material is copied into forbidden public content.

There is no receipt read, purge, cancellation, or argument-recovery endpoint,
no replay wrapper or header, and no compatibility alias for the former
prerelease MCP schemas. Operation UUIDs never enter ordinary domain responses,
events, checkpoints, search/recall, live invalidations, or logs.

## Shipped coverage

Ten REST mutations use the generic receipt contract:

- `create_work`;
- `add_checkpoint`;
- `append_event`;
- `add_relationship` (all five relationship types);
- `update_work`;
- `defer_work` (REST/dashboard only);
- `complete_work`;
- `delete_work`;
- `remove_relationship`;
- `release_claim`.

The nine operations other than `defer_work` are canonical MCP tools. Those
tools require the UUID and are the only mutating MCP tools with
`idempotentHint=true`. The catalog remains unchanged in size; Phase 6 changes
the protected tool schemas rather than adding duplicate or receipt-management
tools.

The dashboard covers the nine browser-accessible operations in that list:
create work, add a checkpoint, append progress, add a relationship, update,
defer, complete, delete, and remove a relationship. It does not expose release
or any lease-token path.

`create_project`, REST-only `update_project`, `claim_work`,
`claim_and_recall`, and `renew_claim` remain outside the generic ledger. Claims
keep their separate `claim_request_id` replay only while the identical lease is
active; renewal remains a new time-relative intent. At the Phase 6 release,
gate creation and verification submission remained future enrollment work
because their Phase 7 and Phase 11 domain contracts did not yet exist. Phase 7
has since enrolled gate creation; verification submission remains deferred to
Phase 11.

## Recovery contract

Before the first protected call, the caller generates one UUID and privately
retains it with the complete exact tool name and arguments. Timeout, disconnect,
reset/EOF, malformed success JSON, a backend/proxy `5xx`, or
`client_operation_unavailable` leaves the outcome unknown. The caller retries
only the frozen call with the same UUID; MCP makes one outbound attempt per tool
invocation and does not retry automatically.

Phase 9 adds one typed exception to that general rule:
`503 duplicate_graph_invalid` is a definitive integrity stop, not an unknown
protected-write outcome. Callers stop authority-changing work and involve an
operator instead of retrying it.

Any changed tool, target, actor/source value, expected version, metadata, token,
or other argument is a new intent and needs a new UUID. A conflict on an
asserted exact retry is a caller-safety incident, not permission to switch keys.
If either the UUID or exact arguments are lost, the caller must stop, inspect
current state where safe, and request direction rather than guess a retry.

The dashboard keeps frozen browser intents only for the current document. An
ambiguous result survives pane deselection, dialog closure, or component
unmount, blocks intersecting writes, and can be resent exactly. Strict
operation-specific response decoding clears it only after a coherent expected
success or definite rejection. It is never persisted across tabs, reloads, or
browser-process loss, and Phase 6 makes no safe-retry claim after that private
state disappears.

## Shipped acceptance criteria

- Exact same-key retries return the original successful response and create no
  duplicate domain rows, events, version changes, or lease effects.
- Concurrent identical requests serialize to one execution and one durable
  result; same-key semantic mismatches never execute.
- Historical replay remains available after later edit, reopen, deletion,
  relationship removal, lease replacement, backend restart, and backup/restore.
- Existing production content is preserved through `0013`; unsafe downgrade is
  refused once completed receipts exist.
- MCP, browser, proxy, plugin, operations, and agent guidance use the same
  immutable-intent and unknown-outcome contract while keeping claim recovery
  separate.

---

# Phase 7 - First-class Human Gates

**Status: Shipped with Phase 8.**

## Objective

Create a deliberately small, explicit queue of questions and decisions that
truly require human attention. Human gates are durable work-graph facts, not
labels, inferred blockers, agent-authored approvals, or notification messages.

## Shipped model

Migrations `0014_human_gates` and `0015_gate_review_fixes` provide
immutable project/work-scoped `WorkGate` records with:

```text
id, project_id, work_item_id, gate_type=human, attention_sequence
question, requested_by_client/session/model, created_at
requested work/context/relationship revision
status=unresolved|resolved
resolution, resolved_by_client/session/model, resolved_at
resolved reviewed revision
```

One gate request and its exact `human_attention_requested` event commit atomically.
The only update is one unresolved-to-resolved transition with an exact
`human_attention_resolved` event; questions, provenance, anchors, answers, and both
events are immutable. Requester/resolver provenance is asserted under the
single-user bearer boundary, not authenticated identity or independent
verification.

An unresolved gate makes Pending work `waiting`, removes it from ready
discovery, rejects fresh/replacement claims, and prevents completion, terminal
transitions, and deletion. Deferral remains an independent human hold; resolving
a gate does not undefer work. It does not revoke an existing capability: exact
active claim replay, renewal, release, checkpoints, progress, identity edits,
deferral/Pending restoration, and relationship changes remain possible. Several
gates may coexist, and every one must resolve before waiting ends.

Gate reads nest the frozen request anchors under `requested_context_revision`
and expose backend-computed current and resolution drift booleans. Clients do not
rederive those convenience values. Resolution always requires a frozen
three-field reviewed context revision: work version, newest context checkpoint,
and relationship-event count. Another change
before commit returns
`gate_context_changed` and requires a new human review and operation intent. A
stored answer remains historical context rather than renewed authority to
execute.

## Shipped interfaces

- `POST .../gates` requests input after the caller checks existing unresolved
  gates and writes supporting context first.
- `GET /projects/{project_id}/human-attention` cursor-pages unresolved gates in
  allocated sequence order and supports a text-free exact count. A complete
  consumer restarts from the head once before declaring the queue drained because
  sequence allocation can precede commit.
- `GET .../{work_item_id}/gates` pages complete paired history, including an
  exact retained soft-deleted work ID.
- `POST .../{gate_id}/resolve` is the direct REST/dashboard human action. MCP
  deliberately exposes no resolution tool.
- Bounded recall carries unresolved and recent-resolved slices with exact totals
  and omitted counts; gate request/resolution also appear in event history.
- The MCP catalog adds `request_human_input`, `list_human_attention`, and
  `list_work_gates`, reaching exactly 25 tools and ten protected MCP writes.
- The permanent receipt registry reaches exactly twelve REST operations; gate
  resolution becomes the dashboard's tenth frozen mutation.

## Shipped acceptance criteria

- Agents can request concrete human input with exact-retry recovery but cannot
  infer, self-supply, or submit the answer through MCP.
- Gated work leaves ready discovery and fails fresh claim/terminal/delete paths
  closed without breaking an already-issued capability's recovery/release.
- Humans see one dedicated Needs Attention queue and resolve against the exact
  reviewed current revision through the dashboard.
- Questions and answers remain distinguishable, immutable, cursor-pageable
  history and bounded recall context without entering search indexes, logs,
  metrics, browser storage, or data-free invalidation frames.
- Existing production content is preserved through `0015`; migration 0015 has no
  supported downgrade, so recovery requires a forward fix or an explicit
  whole-archive restore boundary.

---

# Phase 8 - Hierarchical Human Presentation

**Status: Shipped with Phase 7.**

## Objective

Prevent Mnemonic itself from recreating issue-tracker noise. Agents retain the
complete graph, while humans see collapsed workstreams with exact branch facts
and progressive disclosure.

## Shipped presentation

Root and child pages keep the existing parent-child graph and add one-statement
`HierarchyPresentation` data:

```text
direct_child_count
descendant_count
blocked_descendant_count
active_descendant_count
completed_descendant_count
discovered_descendant_count
branch_unresolved_human_gate_count
is_discovered_work
discovered_from_parent
next_active_descendant_lease_expires_at
```

Descendant state counts are intentionally independent: one item may be active,
blocked, and waiting. Descendant counts are strict, while the branch gate count
is inclusive of the displayed node. Discovery labels distinguish planned
`parent-child` decomposition, discovery from the displayed parent, discovery
from elsewhere, and ungrouped discovered roots.

The dashboard collapses descendants by default, lazily pages direct children,
and explains when lifecycle/source/tag filters retain a muted ancestor only to
reach a matching descendant. Branch presentation counts remain complete,
unfiltered facts; page and qualifying-root totals follow the selected filter.
Every flat full-view search or browse result has a bounded breadcrumb; roots
legitimately have an empty path. Depth/cycle fallbacks
bound damaged graphs, and the earliest active descendant lease expiry schedules
passive count refresh without a polling scheduler.

The backend pages before computing member facts, disables JIT for the hierarchy
transaction, and computes total, aggregates, match flags, discovery facts, and
lease-time state in one PostgreSQL statement and database-time snapshot. A
five-second cancellation returns typed `hierarchy_timeout`. There is no Python
tree walk, per-branch query, or load-all-descendants fallback.

## Shipped acceptance criteria

- Agent-generated descendants are collapsed out of the default human root view.
- A human can expand any workstream and distinguish planned from discovered
  work without losing complete branch counts.
- Needs Attention, work detail, event/gate history, context, hierarchy, and the
  sidebar count converge through the existing data-free invalidation channel.
- Agents continue to receive complete explicit graph adjacency through canonical
  relationship tools; presentation aggregates do not become authority or an
  execution queue.

---

# Phase 9 - Structural Duplicate Handling

**Status: Shipped.** Application/API/MCP/dashboard `0.4.0`, plugin `0.8.0`, and
migration `0017_duplicate_suggestion_title_key` implement both the Core
authoritative merge and Advisory duplicate suggestions. The validation record
does not claim production cutover, backup rehearsal, or product/operator
permanence signoff.

## Objective

Handle inevitable duplicate work safely and explicitly.

## Implemented Core distinction

```text
duplicate mark
  descriptive source --duplicate-of--> target evidence

authoritative merge
  immutable source alias -> direct canonical destination decision
```

Migration 0016 preserves every old duplicate mark and creates no merge from it.
Fresh generic marks are closed; `merge_work` reuses or creates its one exact
supporting mark atomically. The retained source keeps its lifecycle,
checkpoints, events, gates, relationships, provenance, and receipts, but it is
never ready or claimable and every fresh alias mutation fails without redirect.
Exact alias reads remain source-owned and explicitly identify a bounded path to
the current root.

## Implemented Core operation

```text
merge_work(source_id, destination_id)
```

A merge is non-destructive and permanent. It requires two exact current-root
context revisions, a rationale, truthful asserted provenance, and a mandatory
operation UUID. The source must have no unresolved gate or incident
`blocks`/`parent-child` relationship. An active source lease requires its exact
token; the browser cannot carry that capability and disables merge. One
transaction commits the immutable ledger row, exact supporting relationship,
paired evidence/merge events, endpoint version increments, source lease
consumption when applicable, and receipt.

Core deliberately does not:

- infer a merge from similarity, wording, lifecycle, marks, or embeddings;
- redirect a supplied alias ID or silently substitute the root;
- transfer or coalesce content, lifecycle, relationships, leases, gates,
  provenance, or authority;
- provide unmerge, retarget, merge deletion, or row-level repair; or
- suppress creation or expose a dormant suggestion route.

A mistaken merge requires a complete pre-merge database restore that discards
every later write, or a future separately designed append-only correction
release.

## Implemented Advisory suggestions

On explicit action from a valid creation draft, Mnemonic can show:

```text
Possible existing work
```

without automatically merging, persisting the draft/result, or suppressing
creation. Exact-title candidates are reserved first, lexical retrieval supplies
a bounded shortlist, and local embeddings either cover the full eligible
project or that disclosed shortlist. Results group aliases under one canonical
candidate and expose only categorical signals and semantic coverage.

Embeddings should produce candidates, not truth.

The `suggest_duplicate_work` safe read has bounded body, request, inference,
shortlist, fill, population, response, and timeout budgets. Saturated or failed
model work falls back to lexical results; database/system failures are explicit.
Ordinary search and Create remain independent, and similarity never authorizes
`merge_work`.

## Acceptance Criteria

- Duplicate history is never deleted.
- Canonical work identity is explicit.
- Existing references remain understandable.
- Semantic similarity never silently changes work structure.
- Exact history remains separate from canonical continuation; no redirect or
  coalescing occurs.
- Same-key merge recovery returns one historical result without another
  durable effect.
- Both separately versioned Core and Advisory repository implementation gates have
  concrete evidence; deployment recovery gates remain operator work.

---

# Phase 10 - Repository Freshness Verification

**Status: Shipped in the repository.** The coordinated boundary is
application/API/MCP/dashboard `0.5.0`, plugin `0.9.0`, and migration
`0018_repository_freshness`. Production-target preflight, approval, backup,
and quiesced cutover remain explicit operator work.

## Objective

Make checkpoint provenance actionable without overstating what Git can prove or
giving the server access to a checkout.

## Implemented checkpoint declaration

Every full checkpoint input and read can carry:

```text
repository_branch
verified_against
affected_paths
```

`affected_paths` is an ordered list of source dependencies for that exact
checkpoint's assertions, not merely files its author changed. A non-empty scope
requires the commit the caller actually inspected in `verified_against`.
`repository_branch` remains optional display provenance and is never resolved
or compared by the helper.

Omission and explicit `[]` normalize to one unknown value whose canonical JSON
omits the property. Empty never means no changes or whole-repository coverage;
the literal `**` explicitly declares all eligible repository paths. Non-empty
order, spelling, and case are preserved and bind new receipt fingerprints and
coherence checks. Historical rows receive an empty array, and the sparse wire
form preserves every existing request fingerprint and stored response body.

The v1 grammar permits only slash-separated ASCII components containing letters,
digits, `.`, `_`, `@`, `+`, `=`, `,`, `~`, `-`, and `*`. A single star stays
within one component; `**` is valid only as a whole component. The release caps
scope at 64 entries, 512 bytes per entry, and 16,384 bytes total. It rejects
duplicates, requires every pattern to match independently before `unchanged`,
and does not trim, normalize, sort, expand, infer, or backfill paths. For
example:

```text
backend/src/mnemonic_api/**
backend/alembic/versions/0018_repository_freshness.py
backend/tests/test_repository_freshness_migration_postgres.py
```

Scope appears only on full checkpoint reads: initial/current/recent context,
checkpoint history, receipt-protected create/add/complete responses, resources,
and resume prompts built from full context. Compact pointers, search, hierarchy,
relationships, gates, readiness, events, embeddings, duplicate suggestions, and
derived-cache identity remain scope-free. Search or a pointer must therefore be
followed by full recall before assessment.

Application/MCP/dashboard 0.4.x clients are unsupported once a non-empty scope
exists. Phase 10 updates first-party clients together and adds no legacy/current
model union, response downgrade projection, receipt rewrite, alias field, or
dual database write.

## Implemented local assessment boundary

The backend persists immutable caller declarations but does not inspect Git,
derive paths, accept an assessment, or add a freshness route or tool. The MCP
adapter transports full checkpoint data but is also repository-blind. Browser
code can accept and display declarations but does not inspect a checkout. Only
the installed plugin's `mnemonic-repository-freshness` helper examines the
explicitly selected current local workspace.

The client assesses only the governing full checkpoint whose assertions it is
about to rely on. It never guesses a checkout from the mutable project
repository URL, and it asks for a choice when multiple workspaces could be
intended. View, copy, or summary alone does not execute the helper. The helper
takes only one hexadecimal baseline and 1–64 validated paths; it receives no
dynamic root, project ID, URL, branch, refname, config, or output destination.

The packaged runtime requires Bash 3.2 or newer and Git 2.45.0 or newer, with a
15-second caller-enforced whole-process-group deadline. It hardens Git
configuration and environment, disables lazy fetch, replacement objects, and
user attributes and ignores, and never fetches, clones, checks out, writes
repository state, invokes configured processes, hooks, index/worktree
conversion, textconv, filters, fsmonitor, pagers, editors, credentials, or SSH,
or contacts a remote. Worktree bytes are hashed raw with `--no-filters`;
conditions that make a complete zero unsafe fail closed.

The exact `mnemonic-repository-freshness-v1` ASCII protocol reports one state:

```text
unchanged      no relevant eligible Git change was observed
changed        a repeatable relevant Git change was observed
indeterminate  the comparison could not establish either result safely
```

The two-stage assessment first requires a resolvable commit baseline that is
equal to or an ancestor of captured `HEAD`. Two bracketed sweeps then cover
committed, staged, unmerged, raw unstaged, and nonignored untracked evidence,
with one whole retry after a moving `HEAD`. One stable observation is enough for
`changed`; only two stable, complete zero sweeps produce `unchanged`. Unmatched
patterns, directory ambiguity, index flags, sparse state,
`core.fileMode=false`, normalization or filter state, symlinks, gitlinks,
command failure, and races make a zero result `indeterminate`.

Ignored untracked files, submodule interiors, generated or external artifacts,
runtime state, external symlink targets, and semantic correctness are outside
the result. Actual names are byte-quoted, capped at 100, and enter tool and model
context only as privacy-sensitive evidence; helper stdout is capped at 32 KiB.

The client presents evidence-oriented language such as:

```text
Repository freshness: RELEVANT CHANGE OBSERVED
Checkpoint baseline:  a832bc1… (resolved locally)
Current HEAD:          d7be142…
Declared scope:        3 patterns

  app/services/foo.py
  tests/test_foo.py

Reinspect current source before relying on this checkpoint.
This is a Git-state comparison, not a semantic-correctness result.
```

`changed` and `indeterminate` require source reinspection or a repository choice.
`unchanged` only means that no relevant eligible Git change was observed. No
outcome grants authority, resolves a gate, changes readiness or lifecycle,
renews a lease, mutates a work item, or proves a checkpoint correct, current,
verified, or safe. Results remain ephemeral client evidence and are never
copied automatically into checkpoints or events.

## Acceptance Criteria

- Existing production rows and all permanent receipt bytes are preserved by the
  empty-only migration and sparse canonical serialization.
- Full checkpoints carry exact ordered declared scope while compact and derived
  surfaces remain unchanged.
- Repository-aware clients warn and reinspect on `changed` or `indeterminate`;
  they never turn `unchanged` into semantic or execution authority.
- The server, MCP adapter, and browser never mount or trust a repository, and
  the local helper performs no repository mutation, configured process launch,
  or network operation.
- A guarded downgrade succeeds only before any non-empty scope exists; after
  scoped use, recovery is fix-forward or whole-database restore rather than a
  lossy compatibility path.

---

# Phase 11 - Structured Completion Evidence

**Status: Planned; completion history shipped.** Mnemonic already requires a
completion checkpoint and records an immutable completion event, but it has no
structured verification-result or artifact-reference model.

## Objective

Allow completed work to answer:

> What proves this was actually completed?

rather than merely:

> An agent changed the status to done.

## Proposed Model

```text
VerificationResult
  id
  work_item_id
  checkpoint_id
  command
  exit_code
  summary
  commit
  created_at
```

Not every verification needs a shell command, so the schema should permit different evidence types later.

## Artifact References

First-class references may include:

```text
commit
pull request
branch
test run
file/path
external issue
build artifact
```

## Design Principle

Keep prose verification instructions in the checkpoint where useful, but store final execution evidence structurally.

## Acceptance Criteria

- Completed work can include machine-readable verification evidence.
- Evidence is append-only.
- Agents can retrieve evidence without parsing long prompt text.
- Humans can inspect completion evidence directly.

---

# Phase 12 - Project Activity Feed

**Status: Planned; per-work activity shipped.** Phase 5 provides paged per-work
event timelines, and the dashboard receives data-free invalidations. There is
no durable project-wide cursor/feed, SSE stream, or webhook surface yet.

## Objective

Provide an efficient incremental coordination API.

Search answers:

> What work matches this idea?

An activity feed answers:

> What changed since I last looked?

## Proposed API

```text
get_activity(
    project_id,
    after_event_id=...
)
```

Use monotonically ordered project events or another stable cursor.

## Future Uses

This can later back:

- SSE,
- webhooks,
- MCP subscriptions,
- live dashboard updates,
- external orchestrators.

Do not require real-time transport initially. The durable ordered feed is the important primitive.

## Acceptance Criteria

- Agents can cheaply retrieve changes since a known cursor.
- Feed ordering is deterministic.
- Clients can resume after interruption without missing events.

---

# Phase 13 - Resource Reservations

**Status: Planned.** Phase 2 leases coordinate ownership of a work item, but no
generic resource-key reservation model or operations have shipped.

## Objective

Coordinate shared resources independently from logical work ownership.

A work lease means:

> I am currently responsible for this work.

A resource reservation means:

> I need temporary exclusive or advisory access to this shared resource.

These should remain separate concepts.

## Possible Resource Keys

```text
files:app/models/**
files:migrations/**
file:package-lock.json
build:frontend
environment:integration
gpu:0
database:migration-runner
```

## Proposed Operations

```text
reserve_resource
renew_resource_reservation
release_resource
```

Use TTL semantics.

## Initial Recommendation

Defer this until work leases and dependency-aware readiness are proven.

File and resource locking can become operationally complex quickly.

## Acceptance Criteria

- Reservations expire automatically.
- Work ownership and resource ownership remain independent.
- Reservation conflicts are visible to agents.
- The system does not imply that a reservation proves nobody can modify an external resource.

---

# Deferred Features

The following ideas may eventually be useful, but should not be early priorities.

## Direct agent-to-agent messaging

This can quickly expand into:

- identities,
- inboxes,
- receipts,
- acknowledgements,
- unread state,
- threading,
- routing,
- presence,
- contact discovery,
- notification policy.

Mnemonic can achieve strong coordination through durable work state without becoming a messaging platform.

Prefer checkpoints, events, gates, and activity feeds first.

## Cross-project dependencies

Useful eventually, but they complicate:

- authorization,
- lifecycle semantics,
- project deletion,
- readiness calculation,
- portability.

Keep dependencies project-local initially.

## Sophisticated scheduling

Avoid early introduction of:

- capability matching,
- load balancing,
- cost-aware routing,
- model-specific work queues,
- complex priority formulas.

Expose enough structure that an external orchestrator can eventually make those decisions.

## Automatic semantic merging

Do not allow embedding similarity to silently merge work or create dependencies.

## Large workflow status taxonomies

Avoid reproducing Jira workflow complexity.

Derive operational state from leases, blockers, gates, and persistent terminal state wherever possible.

---

# Suggested MCP Surface

A mature but still compact MCP interface might eventually resemble:

## Work

```text
create_work
get_work
update_work
complete_work
reopen_work
list_ready_work
```

## Checkpoints

```text
add_checkpoint
recall_work
```

## Claims

```text
claim_work
claim_and_recall
renew_claim
release_claim
```

## Relationships

```text
add_relationship
remove_relationship
list_relationships
```

## Events

```text
append_event
get_activity
```

## Human gates

```text
request_human_input
list_human_attention
list_work_gates
```

Human resolution is intentionally a direct REST/dashboard action, not an MCP
tool an agent could self-invoke.

## Verification

```text
add_verification_result
list_verification_results
```

## Duplicate handling

```text
merge_work
suggest_duplicate_work
```

There is no generic `mark_duplicate` tool. Historical marks remain evidence;
only `merge_work` creates a fresh supporting `duplicate-of` relationship.

## Resource coordination - later

```text
reserve_resource
renew_resource_reservation
release_resource
```

The MCP interface should favor coarse-grained atomic operations over forcing agents to assemble safe workflows from many low-level calls.

---

# Suggested Implementation Order

## Milestone 1 - Durable Work Graph Foundation

**Status: Shipped through Phases 1 and 3.**

1. Introduce `WorkItem`.
2. Convert existing hand-offs into immutable checkpoints.
3. Add typed work relationships and traversal indexes.
4. Enforce a project-local DAG for `blocks` and an acyclic parent hierarchy
   from their first writable release.
5. Define and enforce blocker-resolution semantics.
6. Add hierarchy support.
7. Migrate existing data.

This establishes the long-term data model.

## Milestone 2 - Safe Multi-agent Execution

**Status: Shipped through Phases 2, 4, and 6.**

1. Add TTL work leases.
2. Add atomic `claim_and_recall`.
3. Add blocker-aware `list_ready_work`.
4. Make every claim operation recheck blockers atomically before leasing work.
5. Add idempotent mutation keys. **Shipped in Phase 6.**

At this point multiple agents can safely share a project.

## Milestone 3 - Durable Collaboration History

**Status: Partially shipped.** Phase 5 delivered append-only per-work events and
the timeline UI, and Phase 9 implements authoritative duplicate merging plus
advisory comparison. The Phase 12 project activity feed remains planned.

1. Add append-only work events.
2. Add project activity feed.
3. Add structural duplicate handling.
4. Improve timeline UI.

This creates a robust audit and recovery model.

## Milestone 4 - Human Oversight

**Shipped together in Phases 7–8.**

1. Human gates with immutable request/answer history and drift-aware review.
2. A dedicated `Needs Attention` dashboard and text-free sidebar count.
3. Hierarchical, collapsed workstreams with exact one-statement branch facts.
4. Agent-generated descendants kept out of the default top-level human view.

This directly addresses the original GitHub Issues noise problem.

## Milestone 5 - Provenance and Verification

**Status: Phase 10 shipped.** Declared repository dependency scope and local
advisory assessment are implemented; Phase 11 completion evidence and artifact
references remain planned.

1. Add `affected_paths`. **Shipped in Phase 10.**
2. Add repository freshness checks to the MCP client skill. **Shipped in Phase
   10 as a local three-state assessment.**
3. Add structured verification results.
4. Add artifact references.

This improves trust in resumed and completed work.

## Milestone 6 - Advanced Coordination

**Status: Planned.**

1. Resource reservations.
2. Additional gate types.
3. Cross-project relationships if necessary.
4. Optional event streaming/webhooks.
5. External orchestration hooks.

Only implement these after real usage demonstrates the need.

---

# Architectural Decisions to Preserve

The following existing Mnemonic instincts should remain intact:

- A hand-off should not become a conventional issue ticket.
- The server should not claim to have independently verified repository state that it only stores as client-supplied provenance.
- Agent-authored text should be treated as untrusted input.
- Search should remain retrieval, not authority.
- Concurrency controls should be explicit rather than optimistic assumptions.
- Human-facing GitHub Issues should remain reserved for genuinely human-relevant or externally visible work rather than internal agent coordination.

---

# Success Criteria for Mnemonic as a Product

Mnemonic is succeeding if:

1. A new agent session can safely determine what work is actionable without reading the entire project history.
2. Two agents cannot accidentally claim the same exclusive work.
3. Agents can discover and record additional work without overwhelming the human dashboard.
4. A human can understand the major active workstreams without seeing every machine-generated checkpoint or subtask.
5. A resumed agent can determine who created prior context, when it was created, what repository state it referenced, and whether that context may now be stale.
6. Completed work carries evidence rather than relying only on an agent's assertion.
7. Crashed or abandoned sessions do not permanently strand work.
8. Retries and ambiguous network failures do not create duplicate records.
9. The full history remains auditable.
10. Mnemonic absorbs coordination noise that would otherwise pollute GitHub Issues.

---

# Product Thesis

Mnemonic should not become merely:

> GitHub Issues for LLM agents.

A stronger product definition is:

> **Mnemonic is a durable work graph for ephemeral agents, where work survives sessions and agents leave immutable checkpoints for whoever continues it next.**

That framing should guide future feature decisions.

When considering a new feature, ask:

> Does this improve durable coordination across temporary agents, or are we merely reproducing a familiar human issue-tracker feature?

Prefer the former.
