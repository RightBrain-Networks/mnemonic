# Phase 1 API contract

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
`open`. It cannot set `done`.

`WorkDeletionCreate` is `{"expected_version": N}`. Successful deletion is
body-bearing JSON:

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
terminal work.

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
  }
}
```

Only current `open` work can complete. Repeated completion returns
`work_not_open`; stale completion returns `version_conflict`.

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
| `view` | Phase 1 supports `all` only |
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

`WorkSummary` contains `work_item`, `checkpoint_count`, an empty Phase 1
`ancestor_path`, `ancestor_path_truncated=false`, `current_context` as a
pointer, and `readiness`. Phase 1 readiness is derived only from lifecycle:
open is ready; terminal work reports its lifecycle value. It has no active
lease or blockers.

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
Phase 1.

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
- update permits title, summary, and non-completion lifecycle changes only;
- `DELETE /{handoff_id}?expected_version=N` remains query-versioned and
  returns 204.

Legacy source/tag filters apply to the initial checkpoint to preserve their old
meaning. Prompt, checkpoint provenance, repository fields, tags, and metadata
cannot be rewritten through legacy update. Old IDs remain resolvable.

## MCP contract

Canonical tools are:

```text
list_projects, create_project,
create_work, search_work, get_work, add_checkpoint, list_checkpoints,
recall_work, update_work, complete_work, delete_work
```

The resource
`mnemonic://projects/{project_id}/work-items/{work_item_id}` and prompt
`resume_work` return bounded context. Neither executes stored work or grants
authority.

The eight hand-off tools, old resource URI, and `resume_handoff` remain as
deprecated projections. MCP error handling maps stable application codes and
also accepts legacy string errors during the compatibility window.

## Browser proxy

The same-origin proxy allows exact project and Phase 1 work/checkpoint
read/write routes and their documented query keys. It rejects arbitrary paths,
unknown query keys, untrusted hosts/origins, bodies over 1 MiB, and every
`lease_token` field. Future claim/renew/release paths are denied. The API URL
and bearer key remain server-only.

## Runtime configuration

API: `DATABASE_URL`, `MNEMONIC_API_KEY` (required, at least 32 characters).

MCP: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, `MNEMONIC_MCP_HOST`, and
`MNEMONIC_MCP_PORT`.

Dashboard server: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`, and
`MNEMONIC_DASHBOARD_ORIGINS`. Credentials must never use a
`NEXT_PUBLIC_*` variable.
