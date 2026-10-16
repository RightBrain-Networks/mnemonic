# FastAPI browser backup gateway

FastAPI owns `/api/backups` as a constrained gateway to the existing private
worker on port 8002. The worker still owns job enqueue/status, scheduled backups,
archive storage, project restore guards, and the archive format. This migration
adds a transport owner; it changes neither those services nor the machine API,
MCP catalog, database schema, or browser write/retry protocol.

## Routes and admission

All paths below follow `/api/backups/projects/{project_id}`. Project and job IDs
must satisfy the browser UUID contract. Queries are forbidden on every route.

| Method | Suffix | Worker action |
| --- | --- | --- |
| GET | `/backups` | List retained archives |
| POST | `/backups` | Enqueue one manual backup |
| GET | `/backup-jobs/{job_id}` | Read the existing job state |
| GET | `/backups/{filename}` | Stream one archive |
| POST | `/restore` | Restore the confirmed project from a streamed archive |

Filenames match `[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}\.bz2`. Unsupported paths,
methods, encoded path separators, and trailing slashes are rejected. Host,
Origin, fetch metadata, and forbidden operation/lease controls use the existing
browser policy. Forwarded host/scheme headers confer no trust. Caller cookies,
Authorization, and human-policy headers never reach the worker.

Nonrestore actions accept no body, including GET. A bodyless POST may supply an
empty stream; the gateway verifies EOF. Restore requires an exact
`X-Confirm-Project` matching the URL and `application/octet-stream`. Content
coding must be absent or identity. Content-Length, when supplied, must be a
nonnegative browser-safe integer; streamed uploads are counted even without a
declared length. The compressed upload limit is `MNEMONIC_BACKUP_MAX_BYTES`.
The worker independently validates the archive, its schema/project identity,
expanded size, and restore eligibility using its existing guards.

## Private configuration

| API environment | Default | Purpose |
| --- | --- | --- |
| `MNEMONIC_BACKUP_URL` | `http://worker:8002` | Private HTTP(S) origin, with no credentials, path, query, or fragment |
| `MNEMONIC_BACKUP_TOKEN` | Unset | Separate worker bearer credential, at least 32 characters |
| `MNEMONIC_BACKUP_MAX_BYTES` | `67108864` | Compressed restore upload limit and public UI limit |
| `MNEMONIC_DASHBOARD_ORIGINS` | Localhost dashboard origins | Exact external browser origins admitted by FastAPI |

Compose now passes the URL/token/limit to API. Next retains its existing private
settings until the static cutover so the current delivery path continues working.
The worker continues to receive its own token and archive limits. A static web
image needs neither bearer credential nor the private worker address. Public
runtime configuration exposes only the numeric backup limit. A missing/invalid
gateway connection produces a controlled 503 without disabling other API routes.

The gateway uses a dedicated HTTP client without environment proxies, redirects,
or automatic retries. A 30-minute total deadline includes upload, worker waiting,
and response streaming. Disconnects cancel the transport and close the upstream
response. Cancellation cannot establish whether an already dispatched create or
restore committed. On ambiguous transport failure the browser receives the
existing uncertainty message and must inspect project/job state before another
action. No create/restore retry is introduced on focus, reconnect, or failure.

## Response and streaming contract

Redirects and encoded upstream responses fail closed. JSON is bounded to 4 MiB
and must be valid UTF-8 JSON. The worker's JSON status/body is preserved, including
unauthorized, contention, and archive errors. Response headers are rebuilt from
an allowlist; worker credentials and arbitrary upstream headers are not exposed.

A successful download must be HTTP 200 with a positive browser-safe
Content-Length. The gateway streams raw bytes with backpressure, enforces that
exact length, and constructs `Content-Disposition: attachment` from the admitted
filename. The download bound is the declared length, independent of the restore
upload limit. An invalid/truncated stream terminates rather than becoming a
successful shorter archive. No complete archive is buffered in memory.

Every gateway response carries `Cache-Control: no-store, max-age=0, no-transform`,
`X-Content-Type-Options: nosniff`, `Cross-Origin-Resource-Policy: same-origin`, and
`Content-Security-Policy: sandbox; default-src 'none'`. Downloads use
`application/octet-stream` and retain exact Content-Length.

## Static-server handoff and validation

The additive browser transport overlay routes all four families and the exact
WebSocket path to FastAPI. It preserves the external Host and Origin and denies
machine API, worker, and documentation namespaces. The ordinary web path still
serves Next. The future static server must implement this same routing interface:

- Forward only `/api/mnemonic`, `/api/artifacts`, `/api/transcripts`, and
  `/api/backups` (including their subpaths) to API; handle `/api/mnemonic/sync`
  with HTTP/1.1 WebSocket upgrade headers.
- Preserve external Host/Origin and raw bodies. Do not trust arbitrary forwarding
  headers or rewrite the authority to `api:8000`.
- Disable response/request buffering and compression on raw transfer routes;
  retain identity coding and no-transform for completion evidence.
- Allow the maximum configured archive body and at least the existing 16 KiB
  artifact metadata header. Backup read/send timeouts must exceed the API's
  30-minute deadline; the edge's idle timeout is not the operation deadline.
- Serve static assets and the SPA separately from private `/api/v1`, `/projects`,
  `/healthz` upstream internals, OpenAPI, and docs. A web health endpoint may
  remain explicit.

Run `scripts/test-e2e.sh --browser-transports tests/e2e/project-backups.spec.ts`
for create/poll/download/confirmed restore against disposable PostgreSQL and
worker storage. `scripts/test-nginx-e2e.sh --browser-transports` exercises the
FastAPI edge matrix, while the default command retains Next compatibility checks.
Backend gateway tests cover admission, size/encoding/redirect failures, exact
streaming, long operations, and cancellation; existing backup service/archive
tests retain responsibility for restore semantics.

No production route or service is switched by these changes. Deployment and
rollback remain separate operator-authorized operations. Child 4 can remove the
Next gateway only after configuring the matching API image and static server;
the prior Next/API pair remains the rollback delivery path.
