# Phase 3 API contract

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

Lease conflicts use stable codes: `work_not_open`, `lease_held`,
`lease_expired`, `lease_token_mismatch`, and `claim_request_expired`.
`lease_held` may expose only safe holder and expiry context. No error contains a
lease token or claim request ID.

Lifecycle and graph conflicts use stable codes including
`invalid_status_transition`, `work_blocked`, `relationship_context_invalid`,
`relationship_cycle`, `parent_already_set`, and `active_relationships`.
Self-edges and a missing discovery context fail strict request validation with
422. Missing or cross-project endpoints/checkpoints use sanitized 404 codes.
Error context never includes checkpoint content or non-allowlisted upstream values.

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

## Canonical work-item routes

Base path: `/projects/{project_id}/work-items`.

- `POST /` creates one work item, its initial context checkpoint, and optional
  initial relationships atomically, returning `WorkCreation` (201).
- `GET /` browses or searches one compact `WorkSummary` per work item.
- `GET /{work_item_id}` returns `WorkItemRead` only.
- `PATCH /{work_item_id}` performs a version-protected work identity or
  lifecycle edit.
- `POST /{work_item_id}/delete` soft-deletes version-protected work and
  returns `DeletionResult`.
- `GET /{work_item_id}/checkpoints` returns a stable checkpoint page.
- `POST /{work_item_id}/checkpoints` appends an immutable `context` or
  `progress` checkpoint (201).
- `GET /{work_item_id}/context` returns bounded `WorkContext`.
- `POST /{work_item_id}/complete` atomically adds a completion checkpoint and
  marks open work done.
- `POST /{work_item_id}/claim` atomically acquires or replays an expiring lease.
- `POST /{work_item_id}/claim-and-recall` acquires/replays the lease and returns
  bounded context inside the same transaction.
- `POST /{work_item_id}/renew-claim` renews an unexpired matching capability.
- `POST /{work_item_id}/release-claim` releases a matching retained capability.
- `GET /{work_item_id}/relationships` pages immediate adjacent graph facts.
- `GET /{work_item_id}/children` pages subtree-aware direct child branches.

There are no checkpoint update/delete routes. PostgreSQL also rejects direct
`UPDATE` and `DELETE` against checkpoint rows.

### Work-item requests

`WorkItemCreate`:

```json
{
  "title": "Investigate stale cache entries",
  "summary": "Cached state survives invalidation after a branch switch.",
  "priority": 40,
  "status": "open",
  "initial_checkpoint": {
    "prompt": "Exact complete cold-session context",
    "source_client": "claude-code",
    "source_session_id": "opaque-session-id",
    "source_model": null,
    "source_session_url": null,
    "repository_branch": "feature/cache",
    "verified_against": "abc1234",
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
`blocks` makes it the prerequisite. Work, checkpoint, and every requested edge
commit or roll back together, and relationship creator provenance is copied
from the supplied initial checkpoint.

`status` may initially be `open`, `wont-do`, or `promoted`, never
`done`. Priority is an integer from 0 through 100 and defaults to 0.

`WorkItemPatch` contains `expected_version` and at least one of `title`,
`summary`, `priority`, or `status`. It may move open work to
`wont-do`/`promoted`, return either to `open`, or reopen `done` work to
`open`. It cannot set `done`. It may contain `lease_token`; a transition from
open to `wont-do` or `promoted` requires the matching token while an unexpired
lease exists and removes that lease atomically. Identity-only edits remain
version-controlled and do not require a token.

`WorkDeletionCreate` is `{"expected_version": N, "lease_token": "..."}`;
the token is optional for unleased work and required when a lease is active.
Deletion returns `active_relationships` until every adjacent edge is removed.
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
`source_session_url`, `repository_branch`, `verified_against`, `tags`,
and `source_metadata`. Prompt text is not stripped or rewritten. Tags are
normalized, de-duplicated, and capped at 20; metadata must be a JSON object no
larger than 16 KiB.

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
  "lease_token": "opaque-capability-when-active"
}
```

Only current `open`, unblocked work can complete. A terminal item returns
`work_not_open`, a stale expected version returns `version_conflict`, and an
unresolved incoming blocker returns `work_blocked`. An active lease requires
the matching token, and successful completion removes the lease in the same
transaction. An expired lease is not ownership; presenting its stale token
returns `lease_expired`.

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
the same token and timestamps without extending expiry. Any different tuple
returns `lease_held`. Once that retained request has expired, the identical
request returns `claim_request_expired`; a new request ID can replace the row
and acquire a fresh lease. This is bounded lost-response recovery, not general
idempotency.

`renew-claim` and `release-claim` accept `{"lease_token": "..."}`. Renewal
requires a matching unexpired row and returns the same token/request ID with
database-timed renewal and expiry values. Release deletes a matching retained
row even after expiry. An absent row returns `{work_item_id, released: false}`.
A different active replacement returns `lease_token_mismatch`; a different
expired row remains untouched and also returns `released: false`.

Lease acquisition, replay, renewal, and release do not change work version or
`updated_at`. Deleted or terminal work cannot be claimed. Open visible work is
eligible for a new claim only when no unexpired lease and no unresolved incoming
`blocks` edge exists; the exact retained-request replay described above remains
available. Only a blocker source in `done` resolves that edge; `wont-do` and
`promoted` do not. A blocker added after acquisition makes work both active and
blocked without revoking the retained lease.

### Search and pagination

Work list/search accepts:

| Key | Contract |
| --- | --- |
| `q` | optional text, at most 500 characters |
| `semantic` | false by default; true opts into hybrid retrieval |
| `status` | `open` by default; one lifecycle status or `all` |
| `tag` | matches any checkpoint |
| `source_client` | matches any checkpoint |
| `source_session_id` | matches any checkpoint |
| `view` | `full` by default; `minimal` for pointer-only results; `roots` for structural root browsing |
| `limit` | 30 by default, maximum 100 |
| `offset` | 0 by default |

Blank `q` browses by recent activity. A nonblank query searches weighted work
title/summary, checkpoint text, and literal IDs/provenance/tags without
duplicating work rows. Lexical `total` is the number of matching work items.
Hybrid `total` retains the full lifecycle/metadata-qualified candidate count;
relevance controls its page order. Search results never contain prompt or
source-metadata bodies.

`view=full` returns flat `WorkSummary` pages. A nonblank `q` requires `full` or
`minimal`, and under `full` gives each direct hit a bounded root-to-parent
`ancestor_path` plus an `ancestor_path_truncated` flag. `view=minimal` returns
`WorkSummaryMinimal` pages carrying only `work_item` (`id`, `title`, `status`,
`priority`, `version`, `updated_at`), `checkpoint_count`, and `display_state`;
it skips the ancestor-path query entirely. REST defaults to `full` for the
dashboard; the MCP `search_work` tool defaults to `minimal` for agent callers.
`view=roots` forbids free-text search and returns
`HierarchySummary` root branches. Root filters are subtree-aware: a structural
root remains when it or any descendant matches, and `total` counts qualifying
roots rather than descendants.

Checkpoint list accepts `order=oldest|newest`, `limit` up to 100, and
`offset`. Context accepts `recent_limit`, default 5 and maximum 20.
Child pages inherit `status`, `tag`, `source_client`, and `source_session_id`,
with `limit` defaulting to 50 (maximum 100) and `offset=0`; totals count
qualifying direct child branches. Relationship pages use the filters documented
in the relationship contract below.

### Core response shapes

`WorkItemRead` contains:

```text
id, project_id, title, summary, status, priority, initial_checkpoint_id,
version, created_at, updated_at
```

`CheckpointRead` contains:

```text
id, work_item_id, kind, prompt, source_client, source_session_id, source_model,
source_session_url, repository_branch, verified_against, tags, source_metadata,
migration_origin, legacy_record_id, created_at
```

`CheckpointPointer` omits prompt, source session URL, and source metadata. It
retains compact source/session/model, repository, tag, migration, kind, ID, and
time fields.

`WorkSummary` contains `work_item`, `checkpoint_count`, `ancestor_path`,
`ancestor_path_truncated`, `current_context` as a pointer, and `readiness`.
`WorkSummaryMinimal` contains only a `work_item` pointer (`id`, `title`,
`status`, `priority`, `version`, `updated_at`), `checkpoint_count`, and
`display_state`.
The ancestor path is empty for browse/root/child results and root-to-parent for
free-text descendant hits. `Readiness` contains lifecycle, terminal, active,
blocked, and ready booleans, unresolved blocker count, display state, and an
optional safe active lease. Display precedence is terminal lifecycle, blocked,
active, then ready; independent flags remain authoritative because active and
blocked can overlap.

`LeasePublic` contains only holder client/session and acquired, renewed, and
expiry timestamps. `Readiness.active_lease` uses that safe projection and never
contains request ID or token. Its independent `has_active_lease`, `is_ready`,
and lifecycle fields remain authoritative.

`WorkCreation` contains `work_item`, `initial_checkpoint`, and the exact
`initial_relationships` edges created in the transaction (empty when omitted).

`WorkContext` contains:

```text
work_item
initial_checkpoint
current_context
current_context_is_initial
recent_checkpoints
checkpoint_total
omitted_checkpoint_count
readiness
incoming_relationships
outgoing_relationships
undirected_relationships
relationship_counts
```

`current_context` is the newest context-kind checkpoint, not the newest
progress or completion record. It is `null` when that checkpoint is the initial
one, in which case `current_context_is_initial` is `true` and the client reads
`initial_checkpoint`; no checkpoint body is ever serialized twice. Recent
checkpoints are chronological and exclude the initial/current IDs.
`checkpoint_total` counts the whole history and `omitted_checkpoint_count`
counts what this payload left out. Each immediate relationship list contains at most 50
pointer-only counterparts; `relationship_counts` covers all adjacent edges by
direction even when a list is truncated.

`HierarchySummary` contains `summary` (a compact `WorkSummary`),
`self_matches_filter`, and `has_matching_descendants`. Root and child summaries
have empty ancestor paths. The flags distinguish a direct filter match from an
ancestor retained only to navigate to a matching descendant.

Pages retain `items`, `total`, `limit`, and `offset`.
`CompletionResult` contains `work_item` and `checkpoint`.

## MCP contract

Canonical tools are:

```text
list_projects, create_project,
create_work, search_work, get_work, add_checkpoint, list_checkpoints,
recall_work, update_work, complete_work, delete_work,
claim_work, claim_and_recall, renew_claim, release_claim,
add_relationship, get_relationship, list_relationships, remove_relationship
```

The resource
`mnemonic://projects/{project_id}/work-items/{work_item_id}` and prompt
`resume_work` return bounded context. Neither executes stored work or grants
authority.

MCP error handling maps stable application codes and also tolerates plain
string error bodies. A 404 names the entity kind that missed — project, work
item, checkpoint, or relationship — so a caller knows whether to re-resolve the
project or search again within it, falling back to the combined wording when
the code is absent. A rejected input names the allowlisted field path and its
pydantic error kind, for example `initial_checkpoint.prompt (string_too_long)`.
Field paths are built only from allowlisted names and error kinds only from an
allowlisted set, so neither can carry a caller-supplied value; an unknown key
rejected by `extra_forbidden` reports the kind alone and never the key itself.
No error text contains a supplied value, a UUID, prompt content, a
`claim_request_id`, or a lease token.

Every top-level tool input schema rejects unknown fields and publishes
`additionalProperties: false`. Direct, HTTP, and stdio validation failures
return only allowlisted field names, never supplied values, prompt/metadata
content, claim request IDs, or lease tokens. A claim or claim-and-recall 5xx is
always treated as an unknown outcome and directs exact-request-ID recovery.

## Browser proxy

The same-origin proxy allows exact project, Phase 3 work/checkpoint/hierarchy,
and human-facing relationship list/add/remove routes with documented query
keys. Project-level relationship GET-by-ID is intentionally denied. It rejects
arbitrary paths, unknown query keys, untrusted hosts/origins, bodies over 1 MiB,
and every `lease_token` field at any nesting depth. All four claim/renew/release
routes are denied rather than stripped. The API URL and bearer key remain
server-only; the dashboard can display `LeasePublic` but never receive or
forward a token.

## Runtime configuration

API: `DATABASE_URL`, `MNEMONIC_API_KEY` (required, at least 32 characters),
`MNEMONIC_LEASE_TTL_SECONDS` (default 900, allowed 60 through 3600), and
`MNEMONIC_DASHBOARD_ORIGINS` for exact browser/WebSocket origins.

MCP: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, `MNEMONIC_MCP_HOST`,
`MNEMONIC_MCP_PORT`, `MNEMONIC_MCP_ALLOWED_HOSTS`, and
`MNEMONIC_MCP_ALLOWED_ORIGINS`.

Dashboard server: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, and
`MNEMONIC_DASHBOARD_ORIGINS`. Credentials must never use a
`NEXT_PUBLIC_*` variable.

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
add returns the existing edge with `created=false`.

`RelationshipEdgeRead` contains the relationship/project/type, source and
target IDs, optional context checkpoint composite, truthful creator
client/session/model, and creation time. Project-scoped create returns
`{relationship, created}`. Delete returns
`{project_id, relationship_id, removed}`; repeating it returns `removed=false`
without affecting a different edge.

`GET /projects/{project_id}/work-items/{work_item_id}/relationships` accepts
`direction=incoming|outgoing|undirected|both` (default `both`), optional `type`,
`limit` (default 50, maximum 100), and `offset`. Each
`AdjacentRelationshipRead` includes the neutral edge, the requested relative
work ID, endpoint-relative direction, and a compact counterpart containing only
ID, title, lifecycle status, and readiness. It never embeds checkpoint prompt
or metadata.

Only an unresolved incoming `blocks` edge changes readiness or claimability.
The other four types are descriptive in Phase 3.
