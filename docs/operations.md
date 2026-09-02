# Operating Mnemonic

## Configuration

`python scripts/setup.py` creates two independent random secrets in `.env`,
refuses to overwrite an existing file, and never prints secrets. Keep that file
private. On Unix it is created with mode 0600; on Windows use account-private
filesystem access controls.

The published addresses default to `127.0.0.1:3000` (dashboard), `:8000` (API),
and `:8001` (MCP). Change the corresponding port values in `.env`. If the web
port changes, update `MNEMONIC_DASHBOARD_ORIGINS` with each exact browser origin
and recreate both API and web; both the server proxy and data-free live-sync
socket enforce that list.

For host-managed TLS, use
[`deploy/nginx/mnemonic.conf`](../deploy/nginx/mnemonic.conf) and its
[installation guide](../deploy/nginx/README.md). `compose.tls.yaml` adds the
chosen HTTPS host/origin without changing the loopback defaults.

Important API settings are:

- `MNEMONIC_LEASE_TTL_SECONDS`, default 900, accepted range 60 through 3600.
  It affects later claims and renewals, not existing lease rows.
- `MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS`, default 10, accepted range 1
  through 10. A wait timeout returns `client_operation_unavailable`; only an
  exact retry of the privately retained intent is safe.
- `MNEMONIC_EMBEDDING_CACHE`, the model-cache path used by semantic search.
- `MNEMONIC_DASHBOARD_ORIGINS`, a comma-separated exact-origin allowlist.

Never put credentials in browser-public environment variables. The dashboard's
API key is server-only. The PostgreSQL password must be URL-safe because it is
interpolated into a connection URL. Changing `POSTGRES_PASSWORD` after volume
initialization does not rotate the database role: change it in PostgreSQL first,
then update `.env` and recreate services. To rotate the API key, update `.env`,
recreate API/MCP/web, and update every connected client.

## Semantic search

Semantic search is opt-in. Ordinary dashboard and MCP queries use PostgreSQL
lexical search. A nonblank semantic query runs `BAAI/bge-small-en-v1.5` inside
the API container and lazily refreshes derived `work_item_embeddings` rows in
batches of 16, so a first query after canonical content changes may take longer.

The image build downloads model artifacts into `/app/.embedding-cache`; runtime
uses offline mode and never sends prompt or query text to a hosted model API.
Embedding rows are derived cache: canonical work and checkpoints are sufficient
to rebuild them. On `semantic_unavailable`, turn semantic mode off and continue
with lexical search.

## Upgrading the single-host Compose stack

Mnemonic has one API container, and that container is the only schema migrator.
It runs `alembic upgrade head` before it becomes ready. There is no mixed API
pool or routing cutover. Upgrade the schema, API, MCP, dashboard, and plugin as
one release boundary; do not deliberately restart an older writer against a
newer contract.

1. Confirm the current stack is healthy, then take and validate a fresh custom
   dump while it is still serving:

   ```sh
   docker compose ps
   docker compose exec backup sh /opt/mnemonic/backup.sh once
   docker compose logs --tail=20 backup
   ```

   Copy the completed dump to a different device or backed-up location. A dump
   on the same disk as the PostgreSQL volume is not a disaster-recovery copy.
2. Quiesce every writer. Stop the bundled entry points and stop any direct REST
   clients before proceeding:

   ```sh
   docker compose stop web mcp api
   ```

3. Optionally run migration as a separate visible step. API startup repeats it
   as a no-op:

   ```sh
   docker compose run --rm api alembic upgrade head
   ```

4. Build and start the whole compatible stack. Do not recreate API alone while
   web or MCP retain connections to the old container:

   ```sh
   docker compose up --build -d --wait
   docker compose ps
   docker compose logs --tail=100 api
   ```

5. Verify the release artifact before reopening any external writer. The schema
   parity check is
   `backend/tests/test_schema_parity_postgres.py::test_migrated_schema_matches_orm_metadata`;
   it compares columns, types, defaults, every ORM table's non-trigger
   constraints, and indexes with an independently built ORM schema. The
   committed OpenAPI snapshot and its strict consumer tests must also pass.
6. Run the live read-only path check from the MCP environment:

   ```sh
   uv run --project mcp python scripts/check-stack.py
   ```

   Supplying `--project-id` authorizes synthetic writes and immutable history in
   that project. Use it only for a disposable or explicitly approved project,
   optionally with a different `--other-project-id` for isolation coverage.

Application rollback means redeploying a compatible fixed image while leaving
the database at head. Prefer a forward fix. Migration `0015_gate_review_fixes`
has no supported Alembic downgrade path, so no later revision can downgrade
past it. Once 0015 has been applied, the only data rollback boundary is a complete restore of a chosen
pre-upgrade archive, explicitly accepting loss of every later write. Never
truncate receipts, edit events, resolve/delete gates, or disable constraints to
force an application or schema rollback.

A restore rehearsal for this release should cover unresolved and resolved gates,
attention sequence state, paired gate events, request/resolution receipts, and
same-key replay without a new durable effect. An older archive can migrate
forward, but it cannot recover graph, event, receipt, or gate facts created after
that archive.

## Durable runtime invariants

### Leases

Lease tokens are capabilities inside the shared bearer-key trust boundary. They
belong only in claim/renew responses and JSON mutation bodies. Never copy them
into checkpoints, events, URLs, chat, tickets, metrics, logs, or screenshots.
The browser cannot claim, renew, release, receive, or forward a token. Expired
lease rows are deliberately retained until a later acquisition replaces them;
TTL expiry is abandoned-session recovery, not an operator force-release task.

### Immutable work events

Authoritative lifecycle, lease, relationship, checkpoint, gate, and identity
events commit in the same transaction as their canonical fact. Exact receipt
replay and natural no-ops do not fabricate another event. Clients may append
only `progress`; checkpoints remain the resume surface. Events are immutable,
and a database trigger rejects update/delete. Use only bounded event-type
labels in telemetry and never record event bodies, actors, projects, or IDs as
metric labels.

### Idempotent mutation receipts

Completed receipts have no TTL or cleanup task. They are private retry state and
may contain frozen response bodies. Never edit, delete, or truncate them; a
historical exact retry can arrive after the domain object changes. A committed
`pending` receipt is an invariant failure. Inspect only aggregates:

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
    request_fingerprint_version,
    response_contract_version,
    count(*) AS completed_receipts
FROM client_operations
GROUP BY operation_kind, request_fingerprint_version, response_contract_version
ORDER BY operation_kind, request_fingerprint_version, response_contract_version;
```

`client_operation_unavailable` means a protected outcome may be unknown; replay
only the exact privately retained operation UUID and full request. A
`client_operation_conflict` on an asserted exact retry is a caller-safety
incident, not permission to substitute a UUID.

### Human gates, deferral, and hierarchy reads

An unresolved gate makes Pending work `waiting`, removes it from ready
discovery, and blocks a fresh/replacement claim, completion, terminal
transition, and deletion. It does not revoke an existing lease. An agent checks existing unresolved gates, writes the supporting `context`
checkpoint, and then requests
one concrete decision; agents cannot resolve, edit, cancel, or withdraw it. If
later evidence makes the question moot, append a context checkpoint explaining
why and have a person resolve it as "No longer needed".

Resolution is a dashboard/direct-REST human action. Every attempt must submit
`reviewed_context_revision` equal to the exact current work version, newest
context checkpoint ID, and relationship-event count the person reviewed. Any
intervening change makes that frozen attempt stale; reload, review, and prepare
a new operation intent. Resolver fields are asserted provenance under the
shared bearer, not a signed identity. Gate reads nest the requested three-field
revision and expose server-computed current and resolution drift flags; clients
should validate their types and nullability rather than rederive them.

`deferred` is a persisted human hold, distinct from waiting and blocking. It is
absent from ready discovery and must not be returned to Pending unless the
current human instruction explicitly selects it. Resolving a gate does not
undefer a work item; it only removes the gate's independent readiness fact.

Attention pages use opaque `next_cursor` values. Pass the returned value back as
`cursor`. Sequence allocation precedes transaction commit, so a forward walk
can miss a lower sequence that commits later. Restart once without a cursor
before concluding the queue is drained. A malformed, foreign-scope, or
filter-mismatched cursor returns `422 invalid_cursor`; restart from the first
page.

Hierarchy reads have a five-second database statement timeout. A typed
`503 hierarchy_timeout` means that read was canceled and its transaction rolled
back; retry with narrower filters or a smaller page. Repeated timeouts are a
hierarchy performance incident. Do not report them as a general database outage
or weaken hierarchy constraints.

## Monitoring and repair boundaries

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
boundary or producing a hybrid restore. The API applies every migration newer than the archive through the current head
before becoming ready. Do not expose API, MCP, or dashboard traffic until
readiness succeeds and the restored schema/data checks pass. Rehearse a restore
from before a schema change on an isolated instance first. Restore is not a
substitute for schema downgrade; downgrade is explicitly unsupported beginning
with migration 0015. A pre-Phase-3 archive cannot recover later graph facts, a
pre-Phase-5 archive cannot recover later event history, a pre-Phase-6 archive
cannot recover later client-operation receipts, and a pre-Phase-7 archive cannot
recover later gates, gate events, attention order, or gate-operation receipts.

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

The API and HTTP MCP endpoints require bearer authentication. nginx logs the raw
`$uri`, so route UUIDs can appear in private access logs; nginx has no
route-template placeholder. Keep those logs local, access-controlled, and
rotated. The MCP process sets the `httpx` logger and server log level to WARNING
so query text, cursors, and URL identifiers are not emitted at INFO. Checkpoint and
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
