# Phase 2 API contract

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

- `POST /` creates one work item and its initial context checkpoint
  atomically, returning `WorkCreation` (201).
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
  }
}
```

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

Only current `open` work can complete. Repeated completion returns
`work_not_open`; stale completion returns `version_conflict`. An active lease
requires the matching token, and successful completion removes the lease in the
same transaction. An expired lease is not ownership; presenting its stale token
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
`updated_at`. Deleted or terminal work cannot be claimed. Before Phase 3 there
are no relationship blockers, so an open visible item is base-claimable; an
unexpired lease then determines whether it is already active.

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
| `view` | Phase 2 supports `all` only |
| `limit` | 30 by default, maximum 100 |
| `offset` | 0 by default |

Blank `q` browses by recent activity. A nonblank query searches weighted work
title/summary, checkpoint text, and literal IDs/provenance/tags without
duplicating work rows. Lexical `total` is the number of matching work items.
Hybrid `total` retains the full lifecycle/metadata-qualified candidate count;
relevance controls its page order. Search results never contain prompt or
source-metadata bodies.

Checkpoint list accepts `order=oldest|newest`, `limit` up to 100, and
`offset`. Context accepts `recent_limit`, default 5 and maximum 20.

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

`WorkSummary` contains `work_item`, `checkpoint_count`, an empty Phase 2
`ancestor_path`, `ancestor_path_truncated=false`, `current_context` as a
pointer, and `readiness`. Phase 2 readiness is derived from lifecycle and
database-time lease expiry: visible open unleased work is `ready`, visible open
work with an unexpired lease is `active`, and terminal work reports its
lifecycle value. Blocker fields remain zero/false until Phase 3.

`LeasePublic` contains only holder client/session and acquired, renewed, and
expiry timestamps. `Readiness.active_lease` uses that safe projection and never
contains request ID or token. Its independent `has_active_lease`, `is_ready`,
and lifecycle fields remain authoritative.

`WorkCreation` contains `work_item`, `initial_checkpoint`, and an empty
`initial_relationships` list.

`WorkContext` contains:

```text
work_item
initial_checkpoint
current_context
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
progress or completion record. Recent checkpoints are chronological and exclude
the initial/current IDs. The three relationship lists and counts are empty in
Phase 2.

Pages retain `items`, `total`, `limit`, and `offset`.
`CompletionResult` contains `work_item` and `checkpoint`.

## Deprecated hand-off compatibility

The old `/projects/{project_id}/handoffs` routes remain available during the
cutover window, but all reads and writes use canonical tables:

- save creates work plus its initial checkpoint;
- search returns a unique compact work projection;
- recall flattens work fields with the preserved initial checkpoint;
- comment listing projects every later checkpoint (`completion` becomes
  `work-summary`, other kinds become `comment`);
- comment append creates a progress checkpoint;
- completion uses canonical atomic completion;
- update permits title, summary, and non-completion lifecycle changes only and
  accepts a token for an actively leased terminal transition;
- `DELETE /{handoff_id}?expected_version=N` remains query-versioned and
  returns 204 only while unleased. The MCP `delete_handoff` alias uses the
  canonical JSON action and accepts an optional token.

Legacy source/tag filters apply to the initial checkpoint to preserve their old
meaning. Prompt, checkpoint provenance, repository fields, tags, and metadata
cannot be rewritten through legacy update. Old IDs remain resolvable.

## MCP contract

Canonical tools are:

```text
list_projects, create_project,
create_work, search_work, get_work, add_checkpoint, list_checkpoints,
recall_work, update_work, complete_work, delete_work,
claim_work, claim_and_recall, renew_claim, release_claim
```

The resource
`mnemonic://projects/{project_id}/work-items/{work_item_id}` and prompt
`resume_work` return bounded context. Neither executes stored work or grants
authority.

The eight hand-off tools, old resource URI, and `resume_handoff` remain as
deprecated projections. MCP error handling maps stable application codes and
also accepts legacy string errors during the compatibility window.

## Browser proxy

The same-origin proxy allows exact project and Phase 2 work/checkpoint
read/write routes and their documented query keys. It rejects arbitrary paths,
unknown query keys, untrusted hosts/origins, bodies over 1 MiB, and every
`lease_token` field at any nesting depth. All four claim/renew/release routes
are denied rather than stripped. The API URL and bearer key remain server-only;
the dashboard can display `LeasePublic` but never receive or forward a token.

## Runtime configuration

API: `DATABASE_URL`, `MNEMONIC_API_KEY` (required, at least 32 characters), and
`MNEMONIC_LEASE_TTL_SECONDS` (default 900, allowed 60 through 3600).

MCP: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, `MNEMONIC_MCP_HOST`, and
`MNEMONIC_MCP_PORT`.

Dashboard server: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, and
`MNEMONIC_DASHBOARD_ORIGINS`. Credentials must never use a
`NEXT_PUBLIC_*` variable.
