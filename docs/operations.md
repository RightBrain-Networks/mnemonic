# Operating Mnemonic

## Configuration

`python scripts/setup.py` creates two independent random secrets in `.env`,
refuses to overwrite an existing file, and never prints secrets. Keep that file
private. On Unix it is created with mode 0600; on Windows keep the checkout
under an account-private directory and use filesystem access controls.

For a host-managed nginx TLS proxy, use
[`deploy/nginx/mnemonic.conf`](../deploy/nginx/mnemonic.conf) and its
[installation guide](../deploy/nginx/README.md). The optional `compose.tls.yaml`
adds the exact HTTPS host/origin without changing the local-only defaults.

The published addresses are `127.0.0.1:3000` (dashboard), `:8000` (API), and
`:8001` (MCP). Change the three port variables in `.env` if needed. When changing
the web port, also change `MNEMONIC_DASHBOARD_ORIGINS` to list its exact origins,
such as `http://localhost:3100,http://127.0.0.1:3100`. Both the web service’s
HTTP proxy and the API’s data-free WebSocket endpoint consume this allowlist;
recreate both services after changing it. Browser live sync reconnects
automatically after a temporary interruption.

`MNEMONIC_LEASE_TTL_SECONDS` controls every server-issued work lease. It
defaults to 900 seconds and startup rejects values outside 60 through 3600.
Clients cannot choose an expiry or request an unlimited claim. Changing the
setting affects later acquisitions and renewals; it does not rewrite retained
lease rows.

Never set browser-public environment variables containing credentials. The
dashboard's API key is a server-only setting. The database password must be
URL-safe because it is interpolated into the API connection URL.

Changing `POSTGRES_PASSWORD` after initialization does not change the password
inside an existing PostgreSQL volume. Rotate the database role password through
PostgreSQL first, then update `.env` and recreate services. To rotate the API key,
change `.env`, recreate API/MCP/web, and update connected clients.

## Semantic search

Semantic search is opt-in; ordinary dashboard and MCP searches continue using the
existing PostgreSQL lexical path. A nonblank semantic query runs
`BAAI/bge-small-en-v1.5` inside the API container and can fill stale rows in the
derived `work_item_embeddings` table in batches of 16, so its first request
after new or changed work items or checkpoints can take longer than later
requests.

The image build downloads model artifacts into `/app/.embedding-cache`; image
builds therefore need network access. The running image sets Hugging Face offline
mode and will not download a missing model or send prompt/query text to a hosted
model API.

Each derived row carries the embedding configuration and a content digest. A
mismatch is rebuilt lazily. The bounded embedding source combines work title and
summary, initial context, and recent checkpoint text. The rows can be present in
a backup, but canonical work/checkpoint rows are sufficient to regenerate them.
If semantic retrieval returns 503, turn it off to keep using the independent
lexical path.

## Phase 1 cutover

The Phase 1 migration is a maintenance-window cutover, not a rolling migration.
The API runs Alembic before serving, and `0005_work_graph_backfill` copies
legacy rows while writers are quiesced. Before deploying that image:

1. stop API, MCP, and dashboard writers;
2. create a fresh custom-format backup and verify it with `pg_restore --list`;
3. record hand-off/comment counts by project and lifecycle;
4. rehearse `0003_handoff_comments -> 0005_work_graph_backfill` against an
   isolated restored database;
5. deploy API, MCP, and dashboard images as one compatible stack.

The Phase 1 runtime head retains the legacy tables read-only for an observation
window. Canonical and deprecated compatibility endpoints both use
`work_items`/`checkpoints`; there is no dual-write path. Do not drop the
legacy tables until migrated counts and representative exact values have been
audited, a post-upgrade backup has passed an isolated restore drill, and the
operator explicitly accepts the rollback boundary.

Before any new canonical write, an old image may be usable only if that exact
rollback has been rehearsed. After new work or checkpoint writes, old code
cannot see them; safe rollback requires restoring the pre-cutover backup. An
Alembic downgrade cannot losslessly collapse multiple checkpoints into one
mutable legacy row.

## Phase 2 contract and lease deployment

Phase 2 follows the Phase 1 observation window. Before deploying it, confirm
the canonical stack passed its parity audit and restore drill, take and verify a
fresh custom-format backup, and obtain the explicit operator go/no-go to cross
the contract boundary. `0006_work_graph_contract` drops the frozen legacy
tables and unused ORM metadata; compatibility API/MCP operations continue over
canonical work/checkpoint rows. This contract step is forward-only
operationally: rollback after it is database restore, not Alembic downgrade.

`0007_work_leases` then adds the optional lease table and expiry index. Deploy
API, MCP, and dashboard images together so token-aware terminal mutations,
claim tools, safe readiness projection, and browser denial agree. Validate one
claim/replay/renew/completion flow after migration. A normal backup includes any
retained lease rows, but an expired restored lease is not ownership and cannot
strand work.

Lease tokens are capabilities inside the existing single-user bearer-key trust
boundary. They may appear only in claim/renew receipts and JSON request bodies.
Keep MCP client tool traces private; never copy tokens into checkpoint text,
URLs, tickets, chat, metrics, or logs. The dashboard intentionally cannot claim,
renew, release, receive, or forward a token.

Expired lease rows may remain indefinitely and are replaced atomically by a
new request. There is no cleanup worker or force-release UI. For diagnostics,
inspect only work ID, holder fields, and lease timestamps; avoid selecting or
logging `lease_token`. TTL expiry is the abandoned-session recovery path.

## Backups

The backup container starts after the API has migrated the database and become
healthy. It runs a transactionally consistent custom-format `pg_dump`, checks
that `pg_restore` can read its archive, and atomically renames the completed file
into the backup directory. Failed partial dumps never become successful dumps.
The interval defaults to 86400 seconds (24 hours). An unhealthy or restarting
backup container needs attention; `docker compose ps` shows its state.

```sh
docker compose exec backup sh /opt/mnemonic/backup.sh once
docker compose logs --tail=20 backup
```

Files appear under `MNEMONIC_BACKUP_DIR` (`./backups` by default). They include
full checkpoint text, provenance, and metadata; treat them as private. The backup service never
deletes earlier dumps. Set a retention policy appropriate for available disk
space, and copy successful dumps to another device or a backed-up location.
The local PostgreSQL volume and a backup on the same disk can both be lost.

An archive listing check is not a restore drill. Periodically restore a dump
into an isolated PostgreSQL instance and verify representative projects, work
items, checkpoint history, and compatibility reads. Keep the PostgreSQL major
version compatible with the dump tools.

## Restore

Restore replaces objects present in the chosen backup. First take a fresh
backup, identify the exact dump filename, and stop all writers. The dump path
must already exist in the configured backup directory. Do not run a restore
against a database you have not explicitly chosen to replace.

```sh
docker compose exec backup sh /opt/mnemonic/backup.sh once
docker compose stop web mcp api backup
docker compose --profile maintenance run --rm -e MNEMONIC_RESTORE_FILE=mnemonic-YYYYMMDDTHHMMSSZ-SUFFIX.dump -e MNEMONIC_CONFIRM_RESTORE=replace-mnemonic-data restore
docker compose up -d --wait
```

PostgreSQL must remain running during this sequence. The restore script refuses
to run without the explicit confirmation value, rejects filenames containing
directory paths, and uses a single transaction so errors roll back. The API
applies any newer migrations when restarted. A restore from before a schema
change should be rehearsed on an isolated instance first; restore is not a
substitute for a planned schema downgrade.

Deletion from the dashboard is a soft delete. No ordinary API or MCP read can
retrieve a deleted work item or any of its checkpoints. An operator can recover
it from a backup or, after confirming the exact project and work-item UUIDs,
clear `work_items.deleted_at` and increment its `version`. Checkpoint rows
must not be edited during recovery; the database immutability trigger rejects
ordinary update/delete statements. There is no trash-management UI.

## Trust boundary and remote clients

This is a single-user application. One API key authorizes access to every
project; project scoping prevents accidental cross-project operations, not
access control between people. The local dashboard has no login screen. Its
server proxy validates request hosts and browser origins, but any trusted local
process can access that dashboard. Do not share a machine account with people
who should not see its prompts.

The API and HTTP MCP endpoints require bearer authentication. Checkpoint content
is rendered as text, not executable HTML. The MCP adapter does not follow URLs
from stored context and never connects to the database directly.

Do not expose these ports directly to the internet. A remote deployment needs
HTTPS, a real authentication boundary for the dashboard, explicit allowed
origins/hosts, request limits, and an operational backup policy. Browser origin
allowlisting is not user authentication. ChatGPT's cloud clients cannot reach
this machine's loopback address, and this MVP does not implement OAuth or
provision a public MCP endpoint. Those are later integration work.
