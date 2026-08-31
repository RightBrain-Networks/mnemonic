# Internal MVP API contract

All routes below use `/api/v1` and `Authorization: Bearer <MNEMONIC_API_KEY>`.
`GET /healthz` (liveness) and `GET /readyz` (database readiness) are unauthenticated
and disclose no credentials. JSON dates are ISO 8601 UTC. IDs are UUID strings.
Unknown fields are rejected. Errors use FastAPI's `detail` field. Invalid input
is 422, missing resources 404, conflicts 409, bad/missing authorization 401.

## Projects

- `GET /projects?limit=100&offset=0`: `{items: Project[], total, limit, offset}`.
- `POST /projects`: `{name, slug?, description?, repository_url?}` -> Project (201).
  Slug defaults to a normalized name, is unique, lowercase, and hyphen separated.
- `GET /projects/{project_id}` -> Project.
- `PATCH /projects/{project_id}`: optional name, description, repository_url.

Project fields: `id`, `name`, `slug`, `description` (default empty string),
`repository_url` (nullable), `created_at`, `updated_at`.
Name is nonblank, at most 120 characters; slug at most 100; description at most
4000; repository_url must be an http(s) URL of at most 2000 characters.

## Hand-offs

Base path: `/projects/{project_id}/handoffs`.

- `POST`: HandoffCreate -> Handoff (201).
- `GET`: `{items: HandoffSummary[], total, limit, offset}`. Query parameters:
  `q` optional (max 500 chars); `status` defaults to `open` and supports
  `open|done|wont-do|promoted|all`; optional `tag`, `source_client`,
  `source_session_id`; `limit` defaults to 30, max 100; `offset` defaults to 0.
  Blank q means browse; matching results rank by relevance then updated_at/id.
- `GET /{handoff_id}` -> Handoff (any non-deleted status).
- `PATCH /{handoff_id}`: `{expected_version, ...editable_fields}` -> Handoff.
- `DELETE /{handoff_id}?expected_version=N` -> 204, soft-delete.

HandoffCreate fields:

| Field | Shape |
| --- | --- |
| title | required nonblank string, max 200 |
| summary | required nonblank retrieval description, max 1000 |
| prompt | required nonblank complete prompt, max 100000; preserve exact text |
| source_client | required nonblank string, max 80, e.g. claude-code |
| source_session_id | required nonblank opaque string, max 200 |
| source_model | nullable string, max 120 |
| source_session_url | nullable http(s) URL, max 2000 |
| repository_branch | nullable string, max 200 |
| verified_against | nullable git commit ID, 7-64 hexadecimal characters |
| tags | list of at most 20 nonblank strings, max 50 each; normalize and dedupe |
| source_metadata | JSON object, max 16 KB, default {} |
| status | open (default), done, wont-do, promoted |

Handoff adds `id`, `project_id`, `created_at`, `updated_at`, `version` (starts 1).
HandoffSummary has all Handoff fields except `prompt` and `source_metadata`.
The originating `source_client`, `source_session_id`, `source_model`, and
`source_session_url` are immutable. Other create fields are editable, including
source_metadata. PATCH requires `expected_version` and at least one editable
field. DELETE also requires the current version. A mismatch returns 409.

Cross-project IDs always return 404. Soft-deleted records are absent from all
ordinary reads. Status=all includes lifecycle states, never deleted records.

## Runtime configuration

API: `DATABASE_URL`, `MNEMONIC_API_KEY` (required, >=32 chars).
MCP: `MNEMONIC_API_URL` (default http://api:8000), `MNEMONIC_API_KEY`,
`MNEMONIC_MCP_HOST` (default 0.0.0.0), `MNEMONIC_MCP_PORT` (default 8001).
Dashboard server: `MNEMONIC_API_URL`, `MNEMONIC_API_KEY`,
`MNEMONIC_DASHBOARD_ORIGINS` (comma-separated allowed origins; default
http://localhost:3000,http://127.0.0.1:3000). Never NEXT_PUBLIC_* credentials.

MCP tool names: `list_projects`, `create_project`, `save_handoff`,
`search_handoffs`, `recall_handoff`, `update_handoff`, `delete_handoff`.
Tool arguments use these field names; every handoff tool requires project_id.
`update_handoff` takes `project_id`, `handoff_id`, `expected_version`, and a
`changes` object containing the editable fields; it flattens those fields for
the REST PATCH request. Explicit null clears a nullable field; omission keeps it.
Search exposes pagination and returns the same compact records as the REST API.
An MCP resource `mnemonic://projects/{project_id}/handoffs/{handoff_id}` and a
`resume_handoff` MCP prompt may return the full saved record/prompt, respectively.
No automatic execution or external issue creation is part of any tool.
