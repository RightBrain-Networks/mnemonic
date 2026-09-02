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

`MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS` bounds how long a protected mutation
waits for another transaction using the same project/operation UUID. It defaults
to 10 seconds and startup rejects values outside 1 through 10. A timeout returns
sanitized `client_operation_unavailable`; it must never fall through to a
second domain execution. Lower values fail ambiguous concurrent retries sooner,
while higher values occupy a database connection longer.

`MNEMONIC_HUMAN_GATE_REQUESTS_ENABLED` defaults to `false`. It fences only
genuinely new human-gate requests; exact completed request replay, attention and
history reads, readiness/lifecycle enforcement, and direct REST/dashboard
resolution remain active. Keep it false through the coordinated Phase 7–8
cutover, then set it consistently across the gate-aware API pool and recreate
the API service. Turning it false later is an incident brake, not a way to hide
or discard existing gates.

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
window. Canonical endpoints use
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
tables and unused ORM metadata. This contract step is forward-only
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

## Phase 3 graph deployment

`0008_work_relationships` adds the project-local typed graph after the lease
schema. Deploy API, MCP, and dashboard images together so relationship shapes,
readiness, hierarchy, browser proxy rules, and the four MCP graph tools agree.
The migration creates no inferred edges: existing work initially has no graph
facts, and its readiness continues to derive from lifecycle and lease state.

Relationship mutations serialize briefly on the project row and lock endpoint
work in UUID order. That favors correct cycle/parent checks over maximum write
parallelism; monitor unusually long graph-write latency, but do not bypass the
API with direct edge updates. There is no graph repair worker or scheduler.

After migration, exercise an idempotent add/get/list/remove flow, an unresolved
blocker that rejects a new claim, removal or completion that restores readiness,
an attempted block or parent cycle that returns sanitized `relationship_cycle`,
atomic child/discovery creation with context evidence, subtree-aware root/child
browse, and relationship-protected deletion. Inspect errors only through their
sanitized codes; checkpoint context and relationship provenance can be private.

A pre-Phase-3 backup can still be restored into an isolated database and then
migrated forward: `0008` creates an empty relationship table because the older
archive contains no graph facts. Do not infer or reconstruct edges from prompt
text. A Phase 3 backup is required to restore relationships that existed.

## Phase 4 ready-work deployment

`0009_ready_work_indexes` is an additive index migration for the ready-work
query. Deploy the Phase 4 API and MCP images together so the exact readiness
predicate, filters, pointer-only response, and claim-side recheck agree. The
gate seam was intentionally vacuous at this historical revision. Migration
`0014_human_gates` now supplies the explicit schema and shared gate predicate;
do not emulate it with labels or client-only filtering.

After migration, verify that ready work is visible and Pending, has no unresolved
incoming `blocks` edge, and has no active lease. Exercise the deterministic
`priority DESC, created_at ASC, id ASC` order plus tag and direct-parent filters.
Treat the result as an advisory discovery snapshot: consumers must still call
`claim_work`, which rechecks eligibility atomically and may reject a stale
candidate. There is no automatic scheduler or claim-next operation.

## Phase 5 immutable-event cutover and rollback

`0010_work_events` is a quiesced cutover, not a rolling migration. It creates the
append-only event store and backfills historical canonical facts. Any Phase 4
write accepted after that backfill but before Phase 5 writers take over would
have no matching authoritative event. For the cutover:

1. stop API, MCP, dashboard, and any direct REST writers;
2. take a fresh custom-format backup, verify its archive, and rehearse restore;
3. migrate through `0009` and `0010` on an isolated restored database first;
4. verify migration/model parity, exact backfill counts by event type,
   representative provenance and partial-history flags, event sequence state,
   and the database update/delete rejection triggers;
5. migrate production and deploy API, MCP, and dashboard images as one
   compatible stack; resume traffic only after the same checks pass.

Do not restart a Phase 4 writer against a database whose `0010` backfill has
finished. If the deployment fails before any Phase 5 write, keep writers stopped
and either fix forward or downgrade/restore to the rehearsed pre-cutover state.
After a Phase 5 write, `0010` downgrade drops the event history, including
progress events that have no checkpoint equivalent. Prefer a forward fix. If an
operator accepts a rollback that discards post-cutover writes, first preserve a
forensic backup, quiesce all writers, then restore the pre-cutover archive as the
single rollback boundary.

Authoritative lifecycle, lease, relationship, checkpoint, and identity events
are inserted in the same transaction as their canonical mutation. Exact
idempotent replays and other no-op outcomes add no event. `append_event` accepts
only a progress event; a checkpoint remains durable resume evidence and gets its
own automatic `checkpoint_added` event. Event rows are immutable and ordered per
work by `created_at`, with the server-assigned ID as the tie-breaker. Their
actor provenance is client-asserted audit context, not an authentication or
authorization boundary.

Keep API bearer keys and lease tokens out of event content, metadata, URLs, logs,
and screenshots. Known request credentials echoed into event input are rejected,
but arbitrary accepted prose can still contain unknown sensitive material. The
database trigger rejects event update/delete statements with SQLSTATE `55000`;
do not bypass the API or disable that protection for routine maintenance.

Event retention follows retained work; there is no physical event purge API.
If event operations are instrumented, use only bounded event-type labels. Never
put project names, tag values, actors, event bodies, or unbounded IDs in metric
labels.

## Phase 6 idempotent-mutation deployment and rollback

`0013_idempotent_mutations` follows
`0012_pending_deferred_statuses`, which in turn follows
`0011_project_settings`. The Phase 6 revision is additive for existing
production content: it creates an empty private receipt table and adds a
separate `NOT VALID` recursive check that leaves historical progress metadata
unchanged while enforcing the reserved operation key on new/updated rows. It
does not rewrite work, checkpoints, relationships, leases, or events.

Use one quiesced schema/application boundary:

1. stop API, MCP, dashboard, and every direct REST writer;
2. take a fresh custom-format backup, validate its archive, and rehearse an
   isolated restore through `0013`;
3. confirm the database is at the expected
   `0012_pending_deferred_statuses` baseline
   and no locally created `client_operations` object collides;
4. migrate production to `0013_idempotent_mutations`;
5. deploy API, MCP, dashboard, and plugin guidance together;
6. verify one fresh keyed mutation, its exact replay, a mismatch conflict, a
   natural no-op replay, and the exact 22-tool catalog before reopening writers.

Do not run an older writer against the new contract during cutover. In
particular, pre-Phase-6 progress validation cannot translate the new reserved-
metadata database failure, and older MCP/dashboard clients do not retain the
required keys. There is no dual-write or compatibility mode.

The ledger reserves before current domain lookup and may wait on a concurrent
owner. The configured wait is bounded; `client_operation_unavailable` means
the outcome may be unknown and permits only an exact retry with the retained
UUID and complete semantic request. `client_operation_conflict` means that
project/key is already bound differently; do not generate a new key merely to
bypass it. Application logs intentionally contain only the bounded operation
kind and outcome classification. Never query, paste, log, or metric-label the
operation UUID, fingerprint, salt, response JSON, request body, target, actor,
project, bearer, or lease token.

Completed receipts have no TTL or cleanup task. Do not delete or edit them:
historical retries can arrive after the domain object changes or disappears.
The database rejects completed update/delete and rejects a pending row at
commit. For capacity planning, inspect only aggregate row count and
table/index/TOAST sizes. Receipt response size makes growth workload-dependent.

Run routine inspection only from a private operator shell and return aggregates,
never receipt identities, fingerprints, salts, or response JSON. These queries
show retained volume, invalid committed state, registered contract versions,
age, index health, and database-wide deadlocks without selecting sensitive
columns:

```sql
SELECT
    count(*) FILTER (WHERE state = 'completed') AS completed_receipts,
    count(*) FILTER (WHERE state = 'pending') AS invalid_committed_pending,
    pg_table_size('client_operations') AS table_and_toast_bytes,
    pg_indexes_size('client_operations') AS index_bytes,
    pg_total_relation_size('client_operations') AS total_bytes
FROM client_operations;

SELECT
    operation_kind,
    date_trunc('day', created_at AT TIME ZONE 'UTC') AS utc_day,
    count(*) AS completed_receipts
FROM client_operations
GROUP BY operation_kind, utc_day
ORDER BY utc_day, operation_kind;

SELECT
    operation_kind,
    request_fingerprint_version,
    response_contract_version,
    count(*) AS completed_receipts
FROM client_operations
GROUP BY operation_kind, request_fingerprint_version, response_contract_version
ORDER BY operation_kind, request_fingerprint_version, response_contract_version;

SELECT min(created_at) AS oldest_created_at,
       max(completed_at) AS newest_completed_at
FROM client_operations;

SELECT indexrelname, idx_scan, pg_relation_size(indexrelid) AS index_bytes
FROM pg_stat_user_indexes
WHERE relname = 'client_operations'
ORDER BY indexrelname;

SELECT deadlocks, conflicts
FROM pg_stat_database
WHERE datname = current_database();
```

The committed pending count must be zero. An unexpected contract version,
invalid/missing unique index, growing deadlock count, or sustained unavailable
outcomes is an incident. To aggregate only the intentionally redacted
operation-kind/outcome log fields for a bounded window:

```sh
docker compose logs --since=24h api |
  sed -n 's/.*Client operation outcome kind=\([^ ]*\) outcome=\([^ ]*\).*/\1 \2/p' |
  sort |
  uniq -c
```

`outcome=unavailable` includes receipt waits and invariant failures; inspect
private PostgreSQL/application logs to distinguish them without copying queries
or caller values into tickets or telemetry. Use a privately bound exact scope
only when running `EXPLAIN (ANALYZE, BUFFERS)` for the unique lookup, and
retain only the plan shape, timing, and buffer totals.

If the Phase 6 application must be rolled back after any receipt exists, keep
the database at `0013`, keep writers quiesced, and fix forward or restore the
whole pre-cutover backup. An older application is not idempotency-capable. The
Alembic downgrade takes an exclusive ledger lock and refuses when any row
exists; it is supported only for an unused, writer-quiesced migration and drops
only Phase 6 receipt/metadata objects. Never truncate receipts to force a
downgrade.

A post-Phase-6 backup is the only backup that preserves replay for keyed
mutations issued after cutover. Restore the full archive transactionally,
migrate forward if needed, and keep all writers stopped until exact historical
replay returns the stored body without changing domain rows, events, or leases.
A pre-Phase-6 archive can migrate forward safely but cannot recover receipts or
provide retry safety for operations performed after that archive.

## Phases 7–8 human-oversight deployment and rollback

`0014_human_gates` follows `0013_idempotent_mutations`. It preserves every
existing production row and receipt, creates an empty gate table, adds internal
gate references and constraints to the event store, widens the private receipt
registry from ten to exactly twelve kinds, and installs database fail-closed
guards for fresh/replacement claims plus terminal/delete work mutations. The
hierarchy response also gains strict presentation fields, so this is a
coordinated backend/MCP/dashboard/plugin cutover without compatibility shims.

Before deployment:

1. quiesce API, MCP, dashboard, and direct REST mutation/claim writers;
2. take a custom-format backup, validate its archive, and restore-test it in an
   isolated environment;
3. confirm the live head is exactly `0013_idempotent_mutations`, record canonical
   table counts/content hashes and the legacy metadata-v1 function definition,
   and check that no locally invented gate objects or operation kinds collide;
4. rehearse `0013 -> 0014`, lock duration, hierarchy query plans, and the old-
   backend fail-closed probes on a production-sized restored copy.

Upgrade and enable in this order:

1. apply `0014_human_gates` transactionally;
2. deploy the gate-aware API with
   `MNEMONIC_HUMAN_GATE_REQUESTS_ENABLED=false` while writers remain quiesced;
3. verify migration/model parity, empty attention/history reads, context and
   hierarchy shapes, ready/claim behavior, an old append-event receipt replay,
   and the database claim/terminal/delete guards;
4. drain every old backend process and connection; record image digests,
   replicas, routing membership, and zero old active database connections;
5. deploy the MCP adapter/plugin and dashboard/proxy together. Confirm exactly
   25 MCP tools, exactly ten protected MCP mutation schemas, no MCP resolution
   tool, and all current response models;
6. resume ordinary gate-aware writers and prove new requests still return
   sanitized `human_gates_not_enabled` while attention/history reads and
   resolution remain usable;
7. set the fence to `true` consistently across the gate-aware API pool and
   recreate it; and
8. in a disposable validation project, request a synthetic gate, prove waiting
   and ready/fresh-claim exclusion plus attention/detail/hierarchy visibility,
   resolve it through the dashboard with exact-retry recovery, and retain only
   redacted statuses and aggregate counts.

Do not enable requests while any old strict MCP model or backend remains in the
serving pool. Old clients can reject new readiness/context fields, and an old
backend can mis-list waiting work. The database rejects its fresh/replacement
claim and terminal/delete writes, but that is an incident backstop rather than
a supported mixed mode.

If a problem occurs after gates exist, set the request fence false, keep the
database at `0014`, and deploy the last known gate-aware backend or fix forward.
Reads and human resolution continue, so operators can drain the queue safely.
If no gate-aware binary is safe, quiesce ready/claim/terminal writers and expose
only reviewed read/repair paths. Never resolve, delete, truncate, or hide gates
or receipts to force rollback.

A database downgrade is supported only before any gate, gate event, or gate-
operation receipt exists and while every writer is quiesced. The migration
holds `ACCESS EXCLUSIVE` locks in the writer-compatible order
`client_operations`, `work_items`, `work_gates`, `work_events`, then checks
emptiness. It restores the exact `0013` receipt/event constraints and leaves all
Phase 1–6 rows plus the legacy metadata validator unchanged. Any nonempty check
must abort; do not truncate data or bypass the guard. After the first gate or
receipt, rollback means a forward fix or whole-database restore to an explicitly
accepted pre-cutover archive, with all post-backup writes lost.

A Phase 7–8 restore drill must include resolved and unresolved gates, the
attention identity sequence, paired gate events, and request/resolution receipts.
After isolated restore, verify migration head and invariants, unresolved
ready/fresh-claim/terminal exclusion, exact attention/history/context/hierarchy
projections, and same-key request/resolution replay with no new gate, event,
activity, lifecycle, or receipt effect. A pre-Phase-7 archive can migrate
forward to an empty gate table but cannot recover later questions, answers,
events, attention order, or retry outcomes.

### Identifier-free aggregate monitoring

Run these checks only from a private operator shell and retain aggregates, not
query output containing application rows. The examples deliberately have no
project grouping and return no gate/work/operation ID, question, resolution,
actor/session value, response JSON, fingerprint, or salt.

The first query reports queue pressure and age without identifying a project or
gate. The second reports daily request/resolution flow. Alert on sustained queue
growth or oldest-age growth according to the deployment's own observed baseline;
Mnemonic does not ship a universal queue-age SLO.

```sql
SELECT
    count(*) AS unresolved_gates,
    date_trunc(
        'minute',
        clock_timestamp() - min(created_at)
    ) AS oldest_unresolved_age
FROM work_gates
WHERE resolved_at IS NULL;

WITH transitions AS (
    SELECT
        date_trunc('day', created_at AT TIME ZONE 'UTC') AS utc_day,
        'requested'::text AS transition
    FROM work_gates
    UNION ALL
    SELECT
        date_trunc('day', resolved_at AT TIME ZONE 'UTC') AS utc_day,
        'resolved'::text AS transition
    FROM work_gates
    WHERE resolved_at IS NOT NULL
)
SELECT
    utc_day,
    count(*) FILTER (WHERE transition = 'requested') AS requests,
    count(*) FILTER (WHERE transition = 'resolved') AS resolutions
FROM transitions
GROUP BY utc_day
ORDER BY utc_day;
```

This retained-source-fact audit must return zero. It checks request/resolution
event cardinality for every gate and also counts any orphan event carrying an
internal gate reference. Row checks plus the insert/source-fact triggers enforce
the remaining state, text, provenance, timestamp, and metadata coherence.

```sql
WITH gate_event_counts AS (
    SELECT
        gate.id,
        gate.resolved_at,
        count(event.id) FILTER (
            WHERE event.event_type = 'human_attention_requested'
        ) AS request_events,
        count(event.id) FILTER (
            WHERE event.event_type = 'human_attention_resolved'
        ) AS resolution_events
    FROM work_gates AS gate
    LEFT JOIN work_events AS event
      ON event.gate_id = gate.id
     AND event.work_item_id = gate.work_item_id
    GROUP BY gate.id, gate.resolved_at
),
orphan_events AS (
    SELECT count(*) AS count
    FROM work_events AS event
    LEFT JOIN work_gates AS gate
      ON gate.id = event.gate_id
     AND gate.work_item_id = event.work_item_id
    WHERE event.gate_id IS NOT NULL
      AND gate.id IS NULL
)
SELECT
    count(*) FILTER (
        WHERE request_events <> 1
           OR resolution_events <>
              CASE WHEN resolved_at IS NULL THEN 0 ELSE 1 END
    ) + (SELECT count FROM orphan_events) AS invariant_violations
FROM gate_event_counts;
```

Use only the bounded `operation_kind` and finite
`executed|replayed|no_op|conflict|unavailable` outcome labels for live
gate-operation rates. This 24-hour example returns counts from which the
collector can derive a rate; it never retains the rest of the log line.

```sh
docker compose logs --since=24h api |
  sed -n 's/.*Client operation outcome kind=\([^ ]*\) outcome=\([^ ]*\).*/\1 \2/p' |
  awk '$1 == "request_human_input" || $1 == "resolve_human_input"' |
  sort |
  uniq -c
```

The durable counterpart below groups only the two gate-operation receipt kinds
by day, bounded status/state, and applied/no-op outcome. A nonzero committed
pending count remains an invariant failure; conflicts and unavailable attempts
appear only in the bounded logs because they do not create completed receipts.

```sql
SELECT
    operation_kind,
    date_trunc('day', completed_at AT TIME ZONE 'UTC') AS utc_day,
    state,
    response_status,
    mutation_applied,
    count(*) AS receipts
FROM client_operations
WHERE operation_kind IN ('request_human_input', 'resolve_human_input')
GROUP BY operation_kind, utc_day, state, response_status, mutation_applied
ORDER BY utc_day, operation_kind, response_status, mutation_applied;
```

Track physical growth alongside row counts. Event and receipt relation sizes
also include older non-gate facts, so do not mislabel their bytes as gate-only
storage.

```sql
SELECT
    (SELECT count(*) FROM work_gates) AS gate_rows,
    (SELECT count(*) FROM work_events
      WHERE gate_id IS NOT NULL) AS gate_event_rows,
    (SELECT count(*) FROM client_operations
      WHERE operation_kind IN (
          'request_human_input', 'resolve_human_input'
      )) AS gate_receipt_rows,
    pg_total_relation_size('work_gates') AS gate_relation_bytes,
    pg_total_relation_size('work_events') AS event_relation_bytes,
    pg_total_relation_size('client_operations') AS receipt_relation_bytes;
```

Mnemonic does not install `pg_stat_statements`. Observe hierarchy latency and
timeout rate at the API/reverse-proxy layer only with a templated route name,
finite `view` label, status, and duration bucket; never use the raw URL or
query string because both can contain IDs or search text. For a slow-query
investigation, capture the parameterized hierarchy SQL privately, run
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` on an isolated current restore, and
retain only planning/execution time plus shared/local/temp block totals. Discard
the plan document and bound values.

The built-in PostgreSQL aggregates below provide database-wide buffer/temp/
deadlock deltas and identifier-free current lock-wait signals. They are not
hierarchy attribution; correlate a delta with the templated endpoint metrics
before escalating. A growing deadlock count, unexpected temporary-byte growth,
five-second hierarchy statement timeouts, or sustained lock waits is an
incident, not a reason to disable constraints or edit gates directly.

```sql
SELECT
    stats_reset,
    blks_read,
    blks_hit,
    temp_files,
    temp_bytes,
    deadlocks
FROM pg_stat_database
WHERE datname = current_database();

SELECT
    coalesce(activity.wait_event_type, 'none') AS wait_event_type,
    coalesce(activity.wait_event, 'none') AS wait_event,
    locks.mode,
    count(*) AS waiting_locks
FROM pg_stat_activity AS activity
JOIN pg_locks AS locks USING (pid)
WHERE activity.datname = current_database()
  AND NOT locks.granted
GROUP BY activity.wait_event_type, activity.wait_event, locks.mode
ORDER BY wait_event_type, wait_event, locks.mode;
```

There is intentionally no manual SQL resolve, delete, rebind, unlock, gate
repair, or receipt purge. A nonzero invariant audit or corrupt gate/event pair
requires a reviewed forward migration or whole-database restore.

Gate questions and answers are private application content. Never include them,
actor/session values, operation UUIDs, reviewed revision bodies, or response
hashes in logs, metric labels, stack-check output, screenshots, or tickets.
Request/resolver provenance is asserted under the shared bearer and is not an
authenticated approval signature.

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
canonical work, immutable checkpoint text and provenance, retained leases, typed
relationships, immutable work events and their sequence, human gates and their
attention identity sequence, private durable client-operation receipts, and
migration state; treat them as private. Receipt
rows include stored successful response bodies and salted fingerprints, so they
receive the same confidentiality and integrity protection as canonical content.
The backup service never deletes earlier dumps. Set a
retention policy appropriate for available disk space, and copy successful dumps
to another device or a backed-up location. The local PostgreSQL volume and a
backup on the same disk can both be lost.

An archive listing check is not a restore drill. Periodically restore a dump
into an isolated PostgreSQL instance and verify representative projects, work
items, checkpoint history, exact relationship source/target/context/provenance,
derived readiness, event count/max ID/content checksum and sequence ownership,
all event and gate indexes plus immutability/completeness/fail-closed triggers,
attention sequence state, receipt count/uniqueness/state plus its guards, exact
replay of representative ordinary and gate successes, and the expected
`alembic_version`. Keep
the PostgreSQL major version compatible with the dump
tools.

## Restore

Restore replaces the complete application-owned `public` schema, including
objects that are newer than and therefore absent from the chosen backup. First
take a fresh backup, identify the exact dump filename, and stop all writers.
The dump path must already exist in the configured backup directory. Do not
run a restore against a database you have not explicitly chosen to replace.

```sh
docker compose exec backup sh /opt/mnemonic/backup.sh once
docker compose stop web mcp api backup
docker compose --profile maintenance run --rm -e MNEMONIC_RESTORE_FILE=mnemonic-YYYYMMDDTHHMMSSZ-SUFFIX.dump -e MNEMONIC_CONFIRM_RESTORE=replace-mnemonic-data restore
docker compose up -d --wait
```

PostgreSQL must remain running during this sequence. The restore script refuses
to run without the explicit confirmation value, rejects filenames containing
directory paths, and uses a single transaction for schema replacement and
archive loading so errors restore the original target. Mnemonic does not use
non-public application schemas or optional PostgreSQL extensions; the script
refuses either unexpected layout instead of deleting outside its ownership
boundary or producing a hybrid restore. The API
applies any newer migrations, including `0009`, `0010`, `0011`, `0012`,
`0013`, and `0014`, before becoming ready. Do not expose API, MCP, or dashboard traffic
until readiness succeeds and
the restored schema/data checks pass. A restore from before a schema change
should be rehearsed on an isolated instance first; restore is not a substitute
for a planned schema downgrade. A pre-Phase-3 archive cannot recover later graph
facts, a pre-Phase-5 archive cannot recover later event history, and a
pre-Phase-6 archive cannot recover later client-operation receipts, and a
pre-Phase-7 archive cannot recover later gates, gate events, attention order, or
gate-operation receipts.

Deletion from the dashboard is a soft delete. No ordinary work/checkpoint/event
API or MCP read can retrieve a deleted work item. The immutable rows remain
retained; there is no physical purge endpoint. The application refuses deletion
while any relationship or unresolved gate remains. After every gate resolves,
the exact project/work gate-history route intentionally retains the paired
question/answer audit even after soft deletion.

There is no supported in-place undelete or trash-management UI. Do not clear
`work_items.deleted_at` manually: Phase 5 retains one immutable `work_deleted`
fact and has no recovery event, so such a change would make the timeline false
and can make a later canonical deletion violate the unique fact constraint.
Recovery requires an operator-approved restore at the documented whole-database
backup boundary. Checkpoint and event rows must never be edited during recovery.

## Trust boundary and remote clients

This is a single-user application. One API key authorizes access to every
project; project scoping prevents accidental cross-project operations, not
access control between people. The local dashboard has no login screen. Its
server proxy validates request hosts and browser origins, but any trusted local
process can access that dashboard. Do not share a machine account with people
who should not see its prompts.

The API and HTTP MCP endpoints require bearer authentication. Checkpoint and
human-gate content is rendered as text, not executable HTML. Gate requester and
resolver fields are client assertions under the shared bearer; Mnemonic does
not authenticate a person's identity, sign approvals, or verify answers. The MCP
adapter does not follow URLs from stored context, exposes no resolution tool,
and never connects to the database directly.

Do not expose these ports directly to the internet. A remote deployment needs
HTTPS, a real authentication boundary for the dashboard, explicit allowed
origins/hosts, request limits, and an operational backup policy. Browser origin
allowlisting is not user authentication. ChatGPT's cloud clients cannot reach
this machine's loopback address, and this MVP does not implement OAuth or
provision a public MCP endpoint. Those are later integration work.
