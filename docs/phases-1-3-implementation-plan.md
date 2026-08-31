# Mnemonic Phases 1–3 Implementation Plan

**Status:** Proposed implementation plan

**Scope:** Roadmap Phase 1 (Work Items and Checkpoints), Phase 2 (Atomic Work Leases), and Phase 3 (Typed Work Relationships)

**Source of product intent:** `docs/roadmap.md`
**Planning constraint:** This document defines the work; it does not implement it.

## 1. Outcome

After these three phases, Mnemonic will no longer treat a hand-off prompt as the durable unit of work. A project will contain stable `WorkItem` records, and any number of agent sessions will be able to append immutable `Checkpoint` records to the same item. Agents will be able to claim open, unblocked work through an expiring server-arbitrated lease, and work items will participate in a typed, project-local graph.

The result should have these observable properties:

1. Ten sessions working on one objective still produce one human-visible work item.
2. Every session-authored context packet remains immutable and attributable to its actual source.
3. Two cooperative agents cannot both acquire the exclusive lease for the same work.
4. An abandoned lease expires without an operator repairing durable state.
5. Blockers, decomposition, discovery, duplication, and descriptive relationships are represented structurally rather than buried in prose.
6. Only a true unresolved `blocks` relationship changes readiness.
7. The dashboard presents durable work first and progressively reveals checkpoints and descendants.
8. Existing projects, prompts, comments, completion summaries, lifecycle state, IDs, and provenance survive migration.

These phases establish the substrate for later ready-work discovery, append-only events, human gates, hierarchy summaries, duplicate merging, freshness verification, and structured completion evidence. They must create clean extension points for those features without implementing them early.

## 2. Product mental model

Mnemonic is intended to become a coordination system for temporary agents, not an issue tracker whose users happen to be models.

The durable object is the objective. A session is temporary. A checkpoint is the exact context that one session leaves behind. A lease is temporary responsibility, not assignment. A relationship is a fact about how objectives connect, not another workflow status. Human presentation is deliberately smaller than the complete graph agents can inspect.

```mermaid
flowchart TD
    P[Project] --> W[WorkItem: durable objective]
    W --> C1[Checkpoint: immutable context]
    W --> C2[Checkpoint: immutable progress]
    W --> C3[Checkpoint: immutable completion summary]
    W -. optional and expiring .-> L[WorkLease]
    W --> R[Typed relationships]
    R --> B[blocks]
    R --> H[parent-child]
    R --> D[discovered-from]
    R --> U[duplicate-of]
    R --> X[related]
```

The governing invariants are:

- `WorkItem` identity and terminal lifecycle survive agent sessions.
- Checkpoint text and provenance are append-only. Corrections are new checkpoints.
- `open`, `done`, `wont-do`, and `promoted` are the only persisted lifecycle statuses.
- `ready`, `active`, and `blocked` are derived facts, never stored workflow statuses.
- PostgreSQL and the FastAPI service are the sole coordination authority.
- MCP remains a typed REST adapter with no database access or duplicate transaction logic.
- The server stores repository provenance claims but does not pretend to verify a repository it cannot see.
- Semantic retrieval proposes candidates; it does not establish identity, dependencies, or merges.
- Stored agent text remains untrusted historical context and never grants execution authority.
- Human views collapse machine detail; agent recall exposes the immediate graph facts needed to continue safely.

## 3. Current-state baseline and gaps

The existing application is a sound MVP but its `Handoff` row carries too many responsibilities.

| Current concern | Current implementation | Target after Phase 3 |
| --- | --- | --- |
| Durable identity | `Handoff.title`, `summary`, `status`, `version` | `WorkItem` |
| Session context | Mutable `Handoff.prompt` and provenance | Immutable `Checkpoint` rows |
| Progress | Append-only `HandoffComment` | Migrated checkpoint kinds; later events remain Phase 5 |
| Completion | Atomic work-summary comment plus `done` | Atomic completion checkpoint plus `WorkItem.status=done` |
| Retrieval | One hand-off search document plus comments | One work result aggregated over many checkpoints |
| Concurrency | Optimistic version and `SELECT FOR UPDATE` for edits | Same for work edits, plus TTL leases and graph locks |
| Ownership | None | One optional active lease per work item |
| Dependencies/hierarchy | None | Typed project-local edges |
| Human view | One top-level card per hand-off | One card per work item; collapsed descendants |
| Error contract | Mostly string `detail`; MCP infers conflict type | Stable sanitized application error codes |

Important existing behavior to preserve:

- exact prompt/comment bytes, including whitespace and Unicode;
- mandatory, opaque client and session provenance;
- project isolation and cross-project `404` behavior;
- soft-deletion and recoverability;
- optimistic work-item mutation conflicts;
- default open-only, pointer-only lexical search and optional hybrid semantic ranking;
- bounded input, strict unknown-field rejection, safe URL/commit/tag/JSON validation;
- the same-origin dashboard proxy and server-only API key;
- no automatic execution, external issue creation, or hosted model calls.

The current implementation is concentrated in `backend/src/mnemonic_api/main.py`, `models.py`, and `schemas.py`; the dashboard is concentrated in `frontend/components/dashboard.tsx`. These phases should introduce small route/service boundaries before adding coordination transactions, without adding a generic repository abstraction.

## 4. Decisions fixed by this plan

These decisions remove ambiguity before schema or API work begins.

### 4.1 Identity and compatibility

- Every existing `handoffs.id` becomes the corresponding `work_items.id`.
- `WorkItem.initial_checkpoint_id` identifies the initial checkpoint explicitly through a deferred composite foreign key. The initial migrated checkpoint also keeps the work-item UUID in its own table. The same UUID in two tables is valid and makes old copied hand-off IDs useful as work IDs.
- Reserve hand-off UUIDs first. Preserve comment UUIDs when collision-free; if a collision exists, deterministically remap that comment checkpoint and retain the original UUID in `legacy_record_id`. Enforce uniqueness on `(migration_origin, legacy_record_id)`; never silently discard an ID.
- New REST routes and MCP tools use work terminology. Legacy hand-off REST routes, MCP tools, resource URIs, and prompt names remain as compatibility projections for at least one release and throughout Phases 1–3.
- Compatibility operations are backed by the new canonical tables. There is no long-term dual-write system.
- A legacy update may change legitimate work fields, but it may not rewrite checkpoint prompt or provenance fields.
- Legacy flat hand-off fields are projected from the preserved initial checkpoint; every later checkpoint remains visible through the legacy comments timeline.

### 4.2 Work-item fields

`WorkItem` owns only durable mutable identity and lifecycle:

```text
id
project_id
title
summary
status
priority
initial_checkpoint_id
version
created_at
updated_at
deleted_at
```

- Status remains `open | done | wont-do | promoted`.
- Priority is a `SMALLINT` from `0` through `100`, defaults to `0`, and sorts higher values first when a later scheduler uses it. Phases 1–3 expose and edit it but do not turn ordinary search into a scheduler.
- Tags remain checkpoint metadata, matching the roadmap and existing hand-off provenance. Tag filters mean “at least one checkpoint on this work item has this tag.”
- Soft deletion applies to the work item and hides all of its checkpoints from ordinary reads.

Lifecycle transitions are explicit:

- creation defaults to `open` and may accept `wont-do` or `promoted` for compatibility, but never `done`;
- `update_work` may move `open` to `wont-do`/`promoted`, move either back to `open`, or explicitly reopen `done` to `open`; it cannot set `done`;
- `complete_work` is the only route to `done` and requires current `open` work, the expected version, and a completion checkpoint;
- reopening increments the work version and does not erase the older completion checkpoint; checkpoint history and current lifecycle may therefore differ truthfully.

### 4.3 Checkpoint fields and kinds

`Checkpoint` owns immutable session context:

```text
id
work_item_id
kind                       context | progress | completion
prompt
source_client
source_session_id
source_model
source_session_url
repository_branch
verified_against
tags
source_metadata
migration_origin           nullable legacy marker
legacy_record_id           nullable UUID
created_at
search_vector              generated, derived
```

- `context` is a complete hand-off or correction for a future session.
- `progress` preserves a useful intermediate finding or next step.
- `completion` is reserved for the atomic completion operation.
- New work requires an initial `context` checkpoint in the same transaction.
- There are no checkpoint update or delete routes.
- A PostgreSQL trigger rejects direct `UPDATE` and `DELETE` operations on checkpoint rows. Operators may deliberately remove that trigger only as part of an audited migration or recovery procedure.
- Appending a checkpoint changes `WorkItem.updated_at` but does not increment its version. Version protects mutable work fields; appenders should not conflict with one another.
- Checkpoints may be appended to terminal work to preserve later clarification or audit context. Appending one does not reopen the work.

### 4.4 Legacy provenance limitation

The current application allows prompt edits while preserving only the original hand-off session fields. Therefore, the latest stored legacy prompt cannot always be proven to have been authored entirely by the recorded originating session.

Migration must:

- preserve the prompt and recorded provenance exactly;
- label the initial checkpoint with `migration_origin=legacy-handoff-snapshot`;
- label migrated comments with `migration_origin=legacy-comment`;
- retain the old row ID in `legacy_record_id`;
- show a concise UI/API warning for migrated snapshots;
- never fabricate prior prompt revisions or edit provenance that the old schema did not record.

Agent-authored `source_metadata` must preserve a structurally equal JSONB value. PostgreSQL JSONB does not preserve source serialization bytes, whitespace, or key order. Migration markers belong in dedicated columns, not inside user metadata.

### 4.5 Existing comments

Existing comments become checkpoints directly under their work item:

- `comment` becomes checkpoint kind `progress`;
- `work-summary` becomes checkpoint kind `completion`;
- body, comment ID, session provenance, model, and timestamp are preserved;
- missing session URL/repository fields remain null rather than being inferred.

This produces one coherent append-only context history. Phase 5 can later emit work events that reference these checkpoints; it does not require a permanent legacy comment subsystem.

### 4.6 Recall is bounded

Normal work recall must not inject an unbounded history into an agent context.

`WorkContext` returns:

- the work item;
- the initial checkpoint;
- the current context checkpoint, which may be the same row as the initial checkpoint;
- up to five newest additional checkpoints, returned chronologically and excluding the initial/current checkpoint IDs;
- total checkpoint count and the count omitted after de-duplicating all materialized checkpoint IDs;
- derived readiness facts, including the single safe public lease projection or null;
- immediate relationships grouped by direction and counts, not a recursive graph.

`list_checkpoints` provides deterministic pagination over the full history. Clients that need older context must request it explicitly.

### 4.7 Lease behavior

- There is at most one lease row per work item.
- Lease duration is server-configured: default 15 minutes, allowed configuration range 60–3600 seconds. Clients submit no absolute expiry and cannot request an unlimited claim.
- Time comparisons use PostgreSQL time.
- A server-generated 256-bit URL-safe token controls renewal and release. It appears only in claim/renew receipts and lease-token request bodies, never search, ordinary recall, UI data, Mnemonic-controlled logs, errors, or URLs.
- A required client-generated `claim_request_id` makes a claim replay-safe for the life of that lease. This narrow recovery mechanism lands in Phase 2 even though general mutation idempotency is Phase 6.
- This is bounded active-lease recovery, not general idempotency: after release, row replacement, or cleanup, the server retains no durable claim-operation tombstone.
- An identical holder/session/request replay returns the existing active lease and token without extending expiry. A different request conflicts. An expired request ID must be replaced with a new one.
- Lease operations do not change `WorkItem.version` or `updated_at`.
- Terminal lifecycle changes require the matching token when an active lease exists. An unleased item can still be completed or retired by an authorized human/client.
- Checkpoint append remains allowed without a lease because checkpoints are durable observations, not ownership mutations. If a caller supplies a token, an invalid token is reported rather than ignored.

### 4.8 Relationship direction and hierarchy

All edges use `source --type--> target`:

| Type | Directional meaning | Affects readiness? |
| --- | --- | --- |
| `blocks` | source must complete before target is ready | Yes |
| `parent-child` | source is the parent; target is the child | No |
| `discovered-from` | source was discovered while working on target | No |
| `duplicate-of` | source is a duplicate of canonical target | No, until Phase 9 |
| `related` | directionless description; endpoints are normalized | No |

- Parent-child is a strict project-local forest for the first implementation: each child has at most one parent and cycles are rejected. This gives the dashboard deterministic roots and collapse behavior.
- `blocks` may form a DAG and may have multiple incoming/outgoing edges, but cycles are rejected.
- For `related`, UUID endpoints are sorted before storage so reverse duplicates cannot exist.
- Every `discovered-from` edge requires a context checkpoint belonging to the originating target work item. Other relationship types may attach endpoint context optionally.
- Only status `done` resolves a blocker. `wont-do` means the prerequisite will not be done, and `promoted` means Mnemonic no longer owns it; neither silently proves the dependent can proceed. Remove or replace the edge explicitly.
- A new blocker may be added to actively leased work. The lease is not revoked; recall shows both facts, new claims are rejected, and the holder should checkpoint and release.
- Soft deletion is rejected while any relationship references the work item. This prevents an invisible blocker or broken hierarchy.

### 4.9 Phase boundary for ready-work discovery

Phases 2 and 3 implement one shared readiness predicate and expose `is_ready`, but they do not add scheduler ordering or `list_ready_work`; that remains Phase 4.

The predicate after Phase 3 is:

```text
is_ready =
  work_item.status == open
  AND work_item.deleted_at IS NULL
  AND no unexpired lease exists
  AND no incoming blocks edge has a source whose status is not done
```

`claim_work` and `claim_and_recall` use this same predicate. Ordinary search remains retrieval and must not be described as a ready queue.

## 5. Target persistence model

```mermaid
erDiagram
    PROJECT ||--o{ WORK_ITEM : contains
    WORK_ITEM ||--|{ CHECKPOINT : records
    WORK_ITEM ||--o| WORK_LEASE : may_have
    WORK_ITEM ||--o{ WORK_RELATIONSHIP : source
    WORK_ITEM ||--o{ WORK_RELATIONSHIP : target
    CHECKPOINT ||--o{ WORK_RELATIONSHIP : contextualizes
    WORK_ITEM ||--o| WORK_ITEM_EMBEDDING : derives
```

### 5.1 `work_items`

Constraints and indexes:

- primary key on `id`;
- foreign key `project_id -> projects.id ON DELETE RESTRICT`;
- unique `(project_id, id)` to support project-safe composite relationship foreign keys;
- non-null `initial_checkpoint_id`, linked after both tables exist by a `DEFERRABLE INITIALLY DEFERRED` composite foreign key `(id, initial_checkpoint_id) -> checkpoints(work_item_id, id)`;
- creation/backfill must insert the work item and its initial checkpoint in one transaction before that deferred constraint is checked;
- nonblank title/summary and existing length bounds;
- status check for the four persistent states;
- priority check `0 <= priority <= 100`;
- version check `version >= 1`;
- generated weighted search vector over title (`A`) and summary (`B`);
- GIN index on that search vector;
- partial index `(project_id, status, updated_at DESC, id DESC)` where `deleted_at IS NULL`;
- optional priority index only when Phase 4 proves its query shape. Do not add unused scheduler indexes now.

### 5.2 `checkpoints`

Constraints and indexes:

- primary key on `id`;
- foreign key `work_item_id -> work_items.id ON DELETE RESTRICT`; normal work deletion is soft, and audited physical purge must handle immutable history explicitly;
- unique `(work_item_id, id)` for the deferred initial-checkpoint and relationship-context composite foreign keys;
- exact existing validation bounds for prompt, client, session, model, URL, branch, commit, tags, and metadata;
- kind check for `context | progress | completion`;
- migration marker check, nullable legacy ID, and unique `(migration_origin, legacy_record_id)` for migrated rows;
- generated search vector over prompt;
- index `(work_item_id, created_at, id)` for stable history;
- reverse index `(work_item_id, created_at DESC, id DESC)` only if query plans show the first index cannot serve latest lookup adequately;
- GIN indexes for prompt search and tags;
- B-tree indexes for `source_client` and `source_session_id` only if `EXPLAIN` shows the existing project-scoped filter path needs them;
- immutability trigger for update/delete.

### 5.3 `work_item_embeddings`

This remains disposable derived state:

```text
work_item_id PK/FK
model
digest
vector
updated_at
```

The Phase 1 embedding text is a bounded composition of:

- work title and summary;
- the first 1,500 characters of the checkpoint named by `initial_checkpoint_id`;
- the most recent 1,500 characters across later checkpoints.

Increment the embedding configuration identifier. Do not migrate old vectors; drop them and lazily rebuild. A checkpoint append or work identity edit changes the digest.

### 5.4 `work_leases`

```text
work_item_id PK/FK
holder_client
holder_session_id
claim_request_id
lease_token
acquired_at
renewed_at
expires_at
```

Constraints and indexes:

- one row per work item through the primary key;
- nonblank bounded holder and request values;
- `acquired_at <= renewed_at AND renewed_at < expires_at`;
- index on `expires_at` for diagnostics/optional cleanup;
- no partial “active” unique index using `now()`, because time-dependent index predicates are not the right correctness primitive.

Expired rows may remain. Correctness comes from expiry-aware claim/update SQL, not a cleanup worker.

### 5.5 `work_relationships`

```text
id
project_id
relationship_type
source_work_item_id
target_work_item_id
context_checkpoint_work_item_id  nullable
context_checkpoint_id            nullable
created_by_client
created_by_session_id
created_by_model            nullable
created_at
```

Constraints and indexes:

- primary key `id`;
- both endpoints use composite foreign keys `(project_id, work_item_id)` to `work_items(project_id, id)`;
- no self-edge;
- check for the five relationship types;
- unique normalized identity `(project_id, relationship_type, source_work_item_id, target_work_item_id)`;
- partial unique index on `target_work_item_id` for `parent-child`, enforcing one parent;
- indexes for `(project_id, source_work_item_id, relationship_type)` and `(project_id, target_work_item_id, relationship_type)`;
- a both-null-or-both-present check for the two context columns;
- composite foreign key `(context_checkpoint_work_item_id, context_checkpoint_id) -> checkpoints(work_item_id, id)`;
- a row check that context work belongs to either endpoint, and for `discovered-from` equals the target; require both context columns for `discovered-from`;
- a database check `source_work_item_id < target_work_item_id` for `related`, matching service normalization and preventing reverse duplicates through direct SQL;
- no in-place update: replace an incorrect relationship through remove plus add.

### 5.6 Derived readiness projection

Every work summary/context response uses one server helper to compute:

```text
lifecycle_status
is_terminal
has_active_lease
active_lease             safe projection, nullable
unresolved_blocker_count
is_blocked
is_ready
display_state
```

`display_state` is convenience only:

1. a non-open lifecycle status returns that status;
2. unresolved blockers return `blocked`;
3. an active lease returns `active`;
4. otherwise return `ready`.

The independent booleans remain authoritative because active and blocked can overlap.

## 6. Service and transaction architecture

Before adding leases, move multi-step behavior out of route functions into explicit service functions that accept one SQLAlchemy `Session` and commit exactly once at the outer boundary.

Suggested backend layout:

```text
backend/src/mnemonic_api/
  main.py                         application factory and router assembly
  errors.py                       typed application errors
  models.py                       canonical ORM models
  schemas.py                      wire models and validators
  semantic.py                     work-level retrieval/embedding
  routes/
    projects.py
    work_items.py
    leases.py
    relationships.py
  services/
    work_context.py
    work_items.py
    leases.py
    relationships.py
```

This is a focused extraction, not a repository-pattern rewrite. SQLAlchemy queries remain visible in the services that own the invariants.

Transaction rules:

- `create_work`: insert work item and initial checkpoint; commit once.
- `add_checkpoint`: validate visible work, insert checkpoint, update activity; commit once.
- `complete_work`: lock work, validate version/blockers/lease, insert completion checkpoint, set `done`, increment version, remove matching lease; commit once.
- `recall_work` and `claim_and_recall`: assemble counts, initial/current/recent checkpoints, readiness, lease projection, and immediate relationships in one SQL statement using lateral/aggregate subqueries. Under the existing `READ COMMITTED` isolation, merely issuing several statements in one transaction would not guarantee one snapshot.
- `claim_and_recall`: acquire/resume claim and execute that single context statement in the same transaction; commit once.
- `add_relationship`: serialize project graph mutation, lock endpoints, validate/cycle-check, insert; commit once.
- Reusable helpers never call `commit()`.
- The current semantic cache helper, which commits while serving a search, must not be invoked from `claim_and_recall`. Prefer a separate cache-write session or an explicit search-only transaction boundary.

Lock order must be documented and tested:

1. `Project` row when serializing a graph mutation;
2. involved `WorkItem` rows in ascending UUID order;
3. current `WorkLease` row;
4. relationship insert/delete.

Work-row locks prevent duplicate lease acquisition and serialize an incoming-edge mutation against eligibility at the acquisition instant. Readiness may legitimately change afterward—for example, a completed blocker can reopen—so active-plus-blocked remains an expected state. Project-row locking serializes concurrent cycle checks within one project.

## 7. Error contract

Phase 1 introduces a stable sanitized application error envelope before Phase 2 adds several kinds of `409`:

```json
{
  "detail": {
    "code": "lease_held",
    "message": "This work item has an active lease.",
    "context": {
      "holder_client": "claude-code",
      "expires_at": "2026-08-31T18:15:00Z"
    }
  }
}
```

Validation errors may retain FastAPI's structured list. Application errors use stable codes such as:

```text
slug_conflict
version_conflict
work_not_open
work_blocked
lease_held
lease_expired
lease_token_mismatch
claim_request_expired
relationship_cycle
relationship_exists
parent_already_set
active_relationships
```

Rules:

- Error context uses an allowlist and never includes prompts, metadata, credentials, or lease tokens.
- `mcp/src/mnemonic_mcp/api.py` maps error codes, not HTTP method/path guesses.
- `frontend/lib/api.ts` parses allowlisted `{code, message, context}` objects as well as legacy strings and FastAPI validation lists; it never renders arbitrary context values.
- Replace or expand the MCP adapter's current hand-maintained validation-field allowlist for every new work/checkpoint/lease/relationship field.
- The adapter remains compatible with legacy string errors during the cutover.
- Logs contain code, route, project/work IDs, and exception type where useful, but not request bodies or tokens.

## 8. Public contract after each phase

### 8.1 Canonical REST operations

Phase 1:

```text
POST   /api/v1/projects/{project_id}/work-items
GET    /api/v1/projects/{project_id}/work-items
GET    /api/v1/projects/{project_id}/work-items/{work_item_id}
PATCH  /api/v1/projects/{project_id}/work-items/{work_item_id}
POST   /api/v1/projects/{project_id}/work-items/{work_item_id}/delete
GET    /api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints
POST   /api/v1/projects/{project_id}/work-items/{work_item_id}/checkpoints
GET    /api/v1/projects/{project_id}/work-items/{work_item_id}/context
POST   /api/v1/projects/{project_id}/work-items/{work_item_id}/complete
```

Phase 2 adds:

```text
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/claim
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/renew-claim
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/release-claim
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/claim-and-recall
```

Phase 3 adds:

```text
POST   /api/v1/projects/{project_id}/relationships
GET    /api/v1/projects/{project_id}/work-items/{work_item_id}/relationships
GET    /api/v1/projects/{project_id}/relationships/{relationship_id}
DELETE /api/v1/projects/{project_id}/relationships/{relationship_id}
GET    /api/v1/projects/{project_id}/work-items/{work_item_id}/children
```

The Phase 3 `create_work` request also accepts a bounded optional `initial_relationships` list so the new work item and its discovery/decomposition links can be committed atomically.

Mutation-body rules:

- work PATCH carries `expected_version`, edited work fields, and optional `lease_token` when making a terminal transition;
- completion carries `expected_version`, completion checkpoint provenance/content, and optional `lease_token`;
- the canonical delete action carries `expected_version` and optional `lease_token`;
- claim, renew, and release carry tokens/request IDs in JSON bodies, never paths or query strings;
- generic relationship creation is project-level and carries source and target explicitly; adjacency listing remains nested under a work item.

Query parameters and defaults are part of the strict contract:

| Route | Supported query keys |
| --- | --- |
| work list/search | `q?`, `semantic=false`, `status=open`, `tag?`, `source_client?`, `source_session_id?`, `view=all|roots` (default `all`), `limit=30` (max 100), `offset=0` |
| checkpoint list | `order=oldest|newest` (default `oldest`), `limit=100` (max 100), `offset=0` |
| context | `recent_limit=5` (max 20) |
| relationship list | `direction=incoming|outgoing|undirected|both` (default `both`), `type?`, `limit=50` (max 100), `offset=0` |
| children | inherited `status`, `tag?`, `source_client?`, and `source_session_id?` hierarchy filters; `limit=50` (max 100), `offset=0` |

`status` accepts `open|done|wont-do|promoted|all`. A nonblank `q` (and therefore `semantic=true`) requires `view=all`; reject free-text search combined with `view=roots` rather than inventing a second hierarchy-search pagination model.

Canonical source/tag filters match any checkpoint. Deprecated hand-off source/tag filters match the initial checkpoint, preserving the current contract. Lexical search `total` counts matching work items. Hybrid `semantic=true` preserves today's behavior: `total` counts all lifecycle/metadata-qualified candidate work items before relevance pagination.

Dashboard proxy allowlisting is intentionally narrower than the REST API:

| Surface | Browser proxy policy |
| --- | --- |
| work/checkpoint reads, search, context, children | Allow exact GET routes/keys |
| work edit, unleased completion/delete, checkpoint append | Allow exact mutation routes, with request schemas that reject `lease_token` |
| human relationship add/list/remove | Allow exact routes |
| claim, claim-and-recall, renew-claim, release-claim | Deny; these receipts contain or accept lease capabilities |

The proxy must reject—not strip—`lease_token` in every browser-enabled canonical or legacy body before forwarding. Add negative proxy tests for all four lease paths and for token fields on work PATCH, completion, delete, and compatibility mutations. An active lease therefore makes dashboard completion/deletion return a safe conflict; the browser never receives or forwards its token.

### 8.2 Core response shapes

All UUIDs serialize as strings and all timestamps as ISO 8601 UTC with a `Z` suffix. Unknown response additions are tolerated by MCP models only where explicitly intended; request models continue rejecting unknown fields.

| Model | Fields |
| --- | --- |
| `WorkItemRead` | `id`, `project_id`, `title`, `summary`, `status`, `priority`, `initial_checkpoint_id`, `version`, `created_at`, `updated_at` |
| `CheckpointRead` | `id`, `work_item_id`, `kind`, exact `prompt`, all source/repository provenance fields, `tags`, `source_metadata`, migration marker/legacy ID, `created_at` |
| `CheckpointPointer` | checkpoint ID/kind, source client/session/model, repository branch/commit, at most 20 checkpoint tags, migration marker, and creation time; never prompt or source metadata |
| `LeasePublic` | holder client/session, acquired/renewed/expiry timestamps; never request ID or token |
| `WorkIdentityPointer` | work item ID, title, and lifecycle status only |
| `Readiness` | lifecycle status, terminal/active/blocked/ready booleans, unresolved blocker count, display state, and optional `LeasePublic` |
| `RelationshipEdgeRead` | relationship ID/project/type, source/target IDs, optional context checkpoint composite, creator provenance, and creation time; neutral and used by project-level create/get |
| `WorkPointer` | work item ID, title, lifecycle status, and `Readiness`; never checkpoint prompt/metadata |
| `AdjacentRelationshipRead` | `relationship: RelationshipEdgeRead`, `relative_to_work_item_id`, endpoint-relative `direction`, and compact `counterpart: WorkPointer` |

`WorkSummary` is pointer-only:

```text
work_item                   WorkItemRead
checkpoint_count
ancestor_path               WorkIdentityPointer[], root-to-parent; Phase 3 search only
ancestor_path_truncated     boolean
current_context             CheckpointPointer (newest context checkpoint)
readiness                   Readiness
```

Card tags and compact provenance come only from `current_context`; no unbounded union is formed. A search may have matched a different checkpoint, just as current search may match comment text without rewriting card provenance.

`ancestor_path` is empty for ordinary browse/root/child results. In Phase 3 a free-text search hit carries the single-parent path from root to the hit's parent, capped at 50 entries with `ancestor_path_truncated=true` if defensive truncation occurs.

`HierarchySummary` is the item type for `view=roots` and child pages:

```text
summary                     WorkSummary
self_matches_filter         boolean
has_matching_descendants    boolean
```

Both booleans are computed against the same status/tag/source filters as the page. `has_matching_descendants` means any depth, not only a direct child. It is navigation metadata, not the aggregate descendant counts deferred to Phase 8.

`WorkCreation`:

```text
work_item                   WorkItemRead
initial_checkpoint          CheckpointRead
initial_relationships       RelationshipEdgeRead[], empty until Phase 3
```

`WorkContext`:

```text
work_item                   WorkItemRead
initial_checkpoint          CheckpointRead
current_context             CheckpointRead (may equal initial_checkpoint)
recent_checkpoints          CheckpointRead[], chronological, excluding initial/current IDs
checkpoint_total
omitted_checkpoint_count    total minus distinct materialized checkpoint IDs
readiness                   Readiness
incoming_relationships      AdjacentRelationshipRead[]
outgoing_relationships      AdjacentRelationshipRead[]
undirected_relationships    AdjacentRelationshipRead[]
relationship_counts
```

`ClaimReceipt`:

```text
work_item_id
holder_client
holder_session_id
claim_request_id
acquired_at
renewed_at
expires_at
lease_token
```

`ClaimAndRecall`:

```text
lease                       ClaimReceipt
context                     same bounded WorkContext shape
```

`CompletionResult` contains `work_item: WorkItemRead` and `checkpoint: CheckpointRead`. `DeletionResult` contains `deleted=true`, project/work IDs, and the final work version. Pages retain `items`, `total`, `limit`, and `offset`; root and child pages contain `HierarchySummary`, while flat browse/search pages contain `WorkSummary`.

`renew_claim` returns the updated `ClaimReceipt`, including the same token/request ID and the new renewal/expiry timestamps. `release_claim` returns `ReleaseResult`:

```text
work_item_id
released                     true only when this call deleted the retained row
```

A matching retained row—active or expired—is deleted and returns `released=true`. An absent row returns `released=false` as an idempotent success. A different active replacement remains a token mismatch; a different expired row is not deleted and returns `released=false`.

`add_relationship` returns `RelationshipCreationResult`:

```text
relationship                RelationshipEdgeRead
created                     boolean
```

`remove_relationship` returns `RelationshipRemovalResult`:

```text
project_id
relationship_id
removed                     boolean
```

For `related`, the stored endpoints are UUID-normalized but adjacency projection uses `direction=undirected` for either endpoint. It is returned once for `undirected` or `both`, and excluded from strictly `incoming` or `outgoing` filters.

### 8.3 Canonical MCP surface

Phase 1 canonical tools:

```text
create_work
search_work
get_work
add_checkpoint
list_checkpoints
recall_work
update_work
complete_work
delete_work
```

Phase 2 adds:

```text
claim_work
claim_and_recall
renew_claim
release_claim
```

Phase 3 adds:

```text
add_relationship
get_relationship
list_relationships
remove_relationship
```

Also add:

- resource `mnemonic://projects/{project_id}/work-items/{work_item_id}`;
- prompt `resume_work`.

`resume_work` is read-only context loading. Authorized execution uses `claim_and_recall`; neither operation itself grants permission to carry out the stored prompt.

### 8.4 Legacy compatibility mapping

| Legacy operation | Compatibility behavior |
| --- | --- |
| `save_handoff` | Atomically create work plus initial context checkpoint |
| `search_handoffs` | Return a legacy projection of unique work results |
| `recall_handoff` | Treat `handoff_id` as preserved work ID and flatten work plus the initial checkpoint |
| `list_handoff_comments` | Project every post-initial checkpoint; `context`/`progress` become legacy `comment`, `completion` becomes `work-summary` |
| `add_handoff_comment` | Add a progress checkpoint |
| `complete_handoff` | Add completion checkpoint and finish the work; legacy REST and MCP body schemas add optional `lease_token` |
| `update_handoff` | Permit work title/summary/status updates and reject prompt/provenance/tag rewrites; legacy REST and MCP body schemas add optional `lease_token` for terminal transitions |
| `delete_handoff` | MCP alias calls canonical delete and accepts an optional token; direct legacy REST DELETE remains query-versioned and works only while unleased |
| old resource/prompt | Resolve to the new context and include deprecation metadata |

The flat legacy object is deterministic: ID/project/title/summary/status/version/timestamps come from `WorkItem`; prompt, source provenance, repository provenance, tags, and source metadata come from the initial checkpoint. Later corrective context is not silently substituted for the original provenance—it is exposed through the legacy comments timeline. This preserves what old fields meant while canonical `recall_work` supplies the current context directly.

Compatibility mutations follow the canonical lifecycle matrix above; they do not preserve repeated completion or direct `done` updates as accidental legacy behavior.

Compatibility aliases must be marked deprecated in descriptions and docs. Do not remove them until a later versioned contract change, after shipped skills and copied pointers have had a migration window.

## 9. Phase 1 — Separate Work Items from Hand-offs

### 9.1 Phase objective

Make `WorkItem` the only top-level durable work identity and `Checkpoint` the immutable, session-attributed context unit. All current product surfaces must switch to that model before leases land.

### 9.2 Phase 1A — Contract characterization and service seams

1. Freeze current behavior with tests for exact prompt/comment preservation, lifecycle, project isolation, search totals, soft deletion, completion atomicity, and old pointer resolution.
2. Add typed application errors and update MCP error parsing while keeping old string-error support.
3. Extract routers/services from `main.py` far enough that create, checkpoint append, completion, context assembly, lease, and relationship operations can each own one transaction.
4. Define Pydantic models for `WorkItem`, `Checkpoint`, creation, patch, checkpoint page, context, readiness, and safe lease/relationship projections.
5. Preserve existing validation helpers rather than reimplementing text, URL, commit, tag, and JSON rules.

Exit check: no user-visible behavior has changed, and the new service boundaries pass the existing suite.

### 9.3 Phase 1B — Expand and backfill schema

Use separate Alembic revisions, but do not deploy the backfill while legacy writers remain active. `0004` may ship ahead as an unused expansion. `0005` runs only inside the quiesced cutover that also activates the canonical API/MCP/dashboard; otherwise writes between backfill and switch would be lost.

Because the API startup command upgrades to Alembic `head`, an expansion-only production image must have a revision graph ending at `0004`. Do not place `0005` in a deployed image before the quiesced cutover; if maintaining that artifact boundary is impractical, do not deploy the expansion ahead.

1. `0004_work_graph_expand`
   - create `work_items`, `checkpoints`, and `work_item_embeddings`;
   - add constraints, generated vectors, indexes, and migration marker fields;
   - leave legacy tables intact.
2. `0005_work_graph_backfill`
   - preflight for malformed legacy state and reserve hand-off UUIDs;
   - preserve collision-free comment IDs; deterministically remap collisions and record their original IDs;
   - copy every hand-off, including soft-deleted records, into one work item;
   - copy the current prompt/provenance into its initial checkpoint;
   - copy every comment/work-summary into a later checkpoint;
   - default priority to `0`;
   - preserve timestamps/status/version/deletion state exactly;
   - validate counts and referential parity inside the migration;
   - populate/validate each deferred `initial_checkpoint_id`;
   - install the checkpoint immutability trigger only after backfill;
   - make legacy tables read-only only after the canonical stack owns writes.

Required parity assertions:

```text
work_item_count == legacy_handoff_count
checkpoint_count == legacy_handoff_count + legacy_comment_count
one initial checkpoint exists for every work item
all legacy IDs resolve according to the compatibility rule
all exact text/provenance/timestamps and structurally equal JSONB metadata match
all soft-deleted rows remain hidden through canonical reads
no orphan checkpoint exists
```

Do not migrate `handoff_embeddings`; they are disposable and semantically stale.

### 9.4 Phase 1C — Canonical work and checkpoint API

Implement the canonical routes and services:

- `create_work` requires a nested initial checkpoint and commits both records atomically.
- `search_work` returns one work item even when several checkpoints match.
- `get_work` returns identity/lifecycle only.
- `list_checkpoints` is oldest-first by `(created_at, id)`, paginated, and stable.
- `add_checkpoint` inserts exact prompt/provenance, updates activity, and never increments version.
- `recall_work` builds the bounded shared context envelope.
- `update_work` uses the existing `expected_version`/row-lock pattern for title, summary, priority, and permitted lifecycle transitions.
- `complete_work` requires a nonblank completion checkpoint and current version, writes both lifecycle and checkpoint atomically, and forbids a bare `done` patch.
- the `delete_work` POST action remains soft deletion and version-protected, with an optional lease token in its JSON body.

Rules to test explicitly:

- two checkpoint appenders can both succeed;
- a checkpoint append and work edit do not overwrite each other;
- a stale work edit still returns `version_conflict`;
- raw SQL checkpoint update/delete is rejected by the database;
- cross-project work and checkpoint IDs return `404`;
- checkpoint input cannot claim kind `completion` outside the completion endpoint;
- terminal work can receive a clarification checkpoint without changing status;
- creation and every permitted lifecycle transition follow the matrix in Section 4.2;
- repeated completion and completion from non-`open` work return `work_not_open`;
- reopening preserves an older completion checkpoint without presenting it as the current context.

### 9.5 Phase 1D — Retrieval migration

Refactor lexical and semantic retrieval to aggregate by work ID:

1. Search title/summary through `work_items.search_vector`.
2. Search checkpoint prompt through an indexed correlated `EXISTS`/rank subquery.
3. Literal search covers work/checkpoint IDs, provenance, branch, commit, tags, and session IDs.
4. Canonical `tag`, `source_client`, and `source_session_id` mean any matching checkpoint; legacy aliases filter the initial checkpoint.
5. Count distinct work items, never joined checkpoint rows.
6. Keep pointer-only results; omit prompt and source metadata.
7. Retain lexical search as default and hybrid semantic search as explicit opt-in.
   Preserve total semantics: lexical total is the matching work count; hybrid total is the full lifecycle/metadata-qualified candidate count.
8. Re-key the semantic cache by work item and invalidate its digest on identity or checkpoint changes.
9. Verify deterministic ranking/tie-breaking and query plans with representative multi-checkpoint data.

The semantic helper must not introduce an unexpected commit into claim/context transactions. Search-specific cache persistence should be isolated.

### 9.6 Phase 1E — MCP, resources, prompts, and skills

1. Add canonical work/checkpoint Pydantic response models in `mcp/src/mnemonic_mcp/models.py`.
2. Add canonical tools, resource, and `resume_work` prompt in `server.py`.
3. Keep search pointer-only even if an upstream response accidentally adds full checkpoint content.
4. Implement legacy tool projections and clear deprecation guidance.
5. Update the server instructions to distinguish work identity, checkpoint history, recall, and execution authority.
6. Update the bundled skills without renaming their directories:
   - `mnemonic-save`: search work, create work plus an initial checkpoint, or append a corrective checkpoint; never rewrite old context;
   - `mnemonic-search`: search work items, explain lexical default plus optional hybrid retrieval, and never call search a ready queue;
   - `mnemonic-recall`: recall for viewing, list older checkpoints explicitly when needed, and preserve authority warnings.
7. Update copied pointers to use `work_item_id` and `recall_work`, while old pointers continue resolving.

### 9.7 Phase 1F — Work-item-first dashboard

Refactor the monolithic dashboard into focused components before adding hierarchy:

```text
frontend/components/work-item-list.tsx
frontend/components/work-item-card.tsx
frontend/components/work-item-detail.tsx
frontend/components/checkpoint-timeline.tsx
frontend/components/work-item-editor.tsx
frontend/lib/work-item-search.ts
frontend/lib/work-recall-pointer.ts
```

The dashboard should:

- retain project selection, paging, search, status filtering, responsive behavior, clipboard handling, and conflict reconciliation;
- render one card per work item;
- show title, summary, persistent status, priority, version, checkpoint count, last activity, and current-context checkpoint client/tags as provenance—not an assignee or an unbounded tag union;
- separate the mutable work overview from the immutable checkpoint timeline;
- remove prompt/provenance edit/delete controls;
- replace “Edit hand-off” with “Edit work item”;
- append dashboard-authored checkpoints using the existing truthful per-tab dashboard session ID;
- copy the current context checkpoint and the new recall pointer;
- label migrated initial snapshots accurately;
- paginate checkpoint history instead of loading it without bound;
- update layout metadata and all “hand-off library” language to durable work terminology.

Update `frontend/lib/proxy-policy.ts` only for the browser routes in Section 8.1's proxy matrix. Preserve Host/Origin enforcement, UUID path validation, the 1 MiB request-body cap, server-only credentials, and explicit denial of every lease-capability route.

### 9.8 Phase 1G — Cutover, audit, and contract migration

The cutover deployment quiesces writers, runs `0005`, and switches API, MCP, and dashboard to the new canonical tables as one operation. It then retains legacy tables read-only. Run the production-data parity audit and a backup/restore drill.

After an explicit observation window, a separate later deployment adds `0006_work_graph_contract` to remove `handoff_embeddings`, `handoff_comments`, and `handoffs` only after:

- the new stack has passed full validation;
- migrated record counts and representative exact values have been checked;
- a fresh dump has passed `pg_restore --list` and an isolated restore drill;
- operators accept that rollback after new writes requires restoring the pre-cutover backup.

Legacy API/MCP aliases remain backed by new tables after the old tables are removed.

Keep the legacy SQLAlchemy table/ORM metadata registered—but unused by canonical services—through this observation window so Alembic model-parity checks do not propose premature drops. Remove those definitions in the same release that applies `0006`; do not hide other parity differences with a broad Alembic exclusion.

### 9.9 Phase 1 acceptance mapping

| Roadmap criterion | Proof |
| --- | --- |
| Multiple sessions add checkpoints to one work item | API concurrency test plus MCP/full-stack scenario |
| Checkpoints cannot be edited | No routes, Pydantic contract test, raw PostgreSQL trigger test |
| Human project view shows one item for many checkpoints | Component/E2E test with three checkpoints |
| Existing data migrates without provenance loss | Populated `0003 -> head` migration test and restore drill |

Phase 1 is not complete while the dashboard or canonical MCP flow still treats a checkpoint as the top-level work identity.

## 10. Phase 2 — Atomic Work Leases

### 10.1 Phase objective

Allow cooperative agents to acquire temporary exclusive responsibility for one open work item, recover after crashes through expiry, and receive resume context in the same atomic operation.

### 10.2 Phase 2A — Lease schema and configuration

Add `0007_work_leases` with the table and constraints in Section 5.4. Add validated `MNEMONIC_LEASE_TTL_SECONDS`, default `900`, bounded to `60..3600`.

Token rules:

- generate with a cryptographically secure server RNG;
- compare using constant-time comparison after selecting/locking the row;
- retain the raw opaque value only because same-request recovery must return it; the database already holds all application secrets/content under the same single-user trust boundary;
- never render it in Mnemonic `repr`, application/access/structured logs, error context, normal models, resources, or the dashboard proxy; explicitly warn that third-party MCP clients may record tool receipts/arguments in their own traces;
- accept it only in JSON request bodies.

### 10.3 Phase 2B — Atomic claim algorithm

`claim_work` and `claim_and_recall` share one service operation:

1. Select the visible project-scoped work item `FOR UPDATE`.
2. Select any retained lease row `FOR UPDATE`; the work-item lock protects the absent-row case from a competing claimant.
3. Reject non-`open` work with `work_not_open`.
4. Evaluate `base_claimable = visible + open + no unresolved blocker`. Before Phase 3, blocker count is zero. Do not reject merely because a lease exists; identical replay is handled next.
5. Only after both possible lock waits, capture one PostgreSQL `clock_timestamp()` value and inspect the retained lease against it.
6. If no lease exists, insert a new token/request/expiry.
7. If the lease is active and holder/session/request all match, return the same receipt without extending it.
8. If the lease is active and the tuple differs, return `lease_held` with safe holder and expiry context.
9. If the retained row is expired and its tuple is identical, return `claim_request_expired`; require a new request ID.
10. If the retained row is expired and the request is new, replace it with a new claim.
11. For `claim_and_recall`, execute the single-statement bounded `WorkContext` query before the same commit.
12. Commit once and return the receipt/context.

An implementation may use `INSERT ... ON CONFLICT ... DO UPDATE ... RETURNING`, but it must use the captured post-lock database time and preserve the replay branches above. Work eligibility, replay behavior, and context remain inside one transaction. A preflight read followed by an unguarded insert is not sufficient.

### 10.4 Phase 2C — Renew and release

`renew_claim`:

- locks the current lease;
- requires a matching, unexpired token;
- captures `clock_timestamp()` after the lock, then sets `renewed_at=database_now` and `expires_at=database_now + configured_ttl`;
- retains the token and request ID;
- returns `lease_expired` or `lease_token_mismatch` precisely;
- returns the updated `ClaimReceipt`.

`release_claim`:

- deletes a retained row only when its token matches, whether the row is active or expired;
- returns `released=false` for an absent row or a different expired row;
- returns `lease_token_mismatch` if a different active replacement exists;
- never uses holder/session text as authority and returns the structured release result from Section 8.2.

No cleanup worker is required. An optional maintenance delete for old expired rows may be added later for table hygiene, not correctness.

### 10.5 Phase 2D — Interaction with other mutations

- `complete_work`, `wont-do`, `promoted`, and delete inspect the lease while holding the work row lock.
- If an active lease exists, the caller must provide its token.
- A successful terminal transition removes the lease in the same transaction.
- An expired lease is not ownership; a caller presenting its stale token gets `lease_expired`.
- Work identity edits may remain version-controlled without a token because a human may correct title/summary while an agent works. The response exposes both version and active lease facts so clients can reconcile.
- Checkpoint append remains open; it does not confer or steal ownership.
- Lease activity does not change durable work activity time or version.

### 10.6 Phase 2E — MCP and agent workflow

Add the four canonical tools with accurate annotations:

- `claim_work`: mutating, non-destructive, `idempotentHint=false`; an active identical request is replay-safe, but the same arguments can create a new lease after release/cleanup;
- `claim_and_recall`: mutating/context-returning with `idempotentHint=false` for the same bounded-replay reason;
- `renew_claim`: mutating and not idempotent because a successful retry recalculates expiry;
- `release_claim`: mutating with `idempotentHint=true` under the documented token rules.

Update `mnemonic-recall`:

1. Use `recall_work` when the user only wants to view/copy/summarize.
2. Use `claim_and_recall` before beginning already-authorized execution.
3. A successful claim does not add authorization beyond the user's request.
4. Retain the token only for the active session; never put it in checkpoint text or Mnemonic logs, and protect MCP tool traces that contain it.
5. Renew before expiry during long work.
6. Append a useful checkpoint and release when pausing or handing off unfinished work.
7. Complete with the matching lease token when the objective is genuinely done.
8. On a lease conflict, report holder/expiry and choose other work or wait; never work around the lease.
9. After an unknown claim/claim-and-recall outcome, retry promptly with the exact same `claim_request_id` while that lease row can still be retained; ordinary search/recall cannot recover the token.

### 10.7 Phase 2F — Dashboard visibility

The human UI is visibility-oriented:

- show `Ready` or `Active` derived state separately from persistent lifecycle;
- show safe holder client/session, acquired/renewed time, and expiry;
- call it a lease or active session, never an assignee;
- never send the token through the dashboard proxy;
- refresh at the displayed expiry boundary and retain manual refresh;
- do not add a force-release button in this phase. TTL is the recovery path.

### 10.8 Phase 2 tests

Backend/PostgreSQL tests:

- two simultaneous claim calls produce exactly one new lease;
- simultaneous calls from different holders or request IDs produce one acquisition and one typed conflict;
- simultaneous identical requests both succeed as one acquisition plus one replay and return the same token;
- same holder/session/request replay returns the original token without extending expiry;
- different request ID from the same session conflicts;
- expired leases can be replaced;
- wrong/missing/stale tokens cannot renew, release, complete, retire, or delete active work;
- repeat release cannot delete a replacement lease;
- terminal/deleted work cannot be claimed;
- claim context and lease commit atomically;
- lease operations leave work version/activity unchanged;
- token never appears in ordinary API models or Mnemonic-controlled captured logs;
- project isolation remains `404`.

Control expiry by updating `expires_at` in the isolated PostgreSQL schema or through an injected database-time seam. Do not make tests sleep.

MCP tests:

- exact tool schemas/annotations;
- body serialization and no token in URLs;
- typed conflict messages;
- claim-specific unknown-outcome guidance that requires the same request ID;
- generic unknown writes retain search/recall guidance;
- token redaction;
- Streamable HTTP and stdio catalogs.

Dashboard/E2E tests:

- ready to active presentation;
- automatic expiry refresh without a 60-second sleep: expire the isolated database row through the E2E harness and unit-test scheduling with fake timers;
- no token in browser-visible data;
- lifecycle and lease badges remain distinct.

### 10.9 Phase 2 acceptance mapping

| Roadmap criterion | Proof |
| --- | --- |
| Two agents cannot acquire the same lease | Barrier-based real PostgreSQL test |
| Expiry does not strand work | Direct-expiry takeover test |
| Holder can resume and renew | `claim_request_id` replay plus renew test |
| Displayed work state is consistently derived | Shared evaluator unit/integration tests and UI assertions |

## 11. Phase 3 — Typed Work Relationships

### 11.1 Phase objective

Turn the work collection into a project-local typed graph while keeping only true execution blockers authoritative for readiness.

### 11.2 Phase 3A — Relationship schema

Add `0008_work_relationships` using the schema and constraints in Section 5.5.

Database-level project locality is mandatory. API checks alone are insufficient because a malformed or racing write must not connect projects.

Validate relationship context as follows:

- both composite context fields must be present or absent together and reference a visible endpoint checkpoint;
- `discovered-from` requires context and it must belong to the target/originating work item;
- other edge types may omit context; when present, it belongs to either endpoint as supporting context;
- it does not make checkpoint content authoritative and is never followed as an instruction automatically.

### 11.3 Phase 3B — Concurrency-safe add/remove

For every add/remove:

1. Lock the project row `FOR UPDATE` to serialize graph mutation/cycle checks.
2. Lock both endpoint work rows in ascending UUID order.
3. Verify both are visible and in the supplied project.
4. Normalize `related` endpoints.
5. Validate self-edge, uniqueness, single-parent, and context-checkpoint rules.
6. For `blocks` or `parent-child`, run the cycle query.
7. Insert or remove and commit once.

Cycle test for proposed `source -> target`:

- recursively traverse existing edges of the same cycle-constrained type from `target` through their targets;
- reject if `source` is reachable;
- reject a direct self-edge before traversal.

The project lock is load-bearing. Without it, concurrent `A blocks B` and `B blocks A` transactions can both observe an acyclic graph and commit a cycle.

An identical `add_relationship` is a natural-key idempotent success and returns the existing edge in `RelationshipCreationResult` with `created=false`. It is not an invitation to infer semantically similar edges.

### 11.4 Phase 3C — Readiness and claim integration

Extend the shared evaluator so an unresolved blocker is an incoming `blocks` edge whose source work item is not `done`.

Apply it to:

- work summaries and context;
- dashboard badges;
- `claim_work` and `claim_and_recall` eligibility;
- the internal query helper Phase 4 will later use for `list_ready_work`.

Behavioral rules:

- adding a blocker to unleased work immediately makes `is_ready=false`;
- adding a blocker to leased work preserves the lease but sets `is_blocked=true`;
- completing the blocker or removing the edge restores readiness if no other blockers/lease exist;
- setting blocker work to `wont-do` or `promoted` does not resolve the edge;
- completing a blocked target is rejected with `work_blocked`; remove/resolve the dependency first;
- non-blocking relationship types never change readiness.

### 11.5 Phase 3D — REST and MCP relationship operations

`add_relationship` calls the project-level creation route with:

- `source_work_item_id` and `target_work_item_id`;
- exact `relationship_type`;
- truthful `created_by_client`, `created_by_session_id`, and optional `created_by_model`;
- `context_checkpoint_id`, required for `discovered-from` and optional for other types.

The service resolves the context checkpoint under the locked endpoint rows, derives and stores `context_checkpoint_work_item_id`, then relies on the composite foreign key and row checks in Section 5.5. Clients do not supply that redundant companion field.

`list_relationships` supports:

- `direction=incoming|outgoing|undirected|both`;
- optional type filter;
- pagination;
- compact counterpart work title/status/readiness, avoiding N+1 client calls.

`get_relationship` returns one neutral project-scoped `RelationshipEdgeRead`. Nested list/context operations return `AdjacentRelationshipRead` relative to the requested work item. `remove_relationship` removes by relationship ID and returns `removed=false` when already absent, but must not remove a newly created different edge.

Extend `WorkContext` with immediate incoming/outgoing/undirected edges and counts. Never recursively inject the whole graph.

### 11.6 Phase 3E — Atomic discovered/decomposed work creation

Extend canonical `create_work` with at most ten optional `initial_relationships`. Each entry is expressed relative to the new work item:

```text
type
direction                   incoming | outgoing
other_work_item_id
context_checkpoint_id       required for discovered-from; optional otherwise
```

Examples:

- new child under an existing parent: `parent-child`, `incoming`;
- new work discovered from existing work: `discovered-from`, `outgoing`;
- new work blocked by an existing prerequisite: `blocks`, `incoming`.

When `initial_relationships` is nonempty, `create_work` acquires the project graph lock before inserting, locks all existing counterpart work rows in UUID order, and uses the same no-commit relationship validator. Validate/insert the bounded entries in deterministic order so each later cycle check sees earlier staged edges. For initial creation, `discovered-from` must be `outgoing` from the new work and must identify a checkpoint on the existing originating target; use generic relationship creation for other discovery directions after the work exists.

The relationship creator provenance is copied from the new initial checkpoint's caller-supplied source client/session/model fields. It is never inferred from the transport connection.

Creation of the work item, initial checkpoint, and all requested edges succeeds or fails as one transaction. This prevents discovered work from being durably created without the context link required by the roadmap.

### 11.7 Phase 3F — Hierarchical dashboard

For ordinary browse, the dashboard requests `view=roots` and pages structural roots—items with no incoming `parent-child` edge. It fetches children lazily and renders them under collapsible parents. Root counts never include descendants as additional top-level cards, and expanding children never changes the current root page.

Status/tag/source filtering in root view is subtree-aware: a structural root qualifies when it or any descendant matches. Nonmatching ancestors are returned as clearly muted navigation scaffolding, matching ancestor chains are expanded, and `total` counts qualifying structural roots. Thus an open child remains visible beneath a `done`, `wont-do`, or `promoted` parent.

Root and child pages return the `HierarchySummary` flags from Section 8.2. The dashboard passes the same filters to each child request, renders only qualifying direct branches, and follows `has_matching_descendants` to expand the matching path without walking unrelated subtrees. Child-page `total` counts qualifying direct child branches. The renderer's visited/depth cap must offer a flat-search fallback rather than silently hide a deeper match.

Free-text search is different: when `q` is nonblank the dashboard uses `view=all`, may return a matching descendant directly, and renders the response's bounded `ancestor_path` as a breadcrumb. Agent search continues to cover the complete graph.

The detail view groups edges in human language:

- “Blocked by” and “Blocks”;
- “Parent” and “Children”;
- “Discovered from” and “Discovered work”;
- “Duplicate of”;
- “Related”.

Add a project-scoped relationship editor that:

- searches/selects another work item rather than asking for a raw UUID alone;
- previews the edge direction in a sentence before saving;
- requires an originating checkpoint for `discovered-from` and allows optional endpoint context for other types;
- explains cycle/single-parent conflicts inline;
- does not imply that `related`, `duplicate-of`, or `parent-child` affects readiness.

Even with database cycle protection, the renderer keeps a visited-ID set and bounded expansion depth as defensive handling for restored/corrupt data.

### 11.8 Phase 3 tests

Schema/API tests:

- every relationship type round-trips with exact direction/provenance;
- direct self-edges fail;
- cross-project endpoints and context checkpoints fail at API and database layers;
- duplicate edges return the existing row;
- reverse `related` edges normalize to one row;
- a child cannot acquire a second parent;
- direct and transitive block cycles fail;
- direct and transitive parent cycles fail;
- simultaneous reciprocal blocker inserts yield one success and one `relationship_cycle`;
- missing context on `discovered-from` fails, while valid endpoint context round-trips through both API and database constraints;
- context from a non-endpoint checkpoint fails even under direct SQL;
- removal restores claim eligibility where appropriate;
- only `done` resolves a blocker;
- non-blocking edges do not change `is_ready`;
- leased-then-blocked work exposes both facts and cannot be newly claimed;
- deleting work with relationships returns `active_relationships`;
- relationship-aware context is bounded and directionally correct;
- atomic create-with-relationship leaves neither partial work nor partial edge on failure.

MCP tests:

- exact tools, schemas, annotations, paths, and response models;
- counterpart summaries remain pointer-only;
- typed cycle and cross-project errors;
- legacy hand-off aliases still resolve preserved work IDs.

Dashboard/E2E tests:

- roots are collapsed by default;
- child expansion does not alter root pagination;
- descendant search shows ancestry;
- descendant search returns the ordered ancestor path and exposes truncation rather than silently dropping ancestry;
- an open descendant remains visible beneath each terminal parent status through muted ancestor scaffolding;
- subtree-aware filters retain stable structural-root totals and pagination;
- block badges and active/block overlap are understandable;
- root/child `self_matches_filter` and `has_matching_descendants` flags agree with recursive filter results;
- relationship editor shows direction and conflict errors;
- keyboard/dialog behavior and narrow viewport remain usable.

### 11.9 Phase 3 acceptance mapping

| Roadmap criterion | Proof |
| --- | --- |
| Blocked work never appears ready | Shared readiness/claim tests |
| Dependency cycles cannot be created | Sequential and reciprocal-concurrency PostgreSQL tests |
| Discovered work retains origin context | Atomic creation plus required/context checkpoint test |
| Parent/child work can be collapsed | Root pagination and expansion E2E test |

## 12. Delivery sequence

Use reviewable releases rather than one large flag day.

| Increment | Contents | Depends on | Ship gate |
| --- | --- | --- | --- |
| 1 | Characterization tests, typed errors, route/service extraction | None | Existing suite unchanged |
| 2 | `0004` expand revision and characterization/migration-fixture groundwork; deployable artifact ends at `0004` | 1 | Expansion parity and old stack unchanged |
| 3 | Canonical work/checkpoint API/search plus populated `0005` migration tests/cutover code, held until all clients are ready | 2 | Exact migration parity and backend Phase 1 tests |
| 4 | MCP work tools, compatibility aliases, skills | 3 | MCP HTTP + stdio tests |
| 5 | Work-item-first dashboard and proxy routes | 3 | Unit, typecheck, build, E2E |
| 6 | Quiesced `0005` stack cutover and production-data audit | 3–5 | Parity plus backup/restore drill |
| 7 | Observation window, then separate `0006` legacy-table contract release | 6 | Explicit operator go/no-go |
| 8 | `0007` lease model, atomic services, API | 7 | Real concurrency suite |
| 9 | Lease MCP workflow and dashboard visibility | 8 | Full Phase 2 scenario |
| 10 | `0008` relationship schema, locks, cycle/readiness services | 9 | Graph/concurrency suite |
| 11 | Relationship MCP, atomic linked creation, hierarchical UI | 10 | Full Phase 3 scenario |
| 12 | Documentation, full-stack validation record, release cleanup | 11 | All gates green |

Do not begin Phase 2 on the mutable legacy hand-off model. Do not expose relationship writes until the concurrency-safe cycle check and project-local database constraints exist.

## 13. Migration, deployment, and rollback

### 13.1 Pre-deployment

1. Stop or quiesce API/MCP/dashboard writers for the Phase 1 structural cutover.
2. Create a fresh custom-format PostgreSQL backup.
3. Verify the archive is readable with `pg_restore --list`.
4. Record counts by project/status for hand-offs and comments.
5. Confirm available disk space for old and new tables during expand/backfill.
6. Rehearse the exact migration against a restored copy of representative data.

### 13.2 Cutover behavior

The API container runs `alembic upgrade head` synchronously before serving, so schema copy time is startup downtime. Treat this as a maintenance-window migration, not a rolling migration.

The optional pre-cutover expansion image therefore ends at `0004`; the quiesced cutover image first introduces `0005`; the later contract image introduces `0006` and removes legacy model metadata. Never rely on an operator remembering to target a lower revision than the image's `head`.

API, MCP, and web images must be built and deployed as one compatible stack. Health readiness begins only after migration completes.

### 13.3 Rollback boundary

- Before any new canonical writes, application rollback may use the old image if schema compatibility has been rehearsed.
- After new work/checkpoint writes, old code cannot see them. Rollback requires restoring the pre-cutover backup unless a deliberate reverse migration is written and proven lossless.
- Do not claim Alembic downgrade is a safe data rollback after the split. Multiple checkpoints cannot be faithfully collapsed into the old mutable row.
- Contract migration `0006` is forward-only operationally. Its rollback procedure is database restore.

### 13.4 Post-deployment audit

- compare row counts and representative exact values;
- recall preserved IDs through canonical and compatibility operations;
- verify one human card for multi-checkpoint work;
- test a claim/expiry flow after Phase 2;
- test a blocker/cycle/hierarchy flow after Phase 3;
- create a fresh post-upgrade backup and restore it into isolation;
- update `docs/validation.md` only with checks actually run and observed.

## 14. Verification strategy

### 14.1 Backend test organization

Keep existing tests and add focused modules:

```text
backend/tests/test_work_items_postgres.py
backend/tests/test_work_migration_postgres.py
backend/tests/test_leases_postgres.py
backend/tests/test_relationships_postgres.py
backend/tests/test_work_validation.py
```

The migration suite must:

1. upgrade an isolated schema only to `0003_handoff_comments`;
2. insert representative open/done/wont-do/promoted and soft-deleted rows;
3. include exact whitespace, Unicode, large-enough metadata, tags, session IDs, completion and ordinary comments, and disposable embedding rows;
4. upgrade to the target revision;
5. verify exact preservation and compatibility resolution;
6. run Alembic model parity checks.

For head-schema integration fixtures, checkpoint DELETE is forbidden and work-item deletion is restricted; reset with one `TRUNCATE work_relationships, work_leases, work_item_embeddings, checkpoints, work_items, projects RESTART IDENTITY CASCADE` operation or use a fresh/per-test schema. Revision-specific migration tests use fresh schemas so they never name tables that do not exist yet. Do not weaken the production trigger for ordinary test cleanup.

Run parity at both boundaries with their matching code metadata: `0005` plus retained legacy definitions during the observation window, then `0006` plus those definitions removed. A broad include/exclude hook must not mask unrelated drift.

### 14.2 MCP tests

Update `mcp/tests/conftest.py`, `test_tools.py`, and `test_transport.py` for:

- new models and fixtures;
- exact canonical plus compatibility tool catalogs;
- schemas and annotations;
- REST paths/payloads;
- bounded resource/prompt context;
- typed error mapping;
- lease-token redaction;
- legacy pointer/resource resolution;
- real Streamable HTTP and stdio initialization.

### 14.3 Frontend tests

Retain helper tests and add tests for:

- work search query construction;
- new recall pointer text;
- strict proxy allowlisting for every new method/path/query key;
- derived-state formatting and expiry refresh logic;
- relationship direction labels and tree helpers.

The existing pure Node tests are insufficient for grouping, immutable history, lease transitions, and graph expansion. Add an executable Playwright harness:

- `compose.e2e.yaml` starts an isolated API, frontend, and PostgreSQL database on dedicated nonproduction ports and credentials; PostgreSQL uses a tmpfs volume so no working data can be reached or retained.
- `scripts/test-e2e.sh` acquires a task-specific lock, generates a unique `MNEMONIC_E2E_COMPOSE_PROJECT` and `MNEMONIC_E2E_API_KEY`, then selects/verifies configurable loopback-only web/API ports before startup. It exports `MNEMONIC_E2E_WEB_URL`, `MNEMONIC_E2E_API_URL`, and `MNEMONIC_DASHBOARD_ORIGINS` before invoking Compose. Compose consumes the ports, origin, and key for the API/server-side proxy; Playwright config consumes the web URL; global setup consumes the API URL/key. PostgreSQL remains unexposed, and the wrapper never prints the generated key.
- `frontend/playwright.config.ts` targets Chromium, starts with `workers: 1`, captures traces/screenshots on failure, and covers desktop plus narrow viewport projects.
- `frontend/tests/e2e/global.setup.ts` seeds uniquely named projects through the public API. The API key exists only in the Node test process and server-side proxy environment—never in browser code or a `NEXT_PUBLIC_*` variable.
- Test files use unique project IDs and exercise grouping, immutable history, lease visibility/expiry, hierarchy, relationship editing, keyboard/dialog behavior, and every browser-observable phase acceptance scenario. Backend concurrency/raw-SQL, MCP transport, and migration guarantees remain in their dedicated suites.
- An E2E-only helper expires a lease with `docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$repo_root/compose.e2e.yaml" exec -T postgres psql ...`, targeting only the generated project and known synthetic work ID. Browser refresh timing is separately unit-tested with fake timers, so no test waits for the minimum TTL.
- Before registering cleanup, the wrapper validates that the nonempty project name matches its generated `mnemonic-e2e-*` prefix and that `$repo_root/compose.e2e.yaml` is the expected regular file. It then starts/waits for that stack, runs migrations and `playwright test`, and traps success, failure, or interruption with the same explicit scope: `docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$repo_root/compose.e2e.yaml" down -v --remove-orphans`. It must never run an unscoped Compose teardown.
- `npm run test:e2e` runs Playwright against an already-running disposable stack. `npm run test:e2e:stack` invokes the stack-managing wrapper and is the CI/local acceptance command.
- Pin Playwright and its browser version in the frontend lockfile; install Chromium in the CI image or bootstrap with `npx playwright install --with-deps chromium`.

### 14.4 Full-stack check

Rewrite `scripts/check-stack.py` around the canonical lifecycle:

1. create work plus initial checkpoint;
2. search one compact work result;
3. recall bounded context;
4. append a second checkpoint;
5. prove stale work edits conflict;
6. claim and replay the claim request;
7. add/remove a blocker and prove claim eligibility changes;
8. create a child/discovered item atomically;
9. complete with a summary and lease token;
10. verify default-open filtering and compatibility aliases;
11. remove relationships and soft-delete synthetic work.

Keep checks against a user's real stack read-only by default. Prefer a disposable full-stack profile for write-path CI so verification does not leave even soft-deleted synthetic records in a working database.

### 14.5 Standard validation commands

```sh
docker compose -f compose.test.yaml up -d --wait

cd backend
uv sync --frozen
export TEST_DATABASE_URL=postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test
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
npx playwright install --with-deps chromium  # CI image/bootstrap step
npm run test:e2e:stack
```

Also run a production-image build and the full-stack validation path before each phase is declared complete.

## 15. Security, operational, and performance requirements

### Security

- Preserve the shared bearer-key and trusted-local deployment boundary; these phases do not add multi-user authorization.
- Treat a lease token as a capability against accidental cross-session mutation, not a substitute for authentication.
- Never place tokens in paths/query strings, browser state, checkpoint content, errors, metrics, or Mnemonic-controlled logs; operators must separately protect MCP client tool traces.
- Keep exact Host/Origin enforcement and no arbitrary dashboard proxying.
- Continue treating stored text and metadata as untrusted content.
- Require real actor session provenance for checkpoints and relationship creation; never infer it from an MCP transport session.

### Operations

- Expired lease rows do not require immediate cleanup.
- There is no background scheduler, presence system, notification stream, or resource lock in these phases.
- Backups naturally include canonical graph state. Derived embeddings may be absent and rebuilt.
- Restore documentation must explain that an old backup upgrades through the data migration on startup.

### Performance

- Use `EXISTS`, lateral aggregates, or grouped subqueries so matching many checkpoints never duplicates work results.
- Inspect `EXPLAIN (ANALYZE, BUFFERS)` for browse, lexical checkpoint search, latest checkpoint, blocker count, root pagination, and child expansion.
- Bound context and relationship responses.
- Keep graph traversal out of normal recall; recursive CTEs are for cycle validation and explicit hierarchy queries.
- Project-row graph locking intentionally trades brief same-project write serialization for correctness. Revisit only if measured contention justifies a more complex advisory/serializable design.

## 16. File impact map

### Backend

Modify:

- `backend/src/mnemonic_api/main.py`
- `backend/src/mnemonic_api/models.py`
- `backend/src/mnemonic_api/schemas.py`
- `backend/src/mnemonic_api/database.py`
- `backend/src/mnemonic_api/semantic.py`
- `backend/tests/conftest.py`
- `backend/tests/test_api_postgres.py`
- `backend/tests/test_validation.py`
- `backend/tests/test_semantic.py`
- `backend/tests/test_semantic_postgres.py`

Add:

- `backend/src/mnemonic_api/errors.py`
- focused route/service modules from Section 6;
- Alembic revisions `0004` through `0008` as sequenced above;
- focused migration/work/lease/relationship test modules.

### MCP

Modify:

- `mcp/src/mnemonic_mcp/models.py`
- `mcp/src/mnemonic_mcp/api.py`
- `mcp/src/mnemonic_mcp/server.py`
- `mcp/tests/conftest.py`
- `mcp/tests/test_tools.py`
- `mcp/tests/test_transport.py`

### Dashboard

Modify:

- `frontend/components/dashboard.tsx` as the extraction entry point;
- `frontend/app/layout.tsx`;
- `frontend/app/globals.css`;
- `frontend/lib/types.ts`;
- `frontend/lib/api.ts`;
- `frontend/lib/proxy-policy.ts`;
- existing search/pointer/proxy tests;
- `frontend/package.json` and lockfile for pinned Playwright dependencies and scripts.

Add:

- focused work/checkpoint/relationship components and helpers;
- `frontend/playwright.config.ts`;
- `frontend/tests/e2e/global.setup.ts` and focused phase scenario files.

### Agent workflow and integration

Modify:

- `skills/mnemonic-save/SKILL.md`
- `skills/mnemonic-search/SKILL.md`
- `skills/mnemonic-recall/SKILL.md`
- `scripts/check-stack.py`
- `examples/handoff.json` or replace it with a work/checkpoint example while retaining a legacy example if useful.

Add:

- `scripts/test-e2e.sh`;
- `compose.e2e.yaml`.

### Documentation

Update after implementation:

- `README.md`
- `docs/architecture.md`
- `docs/api-contract.md`
- `docs/agents.md`
- `docs/development.md`
- `docs/operations.md`
- `docs/validation.md`

`docs/roadmap.md` remains the product roadmap; implementation should not rewrite its future phases merely to match current code.

## 17. Risks and mitigations

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Legacy prompt edits lack editor provenance | Migration overstates authorship | Dedicated migrated-snapshot marker and explicit warning |
| Structural migration loses text/history | Product trust failure | Populated upgrade test, exact parity assertions, backup/restore drill |
| Old clients call removed hand-off tools | Broken installed workflows/pointers | Preserve IDs and compatibility aliases through Phase 3 |
| Compatibility update rewrites a checkpoint | Violates foundational immutability | Reject prompt/provenance/tag changes; require new checkpoint |
| Search join returns one row per checkpoint | Duplicate human/agent work | Aggregate/`EXISTS`, distinct work counts, regression tests |
| Semantic cache commits during claim transaction | Partial claim/context behavior | Isolate semantic writes; never call from claim context service |
| Two claims both win | Duplicate execution | Work-row lock/conditional upsert and real barrier test |
| Lost claim response strands the holder | Work unavailable until TTL | Required `claim_request_id` replay returning the same receipt |
| Lease token leaks | One session can alter another lease | Body-only tokens, narrow models, redaction tests, no browser exposure |
| Naive cycle check races | Cyclic blocker graph | Project-row serialization plus reciprocal concurrency test |
| Parent cycles/multiple parents confuse UI | Infinite/duplicate hierarchy | Forest constraint, cycle check, defensive visited set |
| Open descendants hidden by terminal ancestors | Runnable work disappears from human view | Subtree-aware root filters, ancestor scaffolding, and terminal-parent E2E cases |
| `wont-do` silently unblocks dependent work | Work runs without prerequisite | Only `done` resolves; explicit relationship removal required |
| Deleted blocker becomes invisible | Permanently confusing blocked state | Reject deletion while relationships exist |
| One huge recall floods context | Poor agent reliability/cost | Initial plus five newest, explicit pagination, immediate edges only |
| Automatic startup migration takes too long | Service downtime | Rehearse, measure, maintenance window, disk/backup preflight |
| Large tool catalog creates agent noise | Poor tool selection | Canonical grouping, concise descriptions, documented temporary aliases |

## 18. Explicitly deferred work

Do not pull these roadmap items into Phases 1–3:

- purpose-built `list_ready_work` ordering or `claim_next_ready_work` (Phase 4);
- general append-only `WorkEvent` and activity feed (Phases 5 and 12);
- general mutation idempotency beyond the lease-specific recovery key (Phase 6);
- human gates and Needs Attention queue (Phase 7);
- aggregate descendant counts and full progressive-disclosure dashboard polish beyond basic collapse (Phase 8);
- merge/canonical duplicate behavior (Phase 9); `duplicate-of` is descriptive storage only here;
- repository freshness checks and `affected_paths` (Phase 10);
- structured verification/artifact tables (Phase 11);
- resource reservations (Phase 13);
- direct agent messaging, cross-project dependencies, sophisticated scheduling, automatic semantic merging, or workflow-status proliferation.

## 19. Definition of done for the three-phase program

The program is complete only when all of the following are true:

- [ ] One canonical `WorkItem` represents each durable objective.
- [ ] New work is never created without an initial immutable checkpoint.
- [ ] Multiple session checkpoints appear under one work item and one human card.
- [ ] Database and API layers both prevent checkpoint mutation/deletion.
- [ ] All legacy hand-offs/comments preserve exact text/provenance/timestamps, structurally equal JSONB metadata, and resolvable IDs.
- [ ] Legacy provenance limitations are disclosed rather than repaired with invented data.
- [ ] Search returns unique compact work items and retains lexical/hybrid behavior.
- [ ] Work recall is bounded and older checkpoints are explicitly pageable.
- [ ] Exactly one active lease can exist, replay/renew/release are safe, and expiry restores claimability.
- [ ] Lease tokens appear only in claim/renew receipts and lease-token request bodies, never ordinary responses, browser data, errors, or Mnemonic-controlled logs.
- [ ] Derived readiness is shared by reads and claims; no transient workflow state is persisted.
- [ ] All five relationship types are project-local and directionally documented.
- [ ] Block and parent cycles are rejected under sequential and concurrent writes.
- [ ] Only unresolved `blocks` edges affect readiness.
- [ ] Discovered work can be created atomically with a link to its originating checkpoint.
- [ ] Root work items are collapsible, descendants do not clutter the top level, and filtered open descendants remain visible beneath terminal ancestors.
- [ ] Canonical and compatibility MCP operations pass HTTP and stdio tests.
- [ ] Backend, MCP, frontend unit/type/build/E2E, migration, and full-stack checks pass.
- [ ] A real backup/restore drill succeeds on the new schema.
- [ ] README, contracts, operations guidance, agent workflow, and validation record match the shipped behavior.

At that point Mnemonic has the durable work-graph foundation described by the roadmap: work survives sessions, sessions leave immutable evidence, execution ownership expires safely, and structural graph facts—not ticket-like narrative—govern coordination.
