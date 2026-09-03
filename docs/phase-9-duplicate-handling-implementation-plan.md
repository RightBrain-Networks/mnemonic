# Mnemonic Phase 9 — Duplicate Handling Implementation Plan

**Status:** Historical implementation plan; repository implementation completed on 2026-09-02,
with deployment-only gates retained below

**Scope:** Roadmap Phase 9, “Duplicate handling,” delivered as an authoritative core release
followed by an independently releasable advisory-suggestions release

**Product source:** docs/roadmap.md

**Planning precedent:** docs/phases-7-8-implementation-plan.md

**Implementation baseline:** main at
2efa84d5e8b489ce7be0b3ad72e27d4a8a5c0a12 (Document shipped roadmap phases), with Phases 1–8
shipped and Alembic head 0015_gate_review_fixes

**Planning origin:** This document was the code-free implementation contract. Implementation was
separately authorized, completed, and validated; observed evidence is recorded in
`docs/validation.md`. Mnemonic is prerelease, so the resulting implementation intentionally chooses
clean contract changes over compatibility shims while preserving and migrating existing database
content. Current operator and client behavior is specified in `docs/operations.md` and
`docs/api-contract.md`.

## 1. Outcome

Phase 9 gives Mnemonic one explicit, non-destructive way to state that a retained work item is no
longer an independent objective and that another retained work item is its canonical continuation.
The source ID, lifecycle, checkpoints, events, gates, provenance, relationships, and receipts remain
auditable. Ordinary discovery and execution stop treating that source as actionable, while an exact
old ID still reaches its source-owned history and identifies the current canonical root.

Similarity remains advisory. It may help a person or agent notice possible existing work before
creation, but it cannot merge work, create a relationship, suppress creation, choose a canonical
item, or substitute for explicit merge authority.

The implementation must have these observable properties:

1. An authoritative merge is a first-class immutable fact. It is not inferred from text,
   embeddings, lifecycle, deletion, or a generic relationship.
2. Direction remains exact: source --duplicate-of--> destination. The source is the duplicate.
   Phase 9 adds no alternate spelling or reverse-direction convenience API.
3. Existing duplicate-of relationships remain byte-for-byte descriptive evidence. Migration 0016
   creates no merge from them.
4. One receipt-protected merge_work operation creates an authoritative merge and atomically reuses
   or creates its exact supporting duplicate-of relationship.
5. The authoritative graph is project-local, acyclic, and bounded to 50 edges from any retained
   alias to its current canonical root. Each source has one immutable outgoing merge; many sources
   may converge.
6. A merged source retains its stored lifecycle. Duplicate is derived presentation/readiness state,
   not another lifecycle value and not deletion.
7. Ready discovery and every shipped claim path use one shared indexed exclusion predicate.
   No route, MCP tool, resource, prompt, or browser action silently substitutes a canonical ID for
   a caller-supplied alias ID.
8. Exact alias reads retain source-owned history. Canonical context is fetched separately; source
   and root checkpoint bodies are never blended.
9. Merge consumes an active source lease only with the exact lease token. A tokenless merge may
   clear an expired source lease. Nothing transfers to the destination.
10. An unresolved source human gate blocks merge. A destination gate may remain unresolved; the
    destination work-version increment makes its existing drift projection reflect the change.
11. The source must have no incident blocks or parent-child relationship. Phase 9 never rewrites,
    clones, redirects, or infers structural edges.
12. Existing discovered-from, related, and unselected duplicate-of evidence remains attached to the
    audit source. After merge, every source-incident relationship is frozen.
13. Every fresh alias mutation is rejected, including release. Historical same-key receipt replay
    occurs before current-state guards and remains recoverable.
14. Merge review binds to exact source and destination MergeReviewRevision objects. Any intervening
    work edit, checkpoint, progress, gate, relationship, or other event changes that revision.
15. The supporting relationship witness, relationship events when needed, merge row, two merge
    events, endpoint versions/activity, lease consumption, and receipt commit atomically once.
16. A same-key retry returns the original typed result after later graph changes and creates no
    second durable or domain effect.
17. Canonical-sensitive reads use a coherent database snapshot and fail closed on corrupt merge
    state. Raw exact history remains available for recovery, and privileged local audits provide
    diagnostics.
18. Default search and blank hierarchy browsing return canonical roots. Explicit alias/all/group
    views retain audit access, and canonical search reports which member supplied a match.
19. Advisory duplicate suggestions are a separate bounded safe read, group by canonical root, cover
    all lifecycle states, reveal categorical evidence rather than confidence scores, and never
    disable Create anyway.
20. Existing work, checkpoints, relationships, events, leases, gates, embeddings, receipts, IDs,
    timestamps, versions, bodies, hashes, and provenance survive migration without loss.
21. No feature automatically coalesces content, selects a root, transfers authority, redirects an
    ID, deletes a source, or repairs an erroneous merge.

## 2. Shipped baseline and constraints

### 2.1 Existing duplicate marks are weak facts

WorkRelationship currently permits duplicate-of alongside four other relationship types. A
duplicate mark has generic identity, endpoint provenance, optional checkpoint evidence, add/remove
operations, and paired events. It has no readiness, claim, lifecycle, search, hierarchy, or
canonical effect.

Production data may therefore contain:

- several outgoing duplicate marks from one work item;
- A -> B -> C chains or reciprocal/longer cycles;
- active, blocked, waiting, Deferred, or terminal endpoints;
- marks later removed through the shipped generic operation; and
- protected create/add/remove receipts containing duplicate-of.

None proves that a human selected a canonical destination. Migration must preserve every row and
must not reinterpret one.

### 2.2 Permanent receipts constrain wire changes

Phase 6 stores canonical successful JSON and validates it on replay. Completed rows may contain
WorkItemRead, WorkCreation, WorkCompletionRead, RelationshipCreationResult,
RelationshipRemovalResult, RelationshipEdgeRead, and WorkEventRead objects.

Do not add merge fields to any receipt-bearing Phase 1–8 shape. Canonical information belongs in new
unreceipted wrappers and the new WorkMergeResult. Internal event foreign keys must never enter
WorkEventRead, including as null keys.

Generic relationship request models continue to parse duplicate-of. The backend performs receipt
reservation/replay first and only then rejects a genuinely fresh generic execution with
duplicate_merge_required. This narrow ordering exists solely to preserve permanent receipts.

MCP must also continue to parse and dispatch these legacy-shaped calls exactly once. It cannot know
locally whether an operation UUID has a completed receipt. Tool descriptions forbid fresh generic
use, while the backend distinguishes replay from rejection. This is not a second supported domain
write path or a compatibility translator.

### 2.3 Human-gate revisions remain untouched

HumanGateContextRevision and all persisted gate revision columns remain byte-for-byte and
semantically unchanged:

~~~text
work_version
context_checkpoint_id                 # non-null UUID
relationship_event_count              # only the shipped four relationship event families
~~~

Do not rename this type, broaden its counter, change nullability, or change its OpenAPI schema.
Human gates capture their revision before their own request/resolution event; counting gate events
would make every gate drift because of itself. Populated-upgrade tests must compare live gate
projections before and after migration, not merely stored receipt JSON.

Phase 9 introduces a separate MergeReviewRevision:

~~~text
work_version                           # positive integer
context_checkpoint_id                  # non-null UUID
work_event_count                       # positive bigint count of committed immutable events
~~~

Every work has a work_created event and an initial checkpoint, so all three fields are non-null.
The event count is commit-visible and monotonic because events are insert-only; unlike the global
identity value, it does not assume sequence allocation order equals commit order. It makes progress,
checkpoint, gate, relationship, lifecycle, and arbitrary event activity stale a merge review
without changing historical gate semantics. WorkContext gains a top-level
merge_review_revision; existing gate objects continue to carry HumanGateContextRevision.

### 2.4 Existing semantic infrastructure is derived

semantic.py uses BAAI/bge-small-en-v1.5, bounded composed text, a disposable embedding cache, and
deterministic reciprocal-rank fusion. It does not persist duplicate decisions. Phase 9 reuses that
local model and cache, but suggestion request text and query vectors remain ephemeral. Only vectors
for existing work items may be cached.

The implementation SQL-bounds checkpoint extraction for both ordinary semantic search and
suggestions. Cache writes occur outside the coherent response snapshot and use bounded,
best-effort locking so cache contention cannot hold the response path indefinitely.

### 2.5 Existing catalog and client seams

- MCP has exactly 25 tools and ten receipt-protected writes.
- REST has twelve registered receipt kinds.
- The browser freezes ten mutation kinds in same-document memory.
- Readiness and claim already share an eligibility builder.
- Search uses limit/offset, not a cursor.
- Blank dashboard browse is structural root hierarchy.
- The proxy has a generic one-MiB streaming cap, but the backend has no suggestion-specific request
  concurrency, body, or inference controls.
- The plugin is 0.6.1 and the FastAPI application is 0.2.0.

Strict clients and generated contracts move together at each release boundary below.

## 3. Decisions fixed by this plan

### 3.1 Two ordered prerelease releases

Correctness is not coupled to an ML-backed advisory endpoint.

**Phase 9 Core — application/API 0.3.0, plugin 0.7.0**

- migration 0016_duplicate_handling;
- authoritative merge ledger and evidence constraints;
- merge_work REST route and MCP tool;
- alias freeze, readiness/claim exclusion, exact/canonical reads, search, hierarchy, browser merge,
  plugin semantics, recovery, and audits;
- 13 REST receipt kinds, 26 MCP tools, 11 MCP protected writes, and 11 browser mutations.

**Phase 9 Advisory — application/API 0.4.0, plugin 0.8.0**

- migration 0017_duplicate_suggestion_title_key, widening the Alembic revision column to retain the
  full descriptive head and adding only a derived normalization function and partial expression
  index to application tables;
- duplicate-suggestions REST route, suggest_duplicate_work MCP tool, and create-form comparison UI;
- explicit transport classification, resource controls, ranking contract, performance evidence,
  and privacy tests;
- 27 MCP tools and still 11 protected writes.

Core may ship without Advisory and has no dormant suggestion route or feature flag. Advisory may
ship in the same maintenance campaign only after Core is fully accepted, but it remains a separate
commit/release gate and can be deferred without weakening duplicate authority. The roadmap marks
Phase 9 Shipped only after both releases pass.

Each user-facing release is a MINOR bump under repository policy. No MAJOR bump is authorized.
Package metadata aligns with the application version at each release. There is no v2 compatibility
surface, runtime dual semantics, or long-lived flag.

### 3.2 Descriptive relationship versus authoritative merge

~~~text
duplicate-of relationship
  retained directional assertion/evidence
  no canonical effect by itself

work duplicate merge
  immutable reviewed decision selecting one exact duplicate-of relationship
  source becomes a retained, non-actionable alias
~~~

merge_work finds the exact source-to-destination duplicate relationship or creates it atomically.
If reused, the relationship keeps its original identity, actor, checkpoint evidence, timestamp, and
events. If created, the relationship and its paired add events carry a private same-transaction
merge witness.

After Core, fresh duplicate-of insertion through create_work initial relationships,
add_relationship, stale Phase 8 code, or direct SQL must fail unless it belongs to a complete
same-transaction authoritative merge. Existing marks are grandfathered because the deferred guard
applies only to new inserts. Unselected historical marks may still be removed while both endpoints
remain canonical.

There is no mark_duplicate tool, canonical setter, alternate route, or automatic promotion.

### 3.3 Immutable canonical forest and depth rule

Each merge has one source and one direct destination. A source may occur in at most one merge row
for all time. Merge rows are insert-only. Both endpoints must be distinct, visible, same-project
current roots when the decision commits. Incoming alias trees are allowed on either endpoint, and a
former root may later become a source:

~~~text
A -> B -> C
     ^
D ---|
~~~

All graph writers use the shipped lock_project_graph primitive: SELECT FOR UPDATE on the projects
row, followed by endpoint work rows in ascending UUID order. The plan does not introduce an
advisory lock.

The database and service validate both existing components with recursive CTEs carrying explicit
depth and a visited UUID array. Let source_reverse_depth be the longest incoming merge path ending
at the source, and destination_forward_depth be the outgoing path from destination. A valid
destination root has destination_forward_depth zero, but calculating it detects corruption. The
new source branch is valid only when:

~~~text
source_reverse_depth + 1 + destination_forward_depth <= 50
~~~

The destination component’s existing maximum reverse depth must also already be at most 50.
Cycles, repeated IDs, missing hops, project escapes, and a 51st edge fail closed. Required boundary
fixtures include 49-to-50 success, 50-to-51 rejection, branching, convergence, and a source that is
the tip of an incoming chain.

Phase 9 deliberately has no unmerge, split, retarget, merge deletion, or ordinary SQL bypass.
Before implementation, the product/operator owner must sign a decision record accepting permanent
aliases for Core. Each browser/tool workflow repeats that consequence before submission.

If an erroneous merge is reported:

1. quiesce writers and take a named backup;
2. preserve the operation UUID, aggregate audit result, and incident timeline outside chat/log
   sinks;
3. rehearse a whole-database restore from a pre-merge archive;
4. restore only with explicit two-person approval acknowledging loss of every later write; or
5. if later writes cannot be discarded, keep the merge immutable and ship a separately designed
   append-only correction release before changing canonical interpretation.

No bespoke UPDATE/DELETE data migration, partial-row restore, or ad hoc trigger bypass is a
supported correction.

### 3.4 Endpoint eligibility and retained provenance

The source may have any stored lifecycle: Pending, Deferred, done, wont-do, or promoted. Lifecycle
disagreement is displayed, not rejected. Under lock, the source must be:

- visible and a current root;
- at the exact reviewed MergeReviewRevision;
- free of unresolved human gates;
- free of incident blocks and parent-child relationships in either direction;
- free of an active lease unless the request presents its exact lease_token; and
- within the post-merge depth bound.

A tokenless merge clears an expired source lease. A supplied expired token returns lease_expired.
A missing or wrong token for an active lease uses the shipped lease_token_mismatch behavior. The
existing terminal-lease helper and error precedence are reused. No lease token is copied into
rationale, events, receipts, logs, errors, or responses.

The destination must be visible, a current root, and at its reviewed MergeReviewRevision.
Destination lifecycle, blockers, lease, and gate state remain authoritative but do not prevent
canonical selection. Its lease is never inspected, consumed, or transferred.

Relationship treatment is explicit:

| Relationship | Phase 9 treatment |
| --- | --- |
| blocks | caller adds any replacement and removes the source edge before merge |
| parent-child | caller explicitly reparents/removes before merge |
| discovered-from | retained on the audit source; checkpoint ownership never changes |
| related | retained as the source’s historical assertion |
| duplicate-of | selected edge supports merge; all other marks remain descriptive |

Once merged, no source-incident relationship can be inserted or removed. Every relationship row is
also update-immutable globally because relationships are facts; changing one requires the existing
remove/add operations while its endpoints are canonical.

### 3.5 Context-bound review

The merge request freezes complete source and destination MergeReviewRevision values returned by
exact WorkContext reads. Under the project and endpoint locks, the service recomputes both tuples.
Any difference returns 409 duplicate_context_changed, rolls back a newly reserved receipt, and
requires new contexts, a new explicit review, and a new operation UUID.

There is no force flag or acknowledgement that waives drift. Because work_event_count covers every
event family:

- progress-first makes a reviewed merge stale;
- merge-first makes subsequent progress fail with work_duplicate;
- gate request/resolution, relationship activity, and checkpoint activity stale the review;
- lease-only activity is handled by the separately locked lease state/token; and
- the merge’s own events occur after the comparison.

Merge increments both endpoint work versions once, assigns one database timestamp to both
updated_at values and all newly created merge facts, and records reviewed versions/event counts plus
resulting versions. A concurrency fixture allocates a lower global event identity, commits a higher
identity, captures the review, and then commits the lower identity; the committed count must drift
even though MAX(id) would not.

### 3.6 Derived duplicate state and mutation matrix

Readiness gains:

~~~text
is_duplicate: boolean
canonical_work_item_id: UUID
~~~

A root points to itself. An alias is never ready regardless of lifecycle, lease expiry, gate
resolution, or blockers.

Fresh operations on an exact alias behave as follows:

| Operation | Result |
| --- | --- |
| direct raw checkpoint/event/gate/relationship history reads | allowed and source-owned |
| canonical-sensitive work/context/summary read | allowed when graph valid; explicit projection |
| default search, root browse, ready list | omitted as an independent work item |
| explicit alias/all/group search | returned as audit work |
| claim, replacement claim, renew, or release | 409 work_duplicate; never redirect |
| checkpoint, progress, arbitrary event | 409 work_duplicate |
| gate request or fresh gate resolution | 409 work_duplicate |
| update, defer, reopen, complete, retire, promote, delete | 409 work_duplicate |
| add relationship through either alias endpoint | 409 work_duplicate |
| remove any alias-incident or chosen relationship | 409 duplicate_relationship_frozen |
| merge_work on an alias | 409 work_already_duplicate |

For relationship add with two alias endpoints, validate project visibility, then source, then target;
the first visible alias supplies the bounded work_duplicate context. Removal addresses an existing
relationship fact and therefore uses duplicate_relationship_frozen.

Receipt replay always precedes these fresh-state checks. A historical create, checkpoint, event,
relationship, gate, lease, or work mutation can replay even though a new execution now fails.

### 3.7 Canonical reads, bounded context, search, and hierarchy

Unreceipted read models use:

~~~text
CanonicalWorkProjection
  is_duplicate
  direct_destination: WorkIdentityPointer | null
  canonical_work_item: WorkIdentityPointer
  path: WorkIdentityPointer[]
  duplicate_member_count: integer
~~~

WorkIdentityPointer is the existing exact type: id, title, and status. Do not alter it or add
updated_at. A root has direct_destination null, points canonical_work_item to itself, and has
path=[]. An alias path contains hops after the requested item and ends with the canonical root.
Every path has at most 50 entries.

There is no path_truncated or partial public corruption shape. A cycle, missing root, project escape,
or excessive path makes every canonical-projected public read fail with 503
duplicate_graph_invalid. Exact raw checkpoint/event/gate/relationship pages remain readable because
they need no canonical projection. A privileged local audit reports bounded diagnostic IDs to the
operator terminal only.

WorkItemDetailRead wraps the receipt-safe WorkItemRead with canonical. WorkContext additionally
contains:

- merge_review_revision;
- duplicate_members, containing strict aliases only;
- duplicate_member_total and omitted_duplicate_member_count;
- at most 20 member pointers;
- the requested alias first when an alias context is requested, then merge_sequence/UUID order;
- at most 100 incoming, 100 outgoing, and 100 undirected relationships, each in
  created_at/relationship-ID order;
- relationship_counts as exact totals and omitted_relationship_counts as derived exact omissions;
  and
- DuplicateMergeEligibility with exact incident_blocks_count,
  incident_parent_child_count, has_unresolved_gate, and source_lease_state
  (none, expired, or active).

The eligibility projection contains no lease holder or token and is advisory to the UI; merge
rechecks under lock. Full relationships remain available through limit/offset relationship pages.
Context always retains the requested item’s checkpoints, events, gates, and relationships and never
embeds a root checkpoint body.

Search keeps limit/offset and adds:

~~~text
duplicate_scope = canonical | aliases | all       # default canonical
canonical_work_item_id = UUID | null
~~~

Rules:

- canonical scope returns one current root per group;
- alias text may nominate its root, but every result is a WorkSearchHit containing the returned
  WorkSummary and matched_member: WorkIdentityPointer;
- for a nonblank text query, matched_member is the exact group member with the winning text score,
  breaking member ties by updated_at descending and UUID ascending;
- for blank or filter-only search, matched_member equals the returned root/row, so the field is
  always defined without inventing text provenance;
- aliases returns source rows; all returns roots and aliases separately;
- total counts canonical groups in canonical scope and returned rows in aliases/all scope;
- grouping and ranking occur before OFFSET/LIMIT inside one coherent response snapshot;
- filters apply to the row returned, except that text matching may identify matched_member;
- canonical_work_item_id is accepted only with aliases/all and must name a visible current root;
- a visible alias filter returns work_duplicate, while absent/deleted/cross-project values use the
  sanitized 404 precedence; and
- later offset pages may shift when concurrent requests commit between page requests, matching the
  shipped offset contract.

Blank hierarchy excludes aliases. Merge never becomes a structural edge.
HierarchyPresentation gains branch_merged_duplicate_count, calculated in one aggregate over visible
structural descendants and their canonical groups. Exact alias hierarchy roots return
work_duplicate.

### 3.8 Advisory duplicate suggestions

Advisory adds:

~~~text
POST /api/v1/projects/{project_id}/duplicate-suggestions
~~~

Strict request fields are title, summary, initial_prompt, tags (default []),
exclude_work_item_id (default null), and limit (default 5, maximum 10). Text/tag character limits
and normalization match valid create-work draft fields; the suggestion service does not silently
truncate the request. No provenance, operation UUID, lease token, relationship, canonical choice,
or create flag is accepted. exclude_work_item_id must be visible and excludes its complete current
group.

The purpose-built response is:

~~~text
DuplicateSuggestionPage
  items: DuplicateSuggestion[]
  limit
  mode: hybrid_full | hybrid_shortlist | lexical
  semantic_available: boolean
  semantic_scope: full_project | lexical_shortlist | unavailable
  composition_version
  exact_title_group_total
  omitted_exact_title_group_count

DuplicateSuggestion
  canonical_work: DuplicateCandidateSummary
  matched_member: WorkIdentityPointer
  rank: integer
  signals: [exact_title | lexical | semantic]

DuplicateCandidateSummary
  work_item_id
  title
  summary
  status
  updated_at
  duplicate_member_count
~~~

The candidate summary deliberately excludes Readiness, checkpoint provenance, lease holder/session,
gate detail, checkpoint bodies, actor data, raw scores, vectors, and merge controls. Signals are a
closed ordered enum set, not floats, probabilities, thresholds, or confidence.
Candidate title and summary are exact stored strings; response validation does not apply the
create-draft trim or normalization rules to retained work.

Candidate selection is frozen as duplicate-suggestion-v1:

1. Migration 0017 defines tested IMMUTABLE PostgreSQL-17 function
   mnemonic_duplicate_title_key_v1(text). It applies normalize(..., NFKC), trim, collapsed POSIX
   whitespace, and lower under the C collation. The deliberately narrow v1 semantics are
   normalization plus ASCII case-insensitivity, not Unicode full case-folding.
2. Migration 0017 adds partial expression index
   (project_id, mnemonic_duplicate_title_key_v1(title), id) WHERE deleted_at IS NULL. The request
   key uses the identical SQL function. No title key is stored as authoritative content.
3. Run the global indexed exact-title lane over every visible member. Group by current root before
   ordering. Exact groups fill response slots first; total/omitted fields reveal exact groups beyond
   the public limit.
4. Build lexical text from title (PostgreSQL weight A=1.0), summary (B=0.4), normalized tags
   (B=0.4), initial prompt’s first 1,500 characters (C=0.2), and the last 1,500 characters across
   later checkpoints (D=0.1). PostgreSQL ranks member rows, canonicalizes them, keeps the best member
   per group, and retains the top 200 non-exact groups.
5. Build semantic text in the same field order with model BAAI/bge-small-en-v1.5, query prefix
   already shipped, batch size 16, RRF K=60, and lexical fusion weight 3.0. Tags are included. The
   exact composition string, title-key version, model name, dimensions, and weights form the cache
   version.
6. When the project has at most 10,000 visible members and all current candidate vectors are
   cached, dense ranking covers the full project and mode is hybrid_full.
7. Otherwise, semantic work is limited to the 200 lexical groups and at most 128 missing member
   vectors computed in that request; mode is hybrid_shortlist. A project above 10,000 never claims
   global semantic coverage.
8. SQL selects the initial prompt and bounded checkpoint tail directly; it never loads every
   checkpoint body and truncates later in Python.
9. Group before the public limit, use the best member per root, and order by exact lane, RRF rank,
   canonical updated_at descending, then canonical UUID ascending. Member ties use updated_at
   descending and UUID ascending.
10. One process-wide inference gate is shared with ordinary semantic search. Suggestions wait at
    most 50 ms for it, then return deterministic lexical success. Model load, inference, vector
    validation, or cache use also falls back to lexical with semantic_available false.
    Database/scope failures remain explicit.

The draft vector and suggestion result are never persisted. Existing-work cache upserts use a
separate compare-by-digest transaction after the response candidate snapshot. Work-row locks are
skip-locked, and cache lock waits are capped at 50 ms within the remaining request deadline. No
work, relationship, event, receipt, version, activity, invalidation, or live-sync message is
created. Create remains enabled for loading, empty, stale, lexical, busy, and unavailable
suggestion states.

### 3.9 Merge history, result, and durable idempotency

Add server-reserved work_merged events, one on source and one on destination. Both use actor_kind
client, origin live, the rationale as body, one database timestamp, and strict metadata version 1:

~~~text
merge_id
source_work_item_id
destination_work_item_id
role = source | destination
source_work_version
destination_work_version
~~~

The event on each endpoint has the matching role. Merge increments both endpoint versions once and
activity timestamps once. It does not alter lifecycle/checkpoint content.

Every merge request requires client_operation_id, including direct REST. Optional idempotency is not
acceptable for an irreversible operation. The Phase 6 target envelope is project plus exact source
path; the canonical request includes every body field except no hidden/default transformations.

WorkMergeResult has fixed fields and ordering:

~~~text
merge
source_work_item
destination_work_item
direct_destination
canonical_work_item
supporting_relationship_created
supporting_relationship
relationship_events     # [] or exactly [source endpoint, destination endpoint]
merge_events            # exactly [source role, destination role]
~~~

relationship_events is length two exactly when the private relationship witness names this merge
and empty for a reused mark. Cross-field validators bind endpoints, IDs, roles, timestamp, actor,
versions, Boolean, and lengths. Checked canonical JSON, digest, and response-v1 vectors freeze the
contract before service implementation.

Completed replay validates and returns this original result without consulting current source,
destination, path, lease, gate, relationship, or root state. A client then performs a current exact
read when it needs current authority.

### 3.10 Authority and privacy boundary

The shared bearer authorizes API access; merged_by_client, merged_by_session_id, and merged_by_model
remain asserted provenance, not verified identity. Similarity, existing marks, model output, and
stored prose are evidence only.

Before receipt reservation, substring-aware scanning rejects every durable merge text field
(rationale, merged_by_client, merged_by_session_id, and merged_by_model) if it contains the actual
bearer token, presented lease token, or any canonical textual spelling of client_operation_id.
The public field is named lease_token so existing designated-secret handling also applies. The
browser never accepts or forwards this field.

Operation UUIDs, tokens, request bodies, identifiers, titles, rationale, candidate text, raw scores,
vectors, actors, and sessions never enter logs or metric labels. MCP ambiguity guidance reuses the
current redacted text byte-for-byte and never echoes the operation UUID; callers recover it from
private orchestration state.

## 4. Requirement identifiers

Implementation tests and review evidence use these stable IDs:

| ID | Requirement |
| --- | --- |
| DH-C01 | Historical duplicate marks and Phase 1–8 receipts remain unchanged and no merge is inferred |
| DH-C02 | One insert-only ledger row selects one exact immutable source-to-destination mark |
| DH-C03 | Source uniqueness, project scope, roots, acyclicity, and the corrected 50-edge bound hold |
| DH-C04 | Project-row and sorted-endpoint lock order serializes all graph writers |
| DH-C05 | Source gate, lease, structural-edge, visibility, and MergeReviewRevision checks are atomic |
| DH-C06 | Supporting relation creation has a provable same-transaction witness and complete events |
| DH-C07 | Every fresh duplicate mark requires a same-transaction merge; legacy rows are grandfathered |
| DH-C08 | Alias domain facts and all relationship facts are immutable under stale writers/direct SQL |
| DH-C09 | Readiness and every shipped claim path exclude aliases without ID substitution |
| DH-C10 | Exact history and explicit canonical projections preserve ownership and fail corrupt reads closed |
| DH-C11 | Canonical search/hierarchy group before limit/offset in a coherent snapshot |
| DH-C12 | merge_work has mandatory durable idempotency and an exact fixed response |
| DH-C13 | HumanGateContextRevision and old event metadata validation retain historical meaning |
| DH-C14 | MCP legacy receipt replay, protected counts, strict schemas, and redacted recovery remain valid |
| DH-C15 | Browser direction, bidi isolation, frozen intent, proxy policy, and recovery are safe |
| DH-C16 | Quiesced migration preserves production content and recovery is validated before traffic |
| DH-A01 | Suggestions are bounded, canonical-grouped, lifecycle-complete, advisory, and inert |
| DH-A02 | Indexed PostgreSQL-17 title-key lookup is global; semantic scope and lexical shortlist are explicit |
| DH-A03 | Direct API body/concurrency/inference controls prevent resource amplification |
| DH-A04 | Candidate responses expose no readiness capability, provenance, raw score, or draft persistence |
| DH-A05 | Suggestion POST is classified as a safe read by MCP, proxy, live sync, and retry policy |
| DH-X01 | Core 0.3.0/plugin 0.7.0 and Advisory 0.4.0/plugin 0.8.0 are separate acceptance gates |
| DH-X02 | Product/operator permanence sign-off precedes implementation |

## 5. Persistence and database invariants

### 5.1 Migration 0016 and authoritative table

Migration 0016 creates an empty work_duplicate_merges table:

| Column | Type/rule |
| --- | --- |
| id | application-generated UUID primary key |
| merge_sequence | BIGINT GENERATED ALWAYS AS IDENTITY, unique |
| project_id | non-null UUID, project FK RESTRICT |
| source_work_item_id | non-null UUID |
| destination_work_item_id | non-null UUID |
| duplicate_relationship_id | non-null UUID, unique |
| duplicate_relationship_type | non-null literal duplicate-of |
| reviewed_source_work_version | positive integer |
| reviewed_source_context_checkpoint_id | non-null UUID |
| reviewed_source_work_event_count | positive bigint |
| reviewed_destination_work_version | positive integer |
| reviewed_destination_context_checkpoint_id | non-null UUID |
| reviewed_destination_work_event_count | positive bigint |
| resulting_source_work_version | reviewed source version plus one |
| resulting_destination_work_version | reviewed destination version plus one |
| rationale | nonblank VARCHAR(4000) |
| merged_by_client | nonblank VARCHAR(80) |
| merged_by_session_id | nonblank VARCHAR(200) |
| merged_by_model | nullable, otherwise nonblank VARCHAR(120) |
| created_at | non-null timestamptz supplied from one captured database clock value |

Required keys and constraints:

- unique (project_id, source_work_item_id);
- scoped source/destination work-item FKs with RESTRICT;
- scoped reviewed-checkpoint FKs owned by their respective endpoints;
- source differs from destination;
- positive revision/result fields and exact plus-one result versions;
- unique (project_id, id) on merges for scoped internal FKs;
- unique
  (project_id, id, type, source_work_item_id, target_work_item_id) on work_relationships;
- composite merge FK
  (project_id, duplicate_relationship_id, duplicate_relationship_type,
  source_work_item_id, destination_work_item_id) to that exact relationship tuple; and
- RESTRICT throughout.

Indexes:

- the unique source index is the readiness/claim anti-join probe;
- (project_id, destination_work_item_id, merge_sequence, id) supports reverse traversal;
- (project_id, merge_sequence, id) supports stable audit pagination; and
- existing work/event/checkpoint indexes support revision and path queries, proven with EXPLAIN.

Do not denormalize canonical_work_item_id, is_duplicate, or member count onto work_items.

### 5.2 Same-transaction supporting evidence

Add private nullable created_for_duplicate_merge_id to work_relationships with a unique scoped
deferred FK to work_duplicate_merges. Reused historical relationships retain null. A relationship
created by merge carries the pre-generated merge UUID.

Add private nullable created_for_duplicate_merge_id to the two relationship_added work events
created with that relationship. Add private nullable work_duplicate_merge_id to work_merged events.
Both columns are internal and excluded from public projection.

Deferred completeness triggers require:

- a new duplicate-of relationship has a non-null witness and a matching merge in the same commit;
- the witnessed relationship is exactly the merge’s chosen source/destination edge;
- a witnessed relationship has exactly two witnessed relationship_added events, ordered/owned by
  source and destination, with the same relationship, actor, checkpoint evidence, and timestamp;
- service/result validation derives supporting_relationship_created=true from a witnessed
  relationship and false from a reused null-witness edge; no redundant Boolean is stored;
- every merge has exactly two matching work_merged events; and
- no witness can be reused.

The circular relationship/merge FKs are DEFERRABLE INITIALLY DEFERRED so one transaction can build
the complete fact set. Tests force the named domain constraints after constructing the complete
domain facts. The live merge path completes its receipt before SET CONSTRAINTS ALL IMMEDIATE,
because the existing deferred receipt trigger correctly rejects a pending receipt at transaction
end. Timestamps or matching provenance alone are never treated as proof of transaction identity.

### 5.3 Insert guard and graph serialization

The BEFORE INSERT merge trigger:

1. locks the exact project row FOR UPDATE, using the same key as lock_project_graph;
2. locks source/destination work rows in ascending UUID order;
3. verifies visibility, project scope, distinct endpoints, and root status;
4. verifies source has no outgoing merge, unresolved gate, incident block/parent edge, or lease;
5. verifies each current checkpoint head and committed work-event count still equals its reviewed
   value; the current endpoint versions have already advanced for the merge;
6. validates the exact supporting relationship composite FK;
7. recursively validates both components with visited arrays;
8. calculates source reverse depth and destination forward depth and enforces the formula in
   Section 3.3;
9. requires current version == resulting version == reviewed version + 1 and requires endpoint
   updated_at == merge created_at; and
10. verifies no witnessed relationship/merge events exist yet, leaving their later
    same-transaction completeness to deferred triggers.

The trigger is a database backstop. The service raises specific safe domain errors first. Constraint
translation never returns raw SQL text or identifiers.

UPDATE or DELETE of a merge always fails. There is no application session flag or privileged
runtime bypass.

### 5.4 Relationship, alias, and stale-writer guards

Database guards enforce:

- every work_relationships UPDATE fails, for legacy and new rows;
- every post-0016 duplicate-of INSERT must have a complete same-transaction merge witness;
- relationship INSERT/DELETE involving an alias fails;
- chosen/witnessed relationship DELETE fails;
- alias work_items UPDATE/DELETE fails;
- alias checkpoint, lease, or human-gate INSERT/UPDATE/DELETE fails;
- user-authored alias event INSERT fails;
- source/destination work deletion fails while referenced; and
- merge and witnessed event deletion fails.

Legacy relationships are grandfathered without a backfill flag: the new-insert deferred trigger
simply never runs on existing rows. Fresh application paths pre-reject duplicate-of with
duplicate_merge_required after replay lookup.

Single-work guard triggers lock the exact work row before testing alias state. Relationship and merge
writers acquire project row then UUID-sorted endpoint rows. Thus mutation-first either commits and
changes MergeReviewRevision before merge, or merge-first causes the stale write to see the alias.
No path takes a work row and later reacquires the project row.

### 5.5 Event extension without legacy validator drift

Add work_merged to the event type CHECK and the two private internal merge columns described above.
Do not change mnemonic_work_event_metadata_v2_is_valid. Freeze its normalized function definition
hash and every accepted/rejected Phase 1–8 vector before and after upgrade.

The replacement table CHECK has explicit branches:

- existing gate events retain their shipped gate validation;
- work_merged calls a new mnemonic_work_merged_metadata_v1_is_valid function;
- all other historical event families continue to call the byte-identical v2 function.

work_merged requires live/client actor, nonblank body up to 4,000 characters, no checkpoint/lease/
relationship/gate references, exact metadata keys, matching endpoint role, and a unique source role
and destination role per merge.

Every SQL query that serializes work_events must select the explicit public column list. Remove
event.*, composite-row to_jsonb, and “subtract one private key” projections. Audit context, event
pages, receipts, migrations, and tests so neither internal merge column appears, even as null.

### 5.6 Receipt registry

Widen operation_kind only to add merge_work. Preserve request_fingerprint_version 1,
response_contract_version 1, pending/completed semantics, uniqueness, retention, and all twelve
existing validators.

Add the thirteenth closed MergeWorkReceiptSpec and checked request/response canonical/digest vectors.
client_operation_id is mandatory at the REST schema. Historical generic duplicate receipts replay
before new execution rejection. Pending-row uncertainty remains the shipped 503
client_operation_unavailable contract.

### 5.7 Migration treatment and parity

Migration creates zero merge rows and never derives one from relationships, text, status,
embeddings, checkpoints, UUID order, or timestamps. Existing descriptive cycles/multi-targets remain
valid.

Before/after populated fixtures compare:

- every Phase 1–8 table count and stable row digest;
- all IDs, versions, timestamps, bodies, hashes, provenance, receipt JSON, and embedding rows;
- every completed receipt replay;
- live HumanGateRead projections for unresolved and resolved gates;
- context/event reads containing each historical event family; and
- zero work_duplicate_merges and zero non-null evidence witnesses.

SQLAlchemy metadata parity covers tables, columns, types, nullability, defaults, identities, keys,
constraints, and indexes. Separate pg_trigger/pg_proc catalog assertions freeze trigger timing,
events, deferrability, enabled state, referenced function, and normalized function-body hashes.
Fresh zero-to-head and populated 0015-to-0016 schemas must match.

Migration 0016 has no supported downgrade.

## 6. Backend domain and transaction design

### 6.1 Focused ownership

services/duplicates.py owns:

- bounded canonical resolution and reverse-member traversal;
- batched projections and member counts;
- merge-review revision computation;
- merge execution and constraint translation;
- canonical grouping helpers; and
- Advisory-only suggestion selection/grouping.

Readiness, work context, search, hierarchy, relationships, leases, gates, events, and work mutations
reuse focused builders. Do not copy recursive SQL or alias predicates. Authority-sensitive
resolution uses recursive CTEs with visited arrays and fails closed.

### 6.2 Exact merge request and response

Strict WorkMergeCreate requires:

- destination_work_item_id;
- reviewed_source_revision: MergeReviewRevision;
- reviewed_destination_revision: MergeReviewRevision;
- rationale;
- merged_by_client;
- merged_by_session_id;
- optional merged_by_model;
- optional lease_token, repr-hidden; and
- client_operation_id, required and repr-hidden.

There is no force, auto_redirect, redirect_relationships, suppress_warning, expected root,
client-selected merge ID, or optional idempotency.

WorkMergeRead is the immutable merge fact without capability/receipt internals. WorkMergeResult uses
the exact fields and two named event arrays in Section 3.9.

### 6.3 Atomic merge algorithm

merge_work runs in this order:

1. Strictly validate and normalize. Substring-scan every durable text field against the bearer,
   presented lease token, and every canonical textual spelling of client_operation_id. Build the
   project/source target envelope, then reserve or replay the receipt.
2. On completed replay, validate and return the frozen result immediately.
3. Lock the projects row through lock_project_graph.
4. Lock source and destination work rows in ascending UUID order; absent/deleted/cross-project uses
   one sanitized 404.
5. Reject self, resolve both components, require two current roots, and calculate the depth rule.
6. Recompute both MergeReviewRevision tuples and compare exactly.
7. Check exact source gate and structural conflict counts.
8. Lock the source lease. Reuse the terminal-mutation helper: tokenless expired cleanup, exact active
   token consumption, and shipped lease errors. Do not inspect destination lease.
9. Pre-generate merge UUID and capture one database merge_time.
10. Lock/find the exact duplicate-of edge. If absent, call an explicitly already_locked
    relationship-row helper that cannot reserve a receipt, reacquire project/work locks, stage
    events, or commit. Insert only the witnessed relationship row with merge UUID and merge_time.
11. Increment each endpoint version once and set both updated_at values to merge_time.
12. Insert the merge row with reviewed and resulting fields. Its trigger observes unchanged
    checkpoint heads/event counts and post-update endpoint versions.
13. If the relationship was created, insert exactly two narrowly permitted witnessed
    relationship_added events. Then insert exactly two narrowly permitted work_merged events. All
    four use merge_time and the exact endpoint/actor/evidence rules.
14. Build and validate WorkMergeResult, complete the receipt as 201 with mutation_applied true,
    execute SET CONSTRAINTS ALL IMMEDIATE, and commit once.
15. Publish one existing data-free project invalidation only after commit.

Any precommit failure rolls back every domain fact and the new pending reservation under the
established registry cleanup behavior. No helper commits internally.

### 6.4 Lock order and race outcomes

Global graph order:

~~~text
receipt reservation
project row FOR UPDATE
work rows in ascending UUID order
source lease row
existing supporting relationship row
new relationship/merge/event rows
receipt completion
~~~

The already_locked relationship helper starts after project/endpoints are held. Direct-SQL merge
trigger takes the same project row and endpoint order. Freeze this mixed schedule with barriers:
relationship writer holds project row and waits for a work row while merge starts; the design must
serialize without deadlock.

Required outcomes:

| Race | Outcome |
| --- | --- |
| two destinations for one source | one succeeds; one work_already_duplicate |
| A->B versus B->A | one succeeds; other root/alias conflict; never cycle |
| A->B versus B->C | serial valid forest or stale review |
| merge versus claim/renew/release | mutation-first lease result is observed; merge-first gets alias rejection |
| merge versus checkpoint/edit/gate/relationship | mutation-first changes revision/precondition; merge-first rejects stale mutation |
| merge versus progress | progress-first returns duplicate_context_changed; merge-first makes progress work_duplicate |
| merge versus source gate resolution | resolution-first stales; unresolved-first blocks merge |
| merge versus delete | delete or merge wins safely; never dangling FK |
| lost merge response | same key/request returns exact original result |

There is no next-ready operation in Phase 8 or Phase 9; do not design or test one here.

### 6.5 Alias guard and readiness

One shared service guard accepts project, work ID, operation category, and lock state. Operation
category is authoritative: ordinary alias mutations and relationship additions return
work_duplicate with only the current canonical ID; relationship removal performs receipt replay and
scoped relationship lookup first, then returns duplicate_relationship_frozen with no canonical
substitution. Apply the guard to every fresh checkpoint, event, lease, work mutation, gate
operation, relationship add/remove, delete, and create-time initial relationship execution.

Precedence is:

1. authentication and syntax;
2. receipt conflict/replay;
3. project-scoped visibility;
4. alias state;
5. capability/version/revision;
6. operation domain rule.

Extend the shared readiness selectable with a NOT EXISTS over the unique merge-source index. Use it
for ready list, fresh exact claim, expired replacement claim, and readiness embedded in summaries.
No shipped next-ready path exists. Explicit alias claim returns work_duplicate; lists omit it.

### 6.6 Coherent projected reads

The default engine remains READ COMMITTED, so a mere sequence of “batched” queries is insufficient.
Every response that combines canonical qualification, grouping, totals, pagination, pointers, or
readiness first captures one database transaction_timestamp() as as_of and binds that exact value
through every eligibility, lease-expiry, readiness, and serialization calculation. It must then use
either:

- one SQL statement producing the complete response snapshot and one as_of CTE; or
- a dedicated REPEATABLE READ transaction started before the first qualifying query and ended only
  after all response rows are captured.

This applies to search, context, hierarchy, relationship pages with counterpart summaries,
attention summaries, ready summaries, and duplicate suggestions. Repeatable read freezes rows, while
the shared as_of freezes time semantics. No embedding-cache commit occurs inside or halfway through
that snapshot.

For semantic reads:

1. acquire the shared process-wide inference capacity before opening a database transaction;
2. capture one database as_of plus all candidate IDs, bounded text, canonical group facts,
   summaries, lease/readiness facts, and valid cached vectors in one repeatable-read snapshot;
3. close the read transaction;
4. calculate rankings over the captured immutable data;
5. upsert newly derived candidate vectors in a separate short transaction with digest/version
   compare-and-set; and
6. serialize only captured snapshot fields.

Barrier tests merge work between each former query boundary and expire a lease between
qualification/enrichment statements. They prove one response never mixes roots, mismatches
total/items, or evaluates readiness at two times.

Ordinary semantic search now composes the first 1,500 initial-prompt characters and a SQL-bounded
1,500-character later-checkpoint tail without aggregating an unbounded prompt history. Its derived
cache refresh is post-snapshot and best effort: locked work rows are skipped, lock waits are capped
at 50 ms, statements at five seconds, and those bounded expirations retain the computed ranking.

### 6.7 Suggestion resource controls

Advisory adds request controls and shares the model gate with ordinary semantic search:

| Control | Frozen default |
| --- | --- |
| authenticated streaming body cap | 2,097,152 bytes |
| suggestion requests per API process | 4 |
| request-slot wait | 250 ms |
| model inference slots per process | 1, shared with ordinary semantic search |
| inference-slot wait | 50 ms; suggestion lexical fallback, semantic-search 503 |
| lexical canonical-group shortlist | 200 |
| missing vectors computed per request | 128 |
| full semantic population ceiling | 10,000 visible members |
| response candidate maximum | 10 |
| transport timeout budget | 60 seconds |

The two-MiB cap exceeds the checked worst-case JSON serialization of a valid creation draft,
including a 100,000-code-point astral initial prompt encoded as surrogate-pair escapes plus maximum
title, summary, tags, UUID, and JSON framing. A contract test computes that maximum from the creation
schema and fails if future valid limits can cross the cap. Enforce it before JSON parsing for
Content-Length, missing length, and chunked transfer. Oversize returns 413
request_body_too_large. A saturated request queue returns 429
duplicate_suggestion_busy with Retry-After: 1. Model saturation/failure returns lexical 200.
The same saturated inference gate returns semantic_unavailable for a valid ordinary semantic
search; lexical search remains available. Database/system suggestion failure returns 503
duplicate_suggestion_unavailable.

Configuration exposes namespaced MNEMONIC_DUPLICATE_SUGGESTION_* settings with these defaults and
validated safe ranges. Authentication occurs before work; a suggestion request slot and the shared
inference capacity are acquired before any database session. One absolute suggestion deadline
starts before body handling and spans inference and application work. On PostgreSQL 17, the
coherent snapshot and cache transaction set route-relative transaction, statement, and lock
timeouts from its remaining budget; cache locking is further capped at 50 ms. Metrics are aggregate
only.

### 6.8 Cache and publication

Canonical roots are query-derived; add no cross-request canonical cache. Embedding rows are
versioned disposable cache and may change on aliases after merge. This is not an authoritative alias
mutation. Suggestion cache refresh skip-locks contended work rows and cannot wait more than 50 ms on
a cache lock. Ordinary semantic refresh uses the same skip-locked pattern, a 50 ms lock timeout,
and a five-second statement timeout; bounded lock/statement expiry leaves its ranked response
intact. Suggestion and search cache writes publish no live-sync signal.

Merge publishes one bounded project refresh with no IDs/content. Failed/replayed transactions
publish nothing new.

## 7. REST and OpenAPI contract

### 7.1 Core routes and revised reads

Core adds:

| Method/path | Contract | Status | Effect |
| --- | --- | --- | --- |
| POST /api/v1/projects/{project_id}/work-items/{source_work_item_id}/merge | WorkMergeCreate -> WorkMergeResult | 201 or original replay | receipt-protected write; one postcommit invalidation |

Advisory later adds:

| Method/path | Contract | Status | Effect |
| --- | --- | --- | --- |
| POST /api/v1/projects/{project_id}/duplicate-suggestions | DuplicateSuggestionRequest -> DuplicateSuggestionPage | 200 | safe read; derived cache only |

There is no redirect, merge-on-create, setter, unmerge, mark route, or compatibility namespace.

Core changes unreceipted reads:

- direct work GET returns WorkItemDetailRead {work_item, canonical};
- WorkContext gains merge review, canonical/group, bounded relationships, and eligibility;
- search returns WorkSearchHit items and duplicate filters;
- summary/readiness/hierarchy/relationship counterpart projections become canonical-aware; and
- raw checkpoint/event/gate/relationship page item shapes remain unchanged.

Every changed model is classified against the receipt registry before implementation.

### 7.2 Strict schemas

WorkIdentityPointer remains id/title/status. MergeReviewRevision, CanonicalWorkProjection,
DuplicateMergeEligibility, WorkItemDetailRead, WorkSearchHit, WorkMergeCreate/Read/Result, filters,
and event metadata are strict with extra fields forbidden.

Advisory schemas use the exact purpose-built shapes in Section 3.8. Validate signal order/no
duplicates, mode/scope/semantic coherence, rank sequence, group uniqueness, exact counts, item
limit, timestamps, and endpoint identity.

### 7.3 Error and precedence contract

| Code | HTTP | Meaning |
| --- | --- | --- |
| duplicate_merge_required | 409 | fresh generic duplicate mark is closed |
| duplicate_self | 409 | identical source/destination |
| work_duplicate | 409 | exact target/filter/add endpoint is an alias |
| work_already_duplicate | 409 | merge source already has an authoritative destination |
| duplicate_destination_not_canonical | 409 | destination has gained an outgoing merge |
| duplicate_context_changed | 409 | a reviewed MergeReviewRevision changed |
| duplicate_source_gate_unresolved | 409 | resolve the source gate, including “no longer needed,” then reread |
| duplicate_structural_relationships | 409 | reconcile source blocks/parent-child edges |
| duplicate_depth_exceeded | 409 | new path would exceed 50 |
| duplicate_relationship_frozen | 409 | retained/chosen relationship cannot be removed |
| duplicate_graph_invalid | 503 | persisted canonical graph is corrupt; stop authority-changing work |
| duplicate_suggestion_busy | 429 | bounded safe-read queue saturated; retry later |
| request_body_too_large | 413 | direct request crossed the route cap |
| duplicate_suggestion_unavailable | 503 | suggestion database/system path unavailable; creation remains independent |

Reuse exact shipped client_operation_conflict, client_operation_unavailable,
lease_token_mismatch, lease_held, lease_expired, validation, and sanitized 404 envelopes. Do not
invent idempotency_in_progress, lease_token_required, or lease_conflict aliases.

work_duplicate safe context contains only canonical_work_item_id. duplicate_graph_invalid exposes no
IDs/path. Cross-project/deleted lookup resolves to sanitized 404 before alias details.

### 7.4 Transport effect metadata

HTTP method alone does not define retry safety. Backend OpenAPI extensions, MCP client calls, and
browser proxy policy use one closed effect enum:

- safe_read;
- receipt_protected_write; and
- lease_claim.

Suggestion POST is safe_read: timeouts/5xx are retryable without structural uncertainty and receive
the 60-second semantic budget. Merge is receipt_protected_write: one dispatch, then same-key frozen
recovery. Existing claim semantics remain lease_claim. Live-sync mutation classification explicitly
returns none for duplicate-suggestions.

### 7.5 Version artifacts

Core OpenAPI advertises 0.3.0, one new route, mandatory merge operation UUID, strict projections,
and duplicate defaults. Advisory OpenAPI advertises 0.4.0 and adds only the suggestion route/schemas
plus safe-read metadata.

Generate docs/openapi.json from each implemented application release. Contract tests compare
backend, MCP, frontend, error enums, paths, strict models, tool counts, protected counts, and version
values.

## 8. MCP adapter and plugin

### 8.1 Core catalog

Core adds merge_work:

| Tool | Classification | Behavior |
| --- | --- | --- |
| merge_work | destructive, receipt-protected, closed-world | mandatory operation UUID, one dispatch, exact replay |

Core has exactly 26 tools and 11 protected writes. merge_work has destructiveHint true.

Existing create_work and add_relationship input models still accept duplicate-of and dispatch once.
Descriptions say fresh use returns duplicate_merge_required and direct users to merge_work, but MCP
must not reject locally. Tests cover completed create initial-relationship replay, completed
add_relationship replay, and fresh backend rejection.

The MCP API client marks merge requests receipt_protected_write. It never redirects or internally
retries. Ambiguous failures use the current redacted guidance verbatim, without the actual operation
UUID, token, body, or IDs.

### 8.2 Advisory catalog

Advisory adds suggest_duplicate_work, producing exactly 27 tools and still 11 protected writes. It
has readOnlyHint true and explicit safe_read transport classification. Timeout/5xx guidance permits
ordinary retry and never describes an unknown structural write.

Strict MCP models mirror each release’s OpenAPI. Cross-field validation covers revision identity,
root/path rules, event arrays, result Boolean/witness coherence, suggestion group/rank/mode/signal
rules, and project/work agreement.

lease_token remains SecretStr/equivalent and never appears in repr, validation output, errors, or
results. The tool accepts it for capable direct clients; plugin guidance never encourages copying a
token into chat.

### 8.3 Agent workflow

Canonical merge guidance:

1. Read exact source context and select a visible current-root destination.
2. Read the exact destination context separately.
3. Treat title, marks, and suggestion output as evidence only.
4. Establish explicit authority; reconcile source structural edges and resolve a source gate.
5. If holding the source lease, provide the private token; otherwise do not merge another holder’s
   active lease.
6. Submit once with both revisions, rationale, provenance, and a fresh operation UUID.
7. On ambiguity, retrieve the UUID/request from private state and repeat the identical call.
8. Read the exact source again for the current path; read/claim the root separately only when task
   authority permits.

Aliases in get/recall/search/resources/prompts are labeled as audit identity versus continuation.
Claims never auto-select the root.

### 8.4 Plugin releases

Plugin 0.7.0 ships Core guidance across all three skills and shared references:

- mnemonic-search: canonical default and explicit alias/group audit;
- mnemonic-save: compare contexts, explicit authority, irreversible direction;
- mnemonic-recall: source history versus canonical continuation and fresh root claim;
- authority-and-provenance: weak facts/model output are not authority, IDs/tokens stay private; and
- work-graph: forest/depth, reconciliation, alias freeze, no redirect/unmerge.

Plugin 0.8.0 adds Advisory compare-before-create and categorical suggestion interpretation while
preserving Create anyway.

For each version, update plugin/.claude-plugin/plugin.json and
.claude-plugin/marketplace.json together; test fresh install and sequential 0.6.1 -> 0.7.0 -> 0.8.0
upgrade through the cachebuster workflow. No parallel old skill tree remains.

## 9. Dashboard and browser proxy

### 9.1 Strict client contracts

Core TypeScript adds strict guards/adapters for MergeReviewRevision, canonical projections,
WorkItemDetailRead, WorkSearchHit, duplicate filters, bounded context, WorkMergeCreate/Result, event
metadata, and errors. Advisory adds the purpose-built candidate types. Decoders reject extras,
missing/private fields, invalid UUID relations, bad paths, wrong event ordering, nonfinite values,
mode/signal mismatches, and count errors.

### 9.2 Alias experience

Default lists/search/hierarchy omit aliases. Explicit audit filters expose aliases/all/group.
Alias detail shows:

- a Duplicate badge plus retained lifecycle;
- direct destination and current root as separate pointers;
- bounded hop path and member counts;
- merge rationale/provenance/time;
- source-owned history under Audit history; and
- explicit Open canonical work navigation.

Copy audit ID and Copy canonical ID are distinct actions; the latter appears after root review. The
URL/selected ID never changes invisibly. relationship_added marks and work_merged decisions render
as distinct timeline facts.

### 9.3 Merge review and spoof-resistant direction

Merge is available only on a current root. The dialog:

1. selects a canonical destination;
2. loads exact source/destination contexts and freezes both MergeReviewRevision objects;
3. shows lifecycle, summary, recent context, readiness, gate/lease state, and server-derived exact
   structural conflict counts;
4. blocks unresolved gate/structural conflicts and links to reconciliation;
5. requires nonblank rationale and explicit permanence acknowledgement; and
6. freezes one protected two-work-key intent.

Never render direction as one sentence containing two uncontrolled titles. Use separate
Source—becomes immutable and Destination—remains canonical panels. Each shows the full UUID beside
the title; titles use bdi/dir=auto plus CSS unicode-bidi isolation. Preserve legitimate RTL text.
Tests cover RTL override/isolate characters, zero-width characters, identical-looking titles,
keyboard order, and screen-reader role/direction.

The browser never accepts lease_token. An active source lease disables merge with release/expiry
guidance. After success, navigate to exact source audit view and refresh both endpoint/group views.
Stale review requires explicit refetch and a new operation UUID.

### 9.4 Frozen mutation intent

Core adds merge_work as browser mutation 11. The frozen intent contains project/source slot,
source/destination conflict keys, exact path/method, destination, both revisions, rationale, fixed
browser provenance, and one injected operation UUID.

It remains only in same-document memory. Timeout offers Retry same pending merge using
byte-equivalent JSON. It never rebuilds from refreshed state. client_operation_conflict enters
safety_conflict and freezes both keys. Suggestions bypass this registry.

### 9.5 Advisory create experience

The valid create form offers an explicit Check existing work action; no request runs per keystroke.
The panel shows candidate title, summary, lifecycle, activity, member count, matched member, and
categorical signals. It says “Possible existing work — compare manually,” never preselects a merge,
and marks results stale when any compared draft field changes.

Loading, empty, lexical fallback, busy, offline, invalid, and unavailable states remain accessible,
and Create work remains enabled whenever the ordinary form is valid. Draft/candidate data remains in
component memory only, never URL, localStorage, sessionStorage, analytics, logs, or mutation receipt.

### 9.6 Proxy, live sync, and accessibility

Core proxy policy explicitly allowlists merge fields and recursively rejects lease_token in body,
headers, cookies, query, and nested objects. Search accepts each duplicate query key once.

Advisory allowlists only six suggestion body fields and raises only this route’s proxy streaming cap
to the same 2,097,152-byte backend limit; other proxy routes retain their shipped limit. It uses
safe_read effect and a 60-second timeout, rejects operation IDs, and emits no mutation publication.
Never forward arbitrary headers or duplicate query parameters.

One merge commit emits one project refresh. Receivers refetch selected endpoints, lists, hierarchy,
attention, and readiness. Motion treats source as removed from canonical lists, not deleted.
Reduced-motion, focus, keyboard, non-color signals, and screen-reader labels remain complete.
Untrusted text renders only as isolated text nodes.

## 10. Implementation sequence and review gates

Work occurs on short-lived topic branches/PRs. No partial runtime is enabled on main.

### 10.1 Entry gate — product and frozen contracts

Before code:

- approve the permanent-alias decision record and mistaken-merge procedure;
- check in proposed Core request/response/error/OpenAPI vectors;
- check in old receipt, gate-projection, event-validator, and function-hash baselines;
- approve numeric performance fixtures/ceilings from Section 13.5; and
- record Core versus Advisory release ownership.

### 10.2 Core increment A — migration and immutable facts

Deliver 0016, ORM parity, merge/evidence tables/columns, composite keys, old-validator preservation,
explicit event projections, guards, indexes, and populated fixtures.

Gate: zero inferred merges; byte-preserved production fixture; direct SQL cannot create incomplete
facts, a 51-edge path, a new weak mark, a mutable relationship, or a half event pair.

### 10.3 Core increment B — resolver, merge, and receipts

Deliver canonical resolver, MergeReviewRevision, atomic service/route, lease handling, thirteenth
receipt, exact result, lock/depth/concurrency tests, and safe errors.

Gate: one transaction has exact effects; replay has zero effects; project-row lock schedule has no
deadlock; secrets/private event fields never escape.

### 10.4 Core increment C — alias freeze and reads

Deliver all mutation guards, readiness/claim anti-join, bounded context, coherent canonical
detail/search/hierarchy/relationship/attention projections, explicit audit filters, and query-plan
tests.

Gate: no stale writer mutates an alias; no canonical page mixes snapshots; raw history remains
source-owned; default surfaces expose only roots.

### 10.5 Core increment D — MCP, dashboard, plugin, release

Deliver tool 26, strict clients, replay-safe legacy dispatch, merge UI/proxy/live sync, plugin 0.7.0,
OpenAPI 0.3.0, docs, operations, audits, and release evidence.

Gate: 26/11/11 counts are exact; Core cutover/restore rehearsal passes; no suggestion stub/flag is
present; all repository checks pass.

### 10.6 Advisory increment E — service and transports

Deliver ranking contract, SQL-bounded composition, resource controls, safe-read classification,
route, tool 27, strict schemas, fault/performance/privacy tests, and OpenAPI 0.4.0.

Gate: exact-title lane, scope modes, hard caps, coherent snapshot, lexical fallback, no draft
persistence/domain effect, and numeric budgets pass.

### 10.7 Advisory increment F — create UI, plugin, completion

Deliver candidate panel/proxy behavior, plugin 0.8.0, docs/examples, E2E, and Phase 9 roadmap
evidence.

Gate: Create anyway always works; suggestion states are accessible; 27/11 counts are exact; Phase 9
full validation passes without compatibility code.

## 11. Verification plan

### 11.1 Migration and preservation

Real PostgreSQL tests cover:

- zero-to-0016 and populated-0015-to-0016;
- every lifecycle, lease/gate state, relationship/event type, embedding row, and twelve receipt
  kinds;
- descriptive duplicate cycles, chains, multi-target sources, deleted history, and mixed adjacency;
- exact before/after row digests and receipt replay;
- unchanged live unresolved/resolved gate projections;
- context reads containing legacy/gate/relationship events after internal columns exist;
- old v2 validator function hash and accepted/rejected vectors;
- zero merge/witness backfill;
- transactionally clean failed migration; and
- full archive restore followed by parity/audit.

### 11.2 Direct database invariants

Reject:

- second source destination, self/cross-project/deleted endpoints, nonroot endpoints;
- wrong type/direction/endpoint supporting relation;
- 51st edge, source-side deep-chain overflow, cycle, and project escape;
- mismatched revision/checkpoint/result version;
- unresolved source gate, lease, block, or hierarchy edge;
- merge UPDATE/DELETE;
- every work_relationships mutable-column UPDATE;
- new unwitnessed duplicate-of from raw SQL, stale add_relationship, and create initial relationship;
- missing/extra/mismatched evidence relationship events or merge events;
- alias work/checkpoint/gate/lease/event/relationship mutations;
- chosen relation/event/endpoints deletion; and
- private event-column leakage through public SQL projections.

Positive fixtures prove convergence, branching, exact 50-edge depth, reused legacy marks, created
witnessed marks, and SET CONSTRAINTS completeness.

Catalog tests inspect pg_trigger/pg_proc definitions and normalized hashes, not only ORM metadata.

### 11.3 Merge service, REST, and receipts

Parameterize source/destination lifecycles and cover:

- new versus reused exact relationship;
- unrelated retained marks/provenance;
- incoming alias tree later merged;
- active correct/missing/wrong token, supplied expired token, tokenless expired cleanup;
- source unresolved/resolved gate and destination gate drift via work version;
- all structural directions;
- progress and every event family staling work_event_count, including out-of-commit-order identity
  allocation;
- exact versions/timestamp/event arrays/Boolean/witness/result schema;
- no content/lifecycle/lease transfer;
- mandatory operation UUID;
- every status/error/precedence and sanitized scope;
- same-key replay after later chain extension;
- same-key/different request, pending unavailable, abandoned cleanup, malformed stored result, and
  restored replay; and
- current exact read after historical replay.

Inject failure after lease consumption, witness relationship, each relationship event, endpoint
update, merge insert, each merge event, deferred constraint check, receipt completion, commit, and
publication. Precommit leaves nothing. Lost postcommit response recovers exactly once.

### 11.4 Concurrency and snapshot tests

Use independent connections, barriers, lock/statement timeouts, both orderings, and repetitions:

- merge/merge same source, reciprocal, chained, convergent;
- 49/50/51 depth races;
- merge versus claim/replacement/renew/release;
- merge versus checkpoint/progress/edit/lifecycle/delete;
- merge versus gate request/resolution;
- merge versus every relationship add/remove;
- mixed old relationship writer/project-lock schedule;
- merge versus search, context, relationship page, attention summary, hierarchy, and ready summary;
- lease expiry between composite qualification/enrichment queries with one shared as_of;
- lower event identity committing after a reviewed higher identity, detected by work_event_count;
- merge versus suggestion candidate snapshot/cache write; and
- replay while a later root merge commits.

Assert no deadlock, cycle, uniqueness leak, stranded lease, half pair, duplicate invalidation,
total/item mismatch, or mixed canonical root.

### 11.5 Read/mutation matrix

One canonical-versus-alias matrix proves:

- ready and every shipped claim form agree; no phantom next-ready test;
- all fresh writes fail before side effects and release uses work_duplicate;
- add-through-alias versus remove-frozen errors are exact;
- completed replay succeeds;
- canonical path root=[], alias path ends at root;
- corrupt projected reads return 503 with no diagnostic IDs while raw history remains readable;
- context caps/omission totals and structural counts are exact at high relationship degree;
- requested alias is included in the member list;
- canonical search reports matched_member, groups before offset, and has exact total semantics;
- root-filter alias/cross-project/deleted precedence;
- hierarchy never traverses merge facts; and
- all response query counts are constant per page.

### 11.6 Core MCP/frontend/plugin

MCP asserts exactly 26 tools/11 protected writes, strict schemas, one merge dispatch, redacted
ambiguity, hidden token, legacy duplicate receipt dispatch/replay, fresh backend rejection, and
alias-aware get/recall/search/resource/prompt behavior.

Frontend asserts strict guards, source/destination panels and IDs, bidi/zero-width/screen-reader
safety, revision freeze, permanence acknowledgement, structural counts, active-lease disablement,
two-key conflicts, byte-equivalent retry, proxy recursive token denial, one refresh, exact audit
history, reduced motion, and application 0.3.0.

Plugin tests fresh/upgrade 0.7.0 content and absence of generic duplicate guidance.

### 11.7 Advisory service and client tests

Cover:

- strict two-MiB request/body streaming cap for Content-Length, absent length, and chunked bodies,
  including a maximum 100,000-code-point astral prompt serialized as surrogate-pair escapes;
- queue 429/Retry-After, the cross-route shared inference gate, suggestion fallback, ordinary
  semantic-search 503, DB 503, and absolute timeout effect classification;
- all lifecycle states, deleted/scope/exclude-group behavior;
- PostgreSQL-17 title-key normalization/index usage, tags, global exact-title total/omission, and
  deterministic ordering;
- full-project cached dense-only match beyond lexical 200 at a 10,000-member fixture;
- project above 10,000 explicitly using shortlist and never claiming global semantic;
- alias-heavy groups grouped before caps;
- SQL-bounded prompt extraction with many large checkpoints in both suggestion and ordinary
  semantic-search composition;
- malformed/nonfinite/dimension-wrong vectors and stale composition;
- candidate summary privacy, exact stored title/summary preservation, and categorical signals only;
- draft/query-vector nonpersistence and compare-by-digest cache writes with skip-locked work rows,
  bounded lock waits, and route-relative PostgreSQL transaction/statement deadlines;
- zero domain/receipt/version/activity/invalidation effects;
- safe retry in MCP/proxy and no live-sync mutation; and
- create success for loading, busy, lexical, error, and offline suggestion states.

MCP reaches exactly 27 tools/11 protected writes. Frontend covers explicit-action candidate UI and
application 0.4.0. Plugin fresh/sequential upgrade reaches 0.8.0.

### 11.8 End-to-end scenarios

Core Playwright:

1. Reconcile a blocker, review two contexts, and merge.
2. Alias leaves default/ready but exact URL retains history/root link.
3. A root with incoming aliases merges again and group counts follow the root.
4. Source gate blocks; resolution stales review; refreshed merge succeeds.
5. Active lease disables browser merge.
6. Dropped merge response plus same-key retry yields one complete fact set.
7. Destination/progress change after review causes drift.
8. Spoofing titles cannot obscure source/destination direction.
9. Browser cannot inject lease_token or unsupported duplicate writes.

Advisory Playwright:

1. Suggest completed work, inspect it, and still create a distinct item.
2. Exact title and matched alias are visible as categorical evidence.
3. Lexical fallback/busy/offline never disables create.
4. Draft change invalidates in-memory candidates.

Run material flows at desktop and narrow viewport.

### 11.9 Security and fault assertions

Captured logs/errors/metrics contain no bearer, lease token, operation/work/merge/relationship/gate
ID, request body, title, summary, rationale, checkpoint, tag, actor/session/model, vector, or raw
score. Fuzz NUL, sizes, duplicate JSON keys where detectable, UUIDs, reserved metadata, cross-project
IDs, NaN/infinity, malformed paths, bidi controls, and known-secret substrings.

### 11.10 Required commands

No release is complete until the repository command matrix passes with PostgreSQL tests enabled:

~~~sh
docker compose -f compose.test.yaml up -d --wait
cd backend
uv sync --frozen
TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest -q
uv run ruff check .
uv run ty check src

cd ../mcp
uv sync --frozen
TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp

cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e:stack

cd ..
pre-commit run --all-files
~~~

A skipped database suite, stale generated contract, omitted E2E stack, or bypassed gitleaks hook is
not evidence.

## 12. Migration, deployment, recovery, and audits

### 12.1 Read-only preflight

scripts/audit_duplicate_handling.py uses the configured database and emits versioned aggregate
counts only. It checks:

- expected migration head and no pending/invalid receipt rows;
- current relationship/event/checkpoint/gate invariants;
- weak duplicate mark counts, multi-targets, cycles, and maximum descriptive depth;
- marked-source lease/gate/structural adjacency;
- deleted/cross-project endpoints;
- required PostgreSQL functions/storage; and
- backup capacity.

Weak-mark anomalies are informational because nothing is promoted. Existing invariant, receipt,
head, storage, or backup failure blocks cutover. Output contains no IDs/content/provenance/tokens.

### 12.2 Rehearsal

Against an isolated restore:

1. restore the newest candidate archive;
2. record Phase 1–8 row/receipt/gate/function hashes;
3. run preflight and migration 0016;
4. run schema/model/catalog parity and populated reads;
5. perform committed merge probes, including created/reused edge, chain, depth boundary, failure,
   replay, and alias guards;
6. take a post-0016 archive and restore it into another isolated database;
7. rerun parity, receipts, and integrity audits; and
8. preserve aggregate evidence.

Committed “test merges” occur only in this rehearsal database.

### 12.3 Quiesced Core cutover

1. Build/test Core release candidates and plugin artifact.
2. Announce downtime; stop web, MCP, and API while leaving PostgreSQL/backup available.
3. prove no application connections or writer transactions remain;
4. take the existing one-shot backup, record the exact file, verify pg_restore --list, copy it to
   independent storage, and restore-test it;
5. run preflight against the quiesced source;
6. deploy coordinated 0.3.0 API/MCP/web and plugin 0.7.0 artifacts;
7. apply exactly 0016 with the documented migration command;
8. verify head, zero merge rows/witnesses, schema/model/catalog parity, row hashes, gate projections,
   old receipts, and explicit event reads;
9. start API alone and run read-only health/contract/canonical probes;
10. start MCP/web and run read-only or transaction-rolled-back probes only; verify 26/11/11 counts;
11. take a new post-0016 backup and restore-test it in isolation;
12. rerun restored receipt and integrity audits;
13. only then reopen writer traffic; and
14. monitor bounded telemetry and postcutover audits.

No production probe commits a merge. Never serve a Phase 8 process against 0016 as a supported mode.

### 12.4 Advisory rollout

Advisory migration 0017 widens `alembic_version.version_num` to `VARCHAR(64)` so the full
descriptive revision identifier is preserved, then adds the versioned immutable title-key function
and partial expression index. It rewrites no work content and creates no canonical fact. Rehearse
populated upgrade, function/index parity, EXPLAIN use, and full restore before deploying coordinated
0.4.0 API/MCP/web and plugin 0.8.0. Verify 27 tools/11 protected writes, direct body/queue behavior,
safe-read retry, lexical fallback, Create anyway, and no mutation publication before exposing the
route/UI.

### 12.5 Rollback and mistaken merge

0016 has no downgrade. Before any Core write, restore the complete pre-upgrade archive with matching
Phase 8 binaries or fix forward. After a merge/receipt commits, schema rollback would erase meaning.

| Failure point | Supported response |
| --- | --- |
| before migration | resume Phase 8 or repair candidate |
| migration transaction fails | verify automatic rollback, repair, repeat preflight |
| migrated, traffic closed, zero Core writes | fix forward or restore full pre-upgrade archive |
| traffic opened/Core writes exist | fix binaries forward; database restore needs explicit later-write-loss approval |
| one mistaken merge | follow Section 3.3; full restore or future append-only correction release |
| Advisory defect | remove/repair coordinated 0.4.0 surfaces; Core 0.3 semantics remain authoritative |

Never partially restore work rows, mutate merge/evidence/events, splice receipt JSON, or delete a
source. Existing destructive restore guard and exact archive confirmation remain mandatory.

### 12.6 Continuous integrity audit

Assert:

- unique outgoing source and valid scoped depth/cycles;
- exact composite relationship and witness/event completeness;
- exact two merge events and stored revision/result coherence;
- no post-0016 unwitnessed duplicate relationship;
- no authoritative alias domain mutation, while explicitly permitting derived embedding-cache and
  receipt-registry maintenance;
- no alias lease/gate/structural edge/readiness/claim;
- referenced facts remain present;
- completed merge receipts validate one merge;
- default search/hierarchy contain roots only; and
- cache composition versions are disposable/coherent.

Normal telemetry reports category/count only. Privileged local diagnostic mode may print IDs only
after explicit invocation.

## 13. Security, privacy, observability, and performance

### 13.1 Sink inventory

| Data | Database | Authorized response | Logs/metrics | Browser durable storage |
| --- | --- | --- | --- | --- |
| rationale/provenance | merge/events | exact merge/history | never | never |
| lease/bearer token | existing auth/lease and protected fingerprint only | request only | never | never |
| operation UUID | receipt registry | never echoed by MCP error | aggregate outcome only | same-document private intent |
| draft suggestion fields/query vector | never | request only | never | never |
| candidate title/summary/pointer | existing work | bounded candidate | never | never |
| existing-work vector | disposable cache | never | never | never |
| categorical signals | never canonical | bounded candidate | aggregate mode/count only | never |
| work/merge/relation IDs | canonical tables | project-scoped result | never as telemetry labels | same-document view state |

No new analytics/diagnostic sink without separate review.

### 13.2 Authorization and untrusted data

Scope every resolver/member/suggestion query by project before alias details. Provenance is asserted,
not authenticated identity. Merge grants no destination capability.

Treat all text and metadata as data. Validate length/Unicode/NUL, use bound SQL parameters, escape
URL components, render literal text, isolate bidi direction, and never inject recalled/suggested
content into shell, log formats, labels, HTML, Markdown authority, or tool instructions.

### 13.3 Telemetry

Aggregate metrics only:

- merge new/replay/error and latency;
- path depth/member-count buckets;
- alias rejection category;
- suggestion mode/scope/result/internal-count and busy/unavailable;
- suggestion cold/warm latency and RSS buckets; and
- integrity category.

Labels never contain project, work, merge, operation, actor, session, model, title, rationale,
token, signal score, or error text. Integrity failure, alias ready/claim, invalid receipt, or
duplicate_graph_invalid is a release/incident alert.

### 13.4 Resource/security assertions

Direct REST, MCP, and proxy tests independently cover authentication-before-work, streaming body
limits, bounded queue/inference, safe timeouts, header/query allowlists, secret substring rejection,
and response/error/log redaction. Model loading happens during image construction/warmup where
possible and never while graph/work locks are held.

### 13.5 Frozen performance fixture and ceilings

The implementation lead records results; the repository maintainer approves evidence. Changing a
ceiling requires a reviewed amendment before implementation measurement, not after observing a
failure.

Reference class:

- one API worker and local PostgreSQL on Linux/x86-64;
- four dedicated vCPUs, 8 GiB RAM, SSD-backed Docker volumes;
- warm PostgreSQL buffers unless a test is explicitly cold;
- projects of 100, 1,000, and 10,000 visible work items;
- five checkpoints per work, each 3,000 characters;
- 10% aliases, paths through 50, all lifecycles, and 5% high-degree descriptive relationships;
- 30 measured iterations after five warmups; p95 from request wall time;
- suggestion concurrency one for latency and four for saturation behavior.

Core gates at 10,000:

- ready/claim SQL uses the unique source anti-join and p95 regresses no more than 25% from the
  checked Phase 8 fixture;
- a 100-item canonical search page p95 <= 1,500 ms;
- a capped alias WorkContext p95 <= 750 ms;
- hierarchy page p95 <= 1,000 ms;
- each composite response has constant query count and peak API RSS increase <= 256 MiB over its
  Phase 8 counterpart.

Advisory gates:

- lexical-only p95 <= 1,000 ms at 10,000;
- warm hybrid_full with complete cached vectors p95 <= 5,000 ms at 10,000;
- cold model/query plus bounded 128-vector fill p95 <= 20,000 ms;
- peak API RSS increase <= 768 MiB from premodel baseline;
- request never exceeds the 60-second transport budget; and
- four concurrent requests either complete/fallback or receive bounded 429 within 250 ms; no
  unbounded queue/database occupancy.

Failure requires algorithm/query/resource correction or deferring Advisory, not moving the gate,
raising timeouts, or adding an external service without a new architecture decision.

## 14. Expected implementation surface

This inventory is planning scope, not authorization.

### 14.1 Backend Core

| Path | Expected change |
| --- | --- |
| backend/alembic/versions/0016_duplicate_handling.py | empty ledger, witnesses, events, keys, guards, indexes, no downgrade |
| backend/src/mnemonic_api/models.py | merge/witness/event ORM parity |
| backend/src/mnemonic_api/schemas.py | merge revision/projections/context/search/result |
| backend/src/mnemonic_api/errors.py | exact duplicate errors/safe context |
| backend/src/mnemonic_api/database.py | scoped repeatable-read support if needed |
| backend/src/mnemonic_api/services/duplicates.py | resolver/revision/atomic merge/grouping |
| backend/src/mnemonic_api/services/client_operations.py | thirteenth receipt/vectors/redaction |
| backend/src/mnemonic_api/services/readiness.py | shared alias anti-join |
| backend/src/mnemonic_api/services/work_context.py | explicit event columns, caps, projection/revision |
| backend/src/mnemonic_api/services/hierarchy.py | canonical roots/member aggregation |
| backend/src/mnemonic_api/services/relationships.py | close fresh duplicate, immutable facts, already-locked helper |
| backend/src/mnemonic_api/services/leases.py | alias guard and terminal consumption reuse |
| backend/src/mnemonic_api/services/gates.py | preserve gate revision; alias guard only |
| backend/src/mnemonic_api/services/work_events.py | merge event branch/projections/alias guard |
| backend/src/mnemonic_api/services/work_items.py | alias/delete/create-initial-relation guards |
| backend/src/mnemonic_api/application/guards.py | shared route/domain guard only if it reduces duplication |
| backend/src/mnemonic_api/application/mutations.py | publication classification if current seam requires |
| backend/src/mnemonic_api/application/routes/duplicates.py | focused merge route |
| backend/src/mnemonic_api/application/routes/work_search.py | scope/group/hit/snapshot |
| backend/src/mnemonic_api/application/routes/work_items.py | exact detail wrapper |
| backend/src/mnemonic_api/application/routes/relationships.py | error mapping |
| backend/src/mnemonic_api/application/routes/leases.py | alias lease errors |
| backend/src/mnemonic_api/application/routes/human_gates.py | alias behavior, no revision reinterpretation |
| backend/src/mnemonic_api/application/routes/history.py | raw exact history/private-column exclusion |
| backend/src/mnemonic_api/main.py and package/lock metadata | 0.3.0 |
| scripts/audit_duplicate_handling.py | read-only aggregate pre/post audit |

### 14.2 Backend Advisory

| Path | Expected change |
| --- | --- |
| backend/alembic/versions/0017_duplicate_suggestion_title_key.py | immutable v1 key function, partial expression index, preservation/parity |
| backend/src/mnemonic_api/models.py | matching Advisory expression-index metadata for schema parity |
| backend/src/mnemonic_api/config.py | namespaced caps/concurrency/timeouts |
| backend/src/mnemonic_api/application/middleware.py | authenticated streaming cap/resource boundary as appropriate |
| backend/src/mnemonic_api/semantic.py | v1 composition, SQL-bounded extraction, separated cache writes |
| backend/src/mnemonic_api/services/duplicates.py | exact/lexical/semantic lanes and grouping |
| backend/src/mnemonic_api/application/routes/duplicates.py | safe-read suggestion route |
| backend/src/mnemonic_api/application/mutations.py | explicit no-publication classification |
| backend/src/mnemonic_api/main.py and package/lock metadata | 0.4.0 |

### 14.3 Tests and artifacts

Add focused PostgreSQL tests for migration, merge, constraints, concurrency, readiness, reads,
suggestions, contracts, triggers/functions, and performance. Extend receipt, schema parity, OpenAPI,
semantic, relationship, lease, gate, event, hierarchy, live-sync, middleware, and work-item suites.
Regenerate docs/openapi.json for each release.

### 14.4 MCP

Update models.py, api.py, server.py, validation.py, package/lock metadata, tool snapshots, transport,
OpenAPI, plugin, and PostgreSQL tests. Core adds merge/effect/replay behavior; Advisory adds
suggestion/safe-read behavior.

### 14.5 Frontend

Expected libraries include types.ts, wire-guards.ts, api.ts, proxy-policy.ts,
mutation-responses.ts, mutation-intent.ts, work-item-search.ts, work-item-view.ts,
current-context.ts, hierarchy-presentation.ts, work-relationships.ts, work-events.ts,
work-recall-pointer.ts, live-sync.ts, and work-item-motion.ts.

Expected components include dashboard, editor, detail, card, list, hierarchy, relationship panel,
event timeline, plus focused merge and suggestion components. Update package/lock metadata and add
frontend/tests/e2e/phase9-duplicate-handling.spec.ts. The catch-all proxy remains the only proxy
route.

### 14.6 Plugin/docs/examples

Update both plugin manifests, all three skills, both shared references, README.md, AGENTS.md,
architecture, API contract, operations, development, agents, validation, OpenAPI, roadmap, and
relevant examples. Refresh ignored CLAUDE.md only after each release actually changes its stated
migration head/tool count/retry/error contract.

## 15. Documentation contract

Use one vocabulary:

~~~text
duplicate mark
  descriptive duplicate-of relationship with no canonical effect

merge
  immutable explicit source-to-direct-destination decision

alias
  retained source of an authoritative merge

canonical work
  current root reached by following authoritative destinations
~~~

Every public guide states similarity is advisory; creation is never suppressed; exact direction and
permanence; exact history versus canonical continuation; no redirect/claim substitution; source
preconditions; mandatory same-key recovery; historical replay then current read; no content/
lifecycle/authority coalescing; browser active-lease limitation; and full restore consequences.

Compatibility matrices state Core 0.3.0/plugin 0.7.0/26 tools, then Advisory
0.4.0/plugin 0.8.0/27 tools. Roadmap Phase 9 moves to Shipped only with linked evidence for both.

## 16. Risk register

| Risk | Consequence | Mitigation/evidence |
| --- | --- | --- |
| weak mark inferred as merge | silent retirement | empty ledger, no backfill, insert witness |
| stale writer creates weak mark | dual semantics | deferred new-insert guard and stale-path tests |
| wrong lock primitive/order | race/deadlock | existing project row, sorted endpoints, mixed barrier |
| wrong depth side | 51-edge chain | reverse-source formula and boundary fixtures |
| relationship/evidence mutation | audit falsification | global update immutability, composite FK, witnesses |
| private event field leaks | strict context failure | explicit public projection and populated tests |
| gate revision reinterpretation | every gate drifts | leave type/counter/function exact; separate merge revision |
| receipt shape/replay drift | permanent recovery fails | new wrappers, frozen vectors, MCP dispatch-to-backend |
| alias retains capability | retired work executes | lease consumption, guards, readiness anti-join |
| graph changes mid-response | mixed roots/totals | one statement/repeatable-read and barriers |
| merge direction spoofed | irreversible wrong choice | separate bidi-isolated panels plus full IDs |
| erroneous merge | no local repair | sign-off, confirmation, whole restore or future append-only release |
| suggestion amplification | queue/CPU/DB exhaustion | body, request, model, candidate, and timeout caps |
| exact/dense match hidden | false reassurance | global exact lane, explicit semantic scope, adversarial fixtures |
| suggestion leaks capability | privacy/authority exposure | purpose-built summary/categorical signals |
| POST treated as write | false ambiguity | explicit safe_read across MCP/proxy/live sync |
| production probe persists | permanent junk | committed probe only in rehearsal; prod rollback/read-only |
| recovery point untested | postcutover loss | post-0016 restore test before traffic |
| clients out of sync | decode/authority error | coordinated per-release versions and count probes |
| Advisory delays Core | correctness delayed by ML | two ordered releases; no dormant flag |
| compatibility bloat | conflicting semantics | clean prerelease changes; no shim/alternate surface |

## 17. Explicitly deferred

Phase 9 does not include:

- automatic merge, creation suppression, canonical selection, or confidence threshold;
- ordinary unmerge, split, retarget, merge deletion, or correction API;
- bulk canonicalization/import;
- automatic relationship transfer/reparenting;
- content, checkpoint, event, gate, lease, lifecycle, or provenance coalescing;
- redirects, silent ID replacement, claim-through-alias, or lease transfer;
- cross-project canonical identity or aliases as hierarchy nodes;
- pgvector, hosted embeddings, another queue/service, or external model;
- durable suggestion results, draft/query vectors, or raw scores;
- background duplicate sweeps/notifications;
- another mark tool or compatibility API;
- a duplicate lifecycle value; or
- an ordinary operator SQL escape hatch.

An append-only correction release is explicitly future work if operators cannot restore an erroneous
merge. It must design canonical interpretation, authority, receipt, migration, client, and audit
semantics before use.

## 18. Definition of done

Checked items below have repository evidence in `docs/validation.md`. Unchecked items remain
explicit operator deployment or final acceptance gates; repository shipment does not claim that
they occurred in production.

### 18.1 Core release

- [ ] Product/operator permanence decision is signed.
- [x] 0016 preserves all existing content and creates zero inferred merge/witness rows.
- [x] HumanGateContextRevision, v2 event validator, old receipt JSON, and live gate projections are
      unchanged.
- [x] Ledger, exact relationship, private witnesses, relationship events, and two merge events are
      immutable/complete under direct SQL.
- [x] Existing project-row lock and corrected reverse-depth formula pass all races/boundaries.
- [x] New weak duplicate insertion and every alias mutation fail closed under stale writers.
- [x] merge_work is mandatory-key receipt kind 13 with exact new/replay effects and redaction.
- [x] Source eligibility, lease, gate, structural, revision, lifecycle, and destination rules match.
- [x] Ready/every shipped claim share the indexed alias exclusion.
- [x] Exact history, canonical projection, bounded context, structural counts, and corrupt-read
      behavior are exact.
- [x] Canonical search/hierarchy group before offset and use coherent snapshots/matched_member.
- [x] Core REST/OpenAPI 0.3.0, 26 MCP tools, 11 protected writes, 11 browser mutations, proxy, and
      plugin 0.7.0 agree.
- [x] Direction/bidi, accessibility, live refresh, and lost-response recovery pass.
- [ ] Empty/populated migration plus pre/post-0016 backup restore rehearsals pass before traffic.
- [ ] Core performance/security/audit gates pass.

### 18.2 Advisory release and Phase 9 completion

- [ ] Migration 0017 preserves work content and its immutable title-key function/index pass
      PostgreSQL-17 parity, global lookup, EXPLAIN, upgrade, and restore tests.
- [x] Suggestion selection, normalization, exact lane, semantic scopes, weights/model/composition,
      grouping, caps, and ordering match this plan.
- [x] Direct API body/request/model controls and 413/429/503 contracts pass.
- [x] Suggestions are snapshot-coherent, lifecycle-complete, bounded, inert, and categorical.
- [x] Draft/query vectors never persist; candidate summaries expose no capability/provenance.
- [x] Semantic unavailability yields lexical success and never disables create.
- [x] safe_read behavior agrees across REST, MCP, proxy, live sync, and retry guidance.
- [x] Advisory REST/OpenAPI 0.4.0, 27 MCP tools, 11 protected writes, frontend, and plugin 0.8.0
      agree.
- [ ] Numeric performance, fault, security, frontend, and E2E gates pass.
- [x] Full backend/MCP/frontend/E2E/Ruff/ty/build/OpenAPI/pre-commit matrix passes with database
      suites enabled.
- [x] Documentation, examples, validation, operations, and roadmap link concrete evidence.
- [x] No shim, alternate route/tool, dormant flag, automatic merge, or destructive cleanup remains.

Unchecked deployment gates must pass before production traffic is enabled. They do not negate the
completed repository implementation and do not constitute production-cutover evidence.

## 19. Cold adversarial review record

### 19.1 Method and initial verdict

On 2026-09-02, after the first complete draft was frozen, an adversarial subagent received only the
repository path, the user request, and an instruction to inspect the draft/repository cold and make
no edits. It delegated independent database/concurrency and contract/security passes, then
consolidated them.

Initial verdict: the authoritative-ledger/descriptive-edge split was sound, but the draft was not
implementation-ready. The reviewer identified seven blockers, eleven additional high risks,
eleven medium issues, and four low/scope issues. The draft remained unchanged until that verdict.

### 19.2 Disposition

| Cold finding | Disposition in this revision |
| --- | --- |
| gate counter would self-drift/nullability changed | preserved HumanGateContextRevision exactly; separate non-null MergeReviewRevision |
| nonexistent advisory graph lock | replaced with shipped project-row lock_project_graph and sorted endpoints |
| depth walked destination instead of source ancestry | froze reverse-source formula and 49/50/51 fixtures |
| chosen relationship SQL-mutable | all relationship UPDATEs rejected; composite endpoint/type FK |
| internal event FK leaks through event.* | explicit public event columns required everywhere |
| MCP local rejection blocks old replay | legacy-shaped calls dispatch once; backend decides replay/reject |
| canonical corrupt/path shape impossible | root path empty; corrupt projected reads return sanitized 503 |
| created-edge Boolean unprovable | private relationship/event merge witnesses and deferred completeness |
| stale Phase 8 canonical endpoints can add weak mark | every new duplicate insert requires same-transaction merge |
| canonical multiquery reads mix snapshots | one statement or dedicated repeatable-read snapshot |
| no backend suggestion resource controls | explicit 2-MiB valid-draft cap, 4-request, 1-model, wait/error/config contract |
| suggestion algorithm/cap undecided | indexed exact lane, 200 groups, 128 fills, 10k semantic scope, model/weights frozen |
| source_lease_token bypasses secret registry | public field renamed lease_token plus substring-aware secret rejection |
| MCP ambiguity echoes operation UUID | shipped redacted ambiguity text retained verbatim |
| safe POST inferred as mutation | explicit safe_read/receipt_protected_write/lease_claim classification |
| alias context still unbounded | 100-per-direction caps, omissions, exact structural eligibility |
| rollout commits junk/tests restore too late | committed probes only in rehearsal; post-0016 restore before traffic |
| old event validator amended in place | v2 function byte-frozen; dedicated merge branch/function |
| release behavior contradicted | every fresh alias release returns work_duplicate |
| progress race outcome false | commit-visible work_event_count now stales merge review |
| nonexistent/conflicting error codes | shipped uncertainty/lease codes and exact add/remove mapping |
| next-ready was phantom scope | removed entirely |
| cursor terminology/match provenance wrong | retained offset and added matched_member |
| canonical group-filter semantics missing | visible root required; alias conflict; sanitized scope precedence |
| full WorkSummary leaks readiness/lease | purpose-built DuplicateCandidateSummary |
| merge result event order vague | fixed named arrays, lengths, roles, and order |
| REST idempotency optional | operation UUID required on every merge transport |
| mistaken-merge policy vague | mandatory sign-off and exact restore/future-release procedure |
| bidi spoofing unaddressed | isolated source/destination panels, full IDs, RTL/zero-width tests |
| pointer type undecided | exact existing WorkIdentityPointer retained |
| integrity audit forbade cache writes | authoritative tables scoped; derived cache/receipt maintenance exempt |
| performance gate movable | fixture, hardware, concurrency, ceilings, and owner frozen here |
| suggestions coupled to correctness | split Core 0.3.0 from Advisory 0.4.0 |

### 19.3 First closure-audit findings and disposition

| Closure finding | Disposition |
| --- | --- |
| merge trigger saw post-review relationship events and contradictory versions | relation row now precedes merge; evidence/merge events follow it; trigger compares reviewed counts and resulting versions |
| forcing all constraints while receipt pending fails | result and receipt complete before SET CONSTRAINTS ALL IMMEDIATE |
| global latest event identity is not commit ordered | revision uses committed immutable work_event_count; inverted-commit barrier required |
| repeatable read does not freeze lease-expiry time | one database as_of is bound through every composite query/serializer |
| durable text scan omitted provenance and operation UUID | all four durable text fields scan bearer, lease token, and UUID spellings |
| global Unicode exact lane lacked an indexable contract | Advisory migration 0017 freezes a narrower PostgreSQL-17 title key and partial expression index |
| 128-KiB cap rejects valid 100,000-character drafts | two-MiB cap plus computed escaped-astral worst-case contract test |
| removal guard contradicted frozen-edge error | operation category makes add work_duplicate and removal duplicate_relationship_frozen |
| matched_member undefined without text | equals returned row/root for blank or filter-only search |
| one-MiB cap still rejected escaped astral prompts | raised route-specific backend/proxy cap to two MiB and froze an escaped-astral maximum fixture |
| Advisory expression index omitted from ORM inventory | added models.py expression-index metadata and parity work |

### 19.4 Final closure verdict

The adversarial reviewer performed a full closure reread, followed by a targeted micro-closure of
the two-MiB request cap and 0017 ORM index inventory. The database/concurrency pass and the
contract/security pass independently reported no remaining blocker or internal contradiction.

**Final verdict:** implementation-ready. All original, first-closure, and micro-closure findings are
closed. This verdict approves the plan’s internal completeness; it does not authorize
implementation, waive the Section 10 entry gate, or supply the product/operator permanence
sign-off.
