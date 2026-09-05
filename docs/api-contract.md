# Phase 11 API contract

This is the application/API/MCP `0.7.0`, plugin `0.10.0`, and migration
`0019_structured_completion_evidence` contract. Phase 11 adds optional structured
verification results and artifact references inside atomic completion plus a
bounded, event-backed evidence-history read. Phase 10's caller-declared
repository scopes and local freshness workflow and Phase 9's authoritative
duplicate merges remain unchanged.

Release `0.7.0` retains that schema and the exact 28 MCP tools, 11 protected MCP
writes, 13 REST receipt kinds, and 11 protected browser mutations.

All application routes use `/api/v1` and
`Authorization: Bearer <MNEMONIC_API_KEY>`. `GET /healthz` and
`GET /readyz` are unauthenticated and disclose no credentials. IDs are UUID
strings; timestamps are ISO 8601 UTC. Request models reject unknown fields.

Application errors use a stable sanitized envelope:

```json
{
  "detail": {
    "code": "version_conflict",
    "message": "The work item changed after the supplied version was read.",
    "context": {}
  }
}
```

FastAPI's structured list remains the validation-error format. Invalid input is
422, missing or cross-project resources are 404, lifecycle/version conflicts are
409, and bad or missing authorization is 401. Error context never contains
checkpoint text, metadata, credentials, or request bodies.

Lease conflicts use stable codes: `work_not_pending`, `lease_held`,
`lease_expired`, `lease_token_mismatch`, and `claim_request_expired`.
`lease_held` may expose only safe holder and expiry context. No error contains a
lease token or claim request ID.

Lifecycle and graph conflicts use stable codes including
`invalid_status_transition`, `work_blocked`, `completion_episode_unsealed`,
`relationship_context_invalid`, `relationship_cycle`, `parent_already_set`, and
`active_relationships`. `completion_episode_unsealed` refuses to move work out
of `done` when its completion owns no sealed episode -- work completed before
`0010_work_events` is the case that reaches it. The condition is permanent, so
the refusal is a 409 rather than a retryable fault.
Self-edges and a missing discovery context fail strict request validation with
422. Missing or cross-project endpoints/checkpoints use sanitized 404 codes.
Error context never includes checkpoint content or non-allowlisted upstream values.
Event validation failures are ordinary structured 422 errors; a request-known
secret echo returns `event_secret_echo`, whose context identifies field
locations, never caller values. Human-gate operations use `work_gated`,
`gate_not_found`, `gate_already_resolved`, `gate_context_changed`,
`gate_secret_echo`, and `invalid_cursor`. A malformed, foreign-scope, or
filter-mismatched cursor returns `422 invalid_cursor`; restart at the first page.
Their messages and context never echo a question, answer, reviewed revision,
actor/session value, or control UUID. A hierarchy statement canceled by its
five-second limit returns typed `503 hierarchy_timeout`; retry a narrower read.
Any database failure may return `503 database_unavailable`; a write outcome can
be unknown, so retain the exact request before deciding whether to retry.
The typed `503 duplicate_graph_invalid` response is the exception: it is a
definitive integrity stop, not an unknown write outcome, and must not be retried.

Duplicate conflicts use `duplicate_merge_required`, `duplicate_self`,
`work_duplicate`, `work_already_duplicate`,
`duplicate_destination_not_canonical`, `duplicate_context_changed`,
`duplicate_source_gate_unresolved`, `duplicate_structural_relationships`,
`duplicate_depth_exceeded`, and `duplicate_relationship_frozen`. A corrupt
authoritative graph returns `503 duplicate_graph_invalid` and disables
canonical-sensitive authority changes. Only `work_duplicate` may expose the
current `canonical_work_item_id`; errors never expose rationale, history,
reviewed revisions, tokens, operation IDs, or arbitrary endpoint IDs.
Suggestion-specific failures are `request_body_too_large` (413),
`duplicate_suggestion_busy` (429 with `Retry-After: 1`), and
`duplicate_suggestion_unavailable` (503). The suggestion operation is a safe
read: its timeout/429/503 may be retried normally and never imply an unknown
structural write. Creation remains independent.

Completion evidence adds sanitized `completion_evidence_unavailable` (503) for
a history representation whose stored identity, generation, ordering, or size
cannot be proven coherent. It never contains evidence text, artifact
references, identifiers, or response fragments. Treat it as unavailable data,
not as an empty history. A malformed or unavailable protected completion
receipt instead returns the existing `client_operation_unavailable`; replay
validates the permanent receipt without consulting current evidence rows, and
neither error proves whether a write committed.

## Idempotent mutation receipts

Exactly thirteen project-scoped REST mutations use a top-level
`client_operation_id` UUID. It is optional on the original twelve only while
`complete_work` carries no structured evidence; it is mandatory for every
non-empty evidence completion and every merge:

| Operation | Route |
| --- | --- |
| create work | `POST /projects/{project_id}/work-items` |
| append checkpoint | `POST /projects/{project_id}/work-items/{work_item_id}/checkpoints` |
| append progress event | `POST /projects/{project_id}/work-items/{work_item_id}/events` |
| add relationship | `POST /projects/{project_id}/relationships` |
| update work | `PATCH /projects/{project_id}/work-items/{work_item_id}` |
| defer work | `POST /projects/{project_id}/work-items/{work_item_id}/defer` |
| complete work | `POST /projects/{project_id}/work-items/{work_item_id}/complete` |
| delete work | `POST /projects/{project_id}/work-items/{work_item_id}/delete` |
| remove relationship | `DELETE /projects/{project_id}/relationships/{relationship_id}` |
| release claim | `POST /projects/{project_id}/work-items/{work_item_id}/release-claim` |
| request human input | `POST /projects/{project_id}/work-items/{work_item_id}/gates` |
| resolve human input | `POST /projects/{project_id}/work-items/{work_item_id}/gates/{gate_id}/resolve` |
| merge work | `POST /projects/{project_id}/work-items/{source_work_item_id}/merge` |

A caller generates one UUID before the first attempt and retains the complete,
validated semantic request. An exact retry under the same
`(project_id, client_operation_id)` returns the original successful status and
JSON body without re-running domain work or adding events. This remains true
after the work changes, reopens, is deleted, or a released lease is replaced.
Successful natural no-ops such as duplicate relationship add, absent
relationship removal, or absent lease release replay their original
`created=false`, `removed=false`, or `released=false` result.

The identity scope deliberately excludes actor/session provenance. Those
values, the operation kind, URL target, versions, lease capability, and every
other semantic request field are instead fingerprinted. Reusing a successful
key for anything semantically different returns
`409 client_operation_conflict` and performs no work. A receipt wait timeout,
unsupported/corrupt receipt, or inability to validate the stored response
returns `503 client_operation_unavailable`; the caller must retry the same key
and exact semantic arguments because the outcome may be unknown. A protected
request that copies its operation UUID, bearer, or supplied lease token into a
public content/provenance field returns
`422 client_operation_secret_echo` before reserving a receipt.

The UUID is accepted only as that top-level JSON field. It is rejected in URLs,
queries, headers, cookies, nested objects, progress metadata at any depth, and
excluded mutation bodies. It is control data: it never appears in ordinary
responses, errors, events, resources, prompts, logs, or browser persistence.
`WorkItemPatch`, `WorkDeferralCreate`, `WorkDeletionCreate`,
`RelationshipRemovalCreate`, and `LeaseReleaseCreate` require their nested
`actor` whenever the operation ID is present. Their unkeyed direct-REST form
remains valid and may remain unattributed.

Direct REST callers may omit `client_operation_id` from the original twelve;
that preserves a single unprotected attempt and makes no retry-safety promise.
The exception is a completion with at least one evidence child, which requires
the UUID before reservation or any domain write. `merge_work` has no unkeyed
form. Exactly eleven canonical
MCP mutation tools require it: the previous ten plus `merge_work`.
Human-only deferral and gate resolution have no MCP tools. The dashboard
generates it for eleven covered browser operations: its previous ten plus merge,
while capability-bearing release and gate creation remain denied.
It freezes the entire request and retries only that exact in-memory intent.
Project create/update, project settings, claim, claim-and-recall, and
renew-claim remain outside this ledger. Claim recovery continues to use its
separate `claim_request_id` contract.

Receipts are private durable database state with no public list/get/delete
route and no TTL or cleanup task. They contain salted request
fingerprints and stored successful response bodies, so backups must preserve
and protect them. There is no compatibility header or alternate legacy
idempotency path.

## Projects

- `GET /projects?limit=100&offset=0` returns
  `{items: Project[], total, limit, offset}`.
- `POST /projects` accepts
  `{name, slug?, description?, repository_url?}` and returns a project (201).
- `GET /projects/{project_id}` returns a project.
- `PATCH /projects/{project_id}` updates name, description, or repository URL.

Project fields are `id`, `name`, `slug`, `description`,
`repository_url`, `created_at`, and `updated_at`. Slugs are unique,
lowercase, and hyphen-separated; omitting one derives it from the name.

Project settings use `GET /projects/{project_id}/settings` and
`PATCH /projects/{project_id}/settings`. GET returns exactly
`{project_id, recall_pointer_template}`; `null` selects the built-in template.
PATCH requires exactly `{recall_pointer_template}`, whose value is nonblank text
of at most 100,000 characters or `null` to clear the saved override, and returns
the same read shape. Unknown projects return 404. These routes need no
`client_operation_id`, remain outside the receipt ledger, and are admitted by
the dashboard proxy.

## Canonical work-item routes

Base path: `/projects/{project_id}/work-items`.

- `POST /` creates one work item, its initial context checkpoint, and optional
  initial relationships atomically, returning `WorkCreation` (201).
- `GET /` browses or searches `WorkSearchHit` rows, grouping aliases under
  their canonical root by default.
- `GET /{work_item_id}` returns `WorkItemDetailRead`, containing the exact
  `WorkItemRead` plus an explicit canonical projection.
- `PATCH /{work_item_id}` performs a version-protected work identity or
  lifecycle edit.
- `POST /{work_item_id}/defer` is the human control-plane action that parks
  Pending work outside the agent queue.
- `POST /{work_item_id}/delete` soft-deletes version-protected work and
  returns `DeletionResult`.
- `GET /{work_item_id}/checkpoints` returns a stable checkpoint page.
- `POST /{work_item_id}/checkpoints` appends an immutable `context` or
  `progress` checkpoint (201).
- `GET /{work_item_id}/context` returns bounded `WorkContext`.
- `GET /{work_item_id}/gates` cursor-pages exact request/answer history, including
  retained decisions after soft deletion.
- `GET /{work_item_id}/gates/{gate_id}/context` returns the exact unresolved-gate
  review context without placing the gate ID in a query string.
- `POST /{work_item_id}/gates` atomically requests human input (201).
- `POST /{work_item_id}/gates/{gate_id}/resolve` records one immutable human
  answer and its reviewed context revision.
- `GET /{work_item_id}/events` pages the immutable event timeline.
- `POST /{work_item_id}/events` appends one client-authored `progress` event
  (201); every authoritative event type is server-reserved.
- `POST /{work_item_id}/complete` atomically adds a completion checkpoint and
  marks Pending work done.
- `POST /{work_item_id}/claim` atomically acquires or replays an expiring lease.
- `POST /{work_item_id}/claim-and-recall` acquires/replays the lease and returns
  bounded context inside the same transaction.
- `POST /{work_item_id}/renew-claim` renews an unexpired matching capability.
- `POST /{work_item_id}/release-claim` releases a matching retained capability.
- `GET /{work_item_id}/relationships` pages immediate adjacent graph facts.
- `GET /{work_item_id}/children` pages subtree-aware direct child branches.
- `POST /{source_work_item_id}/merge` permanently merges that reviewed source
  root into one reviewed destination root and returns `WorkMergeResult` (201).
- `GET /projects/{project_id}/human-attention` cursor-pages unresolved gates and
  their work summaries; `limit=0` returns the exact text-free count.
- `POST /projects/{project_id}/duplicate-suggestions` compares one transient
  creation draft and returns canonical-grouped evidence without creating a
  receipt or domain event.

There are no checkpoint update/delete routes. PostgreSQL also rejects direct
`UPDATE` and `DELETE` against checkpoint rows.

There are no event update/delete routes. PostgreSQL rejects direct event
`UPDATE` and `DELETE` statements.

Current work-item responses never return `open`. Immutable event readers still
accept historical `open` snapshots written before migration `0012`; new events
use `pending` and `deferred`.

### Work-item requests

`WorkItemCreate`:

```json
{
  "title": "Investigate stale cache entries",
  "summary": "Cached state survives invalidation after a branch switch.",
  "priority": 40,
  "status": "pending",
  "initial_checkpoint": {
    "prompt": "Exact complete cold-session context",
    "source_client": "claude-code",
    "source_session_id": "opaque-session-id",
    "source_model": null,
    "source_session_url": null,
    "repository_branch": "feature/cache",
    "verified_against": "abc1234",
    "affected_paths": ["app/services/**", "tests/test_cache.py"],
    "tags": ["cache", "correctness"],
    "source_metadata": {}
  },
  "initial_relationships": [
    {
      "type": "discovered-from",
      "direction": "outgoing",
      "other_work_item_id": "11111111-1111-4111-8111-111111111111",
      "context_checkpoint_id": "22222222-2222-4222-8222-222222222222"
    }
  ]
}
```

`initial_relationships` is optional and contains at most ten entries expressed
relative to the new work item: `{type, direction: incoming|outgoing,
other_work_item_id, context_checkpoint_id?}`. An initial `discovered-from` must
be outgoing and cite a checkpoint on its existing originating target. An
incoming `parent-child` makes the existing counterpart the parent; an incoming
`blocks` makes it the prerequisite. The request also accepts the optional
top-level `client_operation_id`. Work, checkpoint, and every requested edge
commit or roll back together, and relationship creator provenance is copied
from the supplied initial checkpoint.

Fresh `duplicate-of` initial relationships are rejected with
`409 duplicate_merge_required`; only `merge_work` may create new duplicate
evidence. The request shape still parses the literal so a completed pre-0016
`create_work` receipt can dispatch to the backend and replay before that
current-state guard.

`status` may initially be `pending`, `wont-do`, or `promoted`, never
`deferred` or `done`. Priority is an integer from 0 through 100 and defaults to
0.

`WorkItemPatch` contains `expected_version` and at least one of `title`,
`summary`, `priority`, or `status`. It may move Pending work to
`wont-do`/`promoted`, return Deferred or terminal work to `pending`, and cannot
set `deferred` or `done`. It may contain `lease_token`; a transition from
Pending to `wont-do` or `promoted` requires the matching token while an
unexpired lease exists and removes that lease atomically. Identity-only edits
remain version-controlled and do not require a token.

`WorkDeferralCreate` is
`{expected_version, actor?, client_operation_id?}`; actor is required when
the operation ID is present. The dedicated defer route is exposed to the
same-origin dashboard but deliberately absent from the agent MCP surface. It
accepts only Pending work, rejects an active lease with
`lease_held`, clears an expired retained lease, sets `deferred`, and increments
the work version atomically. Deferred work is excluded from ready discovery and
cannot be claimed or completed. A human can return it to Pending in the
dashboard; an agent may request the same `deferred -> pending` transition only
when the current human instruction explicitly asks it to work on that item.

`WorkItemPatch` also accepts optional top-level `client_operation_id` and nested
`actor: {actor_client, actor_session_id, actor_model?}`. Actor provenance is not
an editable work field: a patch containing only `expected_version` and `actor`
is rejected and cannot consume a version. Canonical MCP/dashboard callers send
both; actor is required when the operation ID is supplied. An unkeyed direct
REST caller may omit actor and emits an unattributed event.
`WorkDeletionCreate` accepts `expected_version`, optional `lease_token`,
optional `client_operation_id`, and the same nested `actor`. The token is
optional for unleased work and required when a lease is active. Actor is
required for a keyed operation, while omission remains valid for unkeyed direct
REST and is recorded as unattributed provenance.
Deletion returns `active_relationships` until every adjacent edge is removed.
An unresolved human gate independently returns `work_gated` for deletion and
for any transition to `done`, `wont-do`, or `promoted`; identity edits,
deferral, and restoration to Pending preserve the gate.
Successful deletion removes a matching lease and returns body-bearing JSON:

```json
{
  "deleted": true,
  "project_id": "...",
  "work_item_id": "...",
  "version": 3
}
```

### Checkpoint requests

Every checkpoint payload contains exact nonblank `prompt`,
`source_client`, and `source_session_id`, plus optional `source_model`,
`source_session_url`, `repository_branch`, `verified_against`, `affected_paths`,
`tags`, and `source_metadata`. Prompt text and path order/spelling/case are not
stripped or rewritten. Tags are
normalized, de-duplicated, and capped at 20; metadata must be a JSON object no
larger than 16 KiB. The append request additionally accepts optional top-level
`client_operation_id`.

`affected_paths` is an ordered declaration of version-control paths on which
the checkpoint's assertions depend—not merely files the author changed. It has
at most 64 exact, case-sensitive entries, 512 ASCII bytes per entry, and 16,384
bytes in total. Components permit only `A-Z a-z 0-9 . _ @ + = , ~ - *`, `/` is
the only separator, `*` spans bytes within one component, and `**` is valid only
as a complete component. Empty, dot/dot-dot, absolute, drive, UNC, whitespace,
non-ASCII, shell-syntax, raw pathspec-magic, and duplicate entries are rejected.
A literal names one file or gitlink; directory scope is written `directory/**`,
and `**` means every eligible repository path.

A non-empty declaration requires a non-null hex `verified_against` commit the
caller actually inspected. Omission and explicit `[]` both mean no dependency
scope was declared; they never mean that the entire repository was checked or
that nothing changed. Their canonical request fingerprints and response form
omit the property. Non-empty lists serialize in supplied order and participate
in receipt fingerprinting and response/request coherence. A response containing
an explicit empty array is noncanonical. Historical receipt request hashes and
stored response bodies therefore remain byte-for-byte unchanged at contract
version 1.

The append route adds `kind: "context" | "progress"`, defaulting to
`context`. Callers cannot append `completion` through this generic route.
Appending changes work activity time but not its version and remains allowed on
terminal work. An optional `lease_token` is validated when supplied but is not
required; checkpoint append records an observation and never acquires, steals,
or renews ownership.

Completion accepts:

```json
{
  "expected_version": 2,
  "checkpoint": {
    "prompt": "What changed, verification observed, and remaining considerations",
    "source_client": "claude-code",
    "source_session_id": "opaque-completing-session",
    "tags": [],
    "source_metadata": {}
  },
  "lease_token": "opaque-capability-when-active",
  "client_operation_id": "55555555-5555-4555-8555-555555555555"
}
```

Only current `pending`, unblocked, ungated work can complete. Any other lifecycle
state returns `work_not_pending`, a stale expected version returns
`version_conflict`, an unresolved incoming blocker returns `work_blocked`, and
an unresolved human gate returns `work_gated`.
An active lease requires the matching token, and successful completion removes
the lease in the same transaction. An expired lease is not ownership;
presenting its stale token returns `lease_expired`.

### Lease requests and receipts

Both claim routes accept this strict JSON body:

```json
{
  "holder_client": "claude-code",
  "holder_session_id": "opaque-current-session",
  "claim_request_id": "client-generated-unique-attempt-id"
}
```

The server chooses expiry from `MNEMONIC_LEASE_TTL_SECONDS`; callers supply no
absolute time or duration. A successful claim returns `ClaimReceipt`:

```text
work_item_id, holder_client, holder_session_id, claim_request_id,
acquired_at, renewed_at, expires_at, lease_token
```

The token is a server-generated 256-bit URL-safe capability. It is returned
only by claim/replay and renewal, and accepted only in JSON bodies. It never
appears in URLs, ordinary work/search/context models, resources, errors, logs,
or browser data. `claim-and-recall` returns a `ClaimAndRecall` object containing
the `ClaimReceipt` under `lease` and bounded `WorkContext` under `context`.

While retained and active, an identical holder/session/request replay returns
the same token and timestamps without extending expiry, even if a blocker was
added after acquisition. A different tuple returns `work_blocked` when an
unresolved blocker also exists and otherwise `lease_held`. Once that retained
request has expired, the identical
request returns `claim_request_expired`; a new request ID can replace the row
and acquire a fresh lease. This is bounded lost-response recovery, not general
idempotency.

`renew-claim` accepts `{"lease_token": "..."}` and requires a matching
unexpired row. It returns the same token/request ID with database-timed renewal
and expiry values. `release-claim` accepts
`{lease_token, client_operation_id?,
actor?: {actor_client, actor_session_id, actor_model?}}`.
Canonical clients supply the operation ID and release actor; actor is required
when the ID is present. A token-only unkeyed direct REST release is valid and
recorded as unattributed. The retained holder appears only as the
released capability subject, never as the event actor. Release deletes a
matching retained row even after expiry. An absent row returns
`{work_item_id, released: false}`.
A different active replacement returns `lease_token_mismatch`; a different
expired row remains untouched and also returns `released: false`.

Lease acquisition, replay, renewal, and release do not change work version or
`updated_at`. Only Pending work can be claimed. Deferred work stays outside
autonomous discovery and claim paths until a human returns it to Pending or
explicitly directs an agent to do so. Pending visible work is eligible for a
new or replacement claim only when no unexpired lease, no unresolved incoming
`blocks` edge, and no unresolved human gate exists. Exact active claim replay,
renewal, and release remain available after a gate is requested; gating does not
revoke a capability already issued.
Only a blocker source in `done` resolves that edge; `wont-do` and `promoted` do
not. A blocker added after acquisition makes work both active and blocked
without revoking the retained lease.

### Search and pagination

Work list/search accepts:

| Key | Contract |
| --- | --- |
| `q` | optional text, at most 500 characters |
| `semantic` | false by default; true opts into hybrid retrieval |
| `status` | `pending` by default; one lifecycle status, `active`, `dropped`, or `all` |
| `sort` | `updated` by default; `updated`, `created`, or `priority`, descending |
| `tag` | matches any checkpoint |
| `source_client` | matches any checkpoint |
| `source_session_id` | matches any checkpoint |
| `view` | `full` by default; `roots` for structural root browsing |
| `duplicate_scope` | `canonical` by default; `aliases` or `all` only for explicit audit |
| `canonical_work_item_id` | optional root UUID, valid only with `aliases` or `all` |
| `limit` | 30 by default, maximum 100 |
| `offset` | 0 by default |

`active` and `dropped` are derived lease filters, not lifecycle statuses. Both
match Pending work: `active` requires an unexpired lease, while `dropped`
requires a retained lease whose expiry has passed. The `pending` filter means
Pending work with no retained lease, keeping Pending, Active, and Dropped
visually distinct. `deferred` is a persisted lifecycle filter, and `all`
includes every lifecycle and lease state. Dropped work records an unexpectedly
terminated session; it has no active owner and may be ready for a new claim.

`duplicate_scope=canonical` omits aliases as independent rows and groups matches
from every visible member under its current root before offset pagination.
`duplicate_scope=aliases` returns only retained aliases, while `all` returns
roots and aliases separately. `canonical_work_item_id` narrows the latter two
scopes to one group and must itself name a visible root. Hierarchy
`view=roots` permits only `duplicate_scope=canonical`.

Blank `q` uses the selected ordering: most recently updated, most recently
created, or highest priority first. Updated time breaks priority ties. IDs
provide deterministic final tie-breaking. A nonblank query searches weighted work
title/summary, checkpoint text, and literal IDs/provenance/tags without
duplicating work rows. Lexical `total` is the number of matching work items.
Hybrid `total` retains the full lifecycle/metadata-qualified candidate count;
relevance controls its page order, with the selected sort as a deterministic
tie-breaker. Search results never contain prompt or
source-metadata bodies.

Opt-in semantic search acquires the same one-slot process-wide inference gate
as duplicate suggestions before opening its database snapshot. If the 50 ms
capacity wait expires, a valid semantic request returns typed 503
`semantic_unavailable`; clients can retry as lexical search. Existing-work
semantic text is SQL-bounded to the first 1,500 initial-prompt characters and a
1,500-character tail across later checkpoints. Derived cache refresh occurs in
a separate post-snapshot transaction, skips locked work rows, and bounds cache
lock and statement waits; a bounded cache timeout retains the computed ranking.

`view=full` returns flat `WorkSearchHit` pages. Each hit has `summary`, the
returned root or explicitly scoped member's full `WorkSummary`, and
`matched_member`, the exact member whose stored text supplied that match. A
blank-query row matches itself. The pointer is retrieval evidence, not merge
authority and not permission to substitute its ID. Each summary carries a
bounded root-to-parent `ancestor_path` plus an `ancestor_path_truncated` flag.
A nonblank `q` requires `full`.
`view=roots` forbids free-text search and returns
`HierarchySummary` root branches. Root filters are subtree-aware: a structural
root remains when it or any descendant matches, and `total` counts qualifying
roots rather than descendants.

Checkpoint list accepts `order=oldest|newest`, `limit` up to 100, and
`offset`. Context accepts `recent_limit`, default 5 and maximum 20.
Child pages inherit `status`, `sort`, `tag`, `source_client`, and `source_session_id`,
with `limit` defaulting to 50 (maximum 100) and `offset=0`; totals count
qualifying direct child branches. Relationship pages use the filters documented
in the relationship contract below.

### Duplicate suggestions

`POST /projects/{project_id}/duplicate-suggestions` accepts exactly:

```json
{
  "title": "Verify backup restoration",
  "summary": "Check that the backup restores durable work.",
  "initial_prompt": "Restore into an isolated database and verify the selected records.",
  "tags": ["backup", "verification"],
  "exclude_work_item_id": null,
  "limit": 5
}
```

Text and tag limits and normalization match the valid creation fields. `tags`
defaults to `[]`, `exclude_work_item_id` to `null`, and `limit` to 5 with a
maximum of 10. No provenance, operation UUID, lease token, relationship,
canonical choice, create flag, unknown field, or silent truncation is accepted.
An exclusion must name visible same-project work and removes its complete
canonical group.

The strict response is:

```text
DuplicateSuggestionPage {
  items: [{
    canonical_work: {
      work_item_id, title, summary, status, updated_at,
      duplicate_member_count
    },
    matched_member: {id, title, status},
    rank,
    signals: [exact_title | lexical | semantic]
  }],
  limit,
  mode: hybrid_full | hybrid_shortlist | lexical,
  semantic_available,
  semantic_scope: full_project | lexical_shortlist | unavailable,
  composition_version,
  exact_title_group_total,
  omitted_exact_title_group_count
}
```

Ranks are contiguous from one; roots are unique; signals use the displayed
closed order with no duplicates. The mode, semantic availability, and scope
must agree. The candidate summary exposes no readiness, checkpoint body or
provenance, lease holder/session, gate detail, raw score, vector, merge control,
or operation capability. Candidate titles and summaries are returned exactly
as stored, including boundary whitespace; create-draft trimming and
normalization are not reapplied to retained work.

Selection is `duplicate-suggestion-v1`. The immutable PostgreSQL-17 title key
applies NFKC, trims and collapses POSIX whitespace, and lowercases under C
collation. All visible members participate in the indexed exact-title lane;
canonical groups reserve result slots before other lanes and exact total/omitted
counts describe the global lane. Weighted lexical search uses title, summary,
the 30 most-recent distinct normalized tags (chosen by latest checkpoint
occurrence and emitted lexicographically), the first 1,500 characters of the
initial prompt, and a SQL-bounded 1,500-character tail from later checkpoints,
then retains at most 200 non-exact groups. Optional local
`BAAI/bge-small-en-v1.5` rank uses RRF K=60 with lexical weight 3.0 and groups
before the public limit. The cache version includes `tags=recent-30` alongside
the composition, title-key, model, dimensions, text bounds, and rank weights.

Full semantic scope is reported only for a project of at most 10,000 visible
members when all current vectors are cached. Otherwise semantic work is limited
to the lexical shortlist and at most 128 missing vectors. The process-wide
inference gate is shared with ordinary semantic search. Suggestions wait at
most 50 ms for capacity, then fall back to deterministic lexical 200; model
load, inference, vector, or derived-cache failure has the same fallback.
Database/system failure returns the typed 503.

One absolute 60-second request deadline begins before body handling and spans
inference and application work. The PostgreSQL-17 snapshot transaction sets
transaction, statement, and lock timeouts from the remaining route budget.
Existing-work cache updates occur afterward in a separate digest-checked
transaction, skip locked work rows, and cap cache lock waits at 50 ms within
that remaining budget. A cache-row lock timeout therefore falls back without
extending the transport deadline. The draft vector and result are never
persisted. The request creates no work, relationship, event, receipt,
version/activity change, or live invalidation.

### Ready-work discovery

`GET /projects/{project_id}/ready-work` accepts exactly `min_priority` (default
0), normalized exact `tag`, direct `parent_work_item_id`, `limit` (default 30,
maximum 100), and nonnegative `offset`. An unknown/cross-project parent returns
the same `work_item_not_found` 404 as other project-scoped work lookups.

A returned item is visible canonical `pending` work with no active lease, no
unresolved incoming `blocks` edge, and no unresolved human gate at one captured
database time. An alias is never ready. Only a blocker in `done` is resolved. A resolved gate no longer affects
readiness; its answer remains in gate/event history and bounded recall.

The exact order is `priority DESC, created_at ASC, id ASC`. `total` is the exact
filtered ready count from the same statement as the page. Offset pages are one
statement snapshot but can shift after concurrent claims, graph/lifecycle
changes, or new work; clients restart from zero when completeness matters.

The strict `ReadyWorkPage` reuses only `WorkSummaryMinimal`: compact work
identity, priority/version/activity time, checkpoint count, and a
`display_state` of `pending` or `dropped`. It never contains summary,
checkpoint/body/source
metadata, readiness internals, active-holder identity, or capabilities.

Ready discovery is advisory. It is separate from lexical/semantic retrieval,
does not reserve work, and cannot bypass the atomic readiness recheck performed
by `claim_work` or `claim_and_recall`.

### Phase 11 response shapes

`WorkItemRead` contains:

```text
id, project_id, title, summary, status, priority, initial_checkpoint_id,
version, created_at, updated_at
```

The exact detail route wraps that unchanged, receipt-bearing shape as
`WorkItemDetailRead={work_item,canonical}`. `CanonicalWorkProjection` contains
`is_duplicate`, nullable `direct_destination`, `canonical_work_item`, `path`,
and `duplicate_member_count`. Pointers contain only `id`, `title`, and stored
`status`. A root points to itself with no direct destination and an empty path;
an alias path begins with its direct destination, ends at its current root, and
has at most 50 hops. The member count excludes the root itself.

`CheckpointRead` contains:

```text
id, work_item_id, kind, prompt, source_client, source_session_id, source_model,
source_session_url, repository_branch, verified_against, tags, source_metadata,
migration_origin, legacy_record_id, created_at
```

It additionally contains optional `affected_paths` when and only when the
stored declaration is non-empty. Historical and newly explicit-empty values are
represented by property absence. Full create/add/complete responses, checkpoint
history, and bounded context preserve a non-empty list exactly.

`CheckpointPointer` omits prompt, source session URL, and source metadata. It
retains compact source/session/model, repository, tag, migration, kind, ID, and
time fields. It also omits `affected_paths`; search, hierarchy, relationships,
gates, readiness, events, embeddings, suggestions, and cache identity remain
scope-free. A compact pointer must be followed by a full recall before a local
freshness assessment.

### Repository freshness boundary

The REST API and MCP adapter validate, persist, authorize, and transport only
the caller's declaration. They accept no repository root, changed-path result,
or freshness state; expose no assessment endpoint or tool; execute no Git or
filesystem command; and persist no assessment. Project `repository_url` and
checkpoint `repository_branch` are display context, not repository identity or
revisions to resolve.

The installed plugin alone may compare a governing full checkpoint with the
explicitly selected current local workspace. Its advisory result is
`unchanged`, `changed`, or `indeterminate`. “Unchanged” means only that two
bounded, stable sweeps observed no relevant eligible Git change under all
required preconditions. It does not prove that the checkpoint is semantically
correct, current, verified, safe, or authorized. `changed` and `indeterminate`
require reinspection before relying on the checkpoint; no result changes work,
gates, claims, readiness, versions, events, or receipts.

`WorkSummary` contains `work_item`, `checkpoint_count`, `ancestor_path`,
`ancestor_path_truncated`, `current_context` as a pointer, and `readiness`.
`WorkSummaryMinimal` contains only a `work_item` pointer (`id`, `title`,
`status`, `priority`, `version`, `updated_at`), `checkpoint_count`, and
`display_state`.
The ancestor path is root-to-parent for every full flat result and empty only
for a structural root; hierarchy root/child summaries keep their documented
empty compact-summary path. `Readiness` contains lifecycle, terminal, active,
dropped, blocked, and ready booleans, unresolved blocker and human-gate counts,
`is_gated`, `is_duplicate`, `canonical_work_item_id`, display state, and an
optional safe active lease. An alias has `is_ready=false`; display precedence
is duplicate, non-Pending lifecycle, waiting, blocked, active, dropped, then Pending;
independent flags remain authoritative because gated, lease, and blocked facts
can overlap.

`LeasePublic` contains only holder client/session and acquired, renewed, and
expiry timestamps. `Readiness.active_lease` uses that safe projection and never
contains request ID or token. Its independent `has_active_lease`, `is_ready`,
and lifecycle fields remain authoritative.

`WorkCreation` contains `work_item`, `initial_checkpoint`, and the exact
`initial_relationships` edges created in the transaction (empty when omitted).

`WorkContext` contains:

```text
work_item
merge_review_revision
canonical
duplicate_members
duplicate_member_total
omitted_duplicate_member_count
initial_checkpoint
current_context
current_context_is_initial
recent_checkpoints
checkpoint_total
omitted_checkpoint_count
unresolved_gates
unresolved_gate_total
omitted_unresolved_gate_count
recent_resolved_gates
resolved_gate_total
omitted_resolved_gate_count
recent_events
event_total
omitted_event_count
pre_phase5_history_may_be_incomplete
readiness
incoming_relationships
outgoing_relationships
undirected_relationships
relationship_counts
omitted_relationship_counts
duplicate_merge_eligibility
```

`current_context` is the newest context-kind checkpoint, not the newest
progress or completion record. It is `null` when that checkpoint is the initial
one, in which case `current_context_is_initial` is `true` and the client reads
`initial_checkpoint`; no checkpoint body is ever serialized twice. Recent
checkpoints are chronological and exclude the initial/current IDs.
`checkpoint_total` counts the whole history and `omitted_checkpoint_count`
counts what this payload left out. Context returns at most 20 unresolved gates
and 20 recent resolved gates, with exact totals and omitted counts. The nested
`GET .../gates/{gate_id}/context` route guarantees its unresolved gate is
present in the bounded gate slice for a one-snapshot human review; ordinary
context accepts no focus-ID query. Page complete paired decisions through the
dedicated gate-history route. In ordinary bounded context, each immediate
relationship list contains at most 100 pointer-only counterparts,
`relationship_counts` covers all adjacent edges by direction, and
`omitted_relationship_counts` identifies what was not materialized. The
`merge_review_revision` contains positive `work_version`, non-null newest
context-checkpoint ID, and positive committed work-event count. Context returns
at most 20 alias pointers, placing the requested alias first when applicable,
with exact total and omission count. `duplicate_merge_eligibility` reports
source incident block count, incident parent-child count, unresolved-gate fact,
and `source_lease_state=none|expired|active`. The valid nested
review route is the deliberate exception: that same one-statement review
materializes every adjacent relationship, so drift review cannot be prepared from an omitted edge. Focused response size therefore grows with focal
work-item degree and clients must request it only for explicit human review;
invalid or resolved gate IDs are rejected.

`HierarchySummary` contains `summary` (a compact `WorkSummary`),
`self_matches_filter`, `has_matching_descendants`, and `presentation`. Root and
child summaries have empty ancestor paths. The flags distinguish a direct
filter match from an ancestor retained only to navigate to a matching
descendant. `presentation` contains exact direct-child and strict-descendant
counts; blocked, active, completed, and discovered descendant counts; an
inclusive branch unresolved-human-gate count; `branch_merged_duplicate_count`;
`is_discovered_work`;
`discovered_from_parent`; and the earliest active descendant lease expiry. All
fields come from one database statement and one database-time snapshot.

Offset pages contain `items`, `total`, `limit`, and `offset`. Human-attention
and per-work gate-history pages instead contain `items`, `total`, `limit`, and
`next_cursor`.
`WorkCompletionRead` contains `work_item`, `checkpoint`, and optional
`completion_evidence`. The evidence field is absent when no child rows exist;
it is never `null` and never an object with two empty families. A present object
always includes both `verification_results` and `artifact_references`, using
`[]` only for its empty family.

## Structured completion evidence

`POST /projects/{project_id}/work-items/{work_item_id}/complete` extends the
existing strict request with:

```json
{
  "expected_version": 7,
  "checkpoint": {
    "prompt": "Implemented and verified the requested change.",
    "source_client": "codex",
    "source_session_id": "opaque-current-session",
    "verified_against": "7ad62e4",
    "affected_paths": ["backend/**"],
    "tags": [],
    "source_metadata": {}
  },
  "client_operation_id": "11584ccf-c787-4c6a-bb89-a69a02c1554d",
  "completion_evidence": {
    "verification_results": [
      {
        "verification_type": "command",
        "name": "Backend tests",
        "outcome": "passed",
        "summary": "The PostgreSQL-backed suite passed without skips.",
        "command": "uv run pytest -q",
        "exit_code": 0,
        "observed_at": "2026-09-04T18:01:02Z",
        "observed_at_commit": "7ad62e4"
      }
    ],
    "artifact_references": [
      {
        "artifact_type": "pull_request",
        "label": "Reviewed change",
        "reference": "https://github.com/example/mnemonic/pull/123"
      }
    ]
  }
}
```

Omitting `completion_evidence`, supplying `{}`, or supplying only omitted/empty
arrays all canonicalize to field absence. Explicit `null` for the outer field,
either array, or any optional evidence value is invalid. Non-empty evidence has
1–20 total children and at most 32,768 charged UTF-8 string bytes. Order,
spelling, case, and internal whitespace are fingerprinted and preserved except
that a valid `observed_at` is emitted as the same instant in canonical UTC.

Verification is a strict discriminated union. Both `command` and `observation`
records require `name`, `outcome`, and `summary`; outcome is `passed`, `failed`,
`inconclusive`, or `skipped`. A command cannot be `skipped`: `passed` requires
`exit_code=0`, `failed` requires a nonzero signed 32-bit exit code, and
`inconclusive` omits the exit code. Observations forbid command fields.
`observed_at` is an exact bounded RFC 3339 timestamp and
`observed_at_commit`, when supplied, is 7–64 lowercase hexadecimal characters.

Artifact types are `commit`, `pull_request`, `branch`, `test_run`,
`repository_path`, `external_issue`, and `build_artifact`. Commit references
are 7–64 lowercase hexadecimal characters. Repository paths are exact relative
non-glob paths under the Phase 10 safe component grammar. Branches preserve
accepted case and internal whitespace but reject edge whitespace. URL-backed
types require an absolute ASCII lowercase-`https://` URL with a valid host and
no credentials, query, fragment, whitespace, or control characters. Bracketed
IPv6 literals require exact lowercase canonical compressed spelling. Duplicate
`(artifact_type, reference)` pairs within an episode are invalid. No first-party
component dereferences or executes any stored field.

The completion checkpoint, pending-to-done transition, completion event,
evidence children, lease departure, and optional durable receipt are one
transaction. Every child has a server UUID, exact work/checkpoint IDs,
zero-based contiguous family position, and `created_at` equal to the completion
checkpoint time. Exact completed receipt replay returns the original response
without creating another episode. Evidence cannot be inserted, changed,
moved, or deleted separately; correction requires an explicit reopen and later
new completion.

`GET /projects/{project_id}/work-items/{work_item_id}/completion-evidence`
accepts `limit=1..10` (default 10) and an opaque `cursor`. Its strict response is:

```text
work_item_id, work_version, lifecycle_status,
is_duplicate, canonical_work_item_id,
current_completion_checkpoint_id,
as_of_completion_event_id,
items, total, structured_completion_total, limit, next_cursor
```

Each item contains the decimal-string `completion_event_id`, a compact
`completion_checkpoint`, and both evidence arrays. The read returns every exact
completion episode newest first, including pre-0019/evidence-free episodes with
empty arrays. `structured_completion_total` counts episodes having at least one
child; `total` counts every episode at the stable first-page high-water mark.
The current checkpoint pointer is non-null only for visible canonical work that
is presently `done` and owns a completion episode. Work completed before
`0010_work_events` has no episode at all, so the pointer is `null` and `items`
is empty while `lifecycle_status` stays `done`; that is a complete answer, not
an error. An exact alias retains its own history and separately names
its canonical continuation; no redirect or history blending occurs. Soft-deleted
work is concealed from this ordinary read while its evidence rows remain intact
for receipt recovery and operator audit.

The API serializes the entire page before return and rejects a body over
3,145,728 UTF-8 bytes. Every first-party reader requests identity encoding,
rejects any non-identity or malformed `Content-Encoding` before consuming the
body, and incrementally rejects byte 3,145,729 before UTF-8 or JSON parsing.
Generated compact completion request/fingerprint/response/receipt and database
JSON representations remain at most 896 KiB; deployed REST/browser ingress is
independently capped at 1,048,576 raw bytes.

### Sparse, retry, and correction examples

The complete REST request documents
[`completion-with-evidence.json`](../examples/completion-with-evidence.json)
and
[`completion-without-evidence.json`](../examples/completion-without-evidence.json)
show the present and omitted field shapes. The latter contains no
`completion_evidence` member at all; its successful `WorkCompletionRead` also
omits that member. If the first non-empty request has an unknown outcome, send
that exact same body—including the same checkpoint, ordered nested evidence,
expected version, lease token if any, and `client_operation_id`—to the same
completion route. An exact retry returns the original response. Do not create
a new UUID or substitute the sparse request.

Correction is a new lifecycle, never an evidence edit. After the first
completion returns work version 8, a protected REST reopen uses a different
intent:

```json
{
  "expected_version": 8,
  "status": "pending",
  "actor": {
    "actor_client": "codex",
    "actor_session_id": "replacement-session"
  },
  "client_operation_id": "696f8c9a-c221-49fb-b61d-e334ee71ae95"
}
```

Its version-9 result can then be completed with the separate sparse request.
The resulting exact history page is
[`completion-evidence-history.json`](../examples/completion-evidence-history.json):
the version-10 current episode has two empty arrays, while the older episode
retains its structured command. Empty arrays assert only that no structured
rows were recorded for that episode.

The equivalent MCP sequence uses the same frozen-intent rule (illustrative
function-call notation; replace every value with facts from the exact work):

```text
first_intent = {
  project_id, work_item_id, expected_version: 7,
  checkpoint: first_checkpoint,
  client_operation_id: "11584ccf-c787-4c6a-bb89-a69a02c1554d",
  completion_evidence: observed_evidence
}
complete_work(first_intent)
complete_work(first_intent)  # exact unknown-outcome retry; no mutation repeats

update_work(
  project_id, work_item_id, expected_version: 8,
  changes: {status: "pending"},
  actor_client: "codex", actor_session_id: "replacement-session",
  client_operation_id: "696f8c9a-c221-49fb-b61d-e334ee71ae95"
)
complete_work(
  project_id, work_item_id, expected_version: 9,
  checkpoint: replacement_checkpoint,
  client_operation_id: "573d6fe4-5bd0-452c-8400-bc29ee7bf1f7"
)  # completion_evidence omitted
list_completion_evidence(project_id, work_item_id, limit: 10)
```

MCP requires an operation UUID even for the sparse completion. A client that
lost any frozen argument must stop and reconcile safely rather than improvise
a retry.

## Human-gate contract

Only `gate_type="human"` exists. A request is valid only for visible Pending
work, including Pending work with an active or expired retained lease. Agents
must check existing unresolved gates and append any supporting `context`
checkpoint before requesting, because the request anchors the newest context,
work version, and relationship history.

`POST /projects/{project_id}/work-items/{work_item_id}/gates` accepts:

```json
{
  "gate_type": "human",
  "question": "Which rollout policy should this work use? Do not include secrets.",
  "requested_by_client": "claude-code",
  "requested_by_session_id": "opaque-current-session",
  "requested_by_model": null,
  "client_operation_id": "00000000-0000-4000-8000-000000000007"
}
```

`question` is exact nonblank text of 1–4,000 characters. Requester fields are
asserted provenance, not authenticated identity. Direct REST may omit the
operation UUID and then has no retry protection; MCP requires it. Exact
request-known credential or operation-control values in durable gate fields
return sanitized `422 gate_secret_echo`. Gate IDs are public references and
there is no database-wide retained-UUID content scan. This is not universal
secret detection: clients must still keep every credential, capability,
operation UUID, private chain-of-thought, and unnecessary transcript out of a
question or answer.

A new request locks the work, freezes its three-part revision, inserts one
immutable gate with a monotonic `attention_sequence`, appends exactly one
`human_attention_requested` event, advances activity without consuming a work
version, and completes its optional receipt atomically. Completed receipt
replay and UUID conflict handling occur before current-state lookup.

Every `HumanGateRead` contains the immutable request and provenance, the nested
`requested_context_revision` (`work_version`, `context_checkpoint_id`, and
`relationship_event_count`), status, `current_context_revision`, and four
backend-computed drift booleans. `context_changed_since_request` is the OR of the
work, context-checkpoint, and relationship drift flags. A resolved row
additionally contains the answer, resolver provenance/time,
`resolved_context_revision`, and backend-computed
`context_changed_at_resolution`. The resolution-drift boolean is derived from
the retained requested and resolved revisions rather than independently
persisted. Clients validate these fields and their status-dependent nullability;
they do not reconstruct the server-owned drift values. The response has no
acknowledgement field.

`POST .../gates/{gate_id}/resolve` accepts:

```json
{
  "resolution": "Use the staged rollout policy.",
  "resolved_by_client": "mnemonic-dashboard",
  "resolved_by_session_id": "opaque-dashboard-session",
  "resolved_by_model": null,
  "reviewed_context_revision": {
    "work_version": 3,
    "context_checkpoint_id": "11111111-1111-4111-8111-111111111111",
    "relationship_event_count": 2
  },
  "client_operation_id": "00000000-0000-4000-8000-000000000008"
}
```

`reviewed_context_revision` is required on every resolution, even when no drift
occurred. The person first loads the unresolved gate's one-snapshot review
context, reviews the exact work version, newest context checkpoint, and complete
relationship set, then submits that exact tuple. Resolution locks and
revalidates it. Intervening drift returns `409 gate_context_changed` and rolls
back the new receipt reservation; changed reviewed state is a new intent and
requires a new operation UUID. Success changes the gate exactly once, appends
one `human_attention_resolved` event, and advances activity without changing the
work version. A resolved gate is immutable; another new intent returns
`409 gate_already_resolved`, while an exact completed receipt replay still
returns its frozen success.

`GET /projects/{project_id}/human-attention` returns unresolved gates in
`attention_sequence,id` order. Each item is exactly
`{gate: HumanGateRead, summary: WorkSummary}` with a current full summary. It accepts
optional `work_item_id`, `limit=0..100`, and opaque `cursor`; `limit=0` accepts no
cursor and returns an exact text-free count (`items=[]`, `next_cursor=null`).
The envelope is
`{items,total,limit,next_cursor}`. Pass `next_cursor` back unchanged. Sequence
allocation happens before commit, so a forward traversal can miss a lower
sequence that commits later; restart once without a cursor before concluding
the queue is drained. A malformed, foreign-scope, or filter-mismatched cursor
returns `422 invalid_cursor`; restart without a cursor.

`GET .../{work_item_id}/gates` accepts `status=all|unresolved|resolved`,
`limit=1..100`, and opaque `cursor`, with the same cursor envelope. It is newest
request first and remains available for an exact retained soft-deleted work ID.
The `all` traversal is stable across state changes; a state-filtered cursor can
be invalidated, in which case restart from the first page.

`GET .../{work_item_id}/gates/{gate_id}/context` accepts only `recent_limit` and
`recent_event_limit`, requires that gate to remain unresolved on visible work,
and returns the complete adjacent relationship review in the same WorkContext
statement. Ordinary context accepts no gate focus query.

An unresolved gate independently makes Pending work `waiting`, excludes it from
ready discovery and fresh/replacement claims, and rejects completion, terminal
transitions, and deletion with `work_gated`. It does not revoke an existing
lease: exact active replay, renewal, release, checkpoints, progress, identity
edits, deferral/Pending restoration, and relationship changes remain available.
Deferral is an independent human hold; resolving a gate does not automatically
move Deferred work to Pending. Several gates may coexist and every one must be
resolved before waiting ends. Agents cannot withdraw or resolve a gate; if one
becomes moot, they append a visible context checkpoint and a person resolves it
as "No longer needed".

## MCP contract

The catalog is exactly 28 tools:

```text
list_projects, create_project,
create_work, search_work, list_ready_work, get_work, add_checkpoint,
list_checkpoints, recall_work, request_human_input, list_human_attention,
list_work_gates, append_event, list_work_events,
update_work, complete_work, list_completion_evidence, delete_work,
claim_work, claim_and_recall, renew_claim, release_claim,
add_relationship, get_relationship, list_relationships, remove_relationship,
merge_work, suggest_duplicate_work
```

Exactly `create_work`, `add_checkpoint`, `append_event`, `add_relationship`,
`update_work`, `complete_work`, `delete_work`, `remove_relationship`,
`release_claim`, `request_human_input`, and `merge_work` require a
caller-generated `client_operation_id` and are annotated as idempotent
mutations. Prepare the
complete arguments once, retain them privately, and retry only that exact tool,
UUID, and argument object after an unknown outcome. Project administration,
claim acquisition/recovery, and renewal retain their separate contracts.

`complete_work` accepts the same strict optional evidence object as REST and
validates the complete response against the exact frozen request, including
canonical timestamp equivalence, child ownership, positions, order, and record
time. The MCP tool always requires its operation UUID, including with no
evidence. `list_completion_evidence` is a `safe_read`: it makes only the
documented GET, follows opaque cursors without modification, and returns the
complete strict page. Neither tool executes commands, opens artifact URLs, or
turns evidence into authority.

Before SDK parsing, both Streamable HTTP and stdio accept at most 1,048,576 raw
bytes containing exactly one JSON object. HTTP accepts only absent or one
case-insensitive `identity` content coding. A present JSON-RPC ID is either a
strict signed 64-bit integer or a 1–128-character ASCII string matching
`[A-Za-z0-9._:-]+`; invalid requests are rejected without reflecting the ID.
An invalid stdio record terminates that transport without a competing response
writer. The locked SDK's complete evidence success—including JSON text,
`structuredContent`, the maximum ID, and the stdio newline—is capped at
12,582,912 bytes; evidence is never truncated to satisfy that bound.

`search_work` defaults to `view="full"` and `duplicate_scope="canonical"`.
Every result is a `WorkSearchHit`; `matched_member` identifies which exact
member supplied a grouped text match but does not grant merge authority or ID
substitution. Use `aliases` or `all` only for explicit audit, and use
`canonical_work_item_id` only with those scopes. Every summary carries the
root-to-parent `ancestor_path`, including blank-query pages. `list_ready_work`
returns strict compact pointers and directs an already-authorized selection to
`claim_and_recall`; it is not retrieval, reservation, or authority, and excludes
waiting work.

`suggest_duplicate_work` accepts the resolved project plus exactly the six
draft fields documented above. It is read-only, closed-world, and explicitly
classified `safe_read`; it takes no `client_operation_id` and sends one request
with the 60-second Advisory budget. A timeout, transport failure, 429, or 503
permits an ordinary retry because no structural outcome is uncertain. Its
strict response validator binds project-independent candidate fields, unique
canonical groups, contiguous ranks, signal order, exact counts, and coherent
mode/scope/semantic state. It never exposes scores or turns a candidate into an
automatic create, redirect, relationship, or merge.

The `request_human_input` tool description requires its caller to check existing
open questions and write supporting context first. It sends one attempt; after an unknown outcome, retry only the exact retained UUID
and argument object, never a replacement. MCP exposes no resolution or withdrawal tool: agents
direct a person to the dashboard and never infer, self-supply, or time out an
answer. `list_human_attention` is the human queue, supports text-free `limit=0`,
and requires a restart from the head before callers conclude a forward cursor
walk is drained. `list_work_gates` pages paired history newest first.

`append_event` fixes `event_type=progress`. Canonical actor/session provenance is
required for the protected mutation tools that carry an actor. Recall, resources,
and the `resume_work` prompt carry bounded gate/event slices with exact omitted
counts and untrusted-evidence warnings; none grants execution authority.

`merge_work` requires two distinct current roots, the complete
`merge_review_revision` from each exact `recall_work`, a nonblank rationale,
truthful merger provenance, and a mandatory operation UUID. The source must
have no unresolved gate or incident `blocks`/`parent-child` relationship. An
active source lease additionally requires its exact token; the destination's
lease and gate state do not transfer. Review direction as
`source duplicate -> destination canonical` and retain the complete frozen call
before the first attempt. A timeout or malformed/5xx outcome permits only the
same-key, same-arguments retry unless the typed response is
`duplicate_graph_invalid`. That integrity failure is definitive: stop
authority-changing work and involve an operator. The returned receipt is
historical; read the source and current canonical root again afterward.

Stable structured application errors are mapped to value-free guidance. A 404
names only reachable entity kinds (project, work item, checkpoint, or
relationship) when the backend supplies a typed code; otherwise it uses generic
scope wording. Structured 422 errors expose only allowlisted field paths and
error kinds. Unknown/string-detail conflicts are not guessed as slug or version
conflicts. No adapter error renders caller values, request IDs, operation IDs,
lease tokens, prompts, or upstream detail.

Every top-level input rejects unknown fields. For a protected write, a
timeout/reset, upstream 5xx, malformed success, or
`client_operation_unavailable` is an unknown outcome and causes no second
outbound attempt, except that a typed `duplicate_graph_invalid` is a definitive
stop. Safe-read suggestion failures use the separate ordinary-retry rule above.
MCP validates protected success
against strict OpenAPI-aligned response properties, required fields, requested
scope, and request coherence. Its server and `httpx` logger run at WARNING so
query text, cursors, and URL identifiers are not emitted at INFO.

The resource
`mnemonic://projects/{project_id}/work-items/{work_item_id}` and prompt
`resume_work` return bounded context. Neither executes work or grants authority.

## Browser proxy

The same-origin proxy allowlists exact project, work/checkpoint/hierarchy,
human-attention, per-work gate-history/review, relationship, and event routes
with their documented query keys. It allows gate resolution only with the
answer, asserted dashboard resolver fields, required
`reviewed_context_revision`, and operation UUID. It denies gate creation: agents
request input through MCP or an authorized direct REST client, while a person
resolves it in the dashboard.

The proxy also admits the exact merge route with the two reviewed revisions,
rationale, dashboard provenance, and required operation UUID. The browser has
no lease-token surface, so it disables merge while the source has an active
lease and directs the person to release or finish that lease through an
authorized client. It never strips or invents a token.

The proxy admits the exact completion-evidence GET with only `limit` and
`cursor`. It sends `Accept-Encoding: identity`, checks response headers before
pulling a body, strips/replaces any accepted upstream coding marker, and
incrementally caps the identity body at 3 MiB before UTF-8/JSON validation.
Non-identity or malformed coding and over-limit bodies produce only a
content-free 502. The same-origin response explicitly emits
`Content-Encoding: identity`, `Cache-Control: no-store, no-transform`, and
`X-Content-Type-Options: nosniff`; it never publishes a live invalidation.

The exact duplicate-suggestion route is the sole safe POST in this area. Its
body allowlist is only `title`, `summary`, `initial_prompt`, `tags`,
`exclude_work_item_id`, and `limit`; operation IDs, lease tokens, headers,
cookies, query parameters, and nested additions are rejected. Only this route
raises the streaming proxy body cap to 2,097,152 bytes and the transport budget
to 60 seconds. It forwards `Retry-After: 1` only for the typed busy response,
never enters the protected-intent registry, and publishes no live invalidation.

The eleven covered browser writes require one top-level operation UUID and a frozen body.
The existing completion body alone may contain `completion_evidence`; it is
validated before registration and frozen with the rest of the intent. No
standalone evidence POST or browser mutation kind exists.
Event POST accepts `{event_type,body,metadata,actor,client_operation_id}` and
rejects `lease_token` rather than stripping it. Relationship DELETE carries the
serialized actor/key body. Project relationship GET-by-ID, project ready-work,
claim, renew, release, and every capability-bearing body are denied.

The proxy rejects arbitrary paths, unknown query keys, untrusted hosts/origins,
bodies over the route-specific cap, a `lease_token` at any depth, operation IDs outside their
allowed top-level bodies, and gate IDs outside typed path segments. Invalid UUIDs
and values equal to the server bearer are rejected without echo.

Frozen protected requests live only in dashboard memory. Timeout, network, 5xx,
and malformed-success outcomes permit only exact retry. A definite
`gate_context_changed` makes the reviewed revision obsolete: keep only the
editable answer draft, reload the current gate review, and prepare a new UUID
and complete body. Reload or tab close can lose an uncertain intent, so the UI
warns before unloading; it never stores gate text, mutation bodies, UUIDs, or
credentials in browser storage.

Merge freezes one intent under both source and destination work keys so neither
endpoint can begin a conflicting browser mutation while its outcome is unknown.
The confirmation renders source and destination in separate bidi-isolated
panels, shows their full UUIDs, and requires explicit acknowledgement that the
source becomes a permanent alias. A definite stale-context rejection requires
two fresh contexts and a new UUID; an ambiguous outcome permits only exact
retry of the retained body.

## Live invalidation

The authenticated dashboard WebSocket accepts only exact control objects:
`{"type":"ready","revision":n}` and
`{"type":"invalidate","revision":n,"scope":"projects"|"work-items"}`.
Frames contain no project, work, gate, receipt, count, question, or answer data.
A scope invalidation is intentionally conservative: the browser refetches its
currently selected project/list/attention/open-context views as applicable.

## Runtime configuration

API: `DATABASE_URL`, `MNEMONIC_API_KEY` (required, at least 32 characters),
`MNEMONIC_LEASE_TTL_SECONDS` (default 900, allowed 60 through 3600),
`MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS` (default 10, allowed 1 through 10),
`MNEMONIC_EMBEDDING_CACHE`, and `MNEMONIC_DASHBOARD_ORIGINS` for exact
browser/WebSocket origins. Advisory settings are
`MNEMONIC_DUPLICATE_SUGGESTION_BODY_MAX_BYTES`, `_REQUEST_SLOTS`,
`_REQUEST_WAIT_MS`, `_INFERENCE_SLOTS`, `_INFERENCE_WAIT_MS`,
`_LEXICAL_SHORTLIST`, `_MISSING_VECTOR_LIMIT`,
`_FULL_POPULATION_CEILING`, and `_TIMEOUT_SECONDS`, where every abbreviated
name retains the `MNEMONIC_DUPLICATE_SUGGESTION` prefix. Their defaults are the
2 MiB/4/250 ms/1/50 ms/200/128/10000/60-second limits documented above.

MCP: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, `MNEMONIC_MCP_HOST`,
`MNEMONIC_MCP_PORT`, `MNEMONIC_MCP_ALLOWED_HOSTS`, and
`MNEMONIC_MCP_ALLOWED_ORIGINS`.

Dashboard server: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, and
`MNEMONIC_DASHBOARD_ORIGINS`. Credentials must never use a `NEXT_PUBLIC_*`
variable.

## Authoritative duplicate merge

`POST /projects/{project_id}/work-items/{source_work_item_id}/merge` accepts
this strict `WorkMergeCreate` shape:

```json
{
  "destination_work_item_id": "22222222-2222-4222-8222-222222222222",
  "reviewed_source_revision": {
    "work_version": 3,
    "context_checkpoint_id": "33333333-3333-4333-8333-333333333333",
    "work_event_count": 7
  },
  "reviewed_destination_revision": {
    "work_version": 5,
    "context_checkpoint_id": "44444444-4444-4444-8444-444444444444",
    "work_event_count": 12
  },
  "rationale": "These records describe the same durable objective.",
  "merged_by_client": "claude-code",
  "merged_by_session_id": "opaque-current-session",
  "merged_by_model": null,
  "lease_token": null,
  "client_operation_id": "00000000-0000-4000-8000-000000000010"
}
```

Direction is exact and irreversible: the path work item is the duplicate
source and `destination_work_item_id` is its direct canonical destination. Both
must be visible, distinct, same-project current roots at commit and both exact
three-field review revisions must still match. The source may retain any
lifecycle but must have no unresolved gate or incident `blocks` or
`parent-child` relationship. An active source lease requires its exact token; a
tokenless call clears an expired source lease. Destination lease, gate,
lifecycle, and incoming aliases do not block selection and nothing transfers.
The resulting authoritative path may not exceed 50 edges.

The operation reuses an exact historical source-to-destination `duplicate-of`
mark when present, otherwise creates it with its two endpoint events. In one
transaction it records the immutable merge, increments each endpoint version
once at one timestamp, consumes the source lease as applicable, appends one
source and one destination `work_merged` event, and completes the receipt.

`WorkMergeResult` contains exactly `merge`, the resulting source and destination
`WorkItemRead` values, `direct_destination`, `canonical_work_item`,
`supporting_relationship_created`, the exact `supporting_relationship`, zero or
two `relationship_events` ordered source then destination, and exactly two
`merge_events` ordered source then destination.
`WorkMergeRead` contains IDs and sequence, both reviewed revisions, both
resulting versions, rationale, asserted merger provenance, and timestamp. Each
public `work_merged` event uses the rationale as body and exact metadata keys
`merge_id`, `source_work_item_id`, `destination_work_item_id`, `role`,
`source_work_version`, and `destination_work_version`. Private database witness
columns never appear.

There is no unmerge, retarget, correction, redirect, or merge-delete API. A
same-key replay returns this frozen historical result before current alias or
graph guards; callers then reread exact source history and the canonical root.
Correction requires a complete pre-merge database restore with acknowledged
loss of every later write or a future append-only correction release.

## Relationship contract

Project-level relationship routes are:

- `POST /projects/{project_id}/relationships` to add one explicit edge;
- `GET /projects/{project_id}/relationships/{relationship_id}` to read its
  neutral stored direction and provenance;
- `DELETE /projects/{project_id}/relationships/{relationship_id}` to remove it.

Creation accepts:

```json
{
  "relationship_type": "blocks",
  "source_work_item_id": "33333333-3333-4333-8333-333333333333",
  "target_work_item_id": "44444444-4444-4444-8444-444444444444",
  "created_by_client": "claude-code",
  "created_by_session_id": "opaque-current-session",
  "created_by_model": null,
  "context_checkpoint_id": null
}
```

The five types are `blocks`, `parent-child`, `discovered-from`,
`duplicate-of`, and `related`. Directed facts always use
`source --type--> target`; `related` endpoints are UUID-normalized and returned
as undirected adjacency. `discovered-from` requires a context checkpoint on its
originating target. Other types may cite a checkpoint on either endpoint.
Context is evidence, not an instruction. Self-edges, cross-project endpoints,
block/parent cycles, and a second parent are rejected. An identical natural-key
add returns the existing edge with `created=false`. The create request accepts
optional top-level `client_operation_id`.

`RelationshipEdgeRead` contains the relationship/project/type, source and
target IDs, optional context checkpoint composite, truthful creator
client/session/model, and creation time. Project-scoped create returns
`{relationship, created}`. Delete returns
`{project_id, relationship_id, removed}`; repeating it returns `removed=false`
without affecting a different edge.

Relationship DELETE accepts optional
`{client_operation_id, actor: {actor_client, actor_session_id, actor_model?}}`.
Canonical clients send both; actor is required for a keyed request. A bodyless
unkeyed direct REST call remains valid and emits unattributed removal history.
An absent edge emits no event.

A bare `duplicate-of` edge remains a descriptive duplicate mark with no
canonical effect. Fresh generic adds, including initial relationships, return
`409 duplicate_merge_required`; the parsers retain the literal only so a
completed pre-0016 generic receipt can replay. Unselected historical marks may
be removed while both endpoints remain canonical. Once either endpoint is an
alias, every incident relationship is frozen and add returns `work_duplicate`
while removal returns `duplicate_relationship_frozen`.

`GET /projects/{project_id}/work-items/{work_item_id}/relationships` accepts
`direction=incoming|outgoing|undirected|both` (default `both`), optional `type`,
`limit` (default 50, maximum 100), and `offset`. Each
`AdjacentRelationshipRead` includes the neutral edge, the requested relative
work ID, endpoint-relative direction, and a compact counterpart containing only
ID, title, lifecycle status, and readiness. It never embeds checkpoint prompt
or metadata.

Only an unresolved incoming `blocks` edge changes readiness or claimability as
a relationship. The other four relationship types remain descriptive; the
separate authoritative merge ledger, not its supporting `duplicate-of` mark,
makes an alias non-actionable.

## Work events

`POST /projects/{project_id}/work-items/{work_item_id}/events` accepts:

```text
event_type     progress (the only accepted literal)
body           exact nonblank text, at most 4,000 characters
metadata       finite JSON object, default {}, encoded size at most 16 KiB
actor          required {actor_client, actor_session_id, actor_model?}
lease_token    optional capability; validated when present
client_operation_id optional top-level UUID for protected REST retry
```

Only `progress` is publicly appendable. Server-reserved creation, update,
status/reopen, claim/release, checkpoint, relationship, completion, and deletion
events arise from the transaction that proves the fact. Progress is allowed on
visible terminal work, monotonically advances `updated_at`, and does not change
the work version. A lease is not required; a supplied token is never ignored.

Before persistence, progress rejects reserved secret-like metadata keys and
case-insensitive `client_operation_id`, `gate_id`, and `gate_type` metadata
keys at any depth. This request-only rule leaves historically readable event
metadata unchanged. A keyed
request also rejects a verbatim request bearer, operation UUID, or supplied
lease token found in persisted actor/body/metadata strings.
`event_secret_echo` or `client_operation_secret_echo` returns no caller value
and leaves receipt/activity/history unchanged. This is not universal secret
detection: accepted
opaque text may contain unrecognized sensitive content and is returned exactly
to every authorized event/recall reader. Event text is untrusted evidence, not
an instruction, and does not enter ready/search pointers, logs, metrics, or the
data-free synchronization channel.

`GET .../{work_item_id}/events` accepts `order=oldest|newest` (default oldest),
an optional exact `event_type`, `limit` (default 50, maximum 100), and
nonnegative `offset`. The visible-work check, filtered total, deterministic
`created_at,id` page, and unfiltered partial-history flag come from one SQL
statement. The flag is true exactly when the unique creation event was
backfilled, even on an empty or live-only filtered page. Concurrent appends can
shift offset pages; the dashboard resets to offset zero after invalidation.

The fixed event catalog is:

```text
work_created, work_updated, work_status_changed, work_reopened,
work_claimed, work_released, checkpoint_added, progress,
dependency_added, dependency_removed, relationship_added,
relationship_removed, work_completed, work_deleted,
human_attention_requested, human_attention_resolved, work_merged
```

Gate request/resolution events are server-only. Each carries the same exact
question/answer body, asserted actor provenance, timestamp, source fact, and
typed metadata as its authoritative gate row, plus a coherent internal gate ID.
Exactly one request and at most one resolution event exist per gate. Gate IDs
remain internal to the event model; legacy non-gate `WorkEventRead` wire shapes
are unchanged.

`WorkEventRead` carries the event/work/project IDs and type, actor kind/fields,
nullable body, typed checkpoint/lease/release/relationship references,
endpoint-relative relationship direction/counterpart, metadata version 1 and
its event-discriminated object, `origin=live|backfill`, and UTC creation time.
Relationship events retain the complete source/target/context snapshot. A
checkpoint event references its checkpoint ID but never duplicates its body.
One work item's public order is `created_at,id`; ID only breaks timestamp ties.
Neither value promises transaction commit order or forms a resumable project
activity cursor.

`work_merged` is server-only and appears exactly twice per authoritative merge:
source first with role `source`, destination second with role `destination`.
Its body is the exact rationale and its version-1 metadata has only the six
public merge keys documented above. It has no checkpoint, lease, relationship,
release, or gate reference. Internal merge/witness foreign keys are excluded
from every public event and from preserved historical receipt shapes.

`WorkEventPage` has `items,total,limit,offset` plus
`pre_phase5_history_may_be_incomplete`. Reconstructed rows include only facts
provable from retained pre-Phase-5 state and use `origin=backfill`; the absence
of an old update/release/removal event is not proof that action never happened.

`WorkContext` adds:

```text
recent_events
event_total
omitted_event_count
pre_phase5_history_may_be_incomplete
```

`recent_events` is chronological, defaults to the newest ten, and is bounded by
`recent_event_limit=0..20`. `event_total` and `omitted_event_count` cover the
whole per-work timeline. The partial-history flag comes from the unique
creation-event origin and remains correct when no backfilled event is present
in the materialized slice. A referenced checkpoint body is never materialized
again inside an event.
