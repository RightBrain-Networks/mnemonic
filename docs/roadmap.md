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

## Objective

Introduce a stable durable unit of work and stop treating each hand-off as a top-level task.

This is the foundational architectural change and should be completed before substantial coordination features are added.

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

## Objective

Allow multiple agents to safely select and coordinate work without introducing conventional permanent assignees.

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

## Objective

Represent how work relates to other work without forcing every discovered item into a flat issue queue.

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

Explicitly marks canonical work identity.

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
- `duplicate-of` identifies canonical work;
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

## Objective

Give agents a purpose-built way to discover actionable work.

Search and recall are not sufficient coordination primitives.

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

## Objective

Move collaboration history out of mutable work records.

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
duplicate_marked
```

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

## Objective

Make agent retries safe.

Ambiguous network failures are normal. Agents should not need prose instructions telling them how to manually determine whether a timed-out operation actually succeeded.

## Proposed Mechanism

Mutating operations should optionally accept:

```text
client_operation_id
```

A useful uniqueness scope might be:

```text
project_id
source_session_id
client_operation_id
```

On retry, the server should return the original successful result rather than execute the mutation again.

## Operations to Prioritize

- create work,
- create checkpoint,
- append event,
- add dependency,
- create gate,
- submit verification result.

## Acceptance Criteria

- Replaying a successful request with the same operation ID does not create duplicates.
- The original result can be safely returned to the retrying client.
- Idempotency behavior is clearly documented in the MCP skill.

---

# Phase 7 - First-class Human Gates

## Objective

Create a deliberately small queue of questions or decisions that truly require human attention.

Do not represent these as arbitrary labels.

## Proposed Model

```text
Gate
  id
  work_item_id
  type
  question
  requested_by_client
  requested_by_session
  created_at
  resolved_at
  resolution
```

Start with:

```text
type = human
```

## Typical Uses

- architectural decision required,
- ambiguous product behavior,
- destructive action requires approval,
- credentials unavailable,
- conflicting requirements,
- external information needed,
- policy decision required.

## Human Interface

Provide a dedicated:

```text
Needs Attention
```

view.

This should be the primary place where humans interact with the large underlying agent work graph.

## Future Gate Types

Potential later extensions:

```text
human
timer
ci
external-event
other-work-item
```

Do not implement these until human gates prove useful.

## Acceptance Criteria

- Agents can explicitly request human input.
- Gated work leaves the ready queue.
- Humans can resolve the gate with a durable answer.
- The answer becomes part of the work history and recall context.
- Human attention requests are clearly distinguishable from ordinary agent-generated work.

---

# Phase 8 - Hierarchical Human Presentation

## Objective

Prevent Mnemonic itself from developing the same noise problem that motivated moving away from GitHub Issues.

## Example

Instead of showing:

```text
WORK-100 Refactor transaction import
WORK-101 Add missing boundary test
WORK-102 Handle malformed date
WORK-103 Split importer interface
WORK-104 Investigate duplicate imports
```

show:

```text
WORK-100 Refactor transaction import
         4 descendants
         1 blocked
         2 completed
```

The underlying graph remains fully visible to agents.

Humans get progressive disclosure.

## UI Capabilities

- Collapse descendants by default.
- Show counts of:
  - children,
  - blocked descendants,
  - active descendants,
  - completed descendants,
  - human gates.
- Allow drilling into a subtree.
- Distinguish `discovered-from` work from planned child decomposition.

## Acceptance Criteria

- Agent-generated sub-work does not automatically clutter the top-level dashboard.
- A human can expand any workstream to inspect underlying detail.
- Agents continue to receive the complete graph.

---

# Phase 9 - Structural Duplicate Handling

## Objective

Handle inevitable duplicate work safely and explicitly.

## Proposed Relationship

```text
duplicate_of
```

## Proposed Operation

```text
merge_work(source_id, destination_id)
```

A merge should be non-destructive.

Possible behavior:

- mark source as duplicate,
- establish canonical destination,
- preserve original IDs,
- preserve historical checkpoints/events,
- optionally migrate or redirect relationships,
- make normal recall identify the canonical work item.

## Duplicate Suggestions

During work creation, semantic search may suggest:

```text
Possible existing work
```

but should never automatically merge or suppress creation.

Embeddings should produce candidates, not truth.

## Acceptance Criteria

- Duplicate history is never deleted.
- Canonical work identity is explicit.
- Existing references remain understandable.
- Semantic similarity never silently changes work structure.

---

# Phase 10 - Repository Freshness Verification

## Objective

Make checkpoint provenance actionable.

Mnemonic already records repository state such as:

```text
repository_branch
verified_against
```

The next step is to help the client determine whether relevant source code has changed since the checkpoint was created.

## Proposed Checkpoint Metadata

```text
affected_paths
```

Examples:

```text
app/services/envelopes/**
tests/test_envelopes.py
migrations/**
```

## Client-side Recall Check

The MCP client skill can compare:

```text
git diff --name-only <verified_against>..HEAD -- <affected_paths>
```

and return information such as:

```text
Checkpoint based on: a832bc1
Current HEAD:         d7be142
Affected files changed since checkpoint: YES

Changed:
  app/services/foo.py
  tests/test_foo.py
```

This retains Mnemonic's trust boundary: the server stores declared provenance, while the repository-aware client performs local verification.

## Future Extension

For high-value checkpoints, optionally store Git blob IDs for specific files.

## Acceptance Criteria

- Agents are explicitly warned when checkpoint assumptions may have gone stale.
- Freshness checking occurs without requiring the Mnemonic server to mount or trust the repository.
- Path metadata remains advisory and does not pretend to prove semantic correctness.

---

# Phase 11 - Structured Completion Evidence

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
resolve_human_input
list_human_attention
```

## Verification

```text
add_verification_result
list_verification_results
```

## Duplicate handling

```text
mark_duplicate
merge_work
```

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

1. Add TTL work leases.
2. Add atomic `claim_and_recall`.
3. Add blocker-aware `list_ready_work`.
4. Make every claim operation recheck blockers atomically before leasing work.
5. Add idempotent mutation keys.

At this point multiple agents can safely share a project.

## Milestone 3 - Durable Collaboration History

1. Add append-only work events.
2. Add project activity feed.
3. Add structural duplicate handling.
4. Improve timeline UI.

This creates a robust audit and recovery model.

## Milestone 4 - Human Oversight

1. Add human gates.
2. Add `Needs Attention` dashboard.
3. Add hierarchical/collapsed workstream UI.
4. Keep agent-generated descendants out of the default top-level human view.

This directly addresses the original GitHub Issues noise problem.

## Milestone 5 - Provenance and Verification

1. Add `affected_paths`.
2. Add repository freshness checks to the MCP client skill.
3. Add structured verification results.
4. Add artifact references.

This improves trust in resumed and completed work.

## Milestone 6 - Advanced Coordination

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
