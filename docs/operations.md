# Operating Mnemonic

For the coordinated 0.12.0 code-review release (plugin 0.14.0, migration 0023),
see [code-review deployment and recovery](code-reviews.md#recovery-and-deployment).
Back up and quiesce old writers; do not deploy older processes against the new
schema or force a downgrade after review facts/settings changes. Defaults stay
Never/Never/off. The integrity audit is read-only and never repairs ancestry.

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
- `MNEMONIC_DUPLICATE_SUGGESTION_BODY_MAX_BYTES`, default 2097152.
- `MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_SLOTS` and
  `MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_WAIT_MS`, defaults 4 and 250.
- `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_SLOTS` and
  `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_WAIT_MS`, defaults 1 and 50. This
  process-wide model gate is shared by suggestions and ordinary semantic
  search.
- `MNEMONIC_DUPLICATE_SUGGESTION_LEXICAL_SHORTLIST`,
  `MNEMONIC_DUPLICATE_SUGGESTION_MISSING_VECTOR_LIMIT`, and
  `MNEMONIC_DUPLICATE_SUGGESTION_FULL_POPULATION_CEILING`, defaults 200, 128,
  and 10000.
- `MNEMONIC_DUPLICATE_SUGGESTION_TIMEOUT_SECONDS`, default 60.

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
It waits up to 50 ms for the shared process-wide inference slot; saturation
returns `semantic_unavailable`, while lexical search remains available.

The image build downloads model artifacts into `/app/.embedding-cache`; runtime
uses offline mode and never sends prompt or query text to a hosted model API.
Embedding rows are derived cache: canonical work and checkpoints are sufficient
to rebuild them. Composition reads only the first 1,500 characters of the
initial prompt and a SQL-bounded 1,500-character tail across later checkpoints.
Cache refresh runs after the response snapshot, skips locked work rows, and
uses a 50 ms lock timeout and five-second statement timeout. Those bounded
cache expirations leave the computed ranking usable. On other
`semantic_unavailable` failures, turn semantic mode off and continue with
lexical search.

## Duplicate suggestions

Duplicate comparison runs only after an explicit request with a complete
creation draft. Its default per-process limits are a 2,097,152-byte
authenticated streaming body cap, four concurrent request slots with a 250 ms
wait, a shared inference slot with a 50 ms wait, a 200-group lexical shortlist,
at most 30 recent distinct normalized tags composed per existing work item, at
most 128 missing vectors computed per request, a 10,000-visible-member ceiling
for full semantic scope, ten returned candidates, and an absolute 60-second
transport budget that starts before body handling.

`429 duplicate_suggestion_busy` includes `Retry-After: 1` and is safe to retry.
Model saturation, loading, inference, or vector/cache trouble returns a normal
lexical response with semantic availability false; do not page an operator for
that isolated fallback. `503 duplicate_suggestion_unavailable` means the
database/system suggestion path failed. Repeated 429 or 503 responses are an
aggregate capacity or availability incident, but neither implies a write,
unknown mutation outcome, lost create request, or permission to disable Create
anyway.

On PostgreSQL 17, suggestion snapshot and cache transactions set transaction,
statement, and lock timeouts from the remaining route budget. Cache refresh is
post-snapshot and derived: it skips locked work rows and caps cache lock waits at
50 ms. Contention can force a lexical fallback, but cannot turn a cache refresh
into an unbounded request.

Draft text, query vectors, candidate text, IDs, scores, and results do not belong
in logs or metric labels. Suggestion cache rows are derived existing-work data
and may be rebuilt; the draft and result are never persisted. Routine probes
must verify only aggregate behavior and must not commit a merge.

## Current coordinated cutover

The current coordinated boundary is API/MCP/dashboard `0.13.0`, plugin `0.14.0`,
and Alembic `0024_code_reviews`. Inventory exactly 38 MCP tools,
13 protected MCP writes, 18 REST receipt kinds, 15 protected browser mutations,
and 24 work-event types. Keep older writers stopped: fresh closeouts now require
a report and operation UUID, fresh work starts Pending, and settings use revision
checks. Permanent historical receipts remain recoverable with their exact old
request; do not manufacture missing reports or evidence for historical work.

Migration 0023 enables identity-preserving cross-project moves. Quiesce every
writer before upgrading because historical work-owned foreign keys and event
guards change together. After upgrade, verify that each move has one immutable
move row, exactly one source and one target event, and one corresponding activity
entry in each project. A move preserves the UUID, lifecycle/completion state, and
expired retained lease; it never rewrites the project recorded on older facts.

Migration 0024 adds review policy and history that cannot move between projects.
For its final cutover use the [code-review deployment rules](code-reviews.md#recovery-and-deployment)
and `audit_code_reviews.py`. Older-head activity-audit commands below remain
valid at their explicit preflight/restore heads, not as a 0024 certification.

Before changing a production database, pin the coordinated artifacts, reserve a
maintenance window, close ingress and quiesce every writer including direct
clients, then take a fresh named custom-format backup. Validate its archive
listing, copy it off-machine, and restore it on an isolated PostgreSQL 17
instance. Record row/content and permanent-receipt comparisons, migration lock
time, audit duration, activity ordering, and representative old receipt replay.
The isolated repository checks are evidence about the implementation; they do
not establish production-target readiness or execute a production cutover.

With `DATABASE_URL` supplied privately in the operator environment, the read-only
preflight at the prior head is:

```sh
uv run --project backend python scripts/audit_project_activity.py \
  --expected-head 0022_external_references
```

Any nonzero exit, blocking finding, unexpected head, or runtime failure stops
the rollout. Apply `0023_work_item_moves` with writers still stopped. It creates
the immutable move ledger, extends event and receipt catalogs, replaces the
work-owned historical foreign keys and guards, and builds the stable-work
provenance high water without rewriting existing report links. Audit the new
head before starting any writer:

```sh
uv run --project backend python scripts/audit_project_activity.py \
  --expected-head 0023_work_item_moves
```

The aggregate audit checks all prior-phase invariants together with paired move
events/activity, move chains and receipts, current versus historical ownership,
stable-work provenance, enabled guards, exact catalog definitions, and
privileges. Keep its content-free output; do not put report prose, prompts, IDs,
operation keys, or receipt bodies in ordinary telemetry. It accepts only
explicitly frozen migration-built and supported dump/restore catalog
representations.

Deploy the coordinated API, MCP, dashboard, and plugin while ingress remains
closed. Confirm readiness, current catalog/schema versions, a default nonblank
prompt, and historical receipt recovery. Run source-to-target move/replay,
fresh-closeout, dismissal/retry, cross-project follow-up provenance,
settings-conflict, and activity recovery probes against an isolated
production-shaped clone. Take and restore a post-upgrade backup, rerun the
aggregate audit, and only then reopen traffic. The supported restore script
rotates restored activity incarnations inside the schema-replacement
transaction; verify a cursor captured before restore returns
`activity_stream_changed` after restoration.

The dashboard polls at 15-second intervals while visible and treats live socket
messages as hints. A lost socket hint is not a lost journal fact. Monitor
sanitized availability/error counts and bounded mutation/read latency. Fresh
project mutations share a ten-second absolute budget and at most two seconds
per lock wait. A protected `client_operation_unavailable` requires the same
frozen-intent retry; unprotected `project_mutation_unavailable` does not grant
retry safety. A failed audit is an integrity incident, not an invitation to
edit append-only rows, reset counters, purge receipts, or synthesize reports.

A lossless downgrade from 0023 is guarded before DDL and is available only while
no move fact, `work_moved` event, or move receipt exists. Once the move feature
has been used, fix forward or deliberately restore an entire chosen archive with
matching binaries, accepting the loss of all later writes. Never trim history
to make a guard pass.

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
the database at head. Prefer a forward fix. Migrations
`0015_gate_review_fixes` and `0016_duplicate_handling` have no supported
Alembic downgrade path.
Once 0016 has been applied, the only data rollback
boundary is a complete restore of a chosen pre-upgrade archive, explicitly
accepting loss of every later write. Never
truncate receipts, edit events, resolve/delete gates, or disable constraints to
force an application or schema rollback.

Migration `0018_repository_freshness` has one narrower guarded downgrade: it can
return to 0017 only while every checkpoint scope is empty. It takes an
`ACCESS EXCLUSIVE` lock and refuses before any DDL when a non-empty declaration
exists, so no concurrent insert can race the check. Once scoped data exists,
serve only 0.5-compatible binaries and fix forward or restore the complete
database with matching binaries. There is no force option, metadata shadow copy,
old-backend bridge, response projection shim, or receipt rewrite.

Migration `0019_structured_completion_evidence` refuses to upgrade on any
condition it cannot migrate, and names the one it found: a failure reads
`0019 preflight rejected 0018 history: <condition>`, so the refusal identifies
which rows to inspect. It deliberately accepts one historical shape. Work
completed before `0010_work_events` owns no completion checkpoint and therefore
no completion event, because `0010` reconstructs only provable facts; such an
item carries no completion episode, migrates unchanged at generation 0, and
reads back with a null current completion pointer and no episodes. It cannot
leave `done` afterwards: the API answers 409 `completion_episode_unsealed`, and
`completion_episode_departure_guard` fails the write closed behind that. The
refusal is permanent rather than a transient fault, so retrying will not clear
it. A `done` item that does own a completion checkpoint whose event is missing
or duplicated is a real fault and still stops the upgrade.

Migration `0019_structured_completion_evidence` has a broader guarded
downgrade. It can return to 0018 only while both evidence tables are empty,
every retained completion/reopen episode satisfies the reversible chronology,
and no completed receipt response contains Phase 11's top-level
`completion_evidence`. It locks every affected table and refuses before DDL on
any lossy or incoherent state. Invoke it only after stopping and draining every
first-party writer and prohibiting direct evidence/checkpoint/event DML. The
migration requires a fresh READ COMMITTED transaction, sets a five-second
local lock timeout before acquiring its ordered `ACCESS EXCLUSIVE` locks, and
aborts the entire transaction before DDL on a timeout or deadlock. Retry only
from another fresh READ COMMITTED transaction after re-establishing
quiescence. Evidence-free Phase 11 completion/reopen cycles remain eligible
only when this entire preflight proves their chronology reversible. Once an
evidence row or evidence-bearing receipt exists, or any preflight cannot prove
eligibility, keep 0.6-compatible binaries serving and fix forward or restore a
complete matching backup. Never delete evidence, rewrite a receipt, clear a
generation, or disable a guard to force downgrade.

A Phase 9 Core restore rehearsal must also cover authoritative merge rows,
supporting relationship witnesses, paired relationship and `work_merged`
events, alias readiness/mutation guards, merge receipts, and same-key replay
without another durable effect. An older archive can migrate forward, but it
cannot recover graph, event, receipt, gate, or merge facts created after that
archive.

### Phase 9 Core 0.3.0 cutover

Core is one coordinated compatibility boundary: API, MCP, and dashboard
`0.3.0`, plugin `0.7.0`, and migration `0016_duplicate_handling`. Do not serve
a Phase 8 process against 0016. Before scheduling production downtime, obtain
the product/operator permanence signoff and complete the pre- and post-0016
backup restore rehearsals; neither is implied merely because the code or tests
pass.

The release procedure is:

1. Build and pin all coordinated artifacts. Stop web, MCP, API, and every
   direct writer while leaving PostgreSQL and backup access available.
2. Take a named custom-format pre-upgrade dump, verify `pg_restore --list`,
   copy it to independent storage, and prove it restores in isolation.
3. In a private environment with backend dependencies, database connectivity,
   and the configured backup directory, run the aggregate read-only preflight:

   ```sh
   uv run --project backend python scripts/audit_duplicate_handling.py \
     --database-url "$MNEMONIC_OPERATOR_DATABASE_URL" \
     --backup-directory ./backups \
     --expected-head 0015_gate_review_fixes
   ```

4. Apply exactly 0016 with
   `docker compose run --rm api alembic upgrade 0016_duplicate_handling`.
   Verify the head, coordinated package versions, generated OpenAPI snapshot,
   and exact 26 MCP tools/11 protected MCP writes/11 browser mutations before
   reopening traffic.
5. Run the same audit with `--expected-head 0016_duplicate_handling`. Core must
   pass with zero blocking findings. Historical weak duplicate marks, their
   multi-target/cycle/depth shape, and related source facts are informational:
   migration deliberately preserves them and creates zero authoritative merges
   or witnesses from them.
6. Start API first for authenticated read-only health, contract, old-receipt,
   event, and canonical-root probes. Then start MCP and web for read-only probes.
   No production cutover probe commits a merge.
7. Take a post-0016 backup, prove it restores in isolation, rerun the 0016 audit
   and representative old/merge receipt replays on that restore, and only then
   reopen writers.

The current audit defaults to the Phase 11 head, so Core operators must pass
`--expected-head 0015_gate_review_fixes` before migration and
`--expected-head 0016_duplicate_handling` afterward. It opens a repeatable-read,
read-only transaction, emits aggregate JSON without IDs or content, checks
schema/functions/capacity and Core invariants, and returns nonzero when blocked.
An audit runtime failure is not a pass. Keep raw diagnostic IDs out of ordinary
logs and tickets.

### Final Phase 9 Advisory 0.4.0 cutover

Advisory is the next coordinated boundary: API, MCP, and dashboard `0.4.0`,
plugin `0.8.0`, and migration `0017_duplicate_suggestion_title_key`. Migration
0017 preserves every row, rewrites no work text, and creates no canonical fact;
it adds only the versioned title-key function and visible-work expression
index. Do not expose an older API, MCP adapter, or dashboard against head 0017.
Migration 0017 can be downgraded to Core: it drops only those derived objects,
preserves all domain rows, and intentionally leaves the Alembic revision column
widened to hold the descriptive 0017 identifier. That does not reverse any
Core merge; application artifacts must still be changed as one boundary.

After the Core prerequisites above are satisfied:

1. Quiesce every writer, take a named pre-0017 custom-format dump, validate its
   archive listing, copy it off-machine, and prove it restores in isolation.
2. Run the aggregate audit with
   `--expected-head 0016_duplicate_handling`; a runtime failure or blocking
   result stops the rollout.
3. Apply exactly 0017 with
   `docker compose run --rm api alembic upgrade 0017_duplicate_suggestion_title_key`.
4. Verify the migrated function and partial expression index against ORM
   metadata and PostgreSQL-17 catalog definitions. Verify OpenAPI `0.4.0`, exact
   27 MCP tools/11 protected writes, 11 browser mutations, direct and chunked
   body caps, request saturation, lexical fallback, safe-read retry, and Create
   anyway before reopening traffic. No production probe persists a draft or
   commits a merge.
5. Run `scripts/audit_duplicate_handling.py` with
   `--expected-head 0017_duplicate_suggestion_title_key` and zero blocking
   findings. Take a post-0017 backup, restore it in isolation, rerun the audit
   and representative Core receipt replays, and confirm suggestions create no
   domain or receipt facts.
6. Start API for authenticated read-only probes, then MCP and dashboard. Reopen
   writers only after the coordinated artifacts and restored data checks pass.

Passing repository tests does not claim that this production cutover, either
restore rehearsal, or product/operator permanence signoff occurred.

### Phase 10 repository freshness 0.5.0 cutover

Phase 10 is one coordinated prerelease boundary: API, MCP, and dashboard
`0.5.0`, plugin `0.9.0`, and migration `0018_repository_freshness`. Migration
0018 adds only the ordered `checkpoints.affected_paths` declaration, its
versioned validator, and two constraints. Every historical row receives an
empty array; no prompt, metadata, tag, branch, commit, relationship, event,
duplicate fact, or checkout is used to infer a scope. Canonical sparse
serialization keeps that empty history absent from JSON and preserves every
historical receipt request fingerprint and response body.

Do not expose any 0.4.x first-party API, MCP adapter, or dashboard against head
0018. Once a non-empty scope can be returned, older plugins and strict clients
may reject the new property and are unsupported rather than served a downgraded
projection. There is no mixed-version feature flag or compatibility subrelease.

Before scheduling the cutover:

1. Build and pin all coordinated artifacts. Confirm the release source,
   generated OpenAPI, migration/function/trigger hashes, exact 27 MCP tools,
   11 protected MCP writes, 13 REST receipt kinds, and 11 protected browser
   mutations.
2. Inventory every first-party service, direct client, and installed plugin.
   Confirm each helper host provides Bash 3.2 or newer and Git 2.45.0 or newer;
   an older Git must return `unsupported_git_version` before repository access.
3. Take a named custom-format backup at 0017, validate `pg_restore --list`, copy
   it to independent storage, and prove it restores in isolation.
4. In a private environment with database and backup access, run the aggregate
   read-only preflight:

   ```sh
   uv run --project backend python scripts/audit_duplicate_handling.py \
     --database-url "$MNEMONIC_OPERATOR_DATABASE_URL" \
     --backup-directory ./backups \
     --expected-head 0017_duplicate_suggestion_title_key
   ```

5. On the isolated production-shaped restore, rehearse the populated migration,
   exact row/receipt digests, old receipt replay, scoped create/add/complete
   writes and reads, strict old-client failure, empty-only downgrade, post-scope
   downgrade refusal, fix-forward, whole restore, and installed helper. Record
   lock durations and target host capabilities without recording paths, branches,
   SHAs, roots, remotes, raw Git errors, or filenames.

For the live quiesced rollout:

1. Announce maintenance and the required first-party/client/plugin minimums.
   Stop web, MCP, API, every direct writer, and reads that could cross the
   incompatible first-party boundary.
2. Apply exactly 0018:

   ```sh
   docker compose run --rm api alembic upgrade 0018_repository_freshness
   ```

3. Verify `checkpoints.affected_paths` is one-dimensional
   `VARCHAR(512)[] NOT NULL DEFAULT '{}'`, every historical value is empty, and
   no new index exists. Verify `mnemonic_affected_paths_valid_v1(VARCHAR[])`,
   `ck_checkpoints_affected_paths_valid_v1`,
   `ck_checkpoints_affected_paths_require_commit`, and the unchanged checkpoint
   and receipt guards against the reviewed PostgreSQL catalog definitions.
4. Run the audit at the new head; any runtime failure or blocking finding stops
   the rollout:

   ```sh
   uv run --project backend python scripts/audit_duplicate_handling.py \
     --database-url "$MNEMONIC_OPERATOR_DATABASE_URL" \
     --backup-directory ./backups \
     --expected-head 0018_repository_freshness \
     --require-empty-scope
   ```

   `--require-empty-scope` is the pre-enablement gate: it makes any already
   populated declaration block the rollout. Do not use that flag for routine
   steady-state audits after scoped writes have deliberately been enabled.

5. Deploy API/MCP/dashboard 0.5.0 and plugin 0.9.0 together. Verify no 0.4.x
   first-party process is serving before reopening traffic.
6. Replay representative historical receipts, then use a disposable project to
   prove omitted and explicit-empty input remain sparse and a valid ordered
   non-empty declaration survives create, add, complete, checkpoint history,
   bounded context, resource, and resume-prompt reads. Confirm compact pointers,
   events, search, hierarchy, readiness, relationships, suggestions, and caches
   remain scope-free.
7. From an explicitly selected disposable workspace, exercise installed helper
   `unchanged`, `changed`, and `indeterminate` paths under the 15-second
   caller-enforced process-group deadline. Confirm it neither changes the
   repository nor contacts a network, and treat all results as ephemeral
   advisory evidence.
8. Take and independently restore a post-0018 backup, rerun the new-head audit
   and representative old and scoped receipt replays, then reopen traffic.

Before the first non-empty declaration, a coordinated rollback may quiesce all
clients and, while the 0.5 migration code is still available, run:

```sh
docker compose run --rm api alembic downgrade 0017_duplicate_suggestion_title_key
```

The downgrade obtains `ACCESS EXCLUSIVE`, checks every scope, and raises before
DDL if any is non-empty. Deploy the matching 0.4 artifacts only after a
successful downgrade. After scoped use, do not retry it with disabled guards,
copy paths into metadata, rewrite receipts, or place an old backend in front of
0018. Keep 0.5-compatible binaries serving and fix forward, or restore the whole
database plus matching binaries with explicit acceptance of all post-backup data
loss. Passing repository tests does not claim any production rehearsal or
cutover occurred.

### Phase 11 structured completion evidence cutover

Phase 11 introduced one coordinated prerelease boundary: API, MCP, and dashboard
`0.6.0`, plugin `0.10.0`, and migration
`0019_structured_completion_evidence`. It adds two initially empty immutable
evidence tables and private completion/reopen generation bindings. The migration
pairs only already-retained completion checkpoints and `work_completed` events;
it never infers verification or artifacts from checkpoint prose, metadata,
repository state, branches, tags, relationships, or receipt content. Every
pre-0019 row count, pre-existing column value, and permanent receipt byte
remains unchanged; only the new private generation columns receive their
deterministic backfill.

The subsequent API, MCP, and dashboard release `0.7.0` retained the Phase 11
schema, plugin `0.10.0`, and exact tool/mutation catalog. Upgrading an existing
0019 deployment to `0.7.0` requires no new migration; the 0018-to-0019 cutover
procedure below still applies to deployments that have not adopted Phase 11.

Do not expose a 0.5.x first-party API, MCP adapter, dashboard, or plugin against
head 0019. The new checkpoint/work/event columns are private, but strict clients
do not understand evidence-bearing completion responses or the 28th MCP tool.
There is no feature flag, downgraded projection, dual schema, shadow evidence
store, or standalone compatibility mutation.

Before scheduling the cutover:

1. Build and pin the coordinated artifacts. Confirm OpenAPI `0.7.0`, plugin
   `0.10.0`, migration hashes, exactly 28 MCP tools/11 protected writes,
   13 REST receipt kinds, and 11 protected browser mutations.
2. Run the complete backend, MCP, frontend, Playwright, pre-commit, shared
   fixture, worst-case size, and nginx identity-coding gates documented in
   [`development.md`](development.md). A PostgreSQL skip or unavailable real
   transport lane is a failed release gate.
3. Inventory every direct REST client and installed plugin. Confirm each one
   uses the conditional completion operation-ID rule, treats evidence as inert
   caller assertion, and does not fetch artifact URLs or execute command text.
4. Quiesce writers at head 0018. Take a named custom-format pre-0019 dump
   with the shipped script, which uses `--no-owner` but intentionally retains
   archived ACL commands for public-schema application objects. Never add or
   pass `--no-acl` for a shipped archive. Validate `pg_restore --list`, copy
   the archive to independent storage, and prove it restores in isolation with
   matching 0.5 artifacts.
5. On that isolated production-shaped restore, run the aggregate audit at
   `0018_repository_freshness`; it must recognize exactly the reviewed
   migration-built or shipped-backup-restored PostgreSQL 17 survivor-catalog
   digest and reject every other projected form. Any runtime failure or
   blocking result stops the rollout. Record only content-free aggregate
   output.
6. Rehearse 0018-to-0019 upgrade and compare every pre-existing row, identity,
   sequence, timestamp, digest, receipt fingerprint/body/version, and the exact
   incoming raw Phase 10 survivor-catalog representation. Verify evidence
   tables are empty, retained completions received only their deterministic
   private bindings, and the Phase 11 vocabulary checks survive a
   dump/SQL-reparse unchanged.
7. Exercise keyed/unkeyed evidence-free completion, keyed structured
   completion and replay, reopen/recomplete, alias-owned history, soft-delete
   concealment, stable pagination, concurrent completion/replay, and injected
   rollback. Rehearse an unused downgrade/re-upgrade, then confirm evidence and
   Phase 11-only receipt use each refuse downgrade before DDL.
8. Measure exclusive-lock/backfill time, audit time, backup/restore time, and
   the 1 MiB ingress, 3 MiB identity history, 896 KiB canonical representation,
   and 12 MiB complete MCP envelope boundaries on production-shaped hardware.

For the live quiesced rollout:

1. Announce maintenance and stop web, MCP, API, every direct writer, and reads
   that could cross the incompatible boundary. Stop the scheduled backup loop
   as well, so an automatic dump cannot contend with the migration's ordered
   exclusive locks:

   ```sh
   docker compose stop web mcp api backup
   ```

   Retain PostgreSQL and verified backup-directory access.
2. While that live quiescence remains in force, confirm the database is still
   at `0018_repository_freshness` and take a new named custom-format archive
   in a one-shot container that cannot restart the stopped API:

   ```sh
   docker compose run --rm --no-deps backup once
   ```

   Keep the scheduled backup service stopped through the 0018 audit and
   migration. Validate the archive's table of contents with matched PostgreSQL
   17 tools, record its exact name/size/hash, copy it to independent storage,
   and prove it restores in isolation with the matching 0.5 artifacts. This
   fresh archive—not an earlier rehearsal archive—is the live rollback point.
   Do not resume any writer between this backup and migration.
3. Run the read-only aggregate audit against the still-quiesced live database
   at head 0018. Require one of the two approved raw Phase 10 survivor-catalog
   projections and zero blocking findings:

   ```sh
   uv run --project backend python scripts/audit_duplicate_handling.py \
     --database-url "$MNEMONIC_OPERATOR_DATABASE_URL" \
     --backup-directory ./backups \
     --expected-head 0018_repository_freshness
   ```

   Preserve only the content-free report. Any audit, archive, or isolated
   restore failure stops the rollout before DDL.
4. Apply exactly 0019:

   ```sh
   docker compose run --rm api alembic upgrade 0019_structured_completion_evidence
   ```

5. Verify the Alembic head, both empty evidence tables, the three generation
   columns/backfill, every composite ownership constraint/index, immutable and
   truncate guards, completion insertion/event/reopen/liveness guards, the
   sealed-episode validator, effective owner-only privileges on both evidence
   relations, and exact owner-only execution ACLs on every Phase 11 function
   with no `PUBLIC EXECUTE`. Any unexpected evidence row, privilege, unpaired
   completion, or object mismatch stops the rollout.
6. Run the audit at the new head with its Phase 11 pre-enablement option; a
   runtime failure or blocking finding stops the rollout:

   ```sh
   uv run --project backend python scripts/audit_duplicate_handling.py \
     --database-url "$MNEMONIC_OPERATOR_DATABASE_URL" \
     --backup-directory ./backups \
     --expected-head 0019_structured_completion_evidence \
     --require-empty-completion-evidence
   ```

   Use the empty-evidence flag only before enabling 0.6 writers. Nonzero
   evidence becomes expected steady-state inventory afterward, not corruption.
7. Deploy API, then MCP/dashboard `0.7.0`, then plugin `0.10.0`. Before reopening
   writes, confirm no 0.5.x first-party process remains and run authenticated
   read-only health, OpenAPI, exact catalog, old receipt replay, empty history,
   nginx identity, and real MCP HTTP/stdio probes.
8. In an explicitly disposable project, complete once without evidence and
   once with mixed evidence, replay the exact latter intent, page both episodes,
   reopen, and confirm current-pointer movement. Inspect browser/MCP logs,
   storage, URLs, errors, and live frames for absence of content and controls.
9. With writers and the scheduled backup loop still stopped, take a named
   post-0019 dump explicitly:

   ```sh
   docker compose run --rm --no-deps backup once
   ```

   Prove it restores in isolation with archived public-schema
   application-object ACL commands replayed and ownership rebound to the fixed
   current application role, rerun the 0019 audit and representative
   historical/new receipt replays, and compare evidence ownership, privileges,
   function revocations, order, generation, and page identity. Restart and
   health-check the scheduled backup service, then reopen writers:

   ```sh
   docker compose up -d --wait backup
   docker compose ps backup
   ```

Once step 8 writes structured evidence, the guarded downgrade is intentionally
unavailable. If a later validation fails, fix forward or restore the exact fresh
pre-0019 archive from step 2; it contains every production write through the
quiescence point, so the only discarded post-backup facts are from the
explicitly disposable smoke project. Never substitute a stale pre-scheduling
rehearsal archive. Keep writers closed until step 9 succeeds.

Only while both evidence tables are empty, no completed receipt response has a
top-level `completion_evidence` key, and the preflight can prove every retained
evidence-free completion/reopen chronology reversible may a coordinated
rollback stop and drain every first-party writer, prohibit direct
evidence/checkpoint/event DML, and start a fresh transaction at exactly READ
COMMITTED. While the 0.6 migration code is still available, run:

```sh
docker compose run --rm api alembic downgrade 0018_repository_freshness
```

The downgrade sets its five-second local lock timeout, obtains the documented
ordered `ACCESS EXCLUSIVE` locks, and aborts the full transaction before DDL on
any lock timeout, deadlock, evidence row, Phase 11 response, malformed state,
or unsafe chronology. Never retry inside the failed transaction: establish
quiescence again and invoke Alembic from another fresh READ COMMITTED
transaction. Deploy matching 0.5 artifacts only after a successful downgrade
and exact 0018 catalog audit. After structured evidence use or any refused
preflight, do not manually empty tables or generations: fix forward or restore
the whole pre-0019 database and matching binaries with explicit acceptance of
every post-backup write lost. Repository validation proves neither this
rehearsal nor production approval occurred.

## Durable runtime invariants

### Declared checkpoint scope and local repository assessment

`affected_paths` is an untrusted caller declaration stored on an immutable full
checkpoint. It describes every eligible repository path on which that
checkpoint's assertions depend, not merely files changed by its author. A
non-empty ordered list requires a caller-asserted `verified_against` commit and
is retained exactly. Omission and explicit `[]` both mean unknown scope and
serialize without the property; `**` is the only explicit whole-eligible-
repository declaration. Empty history is never evidence of no change.

The v1 declaration is capped at 64 entries, 512 ASCII bytes per entry, and
16,384 bytes total. Each slash-separated component uses only ASCII letters,
digits, `.`, `_`, `@`, `+`, `=`, `,`, `~`, `-`, and `*`; `**` is allowed only
as a complete component. Exact duplicates, empty or dot components, absolute
forms, whitespace, non-ASCII, controls, unsupported wildcard/pathspec syntax,
and excess bounds are invalid. Never repair a declaration by trimming, sorting,
normalizing, expanding, or copying it into metadata.

Scope appears only on authorized full checkpoint reads and the permanent
receipt bodies that already carry them. It remains absent from checkpoint
pointers, events, search, hierarchy, gates, readiness, relationships,
embeddings, duplicate suggestions, and derived-cache identity. The server, MCP
adapter, and browser never inspect Git and never store a freshness result.

Only the packaged local helper assesses the explicitly selected current
workspace. A project repository URL is display context, not repository identity;
the helper neither resolves nor contacts it. `repository_branch` is also
display-only and is not compared. The local runtime floor is Bash 3.2 and Git
2.45.0. Under its 15-second caller-enforced process-group deadline the helper
returns exactly `unchanged`, `changed`, or `indeterminate`; an unsupported
runtime, missing scope/baseline, ambiguous workspace, malformed output, timeout,
or completeness blocker is indeterminate rather than clean.

An unchanged result means only that no relevant eligible Git change was observed
in two stable, complete sweeps. Changed or indeterminate requires current source
reinspection or a workspace choice. No outcome proves semantic correctness,
grants authority, resolves a gate, changes lifecycle/readiness, or mutates
Mnemonic. Do not persist the assessment automatically. Actual filenames and
helper output can enter tool/conversation/model context; retain their byte
quoting and caps, and keep declarations, names, roots, branches, SHAs, remotes,
stderr, and command strings out of routine logs and telemetry.

### Structured completion evidence

Evidence is optional caller-reported history, not an attestation or overall
completion score. The service validates only structural consistency. Operators
must not run a stored command, resolve a commit/branch/path against a checkout,
or fetch an artifact URL as part of routine display, audit, monitoring, or
repair. Do not put credentials, signed URLs, access tokens, private transcripts,
or unnecessary output in evidence; exact accepted strings are durable and
available to authorized readers and backups.

The only write path is nested in `complete_work`. Any structured child makes
the top-level operation UUID mandatory, and the checkpoint, lifecycle/event,
evidence, lease departure, and receipt share one transaction. Empty evidence is
canonical absence. A late CI result or correction does not authorize row edits:
append narrative context while pending or explicitly reopen and create a new
completion episode. Direct update/delete/truncate or generation repair is an
integrity incident, not routine administration.

The safe history route returns at most ten exact completion episodes under a
stable event high-water cursor. An evidence-free item is a real completion, not
missing data. `structured_completion_total` and `total` have different meanings;
do not alert merely because they differ. To claim a current complete audit,
exhaust the cursor chain, fetch a new first page, and compare the high-water,
work version/status, alias projection, and current checkpoint. Continuous drift
means no current audit was established.

The API page and every first-party reader use an inclusive 3 MiB identity-body
limit. Any non-identity or malformed `Content-Encoding`, byte 3,145,729,
`completion_evidence_unavailable`, or decoder/coherence failure is a bounded
read incident and never evidence that history is empty. The supplied nginx
snippet must remain installed so the same-origin path disables compression,
transformation, buffering, and caching. MCP ingress is separately 1 MiB and its
complete SDK-emitted evidence response retains its 12 MiB size proof. The
0.10.0 general stdio result ceiling is 64 MiB to carry full contexts (including
the SDK's text and structured copies); supported maximum-context measurements
exceed 48 MiB. Do not raise limits or truncate data during an incident. Reduce future unreleased input/page maxima
only through a reviewed compatibility change.

### Leases

Lease tokens are capabilities inside the shared bearer-key trust boundary. They
belong only in claim/renew responses and JSON mutation bodies. Never copy them
into checkpoints, events, URLs, chat, tickets, metrics, logs, or screenshots.
The browser never calls a token-bearing claim, renewal, or release and never
receives or forwards a token. Its manual Active action creates a dashboard-owned
lease but returns only the five-field public projection. Its Pending action can
clear only the exact active public lease the person reviewed, or an observed
Dropped row; a replacement lease produces `lease_state_changed`. Both actions
emit actor-attributed work events. Expired lease rows are deliberately retained
until a later acquisition replaces them; TTL expiry is abandoned-session
recovery, not an operator force-release task.

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

### Authoritative duplicate merges

A duplicate mark is a descriptive `duplicate-of` relationship. Only an
immutable row in `work_duplicate_merges` makes its source an alias. Never infer
one from old marks, lifecycle, wording, embeddings, timestamps, or UUID order.
New duplicate marks must be created inside `merge_work`; existing marks remain
unchanged and unselected marks remain removable only while both endpoints are
canonical.

Every merge has one source, one direct destination, one exact supporting mark,
two endpoint `work_merged` events, two relationship events only when it created
that mark, and one completed `merge_work` receipt. The source becomes
non-actionable but retains its own lifecycle and complete checkpoint, event,
gate, relationship, provenance, and receipt audit. Reads expose its bounded
path to the current root explicitly. They never redirect an ID or blend source
and root context, and the mutation never transfers or coalesces relationships,
leases, gates, content, lifecycle, provenance, or authority.

The source must have no unresolved human gate or incident block/parent-child
edge. An active source lease can be consumed only with its exact capability;
the browser never receives one and therefore disables merge until the lease is
released, completed, or expires. After merge, every source-incident
relationship and every alias domain mutation is frozen. An alias must never
appear in ready results or acquire a lease. A nonzero aggregate audit finding
in these categories is an incident; do not bypass triggers or repair rows
manually.

`merge_work` always requires a caller-prepared operation UUID. On timeout,
disconnect, malformed success, 5xx, or `client_operation_unavailable`, resend
only the exact retained UUID and complete source/destination revisions, token,
rationale, and provenance. The replay is historical proof, so reread exact
source history and the canonical root afterward. Generating another UUID can
commit a different irreversible decision and is never recovery.

The typed `503 duplicate_graph_invalid` response is the exception: it is a
definitive integrity incident, not an unknown merge outcome. Do not retry the
mutation. Stop authority-changing work, preserve the frozen intent privately,
and run the aggregate audit before investigating the database invariants.

There is no unmerge, retarget, delete, or operator SQL repair. For a mistaken
merge, quiesce all writers, take a named backup, preserve aggregate audit
evidence privately, and rehearse the complete pre-merge restore. Restore only
with explicit two-person approval accepting loss of every write after that
archive. If those writes cannot be discarded, leave the merge intact and ship
a separately designed append-only correction release.

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
current human instruction explicitly selects it. The detail pane keeps Defer as
the split button’s default action and offers Pending, Active, Done, Won’t Do,
and Promote in its menu, excluding the current state. A manual terminal choice
uses the normal closeout rules; Done creates an immutable decision-only
completion checkpoint, and every terminal choice creates a narrowly truthful
human-decision report. Activity renders the dashboard-authored lifecycle or
lease event as an explicit human decision. Resolving a gate does not undefer a
work item; it only removes the gate's independent readiness fact.

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

At current head 0023, run `scripts/audit_project_activity.py` using the private
`DATABASE_URL` environment variable. It composes the historical domain checks
with Phase 12 activity/report and external-reference checks. Alert on any blocking finding or runtime
failure, and inventory deployed `0.12.0` clients and plugin `0.13.0` together.
The historical audit below applies only to its explicitly named older heads.

For the historical Phase 11 boundary, run `scripts/audit_duplicate_handling.py` with
`--expected-head 0019_structured_completion_evidence` from a private environment
that can reach PostgreSQL and the backup directory. Alert on any blocking
finding. At 0018 and 0019 it requires exactly one of the two reviewed raw
PostgreSQL 17 Phase 10 survivor-catalog representations and rejects all other
drift. At 0019 it retains every Phase 10 scope check and additionally verifies
evidence tables/constraints/indexes, effective owner-only relation privileges,
exact Phase 11 function ACLs with no `PUBLIC EXECUTE`, exact enabled
trigger/function hashes, completion and reopen generations, sealed episode
chronology, event identity
sequence health, child ownership/position/time/aggregate invariants,
receipt-to-row correspondence, and downgrade eligibility without printing any
stored value. Its weak-mark counts remain inventory, not merge
decisions: cycles, multiple targets, leases, gates, or structural adjacency on
historical descriptive marks do not authorize data cleanup or canonical
inference. Invoke privileged ID-level diagnostics only during an incident and
do not copy their output into ordinary telemetry.

A valid nonzero scoped-checkpoint or evidence count after deliberate enablement
is inventory, not corruption; before the coordinated release permits those
writes, the matching explicit empty-state flag stops the rollout. Alongside the database audit, inventory deployed first-party
versions and the installed plugin version, package contents, helper executable
mode, and reference/skill links. Alert on any 0.5.x first-party process serving
against 0019 or any installed helper/package drift. The server has no repository
freshness metric because it performs no assessment. Optional local metrics may
retain only bounded state, reason, duration bucket, pattern/display counts,
truncation, and runtime versions—never declarations or repository evidence.

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
healthy. It runs a transactionally consistent custom-format `pg_dump` with
`--no-owner`, checks that `pg_restore` can read its archive, and atomically
renames the completed file into the backup directory. Ownership is intentionally
rebound to the fixed application role on restore, but archived ACL commands for
public-schema application objects are retained and replayed so Phase 11's
`PUBLIC EXECUTE` revocations survive. Do not add `--no-acl` to a shipped archive:
that can silently weaken existing application-object privileges and, at head
0019, the audited function boundary. Failed partial dumps never become
successful dumps.
The interval defaults to 86400 seconds (24 hours). An unhealthy or restarting
backup container needs attention; `docker compose ps` shows its state.

```sh
docker compose exec backup sh /opt/mnemonic/backup.sh once
docker compose logs --tail=20 backup
```

Files appear under `MNEMONIC_BACKUP_DIR` (`./backups` by default). They include
canonical work, immutable checkpoint text and provenance including declared
repository dependency scopes, retained leases, typed relationships,
authoritative duplicate merges and their sequence/witnesses,
immutable work events and their sequence, human gates and their attention
identity sequence, private durable client-operation receipts, private
completion/reopen generations, immutable verification results and artifact
references, and migration state; treat them as private. Receipt
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
duplicate-forest depth and alias guards, exact merge/relationship event pairs,
attention sequence state, receipt count/uniqueness/state plus its guards, exact
replay of representative ordinary, gate, and merge successes, and the expected
`alembic_version`. At head 0019, also verify exact scope order and case,
empty-history sparse serialization, commit dependency, immutable rows,
validator and constraint catalog definitions, representative scoped receipt
replay, completion/reopen chronology and sequence state, evidence ownership,
positions, record times, sealed-episode and immutable trigger hashes,
representative evidence-bearing receipt replay, bounded history, absence from
compact and derived projections, effective owner-only privileges on both
evidence relations, and exact owner-only function ACLs with no `PUBLIC EXECUTE`.
A pre-0019 archive contains no Phase 11 functions and remains migratable; 0019
creates the reviewed privileges during that upgrade. Keep the PostgreSQL major
version compatible with the dump
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
# Keep ingress closed and direct clients stopped throughout these steps.
docker compose run --rm api alembic upgrade head
# Supply DATABASE_URL privately for this read-only operator command.
uv run --project backend python scripts/audit_project_activity.py
docker compose up -d --wait
# Reopen traffic only after readiness and restored data checks pass.
```

PostgreSQL must remain running during this sequence. The restore script refuses
to run without the explicit confirmation value, rejects filenames containing
directory paths, and uses a single transaction for schema replacement and
archive loading so errors restore the original target. If the archive contains
project activity, it also rotates every stream UUID inside that same transaction;
a missing rotation function makes the restore fail closed. Sequences and source
facts remain unchanged by rotation. An older archive receives new streams when
0020 is applied. Never serve restored activity using its archived stream UUID:
a rewind could otherwise make an acknowledged cursor silently skip later work. It replays archived ACL
commands for public-schema application objects while rebinding ownership to the
fixed current application role. Mnemonic does not use non-public application
schemas or optional PostgreSQL extensions; the script
refuses either unexpected layout instead of deleting outside its ownership
boundary or producing a hybrid restore. The API applies every migration newer than the archive through the current head
before becoming ready. Do not expose API, MCP, or dashboard traffic until
readiness succeeds and the restored schema/data checks pass. Rehearse a restore
from before a schema change on an isolated instance first. Restore is not a
substitute for schema downgrade; downgrade is explicitly unsupported beginning
with migration 0015 and remains unsupported for 0016. Downgrading 0017 only
removes its derived suggestion function/index and leaves Core domain facts in
place. Downgrading 0018 to 0017 is allowed only while every scope remains empty;
its lock-protected guard refuses before DDL after any scoped use. Downgrading
0019 to 0018 additionally requires empty evidence tables, no Phase 11 completion
response, and reversible completion chronology under its exclusive locks. A pre-Phase-3
archive cannot recover later graph facts, a
pre-Phase-5 archive cannot recover later event history, a pre-Phase-6 archive
cannot recover later client-operation receipts, and a pre-Phase-7 archive cannot
recover later gates, gate events, attention order, or gate-operation receipts.
A pre-Phase-9 archive cannot recover later authoritative merges, supporting
witnesses, merge events, or merge receipts. Restoring such an archive after a
merge discards that merge and every unrelated later write as one database-wide
recovery boundary. A pre-Phase-10 archive likewise cannot recover later declared
checkpoint scopes or the scoped receipt evidence that binds them.
A pre-Phase-11 archive cannot recover later evidence rows, completion/reopen
generation bindings, or evidence-bearing receipt responses. A pre-Phase-12
archive cannot recover later reports, human dismissal decisions, follow-up
associations, prompt edits, activity-only facts, or their receipts. Verify these
facts, exact permanent receipt replay, automatic stream rotation, stale-cursor
rejection, and the new-head aggregate audit in every current restore drill.

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
backup boundary. Checkpoint, event, evidence, and receipt rows and private
generation bindings must never be edited during recovery.

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

## External records release: 0021 to 0022

The coordinated application/API/MCP/dashboard is 0.11.0, plugin 0.12.0, Alembic
`0022_external_references`. Counts remain 32 MCP tools, 11 protected MCP writes,
15 REST receipt kinds, 13 browser mutations, 17 event types and three skills.
Restart aligned components together; older processes must not use this schema.
No provider credential, environment setting, external network permission, source
configuration or background refresher is introduced. References use existing
create/update writes; external suggestions remain a non-persistent safe read.

Rehearse migration and a complete backup restore first on isolated PostgreSQL 17,
including populated aliases, deleted work, gates, receipts, completion evidence,
reports, follow-ups, reviews and activity. Compare old content digests, counts,
relationships, sequences and every permanent receipt byte. Existing rows gain only
an empty reference list; do not infer links from prose or rewrite history. Verify
catalog parity, direct SQL rejection, all receipt replay paths, max metadata and
receipt sizes, high-fanout contexts, index and complete route plans. Record actual
model cold/warm 1/16/64 candidate latency and competing semantic search behavior.
These release gates are distinct from executing production cutover.

At the separately authorized cutover, close ingress, stop/drain **all** writers,
then audit and take the final named custom-format recovery backup. An earlier
rehearsal backup is not the final recovery point. The exact audit commands are:

```sh
uv run --project backend python scripts/audit_project_activity.py \
  --expected-head 0021_job_completion_reports
```

Run this pre-upgrade command while the quiescent database still has `0021`.
Validate the final archive, copy it off-machine and restore-test it before
migration. Preserve the final quiescence counts/digests, relationships, sequences,
permanent receipts and catalog. Keep writers stopped, apply `0022`, then start
aligned processes behind closed ingress and verify health/version/catalog plus
create/update/clear/discovery/search/comparison smoke tests in an approved
staging or disposable project. Next run:

```sh
uv run --project backend python scripts/audit_project_activity.py \
  --expected-head 0022_external_references
```

The updated audit recognizes both frozen catalogs and keeps report/activity
checks active. It checks reference storage/index/validator/caps and data
invariants without outputting record text or credentials. Validate and restore a
post-upgrade backup to an isolated database and run the same `0022` audit there
before reopening ingress. The Phase 11 duplicate-handling preflight is not a
`0022` migration preflight. The audit uses existing private database access;
never paste database credentials into documentation, logs, or tool arguments.

Prefer forward repair. Downgrade fails closed if any reference remains in a row,
an event, or a populated permanent receipt, including a created-then-cleared list.
Only an empty-feature disposable database may restore predecessor validators and
remove the new objects. Before reopening ingress, failed cutover recovery restores
the entire verified final backup while writers stay stopped and rotates the
activity stream under the existing recovery procedure. Never erase links or edit
receipts to make old clients start. After reopening, restoring that backup can
lose later accepted writes; recovery then needs a separately decided strategy
preserving those writes or an explicit owner decision about loss.

Use stable credential-free provider permalinks, never signed access URLs.
Request-known secret checks are a narrow safeguard and cannot detect every
third-party secret. Candidate text is ephemeral and untrusted; log only bounded
aggregate timings/counts and categorical failures. Do not log drafts, bodies,
URLs, raw exceptions or authentication material. No external provider checks run
on the server, and no background index is created. D3 requires a new design.
