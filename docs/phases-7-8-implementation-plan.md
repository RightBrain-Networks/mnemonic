# Mnemonic Phases 7–8 — Human Gates and Hierarchical Presentation Implementation Plan

**Status:** Historical implementation plan, amended after the 2026-09-02 review

**Scope:** Roadmap Phase 7, “First-class Human Gates,” and Phase 8, “Hierarchical Human
Presentation,” delivered as one release

**Source of product intent:** `docs/roadmap.md`

**Planning precedent:** `docs/phase-6-idempotent-mutations-implementation-plan.md`

**Implementation baseline:** local `main` at commit `aa91181` (`Bound browser mutation recovery`),
with Phase 6 migration head `0013_idempotent_mutations`

**Planning origin:** this document was the code-free implementation contract. Implementation was
separately authorized, completed, and validated; the final evidence is recorded in
docs/validation.md.

## Post-review amendment — 2026-09-02

The original plan below is retained as a record of the design that produced the
Phase 7–8 implementation. The corrective review changed several owner decisions;
the following contract supersedes conflicting fence, retry, resolution, cursor,
migration, performance, and rollout language later in this document. Current
operator and client behavior is specified in `docs/operations.md` and
`docs/api-contract.md`.

- Migration head `0015_gate_review_fixes` preserves existing rows, uses
  `clock_timestamp()` for concurrent checkpoint and relationship ordering, and
  removes persisted `context_change_acknowledged` and
  `context_changed_at_resolution`. Migration 0015 has no supported
  downgrade; recover by fixing forward or restoring a complete pre-upgrade
  archive, never by editing gate, event, or receipt history.
- Human-gate creation has no runtime request fence. Request idempotency validates
  request-known operation controls only; it does not scan retained UUIDs or treat
  a returned gate ID as a secret. Before asking, inspect existing unresolved
  gates, write the supporting context checkpoint, and then request the gate. An
  agent cannot withdraw a gate; if it becomes moot, record why and let a human
  resolve it in the dashboard as no longer needed.
- Gate reads expose a nested `requested_context_revision` plus backend-computed
  current and resolution drift flags; clients do not rederive those convenience
  values. Every resolution supplies `reviewed_context_revision`, even without
  drift. The reviewed work version, checkpoint ID, and relationship-event count
  are the single explicit
  review precondition; there is no separate acknowledgement boolean. Deferral is
  an independent human hold and neither resolves nor prevents resolving a gate.
- Attention cursors exclude already-seen sequence keys, but insertion sequence is
  not commit order. A lower sequence that commits after a later cursor was read
  appears only after restarting without a cursor. Callers must make that head
  restart before concluding the Needs Attention queue is drained.
- Hierarchy reads disable PostgreSQL JIT, select the requested page before
  enriching its rows, and return typed `503 hierarchy_timeout` guidance on the
  statement-timeout boundary. Every item in a `view=full` page has an
  `ancestor_path`, including blank-query browsing. Earlier hierarchy timings in
  `docs/validation.md` are historical baselines, not current release evidence.
- The supported deployment is the repository single-host Compose stack: stop its
  writers, back up, migrate once, rebuild, verify schema/model parity with
  `backend/tests/test_schema_parity_postgres.py::test_migrated_schema_matches_orm_metadata`,
  and then reopen traffic. The corrective plugin release is `0.6.1`.

## 1. Outcome

Phases 7 and 8 ship together as Mnemonic's first complete human-oversight milestone. An agent can
create a durable, explicit question on a work item; that unresolved gate immediately participates
in the same readiness and fresh-claim authority as blockers and leases. A human can inspect the
small explicit queue, provide one durable answer, and cause the work to become gate-eligible again
without editing history. At the same time, the dashboard presents the existing parent-child forest
as collapsed workstreams with branch-level operational counts, rather than exposing every agent
subtask as a peer at the top level.

The combined release must have these observable properties:

1. An unresolved human gate is a first-class persisted fact, not a label, status value, checkpoint
   convention, progress phrase, or inferred condition.
2. Ready discovery and every fresh lease acquisition use the same gate predicate. Once a gate
   commits, the work cannot newly enter the ready queue or acquire a fresh/replacement lease.
3. A gate may be requested while a valid lease already exists. It does not revoke that capability;
   exact active-claim replay, renewal, release, checkpoints, and progress remain recoverable.
4. Completion, terminal retirement/promotion, and soft deletion cannot race past an unresolved
   gate. The gate request and those transitions serialize on the focal work row.
5. A gate request and its `human_attention_requested` event commit atomically. Its one resolution
   transition and `human_attention_resolved` event also commit atomically. Questions, answers,
   provenance, and events cannot be edited or deleted afterward.
6. Gate request and resolution enroll in the Phase 6 receipt registry. Exact retries recover the
   original typed result, including after later resolution or work deletion, without duplicating a
   gate, answer, event, activity update, or other durable/domain effect; an applied replay may emit
   at most one optional data-free healing invalidation.
7. The dedicated `Needs Attention` view contains only explicit unresolved human gates, uses an
   immutable insertion cursor rather than shifting priority/offset pages, and gives every question
   its workstream breadcrumb and request provenance.
8. Bounded recall always reports authoritative unresolved/resolved totals, a bounded slice of
   unresolved questions, and a separate bounded slice of recently resolved question/answer pairs.
   Complete gate history remains pageable independently of ordinary event recency.
9. Blank dashboard browsing remains root-only and collapsed by default. Each branch reports direct
   children, all descendants, blocked/active/completed descendants, discovered descendants, and
   unresolved human gates without materializing a mutable counter cache.
10. Human presentation is derived strictly from explicit `parent-child` and `discovered-from`
    edges. Mnemonic never invents a parent from discovery provenance, search similarity, wording,
    or adjacency.
11. Agent retrieval, ready discovery, recall, and relationship traversal retain the full graph.
    Human progressive disclosure does not hide or remove graph facts from canonical agent APIs.
12. Existing projects, work, checkpoints, relationships, leases, events, receipts, and browser
    preferences migrate unchanged. No historical question, resolution, gate, or event is
    fabricated.
13. The migration and new backend form one safety boundary. Database guards make an accidentally
    routed older backend fail closed on fresh/replacement claims and gated terminal/delete writes;
    deployment still drains old processes because their ready reads cannot understand gates.
14. No phase adds authenticated users, notifications, arbitrary gate types, automatic approvals,
    automatic parent inference, closure tables, cached hierarchy counts, or a new workflow-status
    taxonomy.

The release is intentionally combined. A gate without the attention queue and branch aggregation
would create invisible waiting work. A hierarchy without gate counts would conceal the very human
decisions that Phase 7 is intended to surface. Neither phase is considered delivered separately.

## 2. Shipped Phase 6 baseline

Implementation starts from the repository as shipped, not from the roadmap's earlier sketches.
Several pieces of Phase 8 already exist; the work is to complete and harden them while integrating
the new gate fact.

### 2.1 Existing work graph and human hierarchy

- `WorkItem` is the durable mutable identity/lifecycle object; checkpoints and work events are
  immutable history.
- `parent-child` is a project-local acyclic forest with at most one parent per child.
- `discovered-from` is independent provenance: `A discovered-from B` means A was discovered while
  working on B and cites B-owned context. It does not make B the structural parent.
- Blank dashboard browsing already requests `view=roots`, returns only structural roots, and lazily
  pages direct children. A free-text query switches to flat direct hits with bounded ancestor
  breadcrumbs.
- Root/child filtering is subtree-aware. A nonmatching ancestor may be retained as navigation
  scaffolding when a descendant matches.
- The browser has explicit cycle and depth fallbacks at 50 levels even though normal writes enforce
  an acyclic forest.
- `HierarchySummary` currently exposes only `self_matches_filter` and
  `has_matching_descendants`; aggregate descendant and gate facts do not exist.
- The current hierarchy service is located in the relationship module and hydrates summaries in
  later statements. Phase 8 may extract a focused hierarchy service rather than expanding that
  coupling indefinitely.

### 2.2 Existing readiness and claim seam

The canonical readiness predicate currently combines:

```text
pending lifecycle
AND no unresolved incoming blocker
AND no active lease
AND gate_eligibility_clause(work_item_id)
```

`gate_eligibility_clause` deliberately returns SQL `true` today. Both the one-statement ready page
and fresh-claim validation call the shared eligibility builder, and a PostgreSQL regression test
already proves that replacing the gate seam affects both paths. Phase 7 replaces that seam with an
indexed `NOT EXISTS` over unresolved gates; it must not add a second, route-specific definition.

The public `Readiness` projection currently reports lifecycle, blocker, active/dropped lease,
ready, and display-state facts. Multiple facts may overlap: for example, a work item can be both
blocked and actively leased. Gates extend this independent-fact model rather than introducing a
stored `waiting` lifecycle.

### 2.3 Existing lifecycle and lease behavior

- Persistent lifecycle values are `pending`, `deferred`, `done`, `wont-do`, and `promoted`.
- `active`, `dropped`, and `blocked` are derived. Phase 7 adds derived display state `waiting`.
- A fresh claim locks the work row, then the retained lease, and rechecks canonical eligibility.
- An identical still-active `claim_request_id` replays its existing lease before fresh eligibility
  checks. This capability-recovery property must survive a later gate.
- Renewal and release operate on an existing capability. They do not constitute fresh work
  selection.
- Completion already rejects unresolved blockers. Terminal update and deletion consume a current
  lease when required.
- Deferral is a human-facing persisted hold, is absent from MCP, and already excludes work from the
  agent queue.

### 2.4 Existing event and idempotency contracts

- `WorkEvent` is append-only in the API and protected from direct SQL update/delete.
- Server-reserved events are tied to retained source facts with database constraints and triggers.
- Per-work event order is `created_at, id`; it is not a project activity cursor or commit order.
- Phase 6 protects ten REST mutations and nine MCP mutations with permanent project/operation-UUID
  receipts. The receipt, domain mutation, and authoritative events share one transaction.
- Matching replay occurs after authentication and strict request validation but before current
  work visibility, lifecycle, version, relationship, or lease checks.
- The closed operation registry was deliberately designed so gates can enroll only after their
  scope, response, event atomicity, fingerprint, and replay guards are fixed.
- Browser protected mutations use a dashboard-lifetime in-memory frozen-intent registry and strict
  operation-specific response decoders.
- The Phase 5 metadata-v1 validator remains a sensitive historical boundary. Gate events must not
  reinterpret any previously valid event or progress metadata.

### 2.5 Existing trust boundary

Mnemonic has one shared bearer key and a trusted-local dashboard. Client/session/model values are
asserted provenance, not authenticated people. The dashboard can represent a human interaction,
but the backend cannot prove which person used it. MCP clients already possess broad mutation
authority.

Phase 7 therefore records submitting client/session provenance and promotes human-origin use
through a human-facing resolution interface and workflow guidance. It does not claim cryptographic
proof of a human identity, protection from a malicious bearer holder, or that stored text is an
execution capability.

## 3. Decisions fixed by this plan

### 3.1 One release and one authoritative cutover

The database, gate-aware backend, REST/MCP contracts, dashboard attention view, hierarchy
aggregates, plugin guidance, and validation evidence are one milestone. Implementation may use
reviewable increments, but production may not expose gate creation before all of the following are
true:

- ready listing and fresh claims consult gates;
- recall and events expose questions/answers;
- the human attention view can resolve them;
- hierarchy cards expose their aggregate count; and
- request/resolution retry recovery is live.

The gate-aware backend ships with a process-wide `human_gate_requests_enabled` setting defaulting
false. When false, an unkeyed gate request returns stable `503 human_gates_not_enabled` before
domain work. A keyed request still enters the permanent receipt registry first: a completed replay
or conflicting UUID reuse returns its canonical replay/conflict result even while creation is
disabled. Only a genuinely new reservation consults the fence; if disabled, its transaction rolls
back the reservation and returns the same `503`, leaving the UUID unbound. All gate-aware read,
readiness, database-guard, resolution, and replay behavior remains active. Operations enable new
requests only after all old backend processes are drained and the MCP/dashboard clients are
deployed. Disabling creation later never hides or bypasses existing gates or their receipts.

The schema revision is `0014_human_gates`. Phase 8 needs no denormalized persistence; its counts are
derived from the gate, relationship, work, blocker, and lease tables added by earlier phases.

### 3.2 Gate identity and state

Use a generic persistence name, `work_gates`, with exactly one allowed initial type:

```text
gate_type = human
```

Public schemas and tools use `HumanGate` terminology so callers do not mistake a Phase 7 row for a
timer, CI result, external event, or work dependency. Future types require their own semantics and
a migration that widens the check; Phase 7 does not create placeholder values.

A gate has exactly two states derived from nullable resolution fields:

```text
unresolved = resolution, resolver provenance, and resolved_at are all null
resolved   = resolution and required resolver provenance and resolved_at are all present
```

There is no mutable status column, `updated_at`, edit, delete, reopen, reject, cancel, waive, or
bulk-resolve operation. “No longer needed” is a valid durable resolution; it is not silent erasure.
One work item may have multiple gates, and identical words under different operation UUIDs remain
distinct explicit questions. Phase 7 performs no semantic de-duplication.

Questions and resolutions are exact nonblank text after the existing NUL/length validation style,
with a 4,000-character maximum each. Whitespace inside accepted text is preserved. There are no
labels, arbitrary metadata, attachments, choice arrays, or embedded credentials.

### 3.3 Request and resolution provenance

Gate creation requires:

```text
requested_by_client
requested_by_session_id
requested_by_model (optional)
```

Resolution requires the analogous `resolved_by_*` fields. They identify the client/session that
submitted the human answer; they do not authenticate a person. The dashboard uses `client =
dashboard` and its existing opaque dashboard session ID. The canonical MCP surface deliberately
does not expose resolution. A direct REST integration remains inside the same shared-bearer trust
boundary and must not be described as proof that a human spoke.

Do not add `human_name`, email, account ID, role, approver group, signature, or authorization claim
without a later authentication design. UI copy says “requested through” and “resolved through,”
not “verified identity.”

### 3.4 Gate request eligibility

A new human gate may be requested only on visible `pending` work. The service locks that work row
before checking lifecycle and before inserting the gate. Deferred or terminal work returns the
existing safe `work_not_pending` family and creates no receipt or event.

A current lease is not required and is not revoked. Requesting attention is a coordination fact,
like appending a checkpoint, rather than proof of lease ownership. The request accepts no
`lease_token`; an unexpected token is rejected by strict schema validation rather than ignored.

This permits the normal sequence:

```text
agent holds lease
  -> records question
  -> leaves a checkpoint if useful
  -> releases the lease or lets an intentional policy decide whether to retain it
```

The gate itself prevents a different fresh/replacement claim after it commits. The release is a
separate capability mutation and retains its Phase 6 retry contract.

#### 3.4.1 Request-state anchor and stale-answer acknowledgement

The service captures three server-derived facts while holding the focal work lock:

```text
requested_work_version
requested_context_checkpoint_id
requested_relationship_event_count
```

The first detects title, summary, priority, and lifecycle edits; the second anchors the immutable
current context checkpoint; the third is the monotonic count of dependency/relationship
added/removed events for that work and detects graph changes. Callers cannot submit or override
these values. They are persisted on the gate and returned with the question.

Every current gate projection returns an exact server-derived `current_context_revision` tuple:

```text
work_version
context_checkpoint_id
relationship_event_count
```

It also derives `work_changed_since_request`,
`context_checkpoint_changed_since_request`, and
`relationships_changed_since_request`, plus their OR
`context_changed_since_request`, by comparing that current tuple with the request anchors.
The tuple is ordinary state evidence, not a secret or capability; clients must treat it as an
opaque, indivisible revision and must not synthesize it from separately cached reads.

Resolution accepts `acknowledge_context_change: boolean = false` and an optional exact
`reviewed_context_revision`. After locking the work and gate, the service recomputes the current
tuple. With no drift, acknowledgement must be false and the reviewed tuple absent. With drift,
acknowledgement must be true and the submitted reviewed tuple must exactly equal the current tuple
under those locks. A missing or unequal tuple—including a B-to-C change after the human reviewed
B—returns `409 gate_context_changed`, rolls back the new receipt reservation, and requires another
refetch/review/new intent. The dashboard therefore reloads one authoritative WorkContext review bundle, whose existing
one-statement snapshot includes the focal work, current context checkpoint, relationships, and gate
projection. It shows those facts and changed categories, then freezes that bundle's exact returned
tuple with the answer and acknowledgement. An attention/history projection may prompt review but
cannot arm an acknowledged submit by itself or by combining independently timed responses.

Every resolution persists the exact accepted tuple as `resolved_context_revision`, derives
whether it differed from the request anchors, and records acknowledgement equal to that drift
fact. Thus the durable audit proves which state was resolved, not merely that some past change was
acknowledged. This is a stale-decision guard, not an authorization check. Ordinary progress/lease
events do not invalidate the revision; durable context checkpoint, graph, and editable work-state
changes do.

### 3.5 Gate interaction with leases and lifecycle

An unresolved gate has these exact effects:

| Operation | Behavior while unresolved |
| --- | --- |
| `list_ready_work` | exclude the work |
| fresh claim with no retained lease | reject `409 work_gated` |
| replacement of an expired retained lease | reject `409 work_gated` |
| different claimant while a lease is active | reject according to the documented fresh-claim conflict order; never acquire |
| exact replay of the same still-active claim | return the existing capability without extending it |
| renew existing active claim | allow; renewal is capability maintenance, not fresh selection |
| release existing claim | allow |
| add checkpoint or progress event | allow |
| nonterminal title/summary/priority edit | allow |
| add/remove relationship | allow under existing graph rules |
| defer Pending work | allow after the existing active-lease guard; the gate remains in Needs Attention |
| return Deferred work to Pending | allow; it becomes derived `waiting`, not ready |
| complete | reject `409 work_gated` after blocker validation |
| transition to `wont-do` or `promoted` | reject `409 work_gated` |
| soft delete | reject `409 work_gated` after relationship validation |

Terminal transitions never auto-resolve a question or strand an unresolved question behind
ordinary soft-delete visibility. The shipped lifecycle matrix permits only Pending to
`wont-do`/`promoted`; Deferred may only return to Pending. Both the service guard and database
backstop are nevertheless target-based, so a future source-to-terminal transition cannot bypass
gates. A human resolves every outstanding gate first; the answer may say that the work should be
retired or removed, after which the normal versioned lifecycle operation is separate and
auditable.

Gate resolution is permitted for any visible work carrying that unresolved gate. In normal data
this means Pending or Deferred, because terminal/delete transitions are guarded. Resolution does
not change lifecycle, version, lease, relationships, or checkpoints and does not automatically
claim, resume, complete, defer, or execute work.

### 3.6 Readiness projection and display precedence

Extend `Readiness` with:

```text
unresolved_gate_count: integer >= 0
is_gated: boolean = unresolved_gate_count > 0
```

`is_ready` becomes:

```text
status = pending
AND no active lease
AND unresolved_blocker_count = 0
AND unresolved_gate_count = 0
```

The convenience `display_state` adds `waiting` with this precedence:

```text
non-Pending lifecycle
waiting (one or more unresolved gates)
blocked
active
dropped
pending
```

The independent flags remain authoritative. A Pending work item may simultaneously be gated,
blocked, and actively leased; the single display state only selects the most human-actionable
badge. Full-summary cards show separate “Needs attention,” “Blocked,” and “Active” badges whenever
their independent facts are true rather than hiding either overlap.

Ready pages cannot contain `waiting`; their existing compact pointer boundary is unchanged except
that its schema recognizes the new display literal for other minimal search results.

### 3.7 Immutable gate history

Add exactly two server-reserved live event types:

```text
human_attention_requested
human_attention_resolved
```

Each event has an internal required `work_events.gate_id`, uses the request or resolution text as
its bounded `body`, and has fixed public metadata:

```json
{"gate_id": "<uuid>", "gate_type": "human"}
```

The request event actor matches requester provenance and time exactly. The resolution event actor
matches resolver provenance and `resolved_at` exactly. Both events reference the same retained
gate source fact. They are immutable, unique per `(work_item_id, gate_id, event_type)`, and live
only; there is no backfill origin.

The existing non-gate `WorkEventRead` wire shape remains byte-for-byte frozen: do not add a nullable
top-level `gate_id` to every event. Gate events are a typed metadata subtype whose required metadata
UUID matches the internal FK column. This preserves replay of every Phase 6 `append_event` receipt,
whose stored response has no such top-level field. The text is intentionally duplicated between
the gate fact and its immutable event so a paged timeline stays self-contained. Database
source-fact guards require the body and typed metadata to match exactly, so neither can diverge
from the retained gate.

The gate service advances `work_items.updated_at` with database time for both request and
resolution, but it does not increment work version. A retry/replay advances it zero additional
times.

### 3.8 Bounded recall contract

`WorkContext` adds:

```text
unresolved_gates: HumanGateRead[]
unresolved_gate_total: integer
omitted_unresolved_gate_count: integer
recent_resolved_gates: HumanGateRead[]
resolved_gate_total: integer
omitted_resolved_gate_count: integer
```

Return at most the first 20 unresolved gates in immutable `attention_sequence ASC` order. The
authoritative readiness count and `unresolved_gate_total` cover all unresolved gates even when the
slice is truncated. `omitted_unresolved_gate_count` directs callers to `list_human_attention` with
the work ID when more exist.

Separately return at most the 20 most recently resolved gates in deterministic
`resolved_at DESC, id DESC` order. This paired gate projection always carries the authoritative
question, answer, request/resolution provenance, and request-state anchor even after unrelated
events push the resolution event out of `recent_events`. Exact resolved totals and omission counts
remain visible. Older gates are pageable through the dedicated per-work gate-history endpoint;
their immutable events also remain pageable through `list_work_events`. Ordinary later activity
therefore cannot erase human decisions from the dedicated recall category, while payload size
remains bounded.

Every embedded relationship counterpart readiness projection also gains the gate count and
`is_gated` flag. The one-statement `WorkContext` query must capture focal work, counterpart facts,
unresolved gate slice/count, and recent events at one statement snapshot.

### 3.9 Exact idempotency coverage

Enroll both new mutations in the Phase 6 generic receipt registry:

| Operation kind | REST request | Target envelope | Response/status | `mutation_applied` |
| --- | --- | --- | --- | --- |
| `request_human_input` | `HumanGateRequestCreate` | `work_item_id` | `201 HumanGateRead` | `true` |
| `resolve_human_input` | `HumanGateResolutionCreate` | `work_item_id`, `gate_id` | `200 HumanGateRead` | `true` |

Direct REST keeps `client_operation_id` optional and explicitly retry-unsafe when omitted. The
request MCP tool requires it. The dashboard resolution action generates it before dispatch and
retains the exact frozen body in the existing memory-only intent registry.

The uniqueness scope stays `(project_id, client_operation_id)`. The fingerprint includes
operation kind, both path IDs, exact validated text, gate type/defaults, complete requester/resolver
provenance, the resolution acknowledgement, and the exact optional reviewed-context revision tuple.
Server-derived request anchors are response/source facts, not caller fingerprint inputs; the
reviewed tuple is an explicit resolution precondition and therefore is. The fingerprint excludes
only the operation UUID and bearer, exactly as Phase 6 specifies.

Replay precedes the creation fence and current work/gate lookup. Consequently:

- a completed keyed request replays and a conflicting UUID reuse conflicts even while new gate
  creation is disabled;
- replaying a gate request after that gate was resolved returns the original unresolved
  `HumanGateRead` snapshot and does not reopen it;
- replaying resolution after the work later changes or is soft-deleted returns the original
  resolved snapshot and does not touch current state; and
- a new UUID attempting to resolve an already resolved gate receives
  `409 gate_already_resolved`, not a successful no-op.

Callers refetch context/attention after any success because a replay is historical outcome
evidence. Gate responses are non-capability-bearing and remain below the Phase 6 receipt bound.

After enrollment, the closed totals become:

```text
12 protected REST mutations
10 protected MCP mutations
10 protected dashboard mutations (the existing nine plus gate resolution)
25 canonical MCP tools (the existing 22 plus request, attention-list, and gate-history tools)
```

### 3.10 Human interface versus authority

`Needs Attention` is the primary human queue, but this phase is an asserted coordination boundary,
not an adversarial identity or authorization boundary. Any holder of the shared bearer can call
the REST resolution route and spoof provenance. The supported agent-facing MCP catalog omits that
write to reduce accidental self-resolution, but only a later authenticated-human design could
prevent a malicious bearer holder from fabricating an answer.

A gate answer remains durable context, not a bearer capability or automatic execution
authorization. In particular:

- resolving an approval question never invokes the approved destructive action;
- stored text must be rechecked for scope, freshness, and current policy before execution;
- no agent-facing canonical tool may resolve a gate;
- direct REST clients must not infer an answer from repository state, another checkpoint, silence,
  timeout, or a model's own preference; and
- current platform rules that require contemporaneous confirmation are not bypassed by old stored
  prose.

A human may record a decision about a destructive action, but the stored resolution is not itself
the authorization to execute that action. If a future product requirement treats gate resolution
as an enforceable approval, authenticated principals or a short-lived human-issued capability are
prerequisites and are outside this release.

The UI and plugin describe gate text as untrusted persisted content. React renders it as text, and
no Markdown/HTML execution is introduced.

### 3.11 Needs Attention queue

The project queue contains one item per unresolved gate, not one item per event or work item. Add
an immutable database-assigned `attention_sequence` to each gate and order by:

```text
attention_sequence ASC
```

Priority remains visible but never changes queue position. The page uses an opaque versioned
cursor encoding the last sequence, so resolving an earlier gate or editing work priority cannot
make a forward traversal skip or repeat another retained gate. Newer requests follow the cursor;
clients restart without a cursor to refresh the head. `total` is current snapshot information,
not a promise that the queue will remain unchanged during traversal.

Each `HumanAttentionItem` contains:

```text
gate: HumanGateRead (necessarily unresolved)
summary: WorkSummary with current readiness
```

The summary carries the existing bounded root-to-parent `ancestor_path` and truncation flag, so a
human can locate the question within a workstream. A structural root has an empty path. The page
does not include checkpoint bodies, relationship context, lease tokens, mutation receipts, or a
whole subtree.

The endpoint accepts only optional exact `work_item_id`, `limit` (default 30, maximum 100, with zero
reserved for a count-only response), and opaque `cursor`. A zero limit requires no cursor and
returns `items=[]`, the current exact `total`, `limit=0`, and `next_cursor=null`; it transmits no
gate item or question text and is what the sidebar uses. An unknown/cross-project work filter
returns the normal project-scoped 404. There is no semantic question search, subscription, notification state,
assignment, snooze, unread flag, priority sort, or arbitrary sort in this phase.

### 3.12 Hierarchy aggregate definitions

Extend each root/child `HierarchySummary` with `presentation`:

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

Definitions are exact. “Visible” means project-scoped work with `deleted_at IS NULL`; lifecycle
filters do not change visibility for these rollups.

- Structural traversal uses visible `parent-child` edges only.
- “Descendant” is strict: it excludes the branch node itself.
- `direct_child_count` counts visible immediate structural children.
- `descendant_count` counts every visible strict structural descendant at any depth.
- `blocked_descendant_count` counts Pending descendants with one or more unresolved incoming
  blockers.
- `active_descendant_count` counts Pending descendants with an unexpired lease at the one captured
  database time. It can overlap the blocked or gated populations.
- `completed_descendant_count` counts descendants whose lifecycle is exactly `done`; `wont-do` and
  `promoted` are not completion.
- `discovered_descendant_count` counts distinct descendants that are the source of at least one
  `discovered-from` edge, regardless of the discovery target.
- `branch_unresolved_human_gate_count` counts unresolved gate rows on the branch node and all of its
  visible descendants. It counts questions, not distinct gated work items.
- `is_discovered_work` means the node is the source of at least one explicit `discovered-from`
  edge.
- `discovered_from_parent` means the node has the exact outgoing `discovered-from` edge to its
  current structural parent. It is false for roots.
- `next_active_descendant_lease_expires_at` is the earliest expiry among the active leases counted
  for strict descendants, or null. It is a refresh hint, not a lease capability.

The operational descendant counts are intentionally nonexclusive. The UI labels and tooltips must
not imply they sum to `descendant_count`.

All counts describe the full visible branch, independent of the current lifecycle/tag/source
filter. Existing `self_matches_filter` and `has_matching_descendants` remain filter-specific and
explain why a branch appears. This prevents a Pending filter from making completed-descendant
counts misleadingly zero.

### 3.13 Discovery presentation without inference

A child with `discovered_from_parent=true` is labeled “Discovered sub-work.” A structural child
with some other discovery origin is labeled “Discovered elsewhere · grouped here.” A child with no
discovery edge is labeled “Planned child.” A discovered work item with no parent remains a visible
root and is labeled “Discovered work · ungrouped.”

Mnemonic never converts `discovered-from` into `parent-child`, and Phase 8 does not hide an
ungrouped discovery. Agent workflow guidance instead says that when newly discovered work is also
sub-work of the current durable objective, creation should atomically include two explicit facts:

```text
parent parent-child child
child discovered-from origin
```

Either fact may legitimately exist without the other. Search similarity and prose never create
either.

### 3.14 Progressive-disclosure behavior

- Unfiltered root branches start collapsed.
- A branch retained only as filter scaffolding may auto-expand as the current UI does, so the
  matching descendant is discoverable.
- Expansion lazily pages direct child branches; it never recursively downloads the entire tree.
- `direct_child_count > 0` controls whether the node is structurally expandable.
  `has_matching_descendants` separately says whether the current filter will return anything.
- If children exist but none match the current filter, expansion identifies whether lifecycle,
  source, or tag predicates suppress them and offers a branch-local “Show all descendants” drill
  that removes all three for that branch rather than displaying an empty, apparently childless
  branch.
- Root pagination remains independent of child pagination.
- Free-text search stays flat with ancestor breadcrumbs. It does not silently replace direct hits
  with roots or hierarchy aggregates.
- The existing 50-level browser guard and cycle fallback remain. Corrupt or unexpectedly deep
  data is explained, never silently omitted.

### 3.15 No mutable aggregate cache

Phase 8 computes hierarchy presentation from canonical rows using PostgreSQL recursive CTEs,
`EXISTS` predicates, and grouped aggregates. Do not add:

- `descendant_count` columns on work items;
- closure/nested-set/materialized-path tables;
- trigger-maintained branch counters;
- a background aggregation worker;
- Redis or process-local authoritative caches; or
- a second human-only graph.

The current forest indexes plus the new unresolved-gate indexes are the initial query model. Add
another index only after representative `EXPLAIN (ANALYZE, BUFFERS)` evidence identifies a real
bottleneck.

## 4. Requirement identifiers

| ID | Requirement |
| --- | --- |
| `HG-1` | Gate request and its request event commit once and atomically with exact submitted requester provenance |
| `HG-2` | Resolution is a single immutable transition with one matching event, exact submitted resolver provenance, and durable evidence of the exact reviewed state revision |
| `HG-3` | Ready listing and every fresh/replacement claim share the indexed unresolved-gate predicate |
| `HG-4` | Existing live capability replay/renew/release survives later gating while fresh ownership does not |
| `HG-5` | Completion, terminal retirement/promotion, and deletion cannot commit with an unresolved gate |
| `HG-6` | Request and resolution use permanent Phase 6 replay receipts with no duplicate durable/domain effect; an applied replay may emit at most one optional data-free healing invalidation |
| `HG-7` | Needs Attention exposes only explicit unresolved human gates through immutable, bounded project-local cursor pages and a text-free count mode |
| `HG-8` | Bounded recall exposes exact unresolved/resolved totals and paired recent decisions; full retained gate history is pageable independently of event recency |
| `HG-9` | Gate text appears only in its explicit authorized sinks and never in logs, URLs, metrics, traces, WebSockets, search indexes, or browser persistence |
| `HG-10` | Resolver/requester values are documented as asserted client provenance, not authenticated human identity |
| `HP-1` | Blank human browse remains root-only and descendants are collapsed except filter-scaffolding disclosure |
| `HP-2` | Every hierarchy branch reports the exact defined unfiltered descendant and gate aggregates |
| `HP-3` | Active-descendant count has an expiry refresh hint so passive lease expiry does not leave a stale badge indefinitely |
| `HP-4` | Discovery provenance is visibly distinct while remaining independent from structural parentage |
| `HP-5` | Root and child filters retain existing subtree-aware semantics and cannot falsify full-branch aggregates |
| `HP-6` | A human can page/drill into any structural subtree and receives an explicit explanation when filters hide children |
| `HP-7` | Agents retain full flat search, ready, recall, event, and relationship graph access |
| `HP-8` | Hierarchy presentation is derived without closure tables, inferred edges, or mutable counter caches |
| `X-1` | Existing Phase 1–6 data and historical validators survive `0014` without fabricated facts |
| `X-2` | A replay-preserving creation fence, old-process drain evidence, and database guards prevent an old backend from acquiring or terminally escaping gated work |
| `X-3` | REST, MCP, proxy, browser, plugin, operations, and roadmap documents agree on gate authority and hierarchy semantics |

## 5. Persistence model

### 5.1 `work_gates`

Migration `0014_human_gates` adds:

```text
id                                UUID PRIMARY KEY DEFAULT gen_random_uuid()
attention_sequence                BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE
project_id                        UUID NOT NULL
work_item_id                      UUID NOT NULL
gate_type                         VARCHAR(16) NOT NULL DEFAULT 'human'
question                          TEXT NOT NULL
requested_by_client               VARCHAR(80) NOT NULL
requested_by_session_id           VARCHAR(200) NOT NULL
requested_by_model                VARCHAR(120) NULL
requested_work_version            INTEGER NOT NULL
requested_context_checkpoint_id   UUID NOT NULL
requested_relationship_event_count BIGINT NOT NULL
created_at                        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
resolved_at                       TIMESTAMPTZ NULL
resolution                        TEXT NULL
resolved_by_client                VARCHAR(80) NULL
resolved_by_session_id            VARCHAR(200) NULL
resolved_by_model                 VARCHAR(120) NULL
resolved_work_version             INTEGER NULL
resolved_context_checkpoint_id    UUID NULL
resolved_relationship_event_count BIGINT NULL
context_changed_at_resolution     BOOLEAN NULL
context_change_acknowledged       BOOLEAN NULL
```

Constraints:

- composite FK `(project_id, work_item_id)` to `work_items(project_id, id)` with `RESTRICT`;
- unique `(work_item_id, id)` for the work-event composite FK;
- composite FK `(work_item_id, requested_context_checkpoint_id)` to the work-owned immutable
  checkpoint, with the insert guard requiring `kind=context`;
- positive immutable `attention_sequence`, positive `requested_work_version`, and nonnegative
  `requested_relationship_event_count`;
- `gate_type = 'human'` exactly;
- question length 1..4,000 after non-whitespace validation and no NUL;
- requester client/session nonblank and bounded; optional model nonblank when present;
- unresolved rows require every resolution/provenance/revision field and both resolution-context
  booleans null;
- resolved rows require nonblank bounded resolution, nonnull resolver client/session,
  `resolved_at`, all three resolved-revision fields, positive resolved work version, nonnegative
  resolved relationship count, and both booleans; optional model is nonblank when present;
- `resolved_context_checkpoint_id` has the same work-owned immutable-context-checkpoint FK and
  kind guard as the request checkpoint;
- `context_changed_at_resolution` equals the OR of the three resolved-versus-request anchor
  comparisons, and `context_change_acknowledged` equals that drift result;
- `resolved_at >= created_at`; and
- no status, caller-authored state override, operation UUID, lease token, bearer, label array,
  metadata JSON, or mutable timestamp column.

Indexes:

```text
ix_work_gates_project_unresolved
  (project_id, attention_sequence) WHERE resolved_at IS NULL

ix_work_gates_work_unresolved
  (work_item_id, attention_sequence) WHERE resolved_at IS NULL

ix_work_gates_work_timeline
  (work_item_id, attention_sequence DESC)

ix_work_gates_work_resolved_recent
  (work_item_id, resolved_at DESC, id DESC) WHERE resolved_at IS NOT NULL
```

The immutable sequence makes attention and full-history cursor traversal independent of mutable
work priority and wall-clock corrections. The resolved partial index supports the dedicated recall
slice. Add no further index until a measured plan requires it.

### 5.2 Gate transition guards

Install database guards analogous in strength, not implementation detail, to checkpoint/event and
receipt guards:

1. A `BEFORE INSERT` trigger accepts only a fully unresolved row on visible Pending work and
   verifies that the server-captured work version, current context-checkpoint ID, and monotonic
   count of focal relationship events match retained state. Direct SQL cannot fabricate a gate
   that starts resolved or with a false request-state anchor.
2. A `BEFORE UPDATE OR DELETE` trigger rejects delete and permits exactly one update from
   unresolved to resolved. It re-derives the current revision tuple while the focal work is locked,
   requires the stored resolution tuple to match it, and verifies the drift/acknowledgement matrix.
   Sequence, IDs, scope, type, question, requester, request anchors, and creation time remain
   byte-for-byte unchanged. A resolved row rejects every later update.
3. A deferred constraint trigger re-reads the gate at commit. Every gate must have exactly one
   matching request event; a resolved gate must additionally have exactly one matching resolution
   event, while an unresolved gate must have none.
4. A `work_leases` trigger rejects INSERT or any generation/holder/token/acquisition-changing
   UPDATE when that work has an unresolved gate. It deliberately permits same-generation renewal,
   pending-release staging, and DELETE, preserving the existing capability-maintenance contract.
   This makes a Phase 6 backend fail closed on fresh or expired-lease replacement.
5. A `work_items` trigger rejects any status transition whose target is `done`, `wont-do`, or
   `promoted`, and any null-to-nonnull `deleted_at` transition, while an unresolved gate exists.
   The new service returns reviewed domain conflicts before these database backstops fire.
6. Direct SQL can `TRUNCATE` only in the existing disposable test-schema cleanup path. Production
   code has no disable/bypass function.

The deferred trigger prevents a gate mutation from committing without its audit event while still
allowing the service to insert/update the source fact before staging the event in one transaction.

### 5.3 Work-event extension

Add nullable `work_events.gate_id UUID` and a composite FK:

```text
(work_item_id, gate_id) -> work_gates(work_item_id, id) ON DELETE RESTRICT
```

Update checks so:

- `gate_id` is nonnull exactly for `human_attention_requested` and
  `human_attention_resolved`;
- body is nonblank and at most 4,000 characters exactly for `progress` and the two gate event
  types, and null for every other event;
- gate events are `origin=live`, `actor_kind=client`, and metadata version 1;
- gate metadata is exactly `{"gate_id":"<uuid>","gate_type":"human"}` with no extra keys,
  and its UUID equals the internal `gate_id` column; and
- all checkpoint, lease, relationship, and gate reference families remain mutually exclusive.

Add one unique partial index:

```text
UNIQUE (work_item_id, gate_id, event_type)
WHERE gate_id IS NOT NULL
```

Keep `mnemonic_work_event_metadata_v1_is_valid` byte-for-byte unchanged for every historical event
family. Replace its check expression only with a conditional boundary:

```text
gate event -> exact new gate metadata check
other event -> existing metadata-v1 function with the existing arguments
```

Before replacing any event CHECK or trigger, execute `SET CONSTRAINTS ALL IMMEDIATE` so no
transaction-local deferred legacy violation crosses the DDL boundary. Add a separate gate-event
source-fact `BEFORE INSERT` trigger. The existing source-fact and deferred-state functions retain
their legacy branches. The new trigger verifies project/work/gate, event kind, exact body, typed
metadata, actor fields, and timestamp against the retained gate. This is safer than rewriting the
large legacy validator and preserves Phase 6's historical-progress guarantee. Measure and document
the lock/table-scan cost of the nullable column, FK validation, and replaced CHECK on a restored
production-sized database.

### 5.4 Client-operation extension

Widen `ck_client_operations_operation_kind_valid` from ten to twelve exact kinds by adding:

```text
request_human_input
resolve_human_input
```

Do not alter any completed receipt, uniqueness scope, fingerprint/response version, trigger,
retention rule, response bound, or logical project-scope decision. In particular, no public
top-level event field is added: a populated `0013` `append_event` receipt must still validate,
re-dump to identical JSON, and replay after `0014`. The application registry and database check
widen in the same increment.

Extend the exhaustive response-coherence switch. A request result must match scope, question, and
requester; be unresolved; have request/current revisions equal; report no drift; and have no
resolution fields. A resolution result must match scope, answer, and resolver; be resolved; have
current and resolved revisions equal at execution; and carry the exact request acknowledgement. If
acknowledged, its resolved revision must equal the fingerprinted reviewed tuple; if unacknowledged,
it must equal the original request anchors. These are receipt-validation rules, not current-read
claims after later state changes.

### 5.5 No historical backfill

The migration creates no gate from:

- `deferred` work;
- blockers;
- progress text or checkpoint wording;
- event types that merely resemble human attention;
- active or expired leases;
- issue labels, tags, or metadata; or
- missing credentials or failed operations.

The new table starts empty. Existing event rows get `gate_id = NULL` without a table rewrite beyond
what PostgreSQL requires for the nullable column/catalog changes. Preserve all existing row counts,
IDs, timestamps, JSON, trigger behavior, and receipt snapshots.

## 6. Service and transaction architecture

### 6.1 Focused gate service

Add `backend/src/mnemonic_api/services/gates.py` as the only domain service that may create or
resolve a gate. It owns:

- `unresolved_gate_counts` and `require_no_unresolved_gates`;
- request/resolve staging;
- gate-to-read-model projection;
- cursor attention and per-work history selection;
- request/current/resolution revision and drift projection; and
- bounded unresolved and recent-resolved projection helpers for recall.

It receives one route-owned SQLAlchemy session and never commits. Ready predicate construction
stays in `services/readiness.py`; event construction stays in `services/work_events.py`.

### 6.2 Request algorithm

After authentication, strict Pydantic validation, and secret-echo validation:

1. For a keyed call, reserve/replay `request_human_input`; reservation is the first coordination
   lock. Return a completed replay or operation conflict immediately, before the feature fence.
   An unkeyed call skips this step.
2. For a genuinely new execution, require `human_gate_requests_enabled=true`. If false, return
   the stable fence error and roll back any just-created reservation so the UUID remains unbound.
3. Load the visible project-scoped work row `FOR UPDATE`.
4. Require `status == pending`; do not inspect or revoke the lease.
5. Capture database time, work version, current context-checkpoint ID, and focal relationship-event
   count after the lock wait.
6. Insert the unresolved gate with its database sequence, server UUID/time, request-state anchors,
   and exact requester/question fields; flush.
7. Monotonically advance work `updated_at` to the same captured time without changing version.
8. Stage the matching request event using the same gate, actor, body, typed metadata, and time;
   flush.
9. Build and strictly validate the historical request `HumanGateRead` with the current revision
   equal to the request anchors and all four current drift fields false.
10. Complete the receipt with `201`, the typed body, and `mutation_applied=true`.
11. Commit once in the route, then allow one data-free live invalidation.

Any lifecycle, database, event, response, receipt, or commit failure rolls back every staged row
and leaves the UUID unbound.

### 6.3 Resolution algorithm

After the same pre-database boundaries:

1. Reserve/replay `resolve_human_input` with target envelope `{work_item_id, gate_id}`.
2. On first execution, lock visible work `FOR UPDATE`.
3. Load the gate by exact project/work/gate scope `FOR UPDATE`; a wrong scope is the same sanitized
   `gate_not_found` 404.
4. If already resolved, return `409 gate_already_resolved`; do not treat an identical answer as a
   no-op.
5. Recompute the exact current work-version/context-checkpoint/relationship-event revision under
   both locks. If it equals the request anchors, require acknowledgement false and no reviewed
   tuple. If it differs, require acknowledgement true and an exact reviewed-tuple match. Any
   mismatch returns `409 gate_context_changed` and rolls back the new receipt reservation.
6. Capture database time after both waits and fill all resolution/provenance fields, the exact
   resolved revision tuple, `context_changed_at_resolution`, and
   `context_change_acknowledged` exactly once.
7. Advance work activity without changing version.
8. Stage the matching resolution event and flush both source/event guards.
9. Build/validate the resolved `HumanGateRead`, complete its receipt, and commit once.
10. Publish one data-free invalidation.

A matching receipt replay stops before current work/gate/drift checks. The route marks the replay
outcome so an applied replay may emit at most one optional data-free healing invalidation and
nothing else. This is not counted as a duplicate durable or domain effect.

### 6.4 Global lock order

Extend the documented order:

```text
1. client-operation receipt reservation, when keyed
2. project row for graph mutations only
3. graph endpoint work rows in UUID order, or the focal work row
4. retained lease row when relevant
5. gate row when resolving one
6. relationship source row when removing one
7. source fact and authoritative event inserts
8. receipt completion
9. one route-owned commit
```

Gate request inserts after locking focal work; resolution locks work before gate. Completion and
terminal/delete guards query unresolved gates only after locking work. Every supported mutation
that advances a revision member must share that serialization point: work edits already lock the
focal row; relationship changes keep their project-then-UUID-ordered endpoint locks; and checkpoint
creation must change its current conditional behavior to lock the focal work even when no lease
token is supplied. Progress and lease activity do not advance the revision. Because every service
path that can create/resolve/escape a gate or change its reviewed state takes the work lock first,
request versus completion and resolution versus claim/checkpoint/graph change are linearizable
without a project-wide gate lock.

Add deterministic two-connection tests for this order, including tokenless checkpoint creation. Do not add an application mutex, advisory
lock, independently committed event, or independently committed receipt.

### 6.5 Concurrency outcomes to preserve

- Claim commits before a gate request: the lease is valid, then the gate commits; the state is
  active and gated.
- Gate request commits before a fresh claim: claim rejects `work_gated`.
- Completion/terminal/delete commits first: request sees non-Pending/deleted work and fails.
- Gate request commits first: completion/terminal/delete sees the unresolved row and fails.
- Resolution commits first: a following fresh claim may acquire if every other eligibility fact is
  clear.
- Fresh claim checks first while unresolved: it fails; resolution may then commit.
- A new gate and resolution of an old gate serialize; the final work remains gated if either gate
  is unresolved.
- Two different resolution UUIDs race: exactly one resolution/event succeeds; the loser gets
  `gate_already_resolved` and binds no receipt.
- The same resolution UUID races: one executes and one receives the original replay.

### 6.6 Lifecycle guard integration

Add `require_no_unresolved_gates` after the focal work lock to:

- completion, after the existing blocker guard and before consuming a lease;
- every status transition whose target is `done`, `wont-do`, or `promoted`, before consuming a
  lease (the shipped API currently exposes only Pending-to-`wont-do`/`promoted` here); and
- soft delete, after relationship validation and before consuming a lease.

Keep existing error precedence stable for prior facts: version/lifecycle checks remain first,
completion's blocker conflict precedes gate conflict, deletion's relationship conflict precedes
gate conflict, and capability validation remains after structural eligibility. Tests freeze the
complete overlap matrix.

Do not apply the guard to checkpoint/progress append, nonterminal edits, relationship mutations,
deferral, reopening to Pending, release, or renewal.

### 6.7 Secret-safety boundary

Gate question/resolution are durable and returned to authorized readers. Before receipt
reservation, reject an exact request-known occurrence of:

- the bearer credential;
- the request's `client_operation_id`; or
- any other control/capability value actually present in that request.

Completed receipt replay and UUID-conflict handling return before any time-varying lookup. For a
genuinely new execution only, reject any UUID substring in a durable gate field that matches a
currently retained gate ID or protected-operation ID, rolling back a new keyed reservation on
rejection. This finite sink enforcement must not make an exact receipt replay depend on later
retained state and is not an attempt to classify arbitrary UUID-shaped or opaque text as secret.

Use one sanitized `422 gate_secret_echo` (or the existing client-operation secret error where the
shared validator requires it), with empty context and no caller value. Identity fields and text are
all checked. Gate schemas accept no metadata or lease token, so there is no nested control channel.

This is not general secret detection. Unknown opaque credentials can still be stored. UI, MCP, and
docs prominently direct humans to record a reference or remediation instruction, never a password,
API key, private key, token, cookie, or private chain-of-thought.

## 7. Public REST contract

### 7.1 Request schemas

`HumanGateRequestCreate`:

```text
gate_type                 literal "human", default "human"
question                  exact nonblank text, max 4,000
requested_by_client       required bounded client name
requested_by_session_id   required bounded session ID
requested_by_model        optional bounded model
client_operation_id       optional top-level UUID
```

`HumanGateResolutionCreate`:

```text
resolution                exact nonblank text, max 4,000
resolved_by_client        required bounded client name
resolved_by_session_id    required bounded session ID
resolved_by_model          optional bounded model
acknowledge_context_change  boolean, default false
reviewed_context_revision   optional exact HumanGateContextRevision object
client_operation_id         optional top-level UUID
```

Both models use `extra='forbid'`. Gate request accepts no caller state anchor. Gate resolution
accepts only the exact reviewed revision object described above: it is a concurrency precondition,
not authority to set stored state. Neither model accepts a gate ID in the body, arbitrary metadata,
lease token, requester override on resolution, resolver override on request, or an operation ID
nested anywhere. Add every new safe field name, including revision members, to
`_PUBLIC_VALIDATION_LOCATION_SEGMENTS` so sanitized 422 locations remain useful without echoing
values.

### 7.2 Response schemas

`HumanGateContextRevision` is a strict, extra-forbidden object with positive `work_version`, a
UUID `context_checkpoint_id`, and nonnegative `relationship_event_count`.
`HumanGateRead` contains:

```text
id, project_id, work_item_id, gate_type,
question,
requested_by_client, requested_by_session_id, requested_by_model,
requested_work_version, requested_context_checkpoint_id,
requested_relationship_event_count, created_at,
status = unresolved | resolved,
current_context_revision = {work_version, context_checkpoint_id, relationship_event_count},
work_changed_since_request, context_checkpoint_changed_since_request,
relationships_changed_since_request, context_changed_since_request,
resolved_at, resolution,
resolved_by_client, resolved_by_session_id, resolved_by_model,
resolved_context_revision = {work_version, context_checkpoint_id, relationship_event_count} | null,
context_changed_at_resolution, context_change_acknowledged
```

Its validator requires the exact nullability/state matrix, anchor/revision bounds, drift-booleans
OR relationship, revision-to-drift coherence, and UTC timestamps. `status`, current revision, and
the four current drift fields are derived at projection time, not stored. The resolved revision is
durable audit evidence. A replayed mutation response is the historical projection frozen at
execution; current GET projections recompute the current revision and drift.

`HumanAttentionItem` contains `gate` and `summary`. `HumanAttentionPage` contains
`items,total,limit,next_cursor` and rejects extra fields. `HumanGatePage` contains
`items,total,limit,next_cursor` with the same strict opaque-cursor contract.

Define one exact stateless cursor codec. Its versioned base64url payload contains an endpoint
discriminator, project/work scope, exact state/work filter, direction, and last
`attention_sequence`, but no gate ID or gate text. The strict decoder rejects unknown keys,
versions, bounds, endpoint reuse, or path/filter mismatch with sanitized `422 invalid_cursor`.
Cursors are continuation hints, not authorization or snapshots; every page reapplies bearer and
project/work visibility checks.

`Readiness`, `WorkSummaryMinimal.display_state`, `WorkSummary`, `WorkPointer`, `WorkContext`, and
`HierarchySummary` gain the exact fields defined above. Additions are deliberate contract changes;
strict downstream models must update in the same release.

### 7.3 Routes

Add:

```text
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/gates
  -> 201 HumanGateRead

POST /api/v1/projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/resolve
  -> 200 HumanGateRead

GET /api/v1/projects/{project_id}/human-attention
  -> HumanAttentionPage

GET /api/v1/projects/{project_id}/work-items/{work_item_id}/gates
  -> HumanGatePage
```

The attention GET accepts optional `work_item_id`, `limit`, and `cursor` only. The work-gate GET
accepts `status=all|unresolved|resolved` (default `all`), `limit` (default 30, maximum 100), and
`cursor`, and traverses immutable `attention_sequence DESC`. The default `all` view is the
complete stable audit traversal. State-filtered pages are explicitly current convenience views:
a resolution can introduce an older sequence into `status=resolved`, so clients restart that
filter at the head after invalidation rather than claiming snapshot completeness. It is an
authoritative paired question/answer audit read. Unlike ordinary work/context/event routes, it may read gates for an
exact retained soft-deleted work ID; project/work scope remains sanitized, and the delete invariant
guarantees no unresolved gate can be hidden there. Existing context, ready, search, child, event,
and relationship paths remain stable.

There is no gate PATCH/DELETE/reopen endpoint, generic future-type endpoint, bulk resolution,
answer suggestion, or operation-receipt API. Current active gates are read through
context/attention; current and archived gate history is read through this dedicated page; matching
immutable events remain the chronological work timeline.

### 7.4 Stable errors

Add or document:

| Status/code | Meaning | Safe client action |
| --- | --- | --- |
| `404 gate_not_found` | gate is absent or outside the supplied project/work scope | refresh attention/context; do not guess another scope |
| `409 work_gated` | a fresh claim or guarded terminal action encountered unresolved gates | read context/attention; do not bypass with a new intent |
| `409 gate_already_resolved` | a new first execution tried to resolve a completed gate | read current context/history; do not overwrite the answer |
| `409 gate_context_changed` | request anchors drifted, acknowledgement/review revision is missing, or reviewed revision no longer equals locked current state | reload work/context/relationships and submit a new intent containing exactly the returned reviewed revision |
| `503 human_gates_not_enabled` | gate creation is fenced during combined cutover | do not retry until operations enables the feature |
| `422 gate_secret_echo` | request-known or currently retained gate/operation control data appeared in a genuinely new durable gate mutation | remove it and submit a genuinely corrected intent |

All contexts are empty except any already-reviewed nonidentifying count that the standard conflict
envelope permits; the preferred gate errors reveal no gate ID, question, answer, actor, or operation
UUID. Existing idempotency conflict/unavailable semantics apply unchanged.

### 7.5 Replay and current reads

OpenAPI and API documentation must show:

- exact request retry after a lost response;
- request replay after later resolution returning the original unresolved snapshot;
- resolution replay after deletion returning the original resolved snapshot;
- same UUID with a changed answer, actor, acknowledgement, or reviewed revision returning
  `client_operation_conflict`;
- a new UUID after prior resolution returning `gate_already_resolved`; and
- success followed by current context/attention refetch.

## 8. MCP adapter and agent workflow

### 8.1 Tool catalog

Add exactly three canonical tools, taking the catalog from 22 to 25:

```text
request_human_input
list_human_attention
list_work_gates
```

`request_human_input` parameters are project/work IDs, question, requester client/session/model,
and required `client_operation_id`. It fixes `gate_type=human` in the REST body.

`list_human_attention` parameters are project ID, optional exact work ID, limit, and cursor.
`list_work_gates` parameters are project/work IDs, optional state filter, limit, and cursor; it is
the complete paired question/answer history path, including retained deleted-work audit when the
caller already has the exact ID.

The request tool uses the existing idempotent mutation annotation; both list tools are read-only and
idempotent. No canonical MCP tool resolves a gate, and no gate tool accepts a lease token.
Destructive/open-world annotations remain truthful. This intentionally narrows the roadmap's
suggested MCP resolution surface: `list_work_gates` takes that catalog slot because authenticated
human resolution does not exist in the current trust model.

### 8.2 Tool policy and descriptions

`request_human_input` says:

- use a gate only for a concrete decision/input that genuinely requires a human;
- make the question self-contained and decision-ready without transcript dumps or secrets;
- do not substitute a gate for ordinary progress, a blocker, or work decomposition;
- retain the exact operation UUID and arguments before the call; and
- after requesting, leave useful context and decide explicitly whether to release an active lease.

The catalog and all plugin guidance say that an agent must not resolve, infer, time out, or
self-approve a human gate. It directs a human to the dashboard and treats any stored decision as
context rather than automatic execution authority.

The request write repeats the Phase 6 immutable-intent recovery guidance.
`list_human_attention` explains that it is a human queue, not agent-ready work; agents use
`list_ready_work` for selection. `list_work_gates` explains the paired historical/audit contract
and that old resolutions do not confer current authority.

### 8.3 Strict response and error handling

Update MCP models for gate/readiness/event/context fields and validate every response with
`extra='forbid'`. Operation-specific coherence checks require:

- request response matches project/work/question/requester, request/current revisions are equal,
  all drift is false, and state is unresolved;
- every attention item is unresolved, project-local, gate/work coherent, and has
  `summary.readiness.is_gated=true`; and
- every gate-history item matches project/work/state filter and the exact state/drift matrix.

One request invocation still makes one outbound attempt. Timeout/reset/EOF/malformed 2xx/backend
5xx is an unknown result and permits only exact same-key retry. Stable errors remain sanitized.
MCP logs and exception strings never include question, answer, IDs, provenance values, or frozen
arguments.

### 8.4 Recall, resources, and prompt guidance

`recall_work`, `claim_and_recall`, the work resource, and `resume_work` include the new bounded
gate fields. Guidance requires an agent to stop before newly starting gated work, inspect every
returned unresolved question, and never treat omission from the 20-row slice as absence when the
total is larger.

Exact active claim replay remains capability recovery; the agent must still recognize the newly
gated context and avoid unapproved continuation. The dedicated recent-resolved slice supplies
paired decision context even after ordinary events advance, and `list_work_gates` pages older
decisions. Neither a resolved gate nor its event overrides repository freshness, current user
scope, or destructive-action policy.

### 8.5 Plugin release

Update all three plugin skills and shared references. Bump the inner plugin manifest from `0.4.0`
to `0.5.0`; update personal marketplace metadata only as required by the existing packaging
contract. Validate fresh install and sequential cache-busted upgrade.

The save skill covers explicit questions and dual parent/discovery edges. Search distinguishes the
human attention queue from ready work and directs resolution to the human dashboard. Recall covers
waiting state, paired human-answer provenance, active claim replay, full gate-history paging, and
the nonauthority boundary.

## 9. Hierarchy query and API architecture

### 9.1 Extract a focused hierarchy service

Move or wrap `hierarchy_page` and `ancestor_paths` in
`backend/src/mnemonic_api/services/hierarchy.py`. Relationship mutation/traversal remains in
`relationships.py`. Avoid a circular dependency by using shared pointer/readiness projection
helpers with narrow inputs.

The route names and query parameters remain unchanged. This is an internal ownership refactor, not
a second `/v2` tree surface.

### 9.2 One-statement page snapshot

Refactor root and child hierarchy pages so candidate selection, subtree filter qualification,
branch aggregates, branch-node summary, current context pointer, readiness facts, and total are
derived in one PostgreSQL statement and one captured `clock_timestamp()` value.

The statement shape is:

```text
database_time AS MATERIALIZED (...)
candidate_branches (... roots or direct children ...)
subtree(branch_id, member_id, visited_path) AS RECURSIVE (... parent-child only ...)
filter_matches (... existing lifecycle/source/tag semantics ...)
member_facts (... blocker, lease, done, discovery, unresolved gate ...)
branch_aggregates (... strict/inclusive counts and filter flags ...)
qualified_page (... stable root/child ordering, limit, offset ...)
summary_projection (... work, current checkpoint pointer, readiness ...)
final JSON aggregation + qualifying total
```

Use `UNION ALL` with a carried UUID path and reject a child already present in that path before
recursing. Derive depth from path cardinality outside the recursion identity. Do not rely on
`UNION(branch_id, member_id, depth)`: a corrupt cycle revisits the same node at a new depth and
never deduplicates. Structural aggregate recursion is not truncated at the browser's 50-level
presentation guard; every reachable acyclic descendant contributes to counts. Apply a reviewed
statement timeout and test corrupt self- and multi-node cycles directly.

Normal acyclicity and one-parent constraints remain the primary invariant. Capture active lease
facts and earliest expiry against the same `database_time.now`. The one-statement snapshot is a
release requirement, not an optional optimization: no two-statement READ COMMITTED fallback may
return branch aggregates and row readiness from different snapshots. Factor the SQL into named
builders/CTEs and document its plan rather than weakening cross-field coherence.

### 9.3 Filter and pagination preservation

Preserve current exact ordering:

```text
updated:  branch.updated_at DESC, id DESC
created:  branch.created_at DESC, id DESC
priority: branch.priority DESC, updated_at DESC, id DESC
```

Root `total` counts qualifying roots. Child `total` counts qualifying direct child branches. Full
aggregate facts never alter qualification, ordering, or totals. Child paging cannot move a root
between root pages.

Status/source/tag filters remain subtree-aware. `pending`, `active`, and `dropped` keep their
shipped lease semantics; Phase 7 does not add a `waiting` lifecycle filter because Needs Attention
is the explicit queue. A branch-local unfiltered drill omits every status/source/tag predicate for
that branch while leaving the surrounding root result and global controls intact; the response/UI
labels that override so hidden descendants cannot be mistaken for filter matches.

### 9.4 Query-performance envelope

Build representative fixtures before setting budgets:

- at least 10,000 visible work items in one project;
- mixed broad and deep forests, including depth 50;
- Pending/Deferred/terminal mix;
- blockers and overlapping active leases;
- discovery edges independent of parentage;
- unresolved and resolved gates, including several on one work item; and
- roots that qualify only through deep descendants.

Record p50/p95 response time, rows, buffers, temporary spill, and query plan for first and later
root/child pages under each sort/filter. Compare against the Phase 6 hierarchy baseline. A count
query may be more expensive than flat search, but it must remain bounded and must not run one query
per branch/descendant.

### 9.5 Passive lease expiry

Gate, relationship, status, and explicit lease mutations publish live invalidations. Lease expiry
is passive and publishes none. Hierarchy components therefore combine:

- each visible node's existing `readiness.active_lease.expires_at`; and
- each node's `presentation.next_active_descendant_lease_expires_at`.

Schedule one bounded refresh at the earliest value, using the existing lease-refresh helper. Tests
freeze clock behavior and prove a collapsed root's active-descendant count changes after expiry
without requiring a WebSocket event.

## 10. Dashboard and proxy integration

### 10.1 Navigation and ownership

Add `/attention` with `Dashboard view="attention"` and a sidebar item “Needs Attention.” The
Dashboard view union becomes `library | attention | settings`. A small sidebar badge shows the
selected project's unresolved total by requesting the attention endpoint with `limit=0`; it is a
count, not notification/unread state.

Extract view-specific state/components where needed rather than adding all queue state and forms to
the existing dashboard monolith. The dashboard shell continues to own project selection, live
sync, the Phase 6 mutation-intent registry, navigation blocking for unresolved mutations, and
global notices.

### 10.2 Needs Attention view

Add a focused `HumanAttentionList` component with:

- question text rendered literally;
- work title, priority, status/readiness badges, and ancestry breadcrumb;
- requester client/session/model and request time;
- an “Open work context” action;
- a resolution form with a 4,000-character counter and durable-content warning;
- immutable cursor paging and loading/error/empty states; and
- a clear message that resolving records an answer but executes nothing.

The empty state says no explicit human questions are waiting; it does not claim all work is ready.
Resolution is per gate. No bulk checkbox, swipe dismissal, edit, delete, auto-answer, or silent
optimistic removal is added.

### 10.3 Work-detail gate panel

Add `HumanGatePanel` near readiness/relationships in work detail. It shows every gate returned in
`context.unresolved_gates`, the total/omitted count, the dedicated recent-resolved slice with its
total/omitted count, and links to filtered attention and full paired gate history. The normal event
timeline also renders request/resolution events chronologically.

When a gate's current projection reports context drift, both detail and attention show which anchor
categories changed and require the human to reload current work/context/relationships. Resolution
stays disabled until the user explicitly acknowledges the reviewed drift. The checkbox and exact
`current_context_revision` returned by that refetch become one frozen mutation body; neither a
separately cached tuple nor a later refetch is silently substituted. The resolved gate records the
exact accepted revision and drift outcome.

Completion, terminal actions, and delete affordances are disabled with a specific unresolved-gate
explanation. Checkpoint/progress and nonterminal edit affordances remain available. Deferral keeps
its lease guard and does not dismiss the panel.

### 10.4 Hierarchy cards

For root/child browse only, render a compact aggregate strip:

- direct children and total descendants;
- blocked, active, and completed descendant counts when nonzero;
- discovered descendant count and node discovery-origin label; and
- unresolved human-gate count with the strongest attention styling.

Accessible text spells out every count; color/icon alone is insufficient. Tooltips explain that
operational populations can overlap. Search-result cards stay focused direct hits and do not
pretend to carry branch aggregates.

Use `direct_child_count`, not filtered match state, for the disclosure affordance. When any
lifecycle, source, or tag filter hides all children, identify the suppressing filter categories and
offer a branch-local “Show all descendants” control that removes all three predicate families for
that drill without changing the root page. Clearly label the override. Preserve keyboard focus,
`aria-expanded`, `aria-controls`, motion-reduction behavior, cycle/depth fallbacks, and independent
child pagination.

### 10.5 Resolution mutation intent

Register `resolve_human_input` in the Phase 6 browser registry:

```text
slot: gate-resolution:{gate_id}
method/path: frozen exact nested resolve route
body: exact resolution, dashboard resolver provenance, drift acknowledgement,
      exact reviewed revision when acknowledged, one UUID
conflict keys: work item and gate only
expected status: 200
```

Add a `mutationGateKey` helper; the existing work key intersects completion/edit/delete intents,
while the gate key distinguishes slots without serializing unrelated gates or every mutation in a
project. The strict decoder verifies exact project/work/gate IDs, `gate_type=human`, resolved state,
submitted answer, dashboard resolver provenance, submitted reviewed revision, coherent
resolved/current revision and drift fields, and nonnull resolution time. A valid response may be historical replay, so it clears the intent and
refetches attention, gate history, hierarchy, open context, event timeline, and sidebar count. It
does not apply an old work snapshot because none is returned. A definite `gate_context_changed` clears the old UUID and obsolete reviewed revision, retains the
answer only as an editable form draft, reloads current state, and requires a new review before a new
UUID and frozen revision are prepared.

Timeout/network/abort/malformed response/5xx remains unresolved and permits only the exact frozen
retry. An asserted operation conflict remains a retained safety state. Modal/view unmount cannot
discard it, navigation is blocked by the existing registry, and no question/answer/UUID is written
to browser storage.

### 10.6 Proxy policy

Allow exactly:

- GET `projects/{project_id}/human-attention` with `work_item_id`, `limit`, `cursor` only;
- GET the nested per-work gate history with `status`, `limit`, `cursor` only;
- POST nested gate resolution with the exact
  resolution/resolver/acknowledgement/reviewed-revision/operation fields;
  and
- the existing context/event/hierarchy reads with their updated response fields.

The dashboard does not request gates, so its proxy denies gate-create POST. Continue denying all
lease routes/tokens. Reject gate IDs or operation IDs in query/header/cookie/nested locations,
unknown body fields, oversized bodies, bad UUIDs, forbidden origins/hosts, and control IDs equal to
the server bearer. Forward the frozen body once and never log it.

### 10.7 Live synchronization

Original request/resolution execution publishes one existing data-free invalidation after commit.
Applied exact replay may publish at most one optional healing invalidation. Failure publishes
none.

On invalidation:

- attention pages restart at the head on invalidation while an in-progress explicit cursor
  traversal remains free of offset-shift skips;
- hierarchy and open context refresh;
- sidebar attention total refreshes; and
- event pages reset according to their shipped behavior.

No gate ID, count, question, answer, project ID, or receipt data enters the WebSocket frame. The
frame remains only revision/invalidation control data.

## 11. Implementation increments

The following are reviewable commits/increments, but the public release gate is the combined
milestone.

### 11.1 Increment 7–8A — contract fixtures and migration

Deliver:

- frozen request/response/event/hierarchy schemas;
- migration `0014_human_gates`;
- ORM gate and event-reference models;
- gate immutability/completeness plus lease/work fail-closed triggers;
- request-state anchors and immutable attention sequence;
- conditional gate-event metadata/source guards preserving the legacy function and public
  non-gate wire shape;
- client-operation check widening and disabled-by-default request feature setting; and
- populated `0013` upgrade (including a completed append-event receipt), empty safe downgrade, and
  downgrade-refusal fixtures.

Exit when direct SQL cannot create a resolved gate, omit either required event, alter/delete a gate
or event, duplicate an event, mismatch body/actor/time/source, or insert a future gate type; and
every Phase 1–6 row is unchanged.

### 11.2 Increment 7–8B — gate service, history, and idempotency

Deliver request/resolve service functions, request/current/resolved revision checks, event staging,
activity updates, secret validation, stable errors, registry enrollment, mutation routes, cursor
gate-history read, canonical vectors, replay, and concurrency tests.

Exit when both operations are atomic and replayable, different-key resolution races have one
winner, later-state replay works, and no failure commits a gate/event/receipt fragment.

### 11.3 Increment 7–8C — readiness, claims, lifecycle, and recall

Replace the gate seam, extend readiness everywhere, add lifecycle guards, update the one-statement
context query, and expose bounded unresolved plus recent-resolved gates with exact totals.

Exit when the full status/blocker/lease/gate matrix agrees across ready list, fresh claim,
relationship counterpart, search summary, direct context, and claim-and-recall; exact active claim
replay/renew/release remain intact.

### 11.4 Increment 7–8D — attention reads and MCP

Deliver immutable-cursor attention/count API, the three MCP request/attention/history tools, strict
model/coherence/error handling, resource/prompt changes, catalog/schema snapshots, and plugin
workflow text.

Exit when a canonical agent can request and inspect a gate but has no resolution tool, sees paired
answers in bounded recall/full history, and never performs an automatic retry or stores a secret.

### 11.5 Increment 7–8E — hierarchy aggregation

Extract/refactor hierarchy query ownership, add the presentation model, compute full-branch counts
and discovery facts, preserve filters/order/pagination, return lease-expiry refresh hints, and add
representative plan measurements.

Exit when nested fixtures prove every count and flag, filters cannot change aggregate meaning,
ungrouped discoveries remain roots, and no N+1 or mutable counter store exists.

### 11.6 Increment 7–8F — dashboard and proxy

Deliver `/attention`, sidebar count, queue/detail components, resolution intent/decoder, proxy
allowlists, hierarchy aggregate/discovery UI, hidden-filter disclosure, lease-expiry refresh, and
responsive/accessibility styling.

Exit when a lost resolution response recovers exactly, unresolved intent survives component/view
unmount, the queue and hierarchy converge after replay, and browser storage/log inspection contains
no gate/control content.

### 11.7 Increment 7–8G — operations and release validation

Update API, architecture, agent, development, operation, roadmap, validation, examples, plugin,
and stack-smoke artifacts. Run migration/rollback drills, backup/restore replay, concurrency/fault
injection, query performance, complete automated suites, and cold review.

Exit only when the Section 20 definition of done is evidenced. A skipped PostgreSQL suite, missing
browser lost-response scenario, stale old-backend rollback procedure, or unmeasured hierarchy
query blocks release.

## 12. Test plan

### 12.1 Migration and database invariants

PostgreSQL tests must prove:

- fresh upgrade to head and populated `0013 -> 0014` preserve all prior data exactly;
- no gate or gate event is backfilled;
- only `gate_type=human` is accepted;
- all text/provenance/time/state bounds, immutable sequence, request/current/resolved revision
  triples, derived drift/acknowledgement booleans, and nullability matrices are enforced;
- scope FKs reject cross-project/work/checkpoint combinations;
- unresolved insert plus request event can commit, but either alone cannot;
- the only update is one unresolved-to-resolved transition accompanied by its resolution event;
- resolved update, unresolve, question/requester mutation, and all deletes fail;
- gate event kind/body/metadata/actor/timestamp/source mismatches fail;
- exactly one request and at most one resolution event exist per gate;
- legacy event rows remain readable and immutable, and the exact definition/hash of
  `mnemonic_work_event_metadata_v1_is_valid` is unchanged;
- new gate events cannot use `backfill` origin and old event types cannot carry internal
  `gate_id` or gate metadata;
- a populated `0013` append-event receipt replays with byte-identical JSON after upgrade;
- work-lease INSERT/generation replacement and terminal/delete work updates fail closed under an
  unresolved gate, while same-generation renewal/release remain allowed;
- the client-operation check accepts exactly the twelve registered kinds;
- downgrade locks writers first, succeeds only when no gate/gate receipt exists, restores the
  exact `0013` catalog, and refuses a nonempty deployment; and
- a two-connection downgrade/write race cannot drop a just-created gate or receipt.

### 12.2 Gate service and REST behavior

Cover:

- request on Pending work with/without active or expired retained lease;
- request rejection on Deferred, terminal, deleted, wrong-project, and missing work;
- multiple distinct gates and identical-text/different-key behavior;
- current unresolved/resolved projection, exact request/current/resolved revisions, and strict
  state validation;
- resolution on Pending and Deferred work with unchanged and changed work/context/relationship
  anchors, including required acknowledgement, exact reviewed-revision matching, and permanent
  audit fields;
- immutable-cursor full history for visible and exact soft-deleted work;
- disabled creation behavior for unkeyed and genuinely new keyed requests, enabled behavior after
  cutover, and no receipt left by a fenced first execution;
- wrong project/work/gate scope, already resolved, changed context, malformed text, and extra
  fields;
- exact activity timestamp movement without work-version movement;
- one request/resolution event with exact actor/body/metadata/source;
- stable sanitized errors and no question/answer echo; and
- direct unkeyed REST success explicitly documented as retry-unprotected.

### 12.3 Idempotency and fault injection

For both gate mutations, test keyed first success, same-key exact replay equality, mismatched reuse,
post-commit response loss, concurrent same-key ownership, owner rollback/waiter execution,
response-render failure, receipt completion failure, commit failure, and replay after current state
changes. Specifically test completed request replay and conflicting UUID reuse while creation is
disabled, plus new keyed/unkeyed disabled requests leaving no receipt.

Assert exact counts for gates, events, receipts, work versions, activity timestamps, and durable
effects. Original execution publishes once; applied replay may publish at most one data-free
healing invalidation and no other duplicate effect. Request replay after resolution must return the
stored unresolved response without reopening. Resolution replay after soft deletion must return
the stored answer without restoring ordinary work visibility.

### 12.4 Readiness and lifecycle matrix

Build a table-driven matrix over:

```text
lifecycle: pending, deferred, terminal
blocker: none, resolved, unresolved
lease: none, active, expired retained
gate: none, one unresolved, several unresolved, resolved only
```

Assert independent flags, counts, display precedence, `is_ready`, ready membership/total, minimal
display state, full summary, relationship counterpart, and context. Specifically prove:

- waiting beats blocked/active only in display convenience; all flags remain true;
- resolved gates do not affect readiness;
- active exact claim replay succeeds after gating without renewal;
- a different/fresh/replacement claim cannot acquire;
- renew/release still work;
- every current source/target lifecycle path is frozen: Deferred-to-terminal remains invalid, and
  any future target-terminal path is covered by the generic service/database gate guard;
- completion and terminal/delete guards reject while unresolved and succeed after resolution when
  all other facts permit; and
- deferral preserves the gate while Pending restoration remains waiting.

### 12.5 Deterministic concurrency and lock order

Use real PostgreSQL connections/barriers for:

1. request versus fresh claim in both serialization orders;
2. request versus completion, retirement/promotion, and deletion;
3. resolution versus fresh claim and completion;
4. resolution versus another new gate request;
5. same-key and different-key resolution races;
6. gate request/resolve racing tokenless context-checkpoint and graph mutations on the same work;
7. a human reads drifted revision B, another mutation advances it to C before resolution locks, and
   the B-bound intent fails until C is reviewed under a new UUID; and
8. unrelated work proceeding while one gate row is contended.

Assert no deadlock, no lock-order inversion, no acceptance of an unreviewed revision, and only the
documented linearized outcomes.

### 12.6 Event, recall, and attention reads

Test event list order/filter for both new types, exact internal/metadata gate-ID coherence and
bodies, legacy non-gate wire shape, bounded unresolved and recent-resolved slices, exact
totals/omitted counts above 20, recall after more than 20 unrelated later events, and source-fact
immutability.

Attention tests cover immutable sequence/cursor traversal, text-free `limit=0` count, optional work
filter, ancestor paths and 50-level truncation, several gates on one work, concurrent resolution
without skip/repeat, current readiness, project isolation, and strict response shape. Gate-history
tests page paired records, filter state, and read retained deleted-work decisions. A resolved gate
must leave attention immediately but remain in both gate history and event history.

### 12.7 Hierarchy aggregate matrix

Construct at least a three-level forest with:

- multiple direct and deep descendants;
- Pending, Deferred, done, wont-do, and promoted states;
- resolved/unresolved blockers;
- active and expired leases, including gated+active and blocked+active overlap;
- multiple gates on one node and gates on the branch root;
- planned children, discovery-from-parent, discovery-from-other, and ungrouped discovered roots;
- lifecycle/source/tag filters whose only match is deep, plus branch-local all-descendant drill;
- a valid tree deeper than the 50-level browser guard; corrupted self/multi-node cycle fixtures
  bounded by statement timeout; and
- root and child pagination under every sort.

Assert every presentation count, inclusive/strict boundary, discovery flag, filter independence,
earliest descendant lease expiry, current filter flags, stable ordering/total, and absence of
duplicates from multiple blocker/discovery/gate edges.

Run query-count assertions and `EXPLAIN (ANALYZE, BUFFERS)` fixtures. A Python tree walk, one query
per branch, or load-all-descendants fallback fails the release gate.

### 12.8 MCP tests

Prove:

- the catalog is exactly 25 and schemas reject unknown fields;
- exactly ten mutation tools require the operation UUID and advertise idempotency;
- gate request forwards its exact body once and validates anchors/coherent result;
- no MCP resolution tool exists;
- list attention/history are read-only, cursor-safe, and return only coherent scoped items;
- full/minimal search, ready, context, event, resource, and prompt models understand waiting/gates;
- request unknown outcomes give same-key guidance; feature-fence/conflict/not-found errors are
  sanitized;
- tool descriptions contain human-answer and nonauthority guardrails;
- no generated example self-resolves, generates a UUID per retry, or stores a secret; and
- fresh and sequential plugin install resolve manifest `0.5.0`.

### 12.9 Frontend unit and component tests

Cover:

- new type guards/strict gate-resolution decoder;
- proxy attention/history GET and resolution POST allowlists plus denial of gate create, leases,
  bad IDs, unknown fields, and secret/control placement;
- frozen resolution body/acknowledgement/reviewed-revision/UUID reuse, double click, timeout,
  malformed 2xx, safety conflict, changed-context rejection/new intent, deterministic reviewed
  B-to-current-C rejection, component/view unmount, and no browser-storage writes;
- attention count/cursor paging, empty/error/background-refresh, breadcrumbs, literal rendering,
  resolver form, current-state reconciliation, and accessibility;
- detail gate panel, both omission counts, full history/deleted audit behavior, guarded terminal
  buttons, and still-enabled history actions;
- hierarchy count labels, nonexclusive tooltips, discovery labels, default collapse,
  lifecycle/source/tag hidden explanation, branch-local all-descendant drill, child paging,
  depth/cycle fallback, and keyboard/ARIA behavior; and
- passive descendant lease-expiry scheduling under fake time.

### 12.10 Full-stack acceptance

Playwright and writable disposable-stack smoke cover:

1. create/claim work, request a gate through MCP, and observe active+waiting overlap;
2. release/expire the claim, prove ready and fresh claim exclude it;
3. see the question in Needs Attention, hierarchy branch count, detail panel, event timeline, and
   recall;
4. resolve through the dashboard, lose/replace the first response, and retry the exact frozen
   request;
5. mutate work/context/relationships under another gate, prove stale resolution is rejected,
   review revision B, mutate again to C before submit, prove the B-bound intent is rejected, then
   review C and record an acknowledged new intent;
6. prove one resolution/event/activity change and current cursor queue/count convergence;
7. append more than 20 unrelated events and prove the paired answer remains in dedicated recall and
   full gate history; work becomes ready only when lifecycle, blockers, and leases also permit;
8. build planned/discovered nested work and verify root collapse, exact aggregates, and the
   all-filter branch drill;
9. let a collapsed descendant lease passively expire and observe count refresh; and
10. inspect WebSockets, browser storage, logs, and outputs for question/answer/UUID/token leakage.

The stack checker records only statuses and aggregate counts; it never writes gate text, actor IDs,
operation IDs, or response hashes into validation logs.

## 13. Delivery sequence and dependency gates

| Gate | Depends on | Evidence before proceeding |
| --- | --- | --- |
| product contract frozen | roadmap/baseline reconciliation | state, authority, lifecycle, history, aggregate, and discovery definitions reviewed |
| migration merged | contract | populated upgrade, trigger invariants, legacy validator preservation, guarded downgrade |
| gate core merged | migration | atomic request/resolve, idempotency, source-event, and fault tests |
| readiness/lifecycle merged | gate core | full matrix and deterministic claim/terminal races |
| attention/MCP merged | backend contract | strict live API reads/writes and 25-tool snapshots |
| hierarchy aggregates merged | gate/readiness facts | count matrix, mandatory one-statement snapshot, cycle bounds, measured query plans |
| dashboard/proxy merged | stable REST | attention, frozen resolution, progressive disclosure, expiry refresh, e2e |
| combined release accepted | all prior gates | full unskipped suites, migration/restore drills, operations/docs/plugin agreement, cold review |

Do not release a “backend gates now, UI later” mode. Backend increments can merge behind an
unexposed prerelease boundary, but no supported client may create a gate until the complete human
oversight path is deployable.

## 14. Migration, deployment, and rollback

### 14.1 Pre-deployment

- Take and restore-test a database backup.
- Confirm schema head is exactly `0013_idempotent_mutations` and application/plugin versions match
  the Phase 6 validation record.
- Record counts/content hashes for canonical tables and the exact legacy metadata-v1 function
  definition.
- Rehearse migration locks and hierarchy queries on a production-sized restored copy.
- Confirm no locally invented `work_gates`, gate event types/columns, or operation kinds collide.
- Quiesce all mutation and claim writers for the schema/backend cutover.

### 14.2 Upgrade and client order

1. Apply `0014_human_gates` transactionally.
2. Deploy the gate-aware backend with `human_gate_requests_enabled=false` while writers remain
   quiesced.
3. Run schema/ready/claim/context/attention probes against an empty gate table, including old
   append-event receipt replay and database fail-closed trigger probes.
4. Drain and terminate every old backend process; record image digest, process/replica inventory,
   routing-pool membership, and zero old active connections before proceeding.
5. Deploy the MCP adapter/plugin and dashboard/proxy that understand the strict response fields and
   new tools/routes.
6. Resume ordinary writers only after all canonical paths target the gate-aware backend; prove gate
   request remains fenced.
7. Enable gate requests consistently across the gate-aware pool.
8. Create a synthetic gate, verify ready/claim exclusion and UI visibility, resolve it, and record
   redacted evidence.

An old MCP strict response model may reject new readiness/context fields, so coordinate its rollout
rather than claiming transparent compatibility. There are no duplicate legacy tools or response
translation shims.

### 14.3 Application rollback boundary

Before any gate or gate operation receipt exists, a quiesced rollback to the Phase 6 application
may be paired with the guarded database downgrade.

After the first gate exists, normal rollback to a backend that ignores gates is forbidden. An old
backend can still mis-list gated work; the new database triggers make its fresh/replacement claims
and terminal/delete writes fail closed, but that is an incident backstop, not a supported serving
mode. Keep revision 0014 and deploy a forward fix or the last known gate-aware backend. If no
gate-aware binary is safe, disable new gate requests, quiesce all ready/claim/terminal writers, and
serve only reviewed read/repair operations until the forward fix is ready.

Never “fix” rollback by resolving, deleting, truncating, or hiding gates/receipts. Gate deletion is
database-forbidden and receipt loss would make delayed retries unsafe.

### 14.4 Database downgrade

Downgrade requires:

- all writers and clients quiesced;
- one transaction acquiring and retaining `ACCESS EXCLUSIVE` locks in this exact writer-compatible
  order before checking: `client_operations`, `work_items`, `work_gates`, then `work_events`;
- zero gate rows;
- zero gate event rows; and
- zero receipts whose kind is either gate mutation.

Abort without force if any condition fails. On a provably unused revision, drop the lease/work
fail-closed triggers, gate-event constraints/index/FK/column, and gate triggers/table; restore the
exact `0013` event/client-operation constraints; and leave the legacy metadata-v1 function plus all
Phase 1–6 rows untouched.

Barrier tests cover a keyed writer after receipt reservation but before gate insert and an unkeyed
writer after focal-work lock. They must prove the downgrade neither deadlocks nor lets a writer
commit between emptiness checks and drop. A future
replacement schema migrates gate/history/receipt semantics forward; it never treats this downgrade
as ordinary data disposal.

### 14.5 Backup and restore

Backups include gates, the identity sequence state, their events, operation receipts, work/graph
facts, and migration objects. The restore drill:

1. requests and resolves gates, leaving at least one unresolved;
2. records redacted counts and retained exact retry arguments outside the backup artifact;
3. backs up/restores into an isolated gate-aware environment;
4. proves unresolved ready/claim exclusion;
5. replays request and resolution receipts; and
6. proves no extra gate/event/activity/lifecycle effect.

## 15. Verification strategy

### 15.1 Required automated suites

Use Python 3.14, separate backend/MCP `uv` environments, Node 24, and a real isolated PostgreSQL
test database. A skipped PostgreSQL-marked suite is a failed release gate.

```sh
docker compose -f compose.test.yaml up -d --wait

cd backend
uv sync --frozen
uv run pytest -q
uv run ruff check .

cd ../mcp
uv sync --frozen
uv run pytest -q

cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e:stack
```

Also run:

- fresh database upgrade to head;
- populated `0013 -> 0014` upgrade with historical edge/event/receipt variants, including exact
  append-event receipt replay;
- exact legacy validator/non-gate event-wire/row parity checks;
- old Phase 6 backend process against `0014`: stale ready read documented, fresh/replacement claim
  and terminal/delete attempts rejected by database guards;
- unused locked downgrade/re-upgrade;
- refused downgrade and two-connection race after gate data;
- supported dump/restore and receipt replay;
- representative hierarchy `EXPLAIN (ANALYZE, BUFFERS)` fixtures;
- plugin manifest validation, fresh install, and sequential upgrade;
- updated writable `scripts/check-stack.py` only against a disposable stack;
- `git diff --check`; and
- Markdown link/path and OpenAPI/tool schema snapshot checks.

### 15.2 Fault-injection checkpoints

Provide deterministic test seams before/after:

- receipt reservation and the first-execution feature-fence check;
- human revision fetch/freeze and a subsequent work/context/relationship mutation;
- work-row lock and gate-row lock;
- gate insert/resolution update;
- activity update;
- gate event insert;
- response-model rendering;
- receipt completion;
- transaction commit;
- post-commit invalidation;
- MCP decoding; and
- proxy/browser response decoding.

Every pre-commit failure leaves no partial gate/event/activity/receipt change. Commit-response and
publication failures recover only through the retained exact request and may repeat only the
data-free healing invalidation.

### 15.3 Manual acceptance

With two independent clients and one browser:

1. acquire a lease, request a gate, and inspect overlapping readiness flags;
2. replay the active claim and prove it does not extend; try a different/fresh claim and observe
   gate rejection;
3. inspect ready list, cursor attention queue/count, hierarchy counts, context, paired gate history,
   and event history;
4. race request against completion in both orders;
5. change work/context/relationships, prove stale resolution rejection, review revision B, advance
   it to C before submit and observe another rejection, then review/acknowledge C, discard the first
   success response, and retry the exact pending action;
6. append more than 20 ordinary events, then verify dedicated resolved recall and full gate history;
7. replay both old request and resolution after later state changes;
8. inspect a mixed planned/discovered tree under lifecycle, source, and tag filters plus the
   branch-local all-descendant drill;
9. wait for a descendant lease expiry and observe collapsed aggregate refresh; and
10. restart/restore services and repeat current-read and exact-replay checks.

Record commands, versions, statuses, timings, query plans, and redacted counts only.

## 16. Security, privacy, performance, and operations

### 16.1 Security and authority

The shared bearer remains the authorization boundary. Gate provenance is asserted and can be
spoofed by a bearer holder; docs and UI must say so. Phase 7 adds no user accounts, roles, CSRF-
independent remote auth story, signature, or approval capability.

The dashboard remains trusted-local/same-origin. Remote exposure still requires HTTPS and a
separate authentication boundary. Canonical MCP cannot resolve a gate. Direct REST resolution is
still only shared-bearer authority and is not proof that a human spoke.

Gate text, like checkpoints/events, is untrusted. Never execute markup, shell, links, or stored
instructions merely by displaying/recalling it.

### 16.2 Data minimization and redaction

Persist only the fields in Section 5.1 plus the two typed events and normal Phase 6 response
snapshot. Do not add IP address, user agent, browser tab ID, trace headers, notification delivery,
read receipts, resolver display name, or request samples.

Use this explicit sink matrix:

| Data | Permitted durable/response locations | Forbidden locations |
| --- | --- | --- |
| question/resolution | authoritative `work_gates`; exact matching gate-event body; authorized gate/context/event API responses; necessary protected-operation response snapshot | logs, errors, metrics, traces, URLs, WebSockets, search/semantic indexes, analytics, browser persistence |
| gate ID | gate/event/FK rows, typed gate metadata, authorized paths/responses, in-memory UI state | question/answer text, metrics labels, WebSockets, analytics, browser persistence, raw access-log paths when route-template logging is available |
| operation UUID/control values | receipt identity and dashboard in-memory frozen intent only | gate/event fields, public response, logs, errors, metrics, traces, WebSockets, analytics, browser persistence |

Existing bounded operation logs may record only registered operation kind and finite outcome.
Question/answer redaction tests assert absence only from forbidden sinks; they also assert exact
presence in the authoritative allowed sinks. The attention `limit=0` path proves a sidebar count
transmits no gate item, body, or provenance.

### 16.3 Capacity and query performance

Measure:

- gate row/event/receipt bytes at maximum text size;
- request and resolution p50/p95/p99 overhead;
- ready/fresh-claim predicate change at representative unresolved-gate density;
- attention first/later page plans;
- hierarchy root/child plans across broad/deep/filter cases;
- active-expiry refresh rate; and
- backup/restore growth.

Set budgets from the measured Phase 6 baseline before declaring done. Do not add caching,
partitioning, compression, archival, TTL, counter tables, or extra indexes solely for a synthetic
microbenchmark.

### 16.4 Operational inspection

Provide safe aggregate queries for:

- unresolved gate count and oldest age by project-free aggregate/time bucket;
- request/resolution counts by day;
- invariant violations (must be zero);
- gate-operation outcome rates from bounded logs;
- gate/event/receipt storage size; and
- hierarchy query latency/buffer/deadlock statistics.

Routine examples do not select IDs, questions, answers, actor/session values, response JSON, or
fingerprints. There is no manual SQL “resolve,” delete, rebind, unlock, or receipt purge runbook. A
corrupt gate/event is an incident requiring restore or reviewed forward migration.

### 16.5 Low-noise guarantee

Only an explicit gate request enters Needs Attention. Blockers, Deferred work, expired leases,
progress phrases, discovered work, and failed calls do not automatically enqueue human attention.
Hierarchy gate badges aggregate only unresolved gate rows. Resolution removes exactly that gate
from the queue; it does not create notification residue.

## 17. Expected file impact

| Area | Expected files | Planned change |
| --- | --- | --- |
| migration | new `backend/alembic/versions/0014_human_gates.py` | gate table/anchors/sequence, fail-closed work/lease guards, event reference/constraints/trigger, receipt-kind widening, guarded downgrade |
| ORM | `backend/src/mnemonic_api/models.py` | `WorkGate`, event gate ID, exact constraints/indexes |
| schemas | `backend/src/mnemonic_api/schemas.py` | gate request/resolution/history/attention, cursor, anchors/drift, readiness/context/event/hierarchy fields while freezing legacy event shape |
| errors | `backend/src/mnemonic_api/errors.py` | gate not-found/gated/already-resolved/context-changed/secret/fence errors |
| gate service | new `backend/src/mnemonic_api/services/gates.py`, exports | request/resolve, anchors/drift, counts/guards, cursor attention/history projections |
| readiness/context | `services/readiness.py`, `work_context.py`, `work_items.py`, `leases.py`, route wiring as needed | canonical predicate, overlap projection, lifecycle guards, unconditional checkpoint work lock, bounded gate recall |
| events | `services/work_events.py` | typed request/resolution event constructors/read validation |
| hierarchy | new `services/hierarchy.py` or focused extraction from `relationships.py` | one-statement branch aggregates, discovery facts, ancestry |
| idempotency/routes | `services/client_operations.py`, `application.py` | two registry entries, two write/two read routes, legacy receipt compatibility, route-owned outcomes |
| backend tests | new migration/gate/hierarchy PostgreSQL suites; `backend/tests/conftest.py`; readiness, claim, event, API, client-operation, live-sync regressions | cleanup table order, invariants, old-writer guards, concurrency, replay, cursors, counts, plans |
| MCP models/API | `mcp/src/mnemonic_mcp/models.py`, `server.py`, `api.py`, `validation.py` | three tools, strict gate/readiness/context/event models, safe guidance |
| MCP tests | `mcp/tests/test_tools.py`, `test_transport.py`, fixtures/snapshots | exact 25-tool catalog, 10 protected writes, no resolve tool, coherence/recovery/security |
| frontend route | new `frontend/app/attention/page.tsx` | dedicated dashboard view |
| frontend components | new `human-attention-list.tsx`, `human-gate-panel.tsx`; `dashboard.tsx`, `work-hierarchy.tsx`, `work-item-card.tsx`, `work-item-detail.tsx`, event timeline | queue/resolution, gate detail, aggregates/discovery, guarded actions |
| frontend libraries | `types.ts`, `api.ts`, `proxy-policy.ts`, `mutation-intent.ts`, `mutation-responses.ts`, `work-events.ts`, `work-item-view.ts`, `lease-refresh.ts` as needed | cursor/history contracts, route policy, work+gate resolve conflict keys/decoder, legacy event shape, waiting/expiry behavior |
| frontend styles/tests | `app/globals.css`, unit/component tests, new Phase 7–8 Playwright spec | responsive accessible attention/hierarchy and lost-response acceptance |
| stack smoke | `scripts/check-stack.py` | explicit disposable gate request/resolution/replay/readiness/hierarchy scenario |
| config/docs | backend settings and `.env.example`; `README.md`; `docs/api-contract.md`, `architecture.md`, `operations.md`, `validation.md`, `agents.md`, `development.md`, `roadmap.md` | disabled-until-cutover fence, shipped contracts, trust boundary, cutover/rollback, verification/status |
| plugin | all three skills, shared references, inner manifest and marketplace metadata if needed | human-gate workflow, discovery hierarchy guidance, version `0.5.0` |
| examples | only relevant MCP/API workflow examples | stable operation UUID, explicit human answer, dual relationship facts, no secrets |

Implementation may split focused helpers/tests when ownership improves. It must not create legacy
gate aliases, `/v2` duplicates, a frontend-only gate store, or a second agent graph.

## 18. Risks and mitigations

| Risk | Consequence | Required mitigation |
| --- | --- | --- |
| old backend runs after gates exist | stale ready read or attempted gated mutation | disabled creation fence, recorded drain, DB lease/work guards, gate-aware rollback floor, quiesce on incident |
| ready and claim predicates drift | advisory list and authority disagree | one eligibility builder plus existing future-gate seam regression |
| gate races completion | done work retains unanswered question or gate is lost | focal work lock before insert/check; deterministic two-connection tests |
| gating revokes capability recovery | agent cannot safely recover an already committed claim | exact active claim replay precedes fresh gate checks; renew/release preserved |
| agent/selfish bearer fabricates resolution | coordination guard is removed without human input | no MCP resolve tool, human dashboard workflow, honest asserted-boundary copy; authenticated enforcement explicitly deferred |
| answer treated as execution authority | destructive action occurs from stale/unverified prose | nonauthority contract, no automatic mutation, current authorization recheck |
| gate/event diverge | history lies about question or answer | composite FK, exact source-fact trigger, deferred event completeness, immutability |
| legacy event validator rewritten | old metadata becomes unreadable | keep function byte-identical; conditional gate check and separate trigger |
| repeated request creates noise | duplicate questions and events | MCP requires keys and keyed REST replays exactly; distinct/unkeyed intents remain explicitly separate |
| terminal/delete strands gate | attention disappears without answer | unresolved-gate guards; no auto-resolution; soft delete only after resolution |
| question/answer leaks | durable secret/privacy exposure | 4K bound, request-known secret rejection, warnings, redaction/storage tests |
| browser loses ambiguous answer | duplicate or conflicting resolution | dashboard-level frozen intent, strict decoder, navigation blocking, exact replay |
| request replay looks current | caller thinks resolved gate reopened | historical replay docs and mandatory current refetch |
| legacy append-event receipt gains a field | permanent Phase 6 replay becomes unavailable | no public top-level gate ID, frozen legacy wire fixture/replay test |
| answer is stale when resolved | decision addresses obsolete work/context/graph | immutable request anchors, exact current/reviewed/resolved revision tuples, locked equality check, and B-to-C race test |
| cursor traversal skips attention | a human misses a waiting question | immutable sequence keyset and text-free count mode; priority display does not reorder |
| corrupt graph cycles recurse forever | hierarchy request exhausts resources | checked visited path, statement timeout, self/multi-cycle fixtures |
| hierarchy facts cross snapshots | branch counts disagree with row readiness | mandatory one-statement database-time snapshot |
| hierarchy counts follow filters | Pending view falsely reports zero completed descendants | full-branch aggregate definition independent of qualification filters |
| joins multiply counts | multiple gates/blockers/discovery edges inflate descendants | aggregate distinct member facts/EXISTS before branch sums; matrix tests |
| discovered-from becomes an inferred parent | provenance changes graph structure and hides work | separate facts/flags, ungrouped roots remain visible, dual-edge workflow guidance |
| active descendant badge goes stale | passive expiry leaves incorrect collapsed summary | earliest descendant expiry hint and scheduled refresh |
| recursive aggregate is slow | dashboard becomes unusable on large forests | one bounded set query, existing/new indexes, representative plans/budgets, no N+1 |
| mutable counter cache drifts | human summary contradicts graph | derive from canonical tables; no counter/closure store |
| too many gate decisions bloat recall | context payload grows without bound | separate fixed 20-row unresolved/resolved slices, exact totals/omissions, pageable gate history |
| downgrade drops durable decisions or deadlocks writer | history/retry loss or aborted maintenance | exact client-operation→work→gate→event table-lock order, empty-only downgrade, barrier tests, no force |

## 19. Explicitly deferred work

These phases do not implement:

- timer, CI, external-event, other-work-item, or generic/custom gate types;
- gate edit/delete/reopen/waive APIs, bulk resolution, templates, votes, quorum, or approval groups;
- authenticated users, human accounts, RBAC, signatures, or verified approver identity;
- email, desktop, Slack, webhook, push, unread, snooze, assignment, or escalation notifications;
- automatic question generation, classification, answer suggestion, timeout resolution, or model
  self-approval;
- secret storage, credential brokerage, encrypted answer fields, or attachment uploads;
- automatic lease release/claim/execute/lifecycle change after request or resolution;
- parent inference from discovery, semantic similarity, checkpoint text, or filesystem paths;
- drag-and-drop reparenting, bulk hierarchy editing, closure tables, cached branch counters, or a
  separate human graph;
- project activity feed/SSE/webhooks (Phase 12); or
- verification results/artifacts (Phase 11).

Observed usage can inform later phases without weakening the explicit, durable human-decision
boundary delivered here.

## 20. Definition of done

Phases 7 and 8 are complete only when every item below is true.

### Persistence and migration

- [x] `0014` upgrades fresh and populated `0013` databases without changing prior content or
      fabricating gates/events.
- [x] Gate type, scope, text, provenance, request/current/resolved revision anchors, immutable
      sequence, state, drift audit, timestamps, indexes, and FKs are database-enforced.
- [x] A gate begins unresolved, resolves exactly once, and cannot be updated/deleted afterward.
- [x] Request/resolution events are required, unique, immutable, and exact matches to their source
      gate.
- [x] The legacy metadata-v1 function and non-gate event wire shape are unchanged; every Phase 5/6
      historical event and populated append-event receipt remains readable/replayable byte-for-byte.
- [x] The receipt registry/check accept exactly twelve protected REST kinds with unchanged Phase 6
      retention and immutability.
- [x] Lease generation/insert and terminal/delete database guards make an old backend fail closed,
      while same-generation renewal/release remain possible.
- [x] Unused downgrade is writer-locked and exact; any gate/event/receipt makes downgrade refuse.
- [x] Backup/restore preserves gates, events, readiness exclusion, and exact mutation replay.

### Gate semantics and concurrency

- [x] Request is allowed only on visible Pending work and may coexist with an existing lease.
- [x] Ready listing and every fresh/replacement claim exclude unresolved gates through one shared
      predicate.
- [x] Exact active claim replay, renewal, release, checkpoints, and progress remain available as
      specified.
- [x] Completion, every target-terminal transition, and deletion cannot race past an unresolved
      gate; the current Deferred-to-terminal path remains invalid.
- [x] Work/version/context/relationship drift is visible; resolution is bound under lock to the
      exact server-returned revision the human reviewed, and that revision/drift acknowledgement is
      permanently recorded.
- [x] Resolution changes only the gate, event history, work activity time, and derived readiness;
      it changes no work version/lifecycle/lease/relationship/checkpoint.
- [x] Same-key concurrency executes once; different-key resolution concurrency has one winner and
      one definite conflict.
- [x] Lock-order tests cover gate/claim/lifecycle/graph races without deadlock.

### API, history, and idempotency

- [x] The two write routes plus attention and per-work history reads have strict documented
      schemas, scope/filter-bound immutable cursors, the attention text-free count-only mode, and
      stable sanitized errors.
- [x] Request/resolution are enrolled in canonical fingerprint/response vectors; request replay
      and UUID-conflict detection precede the creation fence, and all replay precedes current domain
      guards.
- [x] Replay creates no duplicate gate, event, activity change, version change, or durable/domain
      side effect; an applied replay may emit at most one optional data-free healing invalidation.
- [x] Work events expose two typed gate facts with exact bodies and internal/metadata ID coherence
      without changing legacy event response snapshots.
- [x] Work context reports exact unresolved/resolved totals, at most 20 active questions, and at
      most 20 paired recent decisions; more than 20 unrelated events cannot evict the category.
- [x] Full paired gate history remains cursor-pageable for visible and exact retained deleted work.
- [x] Attention pages contain only current unresolved project-local gates in immutable sequence
      order with coherent summaries/ancestry; `limit=0` returns total without gate content.
- [x] The explicit allowed/forbidden sink matrix passes: gate content exists in authoritative
      gate/event/authorized-response/necessary-receipt sinks and nowhere forbidden.

### Hierarchical presentation

- [x] Blank dashboard browse is root-only and collapsed by default, with the documented filter-
      scaffolding exception.
- [x] Every root/child response carries exact direct/descendant/blocked/active/completed/discovered
      and branch-gate counts with the defined inclusive/strict boundaries.
- [x] Aggregate counts are independent of current qualification filters and avoid join
      multiplication.
- [x] Discovery labels derive only from explicit discovery edges; no parent is inferred and an
      ungrouped discovery remains a root.
- [x] Children can be paged/drilled into; lifecycle/source/tag filters that hide them identify the
      cause and offer a branch-local all-descendant override.
- [x] Aggregate and row facts share one statement/database-time snapshot.
- [x] Visited-path cycle protection, untruncated server rollups, browser depth/accessibility guards,
      and flat-search breadcrumbs remain intact.
- [x] Collapsed active-descendant counts refresh after passive lease expiry.
- [x] Representative query plans meet recorded budgets with no N+1, closure table, or mutable
      aggregate cache.

### Client surfaces

- [x] MCP catalog is exactly 25; ten protected writes require operation UUIDs and have truthful
      annotations; no MCP resolution tool exists.
- [x] MCP request/attention/history tools use strict coherent models, one write attempt, safe
      recovery, immutable cursor reads, and explicit human-answer/nonauthority guidance.
- [x] Plugin `0.5.0` fresh/sequential installs pass and all skills agree on gates, readiness, and
      dual parent/discovery facts.
- [x] `/attention`, text-free sidebar total, paired gate history/panel, stale-context review, event
      timeline, and hierarchy aggregate UI work across responsive and accessible paths.
- [x] Dashboard resolution uses the existing frozen same-document registry and strict decoder;
      ambiguity survives unmount/navigation attempts and writes nothing to web storage.
- [x] Proxy permits only exact attention/history GET and resolution POST shapes and continues
      denying gate creation plus all lease capabilities.
- [x] Live invalidation/refetch converges queue, hierarchy, context, events, and sidebar count.

### Operations and quality gate

- [x] Deployment docs require disabled creation, recorded old-process drain, coordinated enablement,
      and the gate-aware rollback floor; old-image-on-0014 guard tests and writer-quiescence incident
      procedure pass.
- [x] Storage, latency, hierarchy plans, expiry refresh, backup, and restore measurements meet
      recorded budgets.
- [x] Backend tests and Ruff pass with PostgreSQL tests unskipped.
- [x] MCP tests pass in its separate frozen environment.
- [x] Frontend unit tests, typecheck, production build, and isolated Playwright stack pass.
- [x] The writable disposable-stack smoke proves request, gating, human resolution, lost-response
      replay, hierarchy aggregation, and no duplicate effect.
- [x] API, architecture, operations, agents, validation, roadmap, examples, and plugin documents
      agree on shipped coverage, trust limits, and deferred work.
- [x] A cold adversarial review finds no unresolved blocker or high-severity planning gap.

## 21. Cold adversarial review disposition

Two fresh-context subagents reviewed the completed first draft without participating in its design:
one checked it against repository implementation details and one attacked the document as a
standalone contract. Their material findings and resulting changes are durable planning evidence,
not implementation validation.

| Severity | Cold finding | Disposition in this revision |
| --- | --- | --- |
| blocker | A nullable public `WorkEventRead.gate_id` would make permanent pre-`0014` `append_event` receipts fail strict replay equality. | Keep the legacy top-level event wire byte-for-byte unchanged. Store the FK internally and expose a required gate UUID only in typed gate-event metadata. Upgrade tests replay a populated `0013` receipt exactly. |
| blocker | Checking the disabled creation fence before receipt lookup would make a committed request unreplayable and hide conflicting UUID reuse. | Keyed calls enter the permanent registry first; replay/conflict returns behind the fence, while a genuinely new disabled reservation rolls back and leaves its UUID unbound. Replay-after-disable, conflict-after-disable, and new keyed/unkeyed fence tests are required. |
| high | A Boolean drift acknowledgement was not bound to the state the human actually reviewed, allowing a B-to-C race before resolution locks. | Return an exact current revision tuple; freeze and fingerprint it with acknowledged resolution; require equality with locked current state; persist the accepted resolution tuple; and test deterministic B-to-C rejection/new review. |
| high | Downgrade table locks inverted keyed writer order and could deadlock after receipt reservation. | Lock `client_operations -> work_items -> work_gates -> work_events` in one transaction, then check/drop. Keyed and unkeyed barrier races are mandatory. |
| high | Shared-bearer MCP resolution could not enforce that a human answered. | Declare gates an asserted coordination boundary, not authenticated approval; remove resolution from canonical MCP; keep dashboard/direct REST within the documented trust boundary; defer enforceable approval to authenticated principals/capabilities. |
| high | Recursion keyed by depth was not cycle-safe. | Carry a visited UUID path with `UNION ALL`, reject revisits, derive depth outside identity, apply a statement timeout, and test self/multi-node corruption. |
| high | Resolved decisions aged out of recall with ordinary events and lacked a paired read path. | Add bounded recent-resolved gate records/totals plus a cursor-paged authoritative per-work history API/tool, including exact retained deleted-work audit. |
| high | The plan simultaneously required one hierarchy snapshot and allowed a two-statement fallback. | Make one PostgreSQL statement and one database-time snapshot a release requirement; remove the fallback. |
| high | Idempotency language forbade duplicate publication while replay deliberately allowed healing invalidation. | Define the invariant as no duplicate durable/domain effect, with at most one optional data-free healing invalidation per applied replay. |
| high | Current/future terminal transition coverage was not fully frozen for Deferred work. | Record the full current transition matrix and use target-terminal service/database guards so Deferred-to-terminal remains invalid and future routes cannot bypass gates. |
| medium | A human could answer after work, current context, or graph facts materially changed. | Persist request anchors and the exact accepted resolution revision, expose current drift, and require an acknowledged client revision to equal the recomputed tuple under lock. |
| medium | Operational cutover alone could not make an accidentally routed Phase 6 writer fail closed. | Add disabled-by-default gate creation, recorded old-process drain, lease/work database backstops, and an old-image-on-`0014` test. |
| medium | Priority-first offset attention pages could skip or repeat questions. | Use an immutable database sequence and opaque keyset cursor, remove priority from ordering, and add a text-free count mode. |
| medium | Security/redaction claims contradicted intentional gate/event/receipt storage, and sidebar counts over-fetched text. | Add an allowed/forbidden sink matrix and use `limit=0` for count-only sidebar refreshes. |
| medium | The hidden-descendant action cleared only lifecycle even when source/tag filters suppressed children. | Identify all suppressing predicate families and provide a branch-local all-descendant drill that overrides lifecycle, source, and tag filters. |
| medium | Gate-resolution browser conflict keys could serialize unrelated project work. | Use only the existing focal-work key plus a new gate key; do not use project or attention-collection keys. |
| medium | Migration/test impact omitted deferred-constraint forcing and explicit cleanup/client fixtures. | Require `SET CONSTRAINTS ALL IMMEDIATE` before event DDL, production-size lock measurements, `backend/tests/conftest.py` updates, and populated legacy receipt fixtures. |

Alternatives deliberately rejected:

- Blocking renewal after gating was rejected because renewal and exact replay maintain an already
  issued capability; agent guidance instead requires stopping at the gate and releasing when safe.
  Fresh/replacement ownership remains database-blocked.
- Authenticated approval was not approximated with spoofable dashboard fields. This release is
  candidly coordination-only; genuine approval security remains deferred.
- Rewriting completed receipt JSON or adding a nullable field to every event was rejected in favor
  of a typed gate-event metadata extension that preserves old replay bytes.
- Mutable priority ordering was rejected for the attention queue because completeness and a small
  low-noise queue matter more than reshuffling.
- A two-statement hierarchy hydration shortcut and mutable aggregate cache were rejected because
  both weaken the exact human-facing snapshot contract.

The original cold reviewer then re-audited the remediated document and explicitly found no
remaining blocker or high-severity issue and no contradiction introduced by the fixes. That
confirmation covered replay/conflict-before-fence ordering, rollback of new disabled reservations,
exact reviewed-revision fingerprint/storage/lock validation with B-to-C rejection, and tokenless
checkpoint plus graph-mutation serialization. This is plan acceptance evidence, not implementation
or test evidence.
