# Mnemonic Phases 4–5 Implementation Plan

**Status:** Proposed implementation plan

**Scope:** Roadmap Phase 4 (Ready-Work Discovery) and Phase 5 (Append-only Work Event Timeline)

**Source of product intent:** `docs/roadmap.md`

**Implementation baseline:** Phase 3 at Alembic revision `0008_work_relationships`

**Planning constraint:** This document defines the work; it does not implement it.

**Baseline correction (2026-08-31):** written against a tree that still carried the deprecated hand-off surface. That surface — the eight MCP tools, the `/projects/{project_id}/handoffs` REST routes, the `handoffs` resource URI, and the `resume_handoff` prompt — has since been removed, so Phases 4 and 5 have no compatibility surface to preserve. The sections that planned for one are corrected below.

## 1. Outcome

After these two phases, an agent can ask Mnemonic for a deterministic, bounded list of work that is actionable now instead of treating ordinary search results as a queue. Mnemonic will also retain an immutable, actor-attributed history of meaningful work changes and expose that history as a bounded per-work timeline.

The completed program must have these observable properties:

1. `list_ready_work` returns only visible, open work with no unresolved blocker and no active lease.
2. Ready results are ordered by one documented server policy and contain compact pointers, never checkpoint bodies, source metadata, or lease capabilities.
3. A ready result remains advisory. `claim_work` and `claim_and_recall` re-evaluate eligibility atomically when they acquire a lease.
4. Search remains retrieval; it is never renamed, reinterpreted, or presented as a ready queue.
5. Meaningful work mutations append a corresponding event in the same transaction as the mutation.
6. A retry-safe replay or idempotent no-op does not create a duplicate authoritative event.
7. Clients may append a lightweight `progress` event, but may not forge lifecycle, checkpoint, lease, or relationship facts.
8. Event rows are immutable at both the API and PostgreSQL layers and have a deterministic per-work order.
9. Bounded recent events improve recall, while the dashboard can page the complete per-work timeline.
10. Existing work, checkpoints, relationships, and any retained lease are conservatively represented in the initial event history without inventing facts the old schema did not retain.

Phase 4 is discovery, not scheduling automation. Phase 5 is a per-work audit timeline, not the durable project activity feed from Phase 12. These boundaries are load-bearing.

## 2. Inherited Phase 3 baseline

The implementation starts from the shipped Phase 3 architecture, not from the formerly proposed state in the Phases 1–3 plan.

| Concern | Shipped Phase 3 behavior | Phase 4–5 extension |
| --- | --- | --- |
| Durable work | `WorkItem` with four persistent statuses and priority `0..100` | No new lifecycle status |
| Context | Immutable `Checkpoint` rows and bounded `WorkContext` | Events reference checkpoints; they do not replace them |
| Readiness | Python projection plus SQL blocker/lease calculations | One reusable eligibility query and ready-list service |
| Claims | Work row, retained lease row, database time, blocker recheck | Preserve claim authority and make replay precedence explicit |
| Relationships | Five project-local types; only `blocks` affects readiness | Relationship writes emit endpoint events |
| Search | Open-only lexical browse by default; optional semantic retrieval | Remains separate from ready discovery |
| Dashboard | Hierarchical work browse, checkpoint history, lease/readiness badges, relationships | Per-work event timeline; no Phase 4 scheduler dashboard |
| Live sync | Data-free WebSocket invalidations after successful HTTP mutations | Timeline refetches on the existing invalidation contract |
| API structure | Routes in `application.py`; transaction helpers in `services/` | Add focused readiness and event services |
| MCP | Exact 27-tool canonical/compatibility catalog | Add three canonical tools, for an exact catalog of 30 |
| Database | Alembic head `0008_work_relationships` | Add `0009_ready_work_index` and `0010_work_events` |

Existing invariants that remain unchanged:

- Project isolation returns `404` rather than revealing cross-project existence.
- `done` is the only status that resolves an outgoing blocker.
- An active lease may coexist with a blocker added after acquisition.
- Lease tokens appear only in capability-bearing request bodies and claim/renew receipts.
- Checkpoint text and provenance remain immutable and untrusted.
- PostgreSQL and the FastAPI service remain the coordination authority; MCP remains a typed REST adapter.
- The shared bearer key authenticates access to the service, not the self-declared identity of an agent session.
- Browser proxy routes remain exact allowlists with Host/Origin enforcement and a server-only API key.

The current work browse index is optimized for status and recent activity, not scheduling order. The current target-edge and lease primary-key indexes are suitable for correlated readiness probes. The Phase 4 query plan must prove whether the new priority order needs its own work-item index; this plan fixes that decision in Section 5.2.

## 3. Decisions fixed by this plan

### 3.1 Phase boundaries

Phase 4 includes:

- one canonical REST ready-list endpoint;
- one canonical MCP `list_ready_work` tool;
- a shared, indexed readiness query used consistently by listing and claim validation;
- deliberately small filters over fields that already exist;
- a measured priority-first database index.

Phase 4 does **not** include `claim_next_ready_work`. The roadmap labels that operation a future extension. Agents list, choose, and then call an existing claim operation; a claim conflict causes the agent to refresh or choose another item.

Phase 5 includes:

- immutable `WorkEvent` storage;
- conservative historical backfill;
- automatic event emission from supported canonical mutations;
- one restricted public append operation for `progress` only;
- bounded per-work event reads, bounded recent events in recall, and a dashboard timeline;
- actor-aware evolution of mutation contracts that currently lack provenance.

Phase 5 does **not** include general mutation idempotency, human gates, verification-result records, a project activity cursor, SSE/webhooks, notifications, or a lease-expiry worker.

### 3.2 Exact ready-work predicate

At Phase 4, `is_ready_now(work_item, database_now)` means:

```text
work_item.project_id = requested project
AND work_item.deleted_at IS NULL
AND work_item.status = open
AND NOT EXISTS an incoming blocks edge whose source status is not done
AND NOT EXISTS a retained lease whose expires_at > database_now
AND no unresolved gate
```

There is no `Gate` model until Phase 7, so the final clause is vacuously true in Phases 4–5. Do not add a placeholder table, `waiting` status, or fake gate count. The readiness service must expose a named extension seam and tests that Phase 7 can extend without rewriting list and claim semantics.

Ordinary readiness is a non-recursive SQL query. Recursive traversal remains for cycle checks and explicit diagnostics, not for deciding whether one item has a direct unresolved incoming blocker.

### 3.3 Ready ordering, filters, and pagination

The exact default order is:

```text
priority DESC, created_at ASC, id ASC
```

Higher priority wins. Within one priority, older work wins. UUID order is the final deterministic tie-breaker. There is no configurable scoring formula in this phase.

The initial filters are:

| Query field | Default | Meaning |
| --- | --- | --- |
| `min_priority` | `0` | Inclusive priority floor |
| `tag` | absent | At least one checkpoint has the normalized exact tag |
| `parent_work_item_id` | absent | Work is a direct child of this visible project-local parent |
| `limit` | `30` | Page size, `1..100` |
| `offset` | `0` | Nonnegative offset in the deterministic order |

An unknown or cross-project parent returns the same `404` as any other project-scoped work lookup. `parent_work_item_id` means direct parent, not the entire subtree. Capability requirements, repository/path scope, creation-age policy, semantic ranking, and model/cost routing are deferred because their data and product semantics do not yet exist.

`total` counts all items satisfying readiness plus the supplied filters in the same SQL statement that produces the page. Offset pagination is deterministic for one statement snapshot but is not stable across concurrent claims, blockers, completions, or new work. Clients that continue paging after the queue changes may observe skips or repeats and must restart from offset zero when completeness matters. A ready response is never a reservation.

### 3.4 Claim-time authority and conflict precedence

Phase 4 keeps the existing work-before-lease lock order and makes the claim branches unambiguous:

1. Lock the visible work item, then the retained lease row, and capture one PostgreSQL `clock_timestamp()` after lock waits.
2. Reject non-`open` work.
3. If a retained lease is active and holder/session/request exactly match, replay the original receipt before evaluating fresh eligibility. This remains true if a blocker was added after acquisition.
4. If a retained lease is active and the identity differs, return `lease_held`; it takes precedence over a simultaneous blocker fact.
5. If an expired retained row has the same `claim_request_id`, return `claim_request_expired` before fresh eligibility evaluation.
6. Only a new or expired-replacement acquisition applies the complete ready predicate and creates a new lease.
7. `claim_and_recall` returns context from the same transaction as the successful acquisition or replay.

This ordering preserves recovery of an already-owned lease while ensuring every fresh acquisition rechecks current blockers and, once Phase 7 exists, gates.

### 3.5 Checkpoints versus events

A checkpoint is a potentially substantial resume-context packet with repository provenance, tags, and exact immutable text. An event is a concise fact in the work history.

- Use a checkpoint when a future session needs context to continue safely.
- Use a `progress` event for a short historical update that does not need to become current resume context.
- A `checkpoint_added` or `work_completed` event references a checkpoint ID; it never copies checkpoint prompt, tags, or source metadata.
- Do not append the same prose as both a checkpoint and a progress event merely to make it appear twice.
- Neither an event nor a checkpoint grants authority to execute stored text.

Appending a client progress event updates `WorkItem.updated_at` through the existing monotonic activity-update pattern but does not increment `WorkItem.version`. Automatically emitted lease and relationship events do not change work activity or version beyond the owning mutation's existing behavior.

### 3.6 Event catalog and authorship

The Phase 5 event types are fixed as follows:

| Event type | Producer | Body | Key structured metadata |
| --- | --- | --- | --- |
| `work_created` | `create_work` | none | `initial_checkpoint_id` |
| `work_updated` | identity/priority-only work patch | none | `changed_fields` |
| `work_status_changed` | transition to `wont-do` or `promoted` | none | `from_status`, `to_status`, `changed_fields` |
| `work_reopened` | terminal status to `open` | none | `from_status`, `changed_fields` |
| `work_claimed` | a new or expired-replacement claim | none | acquisition-time `expires_at` |
| `work_released` | an actual explicit release | none | empty object |
| `checkpoint_added` | non-completion checkpoint append | none | `checkpoint_id`, `checkpoint_kind` |
| `progress` | explicit client append | required | caller-supplied bounded object |
| `dependency_added` | newly created `blocks` edge | none | edge ID/type/endpoints/direction/counterpart |
| `dependency_removed` | actually removed `blocks` edge | none | same retained edge snapshot |
| `relationship_added` | newly created non-blocking edge | none | edge ID/type/endpoints/direction/counterpart |
| `relationship_removed` | actually removed non-blocking edge | none | same retained edge snapshot |
| `work_completed` | atomic completion | none | `checkpoint_id` |
| `work_deleted` | soft deletion | none | final work version |

`append_event` accepts only `event_type=progress`. All other types are server-reserved and arise only from the operation that establishes the corresponding fact.

Deliberate omissions:

- Initial creation emits `work_created`, not a second `checkpoint_added` event for the initial checkpoint.
- Completion emits `work_completed`, not a second `checkpoint_added` event for its completion checkpoint.
- Claim renewal emits no event in Phase 5; it would add high-frequency noise without changing responsibility.
- Passive TTL expiry emits no stored `lease_expired` event. Time passing is not a transaction, reads remain read-only, and this phase adds no scheduler. A later reliable producer may add that reserved type.
- A terminal transition that consumes a lease does not also emit `work_released`; the terminal event is the meaningful fact.
- `verification_run`, `human_attention_requested`, `human_attention_resolved`, `promotion_requested`, and `duplicate_marked` remain reserved for the phases that introduce their authoritative domain operations.

Relationship additions and removals create one event on each endpoint so either work item's timeline is intelligible. Endpoint metadata records the relative direction and counterpart ID. Both endpoint rows are inserted in a deterministic source-then-target order. Natural-key relationship replay (`created=false`) and already-absent removal (`removed=false`) emit nothing.

### 3.7 Actor provenance and compatibility

Event actor data is client-declared provenance, not an authenticated human identity.

```text
MutationActor
  actor_client
  actor_session_id
  actor_model       nullable
```

The event row also records:

```text
actor_kind          client | unattributed
```

`client` requires nonblank client and session fields. `unattributed` requires all actor fields to be null. Never infer an actor from the bearer key, HTTP connection, MCP transport session, relationship creator, or current dashboard user label.

Actor sources are:

| Mutation | Event actor source |
| --- | --- |
| Work creation | Initial checkpoint source fields |
| Checkpoint append | Appended checkpoint source fields |
| Completion | Completion checkpoint source fields |
| Claim/release | Retained lease holder fields |
| Relationship addition | Relationship creator fields |
| Explicit progress event | Required event actor fields |
| Work patch/reopen/delete | New optional REST `actor`; required by updated canonical MCP/dashboard clients |
| Relationship removal | New optional JSON body with `actor`; required by updated canonical MCP/dashboard clients |
| Older direct REST call that omits newly added actor data | `actor_kind=unattributed` |

The optional REST actor fields preserve compatibility for existing API clients without fabricating identity. Canonical MCP tools and the dashboard must always send truthful actor data after Phase 5. Any call that omits them is represented honestly as unattributed history rather than given an invented identity.

### 3.8 Event order, pagination, and future activity feeds

Each event has a PostgreSQL-generated `BIGINT` identity `id` and a server timestamp. Per-work order is:

```text
created_at ASC, id ASC
```

Descending reads reverse both directions. The identity is a deterministic tie-breaker and preserves the staging order of compound events. It is **not** promised to be transaction commit order and is **not** a gap-free Phase 12 activity cursor. Phase 12 must design a resumable project feed that cannot miss a lower ID committed after a higher ID was observed.

Event pages use the repository's existing `limit`/`offset` envelope. New live events can shift a newest-first offset page, so the dashboard restarts at offset zero after a live invalidation. There is no unbounded event response.

Normal `WorkContext` adds:

```text
recent_events                up to recent_event_limit, chronological
event_total
omitted_event_count
```

`recent_event_limit` defaults to `10` and is bounded to `0..20`. Event bodies are bounded to 4,000 characters, so recall remains predictably sized.

## 4. Requirement identifiers

The following identifiers are used by phase steps, tests, and the definition of done.

| ID | Requirement |
| --- | --- |
| `RW-1` | Ready eligibility exactly matches Section 3.2 at one database-time snapshot |
| `RW-2` | Ready order and filters exactly match Section 3.3 |
| `RW-3` | Ready responses remain pointer-only, bounded, and project-local |
| `RW-4` | Fresh claims re-evaluate readiness; active identical replay follows Section 3.4 |
| `RW-5` | Ordinary readiness is indexed and non-recursive |
| `EV-1` | Work events are immutable at API and database layers |
| `EV-2` | Authoritative events commit atomically with their mutations |
| `EV-3` | No-op/replay outcomes do not duplicate events |
| `EV-4` | Only `progress` is directly appendable by clients |
| `EV-5` | Actor provenance is truthful, bounded, and never inferred |
| `EV-6` | Event order, pagination, and recall bounds match Section 3.8 |
| `EV-7` | Historical backfill includes only provable facts and marks its origin |
| `EV-8` | Event bodies/metadata never enter logs, errors, metrics, or pointer-only results |

## 5. Target persistence and query model

### 5.1 Ready state remains derived

Do not add a ready-work table, materialized flag, scheduler status, queue position, or background refresh process. Readiness can change when lifecycle, blocker, lease time, or a later gate changes. Persisting it would create a second authority.

Add `backend/src/mnemonic_api/services/readiness.py` as the canonical home for:

- the Python `Readiness` projection currently in `work_context.py`;
- composable SQL expressions for active lease, unresolved blocker count, and eligibility;
- fresh-claim eligibility validation;
- the ready-page query and its bounded pointer projection.

Refactor summaries, relationship counterpart projections, bounded context, claims, completion validation, and Phase 4 listing to compose the same semantics. A test matrix must compare all surfaces for the same fixtures so an optimized query cannot silently diverge.

The ready-page statement must:

1. capture one `clock_timestamp()` value;
2. select and count eligible IDs using `NOT EXISTS` probes;
3. order and limit IDs before loading checkpoint/count projections;
4. obtain the current context pointer and checkpoint count only for the bounded page;
5. return page rows and total from one SQL statement;
6. avoid loading the relationship graph into application memory.

### 5.2 `0009_ready_work_index`

Add one reversible partial index matching the final order:

```sql
CREATE INDEX ix_work_items_ready_order
ON work_items (project_id, priority DESC, created_at ASC, id ASC)
WHERE deleted_at IS NULL AND status = 'open';
```

Keep the existing relationship target index and lease primary key. Add no speculative capability, event, or path indexes in Phase 4. `EXPLAIN (ANALYZE, BUFFERS)` must confirm the exact default and filtered ready queries before the phase ships. If PostgreSQL proves a different column order materially better on the representative dataset, update this plan and migration together rather than silently deviating.

### 5.3 `work_events`

Migration `0010_work_events` adds:

```text
id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY
project_id          UUID NOT NULL
work_item_id        UUID NOT NULL
event_type          VARCHAR(32) NOT NULL
actor_kind          VARCHAR(20) NOT NULL
actor_client        VARCHAR(80) NULL
actor_session_id    VARCHAR(200) NULL
actor_model         VARCHAR(120) NULL
body                TEXT NULL
metadata            JSONB NOT NULL DEFAULT '{}'
origin              VARCHAR(16) NOT NULL DEFAULT 'live'
created_at           TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
```

Constraints and indexes:

- composite foreign key `(project_id, work_item_id) -> work_items(project_id, id) ON DELETE RESTRICT`;
- check `event_type` against the exact Phase 5 catalog;
- check `actor_kind IN ('client', 'unattributed')` and enforce the nullability matrix in Section 3.7;
- check `length(body) <= 4000` and `length(btrim(body)) > 0` when body is present, while preserving its exact stored bytes;
- require a body only for `progress` and require body null for server-reserved types;
- check `jsonb_typeof(metadata) = 'object'`; API/MCP additionally enforce the existing 16 KiB finite-JSON bound;
- check `origin IN ('live', 'backfill')`;
- index `(project_id, work_item_id, created_at DESC, id DESC)` for timeline/recall reads;
- no project-feed index until Phase 12 fixes its cursor contract;
- no update or delete route;
- an `events_immutable` trigger rejects direct `UPDATE` and `DELETE`, following the checkpoint trigger pattern.

Normal work deletion remains soft and event history remains stored. Ordinary event reads require a visible work item and therefore return `404` after soft deletion; recovery and audit tooling can inspect preserved rows under the existing operator trust boundary.

### 5.4 Conservative event backfill

The `0010` migration backfills only facts derivable from retained rows:

1. One `work_created` event per work item, timestamped from `WorkItem.created_at`, attributed from its initial checkpoint, and referencing that checkpoint.
2. One `checkpoint_added` per later `context` or `progress` checkpoint.
3. One `work_completed` per completion checkpoint, even if the work was later reopened.
4. Two endpoint events per currently retained relationship, attributed from relationship creator fields.
5. One `work_claimed` per currently retained lease, timestamped from `acquired_at` and attributed from the holder. Its metadata may record the currently observed expiry only when labeled as a backfill observation, not as the original unrenewed expiry.

Every backfilled row has `origin=backfill`. Backfill ordering for equal timestamps is deterministic by source kind, source record ID, and endpoint role before identity allocation.

Do not synthesize:

- old title, summary, priority, or lifecycle edits;
- prior releases, expired leases, or replaced leases no longer stored;
- removed relationships;
- reopen events or remover identity;
- a historical creation status that the old schema did not preserve separately.

The UI and documentation must disclose that pre-Phase 5 history is reconstructed from retained facts and may have gaps. The migration installs the immutability trigger only after backfill and verifies the expected formula:

```text
work_created rows
+ non-initial checkpoint rows
+ 2 * retained relationship rows
+ retained lease rows
= total backfilled event rows
```

Soft-deleted work participates in backfill even though ordinary reads continue to hide it.

## 6. Service and transaction architecture

Suggested backend layout after Phase 5:

```text
backend/src/mnemonic_api/
  application.py
  models.py
  schemas.py
  services/
    readiness.py
    work_events.py
    work_context.py
    work_items.py
    leases.py
    relationships.py
```

`work_events.py` owns event construction, event-specific metadata validation, bounded listing, and the no-commit staging helper. Route functions remain the only normal commit boundary.

### 6.1 Atomic event rule

For every automatically recorded mutation:

1. Acquire the mutation's existing locks in the existing order.
2. Validate the mutation and determine whether it will change durable state.
3. Stage/flush the domain change.
4. Stage the event row or rows using the same SQLAlchemy `Session`.
5. Commit once in the outer route.

If event validation/insertion fails, the domain mutation rolls back. If the domain mutation fails, no event exists. Never emit authoritative events in HTTP middleware or after commit. Compatibility routes get the same behavior by calling the canonical service helper exactly once.

Event insertion adds no new row lock. Existing lock order remains:

1. project row for graph mutation;
2. endpoint work rows in UUID order for graph mutation, otherwise the focal work row where already required;
3. retained lease row;
4. relationship insert/delete;
5. event insert(s).

Concurrent checkpoint and explicit progress appenders remain able to succeed without incrementing the work version. Their activity timestamp update uses `greatest(existing_updated_at, database_now)`.

### 6.2 Emission matrix

| Existing operation | Event behavior |
| --- | --- |
| `create_work` | `work_created`; then endpoint relationship events only for edges actually created |
| `add_checkpoint` | one `checkpoint_added` |
| `complete_work` | one `work_completed`; no checkpoint/release duplicate |
| `update_work` | one primary status event when status changes; otherwise `work_updated`; metadata also lists other fields changed in the same patch |
| `delete_work` | one `work_deleted` after all guards pass |
| `claim_work` / new `claim_and_recall` acquisition | one `work_claimed` |
| identical active claim replay | no event |
| expired-row replacement | one new `work_claimed`; no fabricated expiry event |
| `renew_claim` | no event |
| `release_claim` with `released=true` | one `work_released` using captured lease actor before deletion |
| `release_claim` with `released=false` | no event |
| `add_relationship` with `created=true` | two endpoint dependency/relationship events |
| `add_relationship` with `created=false` | no event |
| `remove_relationship` with `removed=true` | snapshot edge, then two endpoint removal events in the deletion transaction |
| `remove_relationship` with `removed=false` | no event |
| `append_event(progress)` | one progress event and monotonic activity timestamp update |

Internal service return types may carry `created`, `replayed`, `released`, and captured pre-delete records so routes can preserve current public responses while event helpers distinguish winning mutations from no-ops.

### 6.3 Claim and recall interaction

On a new `claim_and_recall`, flush `work_claimed` before assembling `WorkContext` so its bounded `recent_events` can include the claim that established the returned lease. An identical replay adds no event but returns the same current event history. The context assembly still observes one transaction snapshot and never invokes semantic-cache writes.

## 7. Public contracts

### 7.1 REST additions

Phase 4 adds:

```text
GET /api/v1/projects/{project_id}/ready-work
```

Supported query keys are exactly those in Section 3.3. Unknown fields return `422` under strict Pydantic validation.

Phase 5 adds:

```text
GET  /api/v1/projects/{project_id}/work-items/{work_item_id}/events
POST /api/v1/projects/{project_id}/work-items/{work_item_id}/events
```

Event-list query fields:

| Field | Default | Rule |
| --- | --- | --- |
| `order` | `oldest` | `oldest | newest` |
| `event_type` | absent | one exact Phase 5 event type |
| `limit` | `50` | `1..100` |
| `offset` | `0` | nonnegative |

`POST .../events` accepts:

```text
event_type            progress (the only accepted value)
body                  required, exact text, 1..4000 nonblank characters
metadata              JSON object, default {}, at most 16 KiB
actor_client          required
actor_session_id      required
actor_model           optional
lease_token           optional capability, validated when supplied
```

As with checkpoint append, a lease is not required to record progress. A supplied token is validated rather than ignored. Terminal visible work may receive later progress clarification. Soft-deleted work remains inaccessible.

Existing mutation request changes:

- `WorkItemPatch` and `WorkDeletionCreate` gain optional nested `actor`.
- `actor` is provenance-only: exclude it from editable-field detection and ORM assignment, and reject a work patch that supplies no domain change besides `expected_version` and `actor`.
- relationship `DELETE` accepts an optional JSON body containing `actor`; a bodyless call remains supported.
- canonical MCP and dashboard calls always send the actor.
- compatibility requests may omit it and produce `unattributed` events.
- validation tests cover actor-only canonical and legacy patches so attribution data can never consume a work version by itself.

The browser proxy must normalize a truly bodyless DELETE and a JSON-bearing DELETE correctly; neither path may forward an empty stream as malformed JSON.

### 7.2 Response shapes

The ready endpoint returns the existing pointer-only envelope:

```text
Page[WorkSummary]
```

Every returned `WorkSummary.readiness.is_ready` is true at the statement snapshot. `active_lease` is null, blocker count is zero, and checkpoint prompts/source metadata are absent. `total`, `limit`, and `offset` retain their existing meanings.

`WorkEventRead` is:

```text
id                    integer
project_id            UUID
work_item_id          UUID
event_type            exact event literal
actor_kind            client | unattributed
actor_client          string | null
actor_session_id      string | null
actor_model           string | null
body                  string | null
metadata              JSON object
origin                live | backfill
created_at            ISO 8601 UTC
```

Event pages use `Page[WorkEventRead]`.

`WorkContext` adds:

```text
recent_events         WorkEventRead[], chronological
event_total           integer
omitted_event_count   total minus materialized recent events
```

No event response contains a lease token, claim request ID, bearer key, checkpoint prompt, or checkpoint source metadata unless the caller itself improperly placed such data in a progress body. Input guidance and validation reject reserved secret-like metadata keys case-insensitively; values remain untrusted and cannot be perfectly classified.

### 7.3 Canonical MCP changes

Phase 4 adds:

```text
list_ready_work
```

It is read-only, pointer-only, and exposes `min_priority`, `tag`, `parent_work_item_id`, `limit`, and `offset`. Its description must say: choose from the result, then use `claim_and_recall`; appearance in the list is not execution authority or a lease.

Phase 5 adds:

```text
append_event
list_work_events
```

- `append_event` is mutating, non-destructive, and `idempotentHint=false` until Phase 6. It accepts only progress.
- `list_work_events` is read-only and maps the exact event-list filters.
- `recall_work` gains `recent_event_limit` and returns typed recent events.
- the work resource and `resume_work` prompt inherit the bounded event context without adding another unbounded resource.
- `update_work`, `delete_work`, and `remove_relationship` require actor client/session in their canonical MCP schemas and serialize the optional REST actor envelope.

The exact Phase 5 MCP catalog is 30 tools: the shipped 27 plus these three. Do not add `get_activity` in this phase.

Strict response models reject unknown fields. The deliberate pointer-only `WorkSummary` behavior remains tolerant only to prevent accidental upstream content additions from reaching a tool result.

### 7.4 Browser proxy and dashboard boundary

Phase 4 does not add a dashboard scheduler or proxy route. The dashboard already shows derived readiness on work cards; agents use MCP for ready discovery. Add a negative proxy-policy test proving `/projects/{project_id}/ready-work` is not accidentally opened by a broad GET rule.

Phase 5 allows only:

| Surface | Browser proxy policy |
| --- | --- |
| Event list | Exact GET path and `order,event_type,limit,offset` keys |
| Progress append | Exact POST path; strict body; reject `lease_token` rather than strip it |
| Work patch/delete | Existing routes plus allowlisted actor object; still reject `lease_token` |
| Relationship removal | Existing exact DELETE route with optional allowlisted actor body |

All claim routes remain denied. WebSocket invalidations remain data-free; event body and metadata never travel over the sync channel.

### 7.5 Compatibility behavior

There is no deprecated surface left to preserve: the hand-off tools, routes, resource URI, and prompt were removed on 2026-08-31, so every write already goes through the canonical work services and emits exactly one event.

- Existing response shapes do not add event fields.
- Existing work and checkpoint IDs continue resolving, including IDs preserved from the migration.

## 8. Error and observability contract

No new Phase 4 application conflict is required. Invalid filters are `422`; an unknown parent is the existing sanitized `work_item_not_found` `404`.

Phase 5 uses strict validation for the public event type and actor/body/metadata rules. If a service-level check is needed after parsing, add only stable codes such as:

```text
event_type_reserved
event_metadata_invalid
```

Error context is an allowlist of non-secret identifiers and never includes event body, metadata values, actor session IDs, checkpoint content, tokens, or request bodies. MCP validation-field allowlists must cover new names without echoing invalid values.

Safe operational observations:

- ready-list duration, returned count, total, and whether each optional filter was present;
- event append/emission success count by bounded event-type enum;
- project/work/event IDs and exception class on failure;
- no tag value, body, metadata, prompt, actor session, claim request ID, API key, or token.

Do not use the immutable event table as a reason to duplicate its content into logs. The in-process live-sync revision remains ephemeral observability, not a durable activity cursor.

## 9. Phase 4 — Ready-Work Discovery

### 9.1 Phase objective

Give agents a purpose-built, deterministic view of work that can be claimed now, without changing search into a scheduler or weakening atomic lease acquisition.

### 9.2 Phase 4A — Characterize readiness and claim precedence

**Depends on:** shipped Phase 3.

1. Add a table-driven PostgreSQL characterization suite covering every lifecycle status, soft deletion, retained active/exactly-expired/expired leases, zero/one/multiple blockers, and all non-blocking relationship types.
2. Assert that only `done` resolves a blocker; `wont-do` and `promoted` remain unresolved.
3. Freeze the active identical-request replay behavior when a blocker is added after lease acquisition.
4. Add explicit tests for the conflict precedence in Section 3.4.
5. Assert current `WorkSummary`, `WorkContext`, relationship counterpart, completion, and claim results agree about the same work item.
6. Add a test seam representing zero unresolved gates without creating a Gate model.

**Exit check:** `RW-1` and `RW-4` semantics are executable before the ready route exists, and all existing Phase 1–3 behavior remains green.

### 9.3 Phase 4B — Extract the canonical readiness service

**Depends on:** Phase 4A.

1. Add `services/readiness.py` and move the pure response projection out of `work_context.py`.
2. Add composable SQL helpers for active-lease and incoming-unresolved-blocker facts using one caller-supplied database timestamp.
3. Refactor summaries, bounded context, relationship pointers, completion, and claims to use the shared helper or an equivalent shared selectable.
4. Preserve independent `is_blocked` and `has_active_lease` flags; active-plus-blocked remains valid.
5. Preserve display precedence `terminal > blocked > active > ready`.
6. Keep semantic embedding/cache work outside readiness and claim transactions.

**Exit check:** the characterization matrix proves semantic parity across every existing read and mutation surface.

### 9.4 Phase 4C — Index and ready-page service

**Depends on:** Phase 4B.

1. Add `0009_ready_work_index` exactly as specified in Section 5.2.
2. Add `ReadyWorkListQuery` with strict query fields and defaults from Section 3.3.
3. Implement one bounded ready-page statement with a captured PostgreSQL time, anti-joins, deterministic order, window/scalar total, and page-limited pointer projection.
4. Verify `tag` against any checkpoint using the existing normalized semantics, including mixed-case migrated tags.
5. Verify `parent_work_item_id` through the project-local `parent-child` edge and validate the parent before running the page query.
6. Keep every item pointer-only and assert the response cannot project prompt or source metadata if a lower-level query grows extra columns.
7. Return a coherent empty page with the correct total when offset is beyond the last row.

**Exit check:** `RW-1`, `RW-2`, `RW-3`, and `RW-5` pass against real PostgreSQL.

### 9.5 Phase 4D — REST and MCP

**Depends on:** Phase 4C.

1. Add the exact `GET /projects/{project_id}/ready-work` route.
2. Add the strict `list_ready_work` MCP tool and reuse the canonical `WorkPage` output model.
3. Update MCP server instructions to distinguish search, ready listing, recall, and claim authority.
4. Update typed validation/error mapping without adding a second readiness implementation to MCP.
5. Update exact HTTP and stdio catalog assertions from 27 to 28 for the Phase 4 increment.
6. Add negative browser-proxy tests for the ready route; do not add a dashboard view.

**Exit check:** an MCP client can list compact ready work, then use the existing `claim_and_recall` tool to arbitrate ownership.

### 9.6 Phase 4E — Workflow documentation and full-stack validation

**Depends on:** Phase 4D.

1. Update `mnemonic-search` so semantic/lexical search finds relevant work while `list_ready_work` finds actionable work.
2. Update `mnemonic-recall` so execution begins only after `claim_and_recall`, never from a ready result alone.
3. Extend `scripts/check-stack.py` to create ready, blocked, terminal, and leased synthetic work; assert only the ready item appears; then remove/complete/release the constraining facts and assert deterministic reappearance.
4. Keep cleanup scoped to exact synthetic IDs and relationships and preserve read-only behavior against an unapproved working stack.
5. Record the representative ready-query plans and observed timings in `docs/validation.md` only after they are run.

**Exit check:** the isolated full-stack flow proves MCP → REST → PostgreSQL ready discovery and subsequent atomic claim.

### 9.7 Phase 4 tests

Backend/PostgreSQL:

- visible open unblocked unleased work appears;
- `done`, `wont-do`, `promoted`, and soft-deleted targets do not appear;
- one or many unresolved blockers exclude the target;
- completing the final blocker or explicitly removing its edge restores eligibility;
- `wont-do` and `promoted` blocker sources do not restore eligibility;
- active lease excludes, while `expires_at = database_now` and older retained rows do not;
- `related`, `duplicate-of`, `discovered-from`, and `parent-child` do not affect eligibility;
- cross-project edges/parents cannot affect or leak into results;
- priority, tag, and direct-parent filters work alone and in combination;
- priority/timestamp ties use UUID order;
- `total`, limit, offset, and out-of-range offset are correct;
- every returned pointer is body/metadata/token-free;
- a ready list followed by a concurrent blocker or lease loses at claim time;
- simultaneous consumers may see the same snapshot, but only one compatible claim wins;
- active identical claim replay succeeds after a blocker is added and creates no new lease;
- active different claim returns `lease_held` under the documented precedence;
- the Phase 7 gate seam composes as an additional anti-predicate in a focused helper test.

MCP:

- exact tool name, input schema, output model, annotations, REST path, and query serialization;
- strict rejection of unknown filters and out-of-range limits;
- pointer-only behavior when an upstream fixture includes accidental prompt/metadata fields;
- sanitized parent/project errors;
- authority guidance mentions claim-time revalidation;
- exact 28-tool Streamable HTTP and stdio catalogs at the Phase 4 boundary.

Proxy/security:

- the ready REST endpoint requires normal API authentication;
- cross-project lookups remain `404`;
- the browser proxy denies the ready path;
- logs contain no tag values, checkpoint content, source metadata, tokens, or request bodies.

Performance:

- seed at least 10,000 work items across 10 projects, 30,000 checkpoints, 10,000 relationships including at least 2,000 blocker edges, and 2,000 retained leases with mixed expiry;
- run `EXPLAIN (ANALYZE, BUFFERS)` for default, tag-filtered, and parent-filtered pages at offset zero and a representative later offset;
- require the open-work selection to use `ix_work_items_ready_order` or a demonstrably equivalent bounded index plan;
- require blocker and lease checks to use endpoint/primary-key indexes rather than loading or sequentially scanning the full graph per page row;
- record execution time and buffers on the validation host; target under 100 ms for the first 30-item warm-cache default page, but treat plan shape and bounded work as the portable ship gate.

### 9.8 Phase 4 acceptance mapping

| Roadmap criterion | Requirement | Proof |
| --- | --- | --- |
| Agent need not search all open work | `RW-1`, `RW-3` | Dedicated API/MCP integration and full-stack flow |
| Blocked, leased, gated, or completed work is excluded | `RW-1`, `RW-4` | PostgreSQL matrix; gate clause marked executable in Phase 7 |
| Ordering is deterministic and documented | `RW-2` | Exact tie/pagination tests and API/MCP docs |
| Ordinary readiness is bounded and indexed | `RW-5` | Representative `EXPLAIN (ANALYZE, BUFFERS)` record |

Phase 4 is not complete if ready work is implemented as a search preset, if it loads all open work into Python, or if a prior list result can bypass claim-time validation.

## 10. Phase 5 — Append-only Work Event Timeline

### 10.1 Phase objective

Record meaningful collaboration history as immutable structured events, atomically coupled to the facts they describe, and expose a coherent bounded timeline to agents and humans.

### 10.2 Phase 5A — Contract characterization and actor seam

**Depends on:** completed Phase 4.

1. Characterize every canonical and compatibility mutation's current transaction/no-op behavior.
2. Add strict `MutationActor`, event input, event output, event-list query, and event-page schemas.
3. Add optional REST actor fields to work patch/delete and relationship removal without breaking bodyless clients.
4. Make canonical MCP actor fields required for `update_work`, `delete_work`, and `remove_relationship`.
5. Make dashboard mutations use `source_client=dashboard` and the existing stable per-tab session ID.
6. Verify older/compatibility calls without actor data are recorded as `unattributed`, never as the relationship creator or transport identity.

**Exit check:** `EV-5` is fixed and covered before any mutation starts emitting events.

### 10.3 Phase 5B — Event schema, backfill, and immutability

**Depends on:** Phase 5A.

1. Add `WorkEvent` to ORM metadata and `0010_work_events` with the exact schema, constraints, index, and trigger from Section 5.3.
2. Backfill only the facts listed in Section 5.4, including soft-deleted work.
3. Allocate same-timestamp backfill IDs deterministically.
4. Verify per-category counts, endpoint duplication, source actor fields, source timestamps, metadata references, and `origin=backfill` inside the migration.
5. Install the immutable trigger after backfill.
6. Update test cleanup to truncate `work_events` before referenced work tables and restart its identity.
7. Run Alembic model parity at `0010`.

**Exit check:** `EV-1`, `EV-6`, and `EV-7` pass on a populated `0009 -> 0010` migration fixture.

### 10.4 Phase 5C — Event service and atomic domain emission

**Depends on:** Phase 5B.

1. Add `services/work_events.py` with no-commit staging and bounded list helpers.
2. Define typed constructors per server-reserved event type; do not let general dictionaries choose authoritative metadata.
3. Integrate events with each service according to the emission matrix in Section 6.2.
4. Capture relationship snapshots and lease holder facts before deleting their mutable rows.
5. Return internal outcome flags so exact claim replay, relationship replay, absent removal, and absent release cannot emit.
6. Generate both relationship endpoint events in one deterministic order and the same transaction as the edge mutation.
7. Keep compatibility aliases on the same canonical helper path to avoid double emission.
8. Ensure event insertion failure rolls back the owning mutation through fault-injection tests.

**Exit check:** `EV-2` and `EV-3` pass for every row in the emission matrix.

### 10.5 Phase 5D — Restricted append and event reads

**Depends on:** Phase 5C.

1. Add exact GET/POST event routes.
2. Restrict public creation to `progress`; reject reserved event types before service mutation.
3. Apply exact body/metadata/actor bounds and reserved secret-like metadata-key rejection.
4. Validate an optional lease token without requiring one.
5. Permit progress on visible terminal work and reject soft-deleted/cross-project work with `404`.
6. Update activity time monotonically without changing work version.
7. Implement stable `(created_at,id)` ordering, filters, page totals, limits, and offsets.
8. Return event text exactly as stored and treat it as untrusted on every consuming surface.

**Exit check:** `EV-4`, `EV-6`, and `EV-8` pass through REST.

### 10.6 Phase 5E — Bounded recall

**Depends on:** Phase 5D.

1. Extend `WorkContextQuery` with `recent_event_limit=10`, maximum 20.
2. Extend the single-snapshot context assembly with newest-N event selection reordered chronologically, total, and omitted count.
3. Flush a newly acquired claim event before `claim_and_recall` assembles context.
4. Keep initial/current/recent checkpoint bounds and relationship direction bounds unchanged.
5. Confirm zero events and `recent_event_limit=0` produce an empty list plus accurate totals.
6. Keep prompt/resource authority warnings and add equivalent language for event bodies.

**Exit check:** recall remains bounded and reflects the mutation that established a newly returned lease.

### 10.7 Phase 5F — MCP tools and workflow guidance

**Depends on:** Phase 5E.

1. Add strict `WorkEvent`, `WorkEventPage`, and progress-input models.
2. Add `append_event` and `list_work_events` with the exact annotations and REST mapping from Section 7.3.
3. Extend `recall_work`, resource, and prompt models for bounded recent events.
4. Preserve upstream-field rejection for full event models and pointer-only suppression for ready results.
5. Add unknown-write guidance specific to `append_event`: do not retry automatically; inspect recent/listed events because general `client_operation_id` arrives in Phase 6.
6. Update validation field allowlists and error sanitization without echoing body/metadata values.
7. Update exact catalogs from 28 to 30.
8. Update bundled skills:
   - `mnemonic-save`: checkpoint for resume context, event for concise progress;
   - `mnemonic-search`: ready discovery remains distinct from retrieval;
   - `mnemonic-recall`: inspect bounded events, page explicitly when needed, then claim before execution.

**Exit check:** canonical event workflows pass through both MCP transports and copied resume pointers remain bounded.

### 10.8 Phase 5G — Dashboard timeline

**Depends on:** Phase 5D; may proceed in parallel with Phase 5F.

Add focused components rather than expanding `dashboard.tsx` inline:

```text
frontend/components/work-event-timeline.tsx
frontend/components/work-event-composer.tsx
frontend/lib/work-events.ts
```

The dashboard must:

- show a paginated per-work Activity timeline ordered newest-first in the UI;
- render deterministic human labels for every event type using structured metadata;
- show actor client/session only when attributed and label older unknown actors as “Unattributed legacy action”;
- mark reconstructed rows and show one concise partial-history notice when any page contains `origin=backfill`;
- display progress body as untrusted text, never HTML;
- render checkpoint events as references and keep exact checkpoint bodies in the existing Checkpoint timeline rather than duplicating them;
- show both endpoint-relative relationship semantics and the counterpart ID/title when available from current context, falling back safely when the counterpart is deleted or absent;
- offer a compact progress-event composer that explains when to use a checkpoint instead;
- use the existing dashboard per-tab identity;
- maintain independent loading, empty, retry, pagination, and append-error states;
- reset the newest event page and refetch context on a matching live invalidation;
- remain usable by keyboard and at the narrow Playwright viewport.

Update the proxy only for the exact Phase 5 matrix. Never expose claim routes or forward a browser-supplied lease capability.

**Exit check:** a human can understand a create → checkpoint → claim → dependency → release → completion/reopen history without editing the work narrative.

### 10.9 Phase 5H — Integration, operations, and validation record

**Depends on:** Phase 5F and Phase 5G.

1. Extend `scripts/check-stack.py` to append progress, create a checkpoint, claim/replay, add/remove a blocker, release, complete, and reopen synthetic work, checking the exact ordered event types after each step.
2. Assert claim replay and relationship no-op responses leave event counts unchanged.
3. Assert ordinary MCP/REST/log/proxy output never includes tokens, claim request IDs, event metadata secrets, checkpoint prompts in pointer results, or request bodies.
4. Add representative-scale event query plans, backfill timing, and backup/restore parity to the validation record.
5. Update architecture, API, agent, development, operation, README, and example documentation as mapped in Section 15.
6. Verify old backups upgrade through `0010` and receive only conservative reconstructed history.

**Exit check:** the entire Phase 1–5 lifecycle passes through MCP → REST → PostgreSQL and the dashboard proxy without leaving non-synthetic data.

### 10.10 Phase 5 tests

Schema/migration:

- populated `0009 -> 0010` backfill exact formula and per-category parity;
- exact known actor/source timestamp/reference preservation;
- soft-deleted work history is backfilled but hidden from ordinary APIs;
- no invented update, release, removal, reopen, or expiry facts;
- raw SQL `UPDATE` and `DELETE` fail under the event trigger;
- cross-project event/work composite foreign keys fail under direct SQL;
- invalid actor/body/type/origin/metadata shapes fail at database or API layers as specified;
- Alembic model parity and isolated downgrade mechanics.

Atomicity and replay:

- every emission-matrix mutation and its event(s) commit together;
- forced event insertion failure leaves no domain mutation;
- forced domain failure leaves no event;
- simultaneous checkpoint/progress appenders both succeed with deterministic event order;
- identical claim replay creates one claim event;
- expired replacement creates one additional claim event and no expiry event;
- renewal creates no event;
- matching release creates one event; absent/different-expired release creates none;
- relationship create replay and absent removal create no duplicate event;
- each real graph mutation creates exactly two endpoint events;
- completion creates only `work_completed`, creation only `work_created` for its initial checkpoint;
- compatibility aliases emit exactly once through canonical services.

REST/recall:

- project/work isolation and soft-delete visibility;
- oldest/newest ordering with equal timestamps and identity tie-break;
- type filter, totals, limits, offsets, and empty pages;
- progress exact text/Unicode/whitespace preservation within nonblank rules;
- terminal append allowed, optional token checked, wrong token sanitized;
- event context is chronological, bounded, counted, and included in new claim-and-recall;
- event body and metadata never appear in ready/search pointers.

MCP:

- exact schemas, annotations, paths, bodies, and typed response models for both event tools;
- canonical actor inputs on update/delete/remove;
- reserved event types rejected locally or by strict API validation;
- no automatic retry after ambiguous append outcome;
- event/list/recalled content remains bounded and secrets are redacted from errors/logs;
- exact 30-tool HTTP and stdio catalogs.

Frontend/E2E:

- empty, loading, retry, attributed, unattributed, and reconstructed timeline states;
- deterministic labels for every event type and endpoint direction;
- progress append appears through live sync without a full page reload;
- checkpoint text is not duplicated inside its event row;
- claim replay and relationship replay do not create duplicate visible rows;
- newest-page reset avoids offset drift after live invalidation;
- event body renders as text under hostile HTML/script input;
- actor-bearing work edit/delete/relationship removal payloads pass exact proxy validation;
- token-bearing event/mutation bodies and unallowlisted event queries are rejected;
- desktop and narrow viewport keyboard/dialog behavior.

Performance and operations:

- seed one work item with at least 100,000 events and multiple ordinary-sized histories;
- inspect first, middle, and last event pages in both orders plus bounded context;
- require the per-work timeline index and bounded row reads; record offset degradation honestly;
- record `0010` backfill runtime against representative production-scale data;
- verify custom-format dump listing, isolated restore, event count/content hash, event identity sequence state, and immutability trigger.

### 10.11 Phase 5 acceptance mapping

| Roadmap criterion | Requirement | Proof |
| --- | --- | --- |
| Progress/history does not require editing main narrative | `EV-2`, `EV-4` | Progress append and unchanged work version tests |
| Events are immutable | `EV-1` | No mutation routes plus raw PostgreSQL trigger tests |
| Event order is deterministic | `EV-6` | Same-time, both-direction pagination, and UI ordering tests |
| UI reconstructs meaningful history | `EV-2`, `EV-3`, `EV-5`, `EV-7` | Emission matrix plus desktop/narrow Playwright lifecycle |

Phase 5 is not complete if clients can append `work_completed` directly, if an authoritative event can commit separately from its mutation, or if backfill invents history the Phase 3 schema never stored.

## 11. Delivery sequence

Use reviewable increments and keep Phase 4 deployable independently from Phase 5.

| Increment | Contents | Depends on | Ship gate |
| --- | --- | --- | --- |
| 1 | Readiness characterization and shared service extraction | Phase 3 | Existing suite plus parity matrix |
| 2 | `0009` index, ready query, REST route | 1 | PostgreSQL correctness and query plans |
| 3 | `list_ready_work`, MCP/docs/check-stack | 2 | Exact 28-tool transports and full-stack Phase 4 flow |
| 4 | Actor contract seam and event schemas | 3 | Compatibility/actor tests |
| 5 | `0010` event table, conservative backfill, trigger | 4 | Populated migration, parity, immutability |
| 6 | Atomic event emission across canonical services | 5 | Emission matrix and fault-injection suite |
| 7 | Progress append, event list, bounded recall | 6 | REST/PostgreSQL Phase 5 tests |
| 8 | MCP event tools and skills | 7 | Exact 30-tool HTTP/stdio catalogs |
| 9 | Dashboard timeline, composer, proxy/live sync | 7 | Unit/type/build and Playwright |
| 10 | Full-stack, performance, backup/restore, documentation | 8–9 | All program gates green |

Do not start automatic event emission before actor compatibility and the immutable table exist. Do not expose `append_event` before server-reserved types are impossible in its request schema.

## 12. Migration, deployment, and rollback

### 12.1 Phase 4 deployment

`0009_ready_work_index` is additive and contains no data rewrite. On a large production table, measure lock/build time on a restored copy. The current single-instance startup migration may create it normally during a maintenance window; if measured lock time is unacceptable, use an explicitly rehearsed non-transactional concurrent-index deployment rather than improvising during release.

The Phase 4 API/MCP image can deploy together after the index migration. Rolling back the application is safe because the index is unused by old code; downgrading drops only the additive index.

### 12.2 Phase 5 pre-deployment

1. Create a fresh custom-format backup and verify it with `pg_restore --list`.
2. Record counts for work items, checkpoints by kind, relationships by type, retained leases, and soft-deleted work.
3. Rehearse `0009 -> 0010` on an isolated restored copy and record backfill duration, table/index size, and parity formula.
4. Confirm disk headroom for the new event table, indexes, backup, and restore.
5. Build API, MCP, and dashboard images from the same commit.

### 12.3 Phase 5 cutover

Old writers cannot emit events, so quiesce all API/MCP/dashboard writers while the `0010` migration backfills and the new compatible stack starts. Do not run old and Phase 5 writers concurrently around the cutover or the timeline will have an unrecorded gap.

The API startup still upgrades to Alembic `head` before readiness. Health traffic may begin only after migration, parity checks, and application readiness succeed. Browser, MCP, and API contracts deploy together because actor bodies and response shapes change in concert.

### 12.4 Rollback boundary

- Before any Phase 5 write, application rollback to the Phase 4 image is safe if rehearsed; the additive event table may remain unused.
- After a Phase 5 mutation has emitted events, an old application can still manipulate canonical work but would create audit gaps. Do not treat that as a safe rollback.
- Alembic downgrade drops immutable history and is operationally destructive after Phase 5 writes. It is not the production rollback plan.
- After Phase 5 writes, recover by forward-fixing the application or restoring the pre-cutover backup and accepting loss of all post-cutover domain changes.

### 12.5 Post-deployment audit

- verify the migration backfill formula and representative event references;
- confirm no trigger permits update/delete;
- run one ready → claim/replay → checkpoint → relationship add/remove → release → complete/reopen flow;
- compare REST, MCP, dashboard timeline, and database event order;
- scan application/MCP/web logs for a seeded canary token and event-body marker;
- create a fresh post-upgrade backup and restore it into isolation;
- verify restored event count, ordered content hash, identity sequence, and trigger;
- update `docs/validation.md` only with observed results.

## 13. Verification strategy

### 13.1 Backend organization

Add focused test modules:

```text
backend/tests/test_ready_work_postgres.py
backend/tests/test_work_events_postgres.py
```

Extend:

```text
backend/tests/test_work_migration_postgres.py
backend/tests/test_leases_postgres.py
backend/tests/test_relationships_postgres.py
backend/tests/test_work_items_postgres.py
backend/tests/test_live_sync.py
backend/tests/test_validation.py
backend/tests/conftest.py
```

Use the existing isolated-schema PostgreSQL fixtures. A missing `TEST_DATABASE_URL` means the database suite is incomplete, not successful validation. Concurrency tests use barriers rather than sleeps. Expiry tests set database timestamps directly or inject the existing database-time seam.

Migration tests must upgrade a populated schema to `0009`, insert representative Phase 1–4 state, then upgrade to `0010`. Include exact Unicode/whitespace, migrated markers, all statuses/checkpoint kinds/relationship types, active and expired retained leases, soft deletion, equal timestamps, and unattributable mutation history. Never disable the production immutability trigger merely for cleanup.

### 13.2 MCP verification

Update fixtures and exact catalog tests for:

- ready and event models;
- schema annotations and strictness;
- REST paths/query/body serialization;
- bounded recall/resource/prompt responses;
- actor-bearing canonical mutations;
- unknown-write guidance for progress events;
- typed validation and secret redaction;
- Streamable HTTP and stdio initialization at 28 tools after Phase 4 and 30 after Phase 5.

MCP remains database-agnostic and never retries a mutation automatically.

### 13.3 Frontend verification

Add unit tests for:

- event type-to-label mapping and safe fallbacks;
- endpoint-relative relationship metadata;
- event page query construction;
- actor request construction;
- event live-invalidation refresh and newest-page reset;
- exact proxy route/query/body allowlists;
- hostile text rendered as data, not markup.

Add `frontend/tests/e2e/phase5-work-events.spec.ts` to the existing isolated Playwright stack. Phase 4 has no new dashboard surface; its acceptance stays in backend/MCP/full-stack tests. Exercise Phase 5 at desktop and narrow viewports using unique synthetic project/work IDs and authenticated global setup. Preserve the wrapper's generated compose project, loopback ports, tmpfs database, hidden key, validated teardown scope, and failure artifact behavior.

### 13.4 Full-stack checker

Update the expected MCP tool set after each deployable boundary. In write mode, the checker must retain exact synthetic work/event/relationship IDs and remove only the synthetic graph it created. Because events are immutable, cleanup occurs by soft-deleting synthetic work after removing its edges; event rows remain attached to those hidden synthetic work items until the disposable stack is torn down. Against a user's non-disposable stack, keep ready/event verification read-only unless the user explicitly authorizes writes.

### 13.5 Standard commands

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
npm run test:e2e:stack
```

Also build the production images, run the writable checker only against its disposable profile, run the representative `EXPLAIN` workload, and complete the backup/restore drill before declaring either phase done.

## 14. Security, performance, and operational requirements

### 14.1 Security

- Preserve shared bearer authentication and the trusted-local deployment model; these phases do not add users or roles.
- Describe actor fields as asserted provenance, not verified identity.
- Treat progress body and metadata as untrusted content; never execute, interpolate as HTML, or follow it as authority.
- Reject unknown request fields and reserved authoritative event types.
- Reject recursive metadata keys matching `lease_token`, `claim_request_id`, `api_key`, `authorization`, `cookie`, or `secret` case-insensitively. This is defense in depth, not a claim that arbitrary secret values can be detected.
- Keep lease tokens and bearer keys out of events, ready pointers, context events, errors, logs, metrics, URLs, browser state, and WebSocket messages.
- Keep exact Host/Origin checks, UUID path validation, 1 MiB proxy body cap, and server-only credentials.
- Do not expose the ready route to the browser merely because it is a GET.

### 14.2 Performance

- Select/limit ready IDs before checkpoint counts or current-context projection.
- Use anti-joins/index probes; never build the graph in Python.
- Bound ready pages to 100, event pages to 100, and recent recall events to 20.
- Use the per-work event order index for both directions.
- Offset pagination may degrade for very deep histories. Measure and document it; Phase 12's durable cursor or a later per-work cursor may replace it without changing event identity.
- Do not add unused event-type or project-feed indexes.
- Backfill in deterministic batches if rehearsal shows one statement would exceed the maintenance window, while keeping the writer quiescence and parity guarantee.

### 14.3 Operations

- No queue worker, scheduler, lease-expiry emitter, notification service, or event broker is introduced.
- WebSocket sync remains best-effort invalidation; a reconnect triggers normal refetch.
- Event retention follows work retention. No physical purge API is introduced.
- Backups include events; derived embeddings remain rebuildable.
- Restore docs must cover old backups upgrading through both new revisions.
- Metrics use bounded event-type labels only; never tag metrics with project names, tag values, actors, body text, or IDs at unbounded cardinality.

## 15. File impact map

### Backend

Modify:

- `backend/src/mnemonic_api/application.py`
- `backend/src/mnemonic_api/models.py`
- `backend/src/mnemonic_api/schemas.py`
- `backend/src/mnemonic_api/services/work_context.py`
- `backend/src/mnemonic_api/services/work_items.py`
- `backend/src/mnemonic_api/services/leases.py`
- `backend/src/mnemonic_api/services/relationships.py`
- `backend/tests/conftest.py`
- existing work/lease/relationship/migration/live-sync/validation tests

Add:

- `backend/src/mnemonic_api/services/readiness.py`
- `backend/src/mnemonic_api/services/work_events.py`
- `backend/alembic/versions/0009_ready_work_index.py`
- `backend/alembic/versions/0010_work_events.py`
- `backend/tests/test_ready_work_postgres.py`
- `backend/tests/test_work_events_postgres.py`

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

- `frontend/components/dashboard.tsx`
- `frontend/components/work-item-detail.tsx`
- `frontend/app/globals.css`
- `frontend/lib/types.ts`
- `frontend/lib/proxy-policy.ts`
- relevant proxy/live-sync/API tests

Add:

- `frontend/components/work-event-timeline.tsx`
- `frontend/components/work-event-composer.tsx`
- `frontend/lib/work-events.ts`
- focused event helper tests
- `frontend/tests/e2e/phase5-work-events.spec.ts`

### Workflow, integration, and documentation

Modify:

- `skills/mnemonic-save/SKILL.md`
- `skills/mnemonic-search/SKILL.md`
- `skills/mnemonic-recall/SKILL.md`
- `scripts/check-stack.py`
- `README.md`
- `docs/architecture.md`
- `docs/api-contract.md`
- `docs/agents.md`
- `docs/development.md`
- `docs/operations.md`
- `docs/validation.md`
- canonical examples where ready/event calls improve clarity

`docs/roadmap.md` remains the product-intent source and must not be rewritten merely to mirror implementation details.

## 16. Risks and mitigations

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Ready list and claims use subtly different predicates | Agent selects work that can never be claimed, or unsafe work is leased | Shared readiness service, parity matrix, claim-race tests |
| Active replay is checked after blockers | Holder loses recovery of its own token after a new blocker | Fixed precedence in Section 3.4 |
| Priority query scans all open work | Ready discovery slows as projects grow | Partial ordered index plus representative plans |
| Offset queue pages move during claims | Skips or duplicates across pages | Document snapshot semantics; claim rechecks; restart scan when completeness matters |
| Gate clause is forgotten in Phase 7 | Gated work remains ready | Named readiness extension seam and deferred acceptance test |
| Generic append can forge completion/dependency facts | Audit history contradicts authoritative state | Public literal restricted to `progress`; typed server constructors |
| Events emit after commit | Mutation exists without history or vice versa | Same-session staging and one outer commit; fault injection |
| Claim/relationship replay duplicates events | Timeline noise and false activity | Internal outcome flags and exact-count tests |
| Relationship removal is attributed to its creator | False provenance | Optional actor body; canonical clients require it; otherwise unattributed |
| Backfill fabricates old state changes | Audit trail overclaims knowledge | Reconstruct only retained facts; `origin=backfill`; partial-history notice |
| Passive TTL has no transaction | Missing or fake expiry event | Omit `lease_expired` until a reliable producer exists |
| Completion appears twice | Noisy timeline | One `work_completed` event referencing its checkpoint |
| Event content leaks tokens or private context | Capability/privacy exposure | Bounded inputs, key denylist, pointer separation, redaction/log tests |
| Event trigger blocks test cleanup | Flaky/destructive cleanup hacks | Scoped `TRUNCATE ... CASCADE` in isolated schemas; never weaken production trigger |
| Phase 5 app rolls back to Phase 4 | New mutations create an audit gap | Quiesced cutover; forward fix or database restore after first event write |
| BIGINT ID is mistaken for Phase 12 cursor | Incremental feed can miss commit reordering | Explicit non-guarantee; Phase 12 designs separate stable cursor |
| Timeline grows indefinitely | Slow deep pages and larger backups | Strict limits, order index, measured offset behavior, restore drills |
| Dashboard duplicates checkpoint content | Confusing, noisy history | Event shows reference/label; checkpoint component retains full text |

## 17. Explicitly deferred work

Do not pull these roadmap items into Phases 4–5:

- `claim_next_ready_work` or any automatic selection-and-claim policy;
- capability matching, repository/path scheduling, cost/model routing, load balancing, or compound scores;
- persisted queue positions or derived workflow statuses;
- general `client_operation_id` idempotency (Phase 6);
- human gates, `waiting`, and Needs Attention (Phase 7);
- aggregate descendant summaries beyond shipped Phase 3 hierarchy (Phase 8);
- structural duplicate merging/canonical redirects (Phase 9);
- affected-path freshness verification (Phase 10);
- structured verification results or artifacts (Phase 11);
- `get_activity`, durable project cursors, SSE, webhooks, or subscriptions (Phase 12);
- resource reservations (Phase 13);
- background lease-expiry events, notifications, direct messaging, cross-project dependencies, or automatic semantic decisions.

The `work_events.project_id` field and server-reserved type catalog are extension points, not permission to implement those later phases early.

## 18. Definition of done

### Phase 4

- [ ] `RW-1`: one database-time snapshot decides every ready result.
- [ ] `RW-2`: `priority DESC, created_at ASC, id ASC` and all filter semantics are documented and tested.
- [ ] `RW-3`: ready pages are project-local, bounded, and pointer-only.
- [ ] `RW-4`: every fresh claim rechecks readiness, while active identical replay remains recoverable.
- [ ] `RW-5`: representative plans use bounded index-supported work, blocker, and lease queries.
- [ ] Search remains retrieval and no dashboard/browser scheduler surface is added.
- [ ] REST, the exact 28-tool MCP catalog, skills, checker, docs, and validation record agree.

### Phase 5

- [ ] `EV-1`: event update/delete is impossible through API and rejected by PostgreSQL.
- [ ] `EV-2`: every supported authoritative event is atomic with its domain mutation.
- [ ] `EV-3`: exact claim replay and all natural no-op mutations create no duplicate events.
- [ ] `EV-4`: public append accepts only bounded `progress` events.
- [ ] `EV-5`: known actors are truthful client assertions and unknown actors are explicitly unattributed.
- [ ] `EV-6`: event order, pages, and bounded recall are deterministic.
- [ ] `EV-7`: migration backfills only provable facts, including soft-deleted history, with exact parity and origin markers.
- [ ] `EV-8`: body/metadata/tokens never leak into pointers, errors, logs, metrics, browser capabilities, or sync messages.
- [ ] Checkpoint and event responsibilities remain distinct and the dashboard avoids duplicate content.
- [ ] The exact 30-tool MCP catalog passes Streamable HTTP and stdio tests.
- [ ] Backend PostgreSQL, Ruff, MCP, frontend unit/type/build, Playwright, production-image, full-stack, performance, and secret-scan checks pass.
- [ ] A real backup/restore drill preserves event order, content, identity state, and immutability.
- [ ] Operational docs state the quiesced cutover and honest rollback boundary.

The two-phase program is complete only when an agent can discover ready work without scanning search results, still relies on an atomic claim to begin, and can later inspect an immutable, bounded, truthful history of what happened to that work.
