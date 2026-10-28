# Operating Mnemonic

Mnemonic supports version-pinned image installs and a separate contributor build overlay.
New installs use private named volumes initialized by Docker; existing bind installations
retain their paths with `compose.bind-mounts.yaml`. Begin with
[Upgrade from any release](#upgrade-from-any-release) for an existing deployment.
[Historical upgrade notes](upgrade-history.md) retain migration-specific prerequisites,
release context, and recovery procedures.

## Work summary length

`MNEMONIC_WORK_SUMMARY_MAX_CHARS` is a positive integer in `.env`, defaulting to
`2048`. Compose passes the same value to API and web. Recreate both services after
changing it; changing this setting does not require rebuilding their images.
Remote HTTP or stdio MCP adapters use the API's policy and need no separate copy.

The limit counts Unicode characters after surrounding whitespace is trimmed. It
applies to new work, changed summaries, report follow-ups, and duplicate-suggestion
drafts. The initial checkpoint prompt retains its separate 100,000-character
limit. Completion report summaries use their existing, separate contract.
Request, event-metadata, and operation-receipt byte budgets still apply separately;
this setting controls the summary character policy, not those transport budgets.

Reducing the setting never truncates stored summaries, hides their history, or
prevents replay of completed operation receipts. An unchanged existing summary may
be retained when editing another field; a changed summary must fit the current
limit. Server-generated remediation summaries fit the configured limit while their
checkpoint retains the complete findings.

Migration `0028_work_summary_limit` changes the work summary column to `text`,
rebuilds its generated search vector/index, and removes the old character cap from
immutable event validation. It takes an exclusive lock on work and event tables;
recreate the API after applying it. Downgrade refuses to proceed when current or
historical summaries exceed the former 1,000-character limit.

## Configuration

`python scripts/setup.py` creates four independent random secrets in `.env`,
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

- `MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS`, default 10, accepted range 1
  through 10. A wait timeout returns `client_operation_unavailable`; only an
  exact retry of the privately retained intent is safe.
- `MNEMONIC_EMBEDDING_CACHE`, the model-cache path used by semantic search.
- `MNEMONIC_DASHBOARD_ORIGINS`, a comma-separated exact-origin allowlist.
- `MNEMONIC_DUPLICATE_SUGGESTION_BODY_MAX_BYTES`, default 2097152.
- `MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_SLOTS` and
  `MNEMONIC_DUPLICATE_SUGGESTION_REQUEST_WAIT_MS`, defaults 4 and 250.
- `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_SLOTS`, default 2, range 1–4,
  selects independently loaded models shared by all semantic searches and suggestions.
- `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_THREADS`, default 1, range 1–8,
  bounds native threads per API model instance and the background worker's single model.
- `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_WAIT_MS`, default 5000, range 1–30000,
  bounds each model-call wait, clipped to its remaining request/stage deadline.
- `MNEMONIC_DUPLICATE_SUGGESTION_INFERENCE_QUEUE_SIZE`, default 8, range 0–16,
  bounds FIFO waiters per API process; zero disables waiting. See
  [semantic inference](semantic-inference.md) for sizing and upgrade guidance.
- `MNEMONIC_DUPLICATE_SUGGESTION_LEXICAL_SHORTLIST`,
  `MNEMONIC_DUPLICATE_SUGGESTION_MISSING_VECTOR_LIMIT`, and
  `MNEMONIC_DUPLICATE_SUGGESTION_FULL_POPULATION_CEILING`, defaults 200, 128,
  and 10000.
- `MNEMONIC_DUPLICATE_SUGGESTION_TIMEOUT_SECONDS`, default 45, range 1–45.
  Change older explicit values above 45 before recreating the API. See
  [duplicate response deadlines](duplicate-suggestion-deadlines.md).

Never put credentials in browser-public environment variables. The dashboard's
API key is server-only. The PostgreSQL password must be URL-safe because it is
interpolated into a connection URL. Changing `POSTGRES_PASSWORD` after volume
initialization does not rotate the database role: change it in PostgreSQL first,
then update `.env` and recreate services. To rotate the API key, update `.env`,
recreate API/MCP/web, and update every connected client.

## Transcript filesystem access

Base Compose mounts configured transcript sources read-only at their original absolute
paths. Select the transcript owner's non-root `MNEMONIC_API_UID` and `MNEMONIC_API_GID`
at runtime; rebuilding the image is unnecessary. Changing this identity for existing
data requires a coordinated offline ownership migration, including artifact, prompt,
transcript, and backup content. Initialization changes only mount roots. See
[the upgrade procedure](#upgrade-from-any-release) and
[source access and recovery](transcripts.md#filesystem-deployment).

## Semantic search

Semantic search is opt-in. Ordinary dashboard and MCP queries use PostgreSQL
lexical search. A nonblank semantic query runs `BAAI/bge-small-en-v1.5` inside
the API container and lazily refreshes derived `work_item_embeddings` rows in
batches of 16, so a first query after canonical content changes may take longer.
Each query embedding or document batch uses the shared model pool. The default
pool has two models and eight FIFO waiting positions, with a five-second wait
per call. Full queues or expired capacity waits return `semantic_unavailable`;
lexical search remains available. Database reads, ranking, and cache publication
do not reserve model capacity. Waiting is clipped to the request deadline;
cancelled or expired requests cannot start another native call.

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
wait, two shared model slots with eight waiting positions and a five-second
per-call wait, a 200-group lexical shortlist,
at most 30 recent distinct normalized tags composed per existing work item, at
most 128 missing vectors computed per request, a 10,000-visible-member ceiling
for full semantic scope, ten returned candidates, and an absolute 45-second
response budget that starts before request admission and body handling. Internal
work reserves response time and retains lexical results before model loading or
inference. The MCP adapter permits 50 seconds, leaving delivery time below the
client's default 60-second timer; no client changes are required.

`429 duplicate_suggestion_busy` includes `Retry-After: 1` and is safe to retry.
Model saturation, loading, inference, or unusable-vector failures return a normal
lexical response with semantic availability false and an incomplete comparison.
A failed derived-cache write preserves the computed ranking and reports
`semantic.cache_refresh.status=failed` separately; do not treat it as failed inference. `503 duplicate_suggestion_unavailable` means the
database/system suggestion path failed. Repeated 429 or 503 responses are an
aggregate capacity or availability incident, but neither implies a write,
unknown mutation outcome, lost create request, or permission to disable Create
anyway.

On PostgreSQL 17, suggestion snapshot and cache transactions set transaction,
statement, and lock timeouts from the remaining route budget. Cache refresh is
post-snapshot and derived: it skips locked work rows and caps cache lock waits at
50 ms. Cache-write contention or failure preserves the completed ranking and
reports a separate refresh disposition; it cannot turn refresh into an unbounded request.

Draft text, query vectors, candidate text, IDs, scores, and results do not belong
in logs or metric labels. Suggestion cache rows are derived existing-work data
and may be rebuilt; the draft and result are never persisted. Routine probes
must verify only aggregate behavior and must not commit a merge.

<a id="current-coordinated-cutover"></a>
<a id="upgrading-the-single-host-compose-stack"></a>

## Upgrade from any release

Upgrade the API, worker, MCP, dashboard, configuration, and compatible plugin together.
The application version and Alembic head are recorded in [AGENTS.md](../AGENTS.md#versioning).
Read the target release's upgrade notes and the [historical prerequisites](upgrade-history.md)
for every migration boundary being crossed. Older releases may need additional credentials,
services, migration audits, or exported data before this procedure applies. Test the upgrade
and restore against a disposable copy first. Many populated migrations refuse downgrade;
starting an old image against a newer database is not a rollback procedure.

Production stop, restart, recreation, and deployment each require explicit operator
approval. Prepare the release files, images, configuration review, and recovery plan first.
Expect the API, worker, dashboard, and MCP connections to be unavailable while writers are
stopped, the backup runs, and migrations finish. Reconnect agent clients after verification.
These instructions do not deploy or authorize interruption of an existing production stack.

### Preserve storage and deployment identity

New installs created by `scripts/setup.py` explicitly select
`MNEMONIC_STORAGE_MODE=volumes`; Docker initializes private named volumes without host sudo
commands. Existing installs MUST keep their original Compose project name, PostgreSQL and
RabbitMQ volumes, `.env`, service UID/GID, and all five storage paths. Use
`compose.bind-mounts.yaml` with `compose.yaml` on every command for an existing bind install.
The overlay sets bind mode and preserves these variables and historical defaults:

| Variable | Existing bind default |
| --- | --- |
| `MNEMONIC_ARTIFACT_DIR` | `./artifacts` |
| `MNEMONIC_PROMPT_DIR` | `./prompts/runtime` |
| `MNEMONIC_TRANSCRIPT_DIR` | `/var/lib/mnemonic/transcripts` |
| `MNEMONIC_TRANSCRIPT_INDEX_DIR` | `/var/lib/mnemonic/transcript-index` |
| `MNEMONIC_BACKUP_DIR` | `./backups` |

Keep relative paths anchored to the existing deployment directory, or make them explicit
absolute paths before changing directories. Do not start a freshly extracted bundle with an
old database and new empty storage volumes. All legacy bind directories must already exist;
the overlay uses `create_host_path: false`. Missing `MNEMONIC_STORAGE_MODE` intentionally fails
initialization in base Compose, directing existing operators to the bind overlay. Do not set
`volumes` merely to suppress that guard. Moving existing data to named volumes is a separate,
operator-planned storage migration with verified copies and recovery backups.

`init-storage` runs once as container root, creates volume roots, and sets their mode to
0700 and owner to `MNEMONIC_API_UID:MNEMONIC_API_GID`. It never recursively changes existing
content. API, worker, and migrate use that non-root identity at runtime; an image rebuild is
not needed when selecting a different UID. Existing content ownership changes still need
an explicit offline migration. Transcript source directories remain read-only host binds at
their original absolute paths, including when retained data uses named volumes.

The startup/migration guard samples at most 100 retained artifact and transcript pointers
per store and up to 100 projects with customized prompts. A readable matching file establishes
a store match; a single historically missing file does not block startup. No match refuses
startup with an explicit sample warning. This is a wrong-mount check, not a complete data
integrity audit. Preserve and back up prompt files: the editable priority rubric is in
PostgreSQL, while other library templates remain filesystem data.

### Prepare, back up, migrate, and start

For image installs, select an actually published release and set `MNEMONIC_VERSION` to its
exact version in the existing private `.env`. Review the matching Compose files and release
notes. Download/pull images before the maintenance window. For contributor builds, add
`-f compose.build.yaml` after the base/bind files and build the images first; the overlay uses
local image names and does not tag local builds as published releases.

The following sequence is for an existing bind installation after authorization. Preserve
any TLS or other approved deployment overlays by adding their `-f` arguments to every command.
For an existing named-volume installation, omit only `-f compose.bind-mounts.yaml` throughout.

```sh
docker compose -f compose.yaml -f compose.bind-mounts.yaml config --quiet
docker compose -f compose.yaml -f compose.bind-mounts.yaml pull
```

Take and verify project archives and separate filesystem backups, preserve the prior images
and configuration, and copy recovery material off-machine. Close external ingress and drain
all direct API/MCP writers. With the maintenance window authorized:

```sh
docker compose -f compose.yaml -f compose.bind-mounts.yaml stop web mcp api worker
docker compose -f compose.yaml -f compose.bind-mounts.yaml up -d --wait postgres rabbitmq
docker compose -f compose.yaml -f compose.bind-mounts.yaml run --rm --no-deps init-storage
docker compose -f compose.yaml -f compose.bind-mounts.yaml run --rm --no-deps migrate
```

`migrate` holds a PostgreSQL advisory lock, rejects unknown/newer schemas and incorrect stores,
and runs `alembic upgrade head` only after its backup succeeds. A fresh empty database needs no
pre-migration dump; a database already at the image's head is a no-op. API and worker startup
only check the schema and mounted stores; they do not run migrations. Their Compose dependencies
also require the one-shot migrate service to finish successfully.

For an existing schema that needs upgrading, `MNEMONIC_PRE_MIGRATION_BACKUP=true` is the default.
It writes a full custom-format `pg_dump` under `/backups/pre-migration/`, on the backup named
volume or configured legacy backup bind. Files are private (0600), published only after the
subprocess succeeds and the file/directory are flushed, and named `before-upgrade-*.dump`.
The space preflight requires the database size plus 64 MiB of free space; actual export size
can differ, so retain additional operating headroom. Insufficient space, a failed dump, or
an unsupported backup connection option aborts before migration. These dumps are separate
from scheduled project `.json.bz2` archives and are not pruned by project archive retention.
Monitor their growth and copy them off-machine. Original artifact bytes, editable prompts,
and retained native transcript objects still require separate filesystem/volume backups.

Only an operator who has arranged another verified recovery snapshot should explicitly set
`MNEMONIC_PRE_MIGRATION_BACKUP=false`. The log records that opt-out. This is not a substitute
for backups and does not waive schema or storage checks. Never print expanded Compose
configuration or database credentials while diagnosing failures.

Keep writers stopped if any step fails. After migration, run the included read-only audits
against the new head before reopening service:

```sh
docker compose -f compose.yaml -f compose.bind-mounts.yaml run --rm --no-deps api \
  python /app/scripts/audit_project_activity.py
docker compose -f compose.yaml -f compose.bind-mounts.yaml run --rm --no-deps api \
  python /app/scripts/audit_code_reviews.py
docker compose -f compose.yaml -f compose.bind-mounts.yaml up -d --wait
docker compose -f compose.yaml -f compose.bind-mounts.yaml ps
```

Confirm health, representative existing work and file access, and a current backup before
reopening ingress. On source installations, run the read-only
`uv run --project mcp python scripts/check-stack.py` as well. Restore a whole-database
pre-migration dump only with matching binaries and coordinated filesystem data, in an
explicitly approved recovery; project archive restore does not undo schema changes.

New configurations default dashboard `TIMEZONE` to `UTC`. An explicit existing `TIMEZONE`
is preserved. Operators who relied on the old implicit `America/Detroit` default should set
that value explicitly before upgrading if they want to retain the same display timezone.

## Historical upgrade notes

Detailed release-specific cutovers remain available in [upgrade-history.md](upgrade-history.md).
The anchors below preserve existing links while separating those commands from the current
procedure above.

<a id="historical-move-boundary-0022-to-0023"></a>

- [Historical move boundary: 0022 to 0023](upgrade-history.md#historical-move-boundary-0022-to-0023)

<a id="phase-9-core-030-cutover"></a>

- [Phase 9 Core 0.3.0 cutover](upgrade-history.md#phase-9-core-030-cutover)

<a id="final-phase-9-advisory-040-cutover"></a>

- [Final Phase 9 Advisory 0.4.0 cutover](upgrade-history.md#final-phase-9-advisory-040-cutover)

<a id="phase-10-repository-freshness-050-cutover"></a>

- [Phase 10 repository freshness 0.5.0 cutover](upgrade-history.md#phase-10-repository-freshness-050-cutover)

<a id="phase-11-structured-completion-evidence-cutover"></a>

- [Phase 11 structured completion evidence cutover](upgrade-history.md#phase-11-structured-completion-evidence-cutover)

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
receives or forwards a token. Active is reserved for agent leases. The Pending action can
clear only the exact active public lease the person reviewed, or an observed
Dropped row; a replacement lease produces `lease_state_changed`. Releases
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
one concrete decision. Agents updating this work or related work must rewrite
affected open questions in place using the current question version; they cannot
resolve, cancel, or withdraw them. Earlier prose is available in version tabs.
If later evidence makes a question moot, rewrite it to explain why and ask the
human to close it.

Resolution is a dashboard/direct-REST human action. The browser supplies the
current `expected_question_version` and `reviewed_context_revision`. The human
reads the current question and answers directly. A concurrent change preserves
the draft and refreshes the question before a new operation intent. Resolver
fields remain asserted provenance under the shared bearer. See
[Needs Attention](attention.md) for the revision and migration contract.

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

At current head 0026, run `scripts/audit_project_activity.py` using the private
`DATABASE_URL` environment variable. It composes the historical domain checks
with activity/report, external-reference, move and review checks plus the exact
supported guard catalog. That guard catalog digests every row a catalog name
owns, so the comparison does not depend on the plan PostgreSQL chooses for its
catalog scans, and a `catalog_foreign_key_triggers_drift` finding is a real
schema change rather than a scan-order artifact. Deploy
`scripts/audit_project_activity.py` and `tests/fixtures/project-activity-catalog-v1.json`
from the same revision: the frozen digests and the code that computes them are
one unit, and a mismatched pair reports drift against an unchanged schema.
`scripts/audit_code_reviews.py` additionally provides
focused review operational counts. Alert on any blocking finding or runtime
failure, and inventory deployed `0.51.0` clients and plugin `0.30.0` together.
The historical audit below applies only to its explicitly named older heads.

All three audits pin the PostgreSQL session settings that decide how the server
renders a catalog definition — `bytea_output`, `DateStyle`, `TimeZone` and
`quote_all_identifiers` — plus `client_encoding`, which decides whether the
rendered definition can be transmitted at all. The pin covers their own
transaction only, and the values are read back rather than trusted to a `SET
LOCAL` that may not have applied. A guard-catalog finding is therefore a
statement about the schema alone. It never follows from a `postgresql.conf`
value, a database default, or an `ALTER ROLE ... SET` on the connecting role,
and the audit leaves the session it was handed unchanged. Treat any catalog
finding as a real schema change and investigate it as one; never try to clear
one by adjusting session settings.

One session setting still stops the audits before they can pin anything:
`client_encoding = 'SQL_ASCII'` on the connecting role or database fails during
client startup, and the audit reports `{"audit_runtime_failure": true, "result":
"blocked"}`. That is a runtime failure to investigate as one, not a schema
finding — clear the role setting and rerun.

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

Project PostgreSQL backups run at startup and every
`MNEMONIC_BACKUP_INTERVAL_SECONDS` (default 86400). Each archive is a bounded,
checksummed JSON-lines data export streamed through bzip2 before any archive
bytes reach disk. Completed files appear atomically under
`/backups/<project UUID>/project-<UTC timestamp>-<random ID>.json.bz2` inside the worker.
New installs keep `/backups` on the private `mnemonic_backups` named volume;
the bind overlay maps the existing `MNEMONIC_BACKUP_DIR` instead.
Only successful publication prunes older archives for that project:
`MNEMONIC_BACKUP_RETENTION_COUNT` defaults to **7** (range 1–10000).
A failed backup retains previous successful archives.

Archives include project database records, immutable histories, leases,
relationships, settings, reports, artifact metadata, normalized extracted text,
retained Tika document properties and completed operation
receipts. **Artifact file contents are excluded.** A separate backup system owns
those files. Raw transcript copies are also separate filesystem backup data.
Editable prompt files require their own backup too. Protect the complete artifact,
prompt, and transcript named volumes or legacy bind trees with a filesystem backup
system. Transcript objects may share bases: copying only recently changed referenced
files is insufficient. The derived transcript index can be rebuilt. Keep volume names
and Compose project identity with the recovery inventory; `down -v` destroys these data.

Full pre-migration `pg_dump` archives live in `/backups/pre-migration/` and have their own
operator-managed retention. See [Upgrade from any release](#upgrade-from-any-release)
for the default-on backup, disk-space guard, and explicit opt-out. Neither kind of local
backup substitutes for a verified off-machine copy and restore rehearsal.

The service holds bounded database locks for a coherent export or transactional
restore; active writers can briefly wait or receive their ordinary busy error.
Pending durable operations must finish recovery first. Keep copies off-machine:
retention on the database host does not protect against loss of that host.

In the dashboard, select a project and open **Settings → Project backups** to
inspect archive existence, creation time, age and size, create a backup, download
an archive, or upload and restore one. Operations use the private backup service
and a separate server-only credential. They are absent from the application REST
contract, MCP tools, agent context and plugin skills.

For operator access:

```sh
docker compose exec worker python -m mnemonic_backup once
docker compose exec worker python -m mnemonic_backup once --project PROJECT_UUID
docker compose logs --tail=20 worker
```

Scheduled and dashboard jobs retry transient failures after 60 seconds. The explicit
operator commands above run synchronously. Container health reports broker and
dispatcher availability; inspect backup job results for export failures. Archives and receipts contain
private project content; use private storage and keep downloads private.

<a id="upgrading-the-backup-service"></a>

The historical dedicated-backup-to-worker transition is retained in
[Upgrading the backup service](upgrade-history.md#upgrading-the-backup-service).

## Restore

Restoring replaces only the selected project's PostgreSQL records, preserving
other projects and the installed schema/ACLs. The archive must match this
release's schema and project UUID. Data is validated as data, never executed as
SQL. A corrupt/truncated archive, schema mismatch, unresolved operation,
cross-project dependency conflict, or failed integrity check rolls back the
entire database restore. A project can be restored into an initialized database
under its original UUID; dependent projects must already exist when referenced.
A moved work item cannot be silently taken back from another project.

The restored project's activity stream gets a new UUID to reject stale cursors.
Global sequences are advanced when necessary and never rewound. Artifact metadata
is restored, but the artifact filesystem is never read, written or deleted.
Coordinate matching artifact content through its separate backup system when
needed; older metadata may refer to absent or newer file bytes.

Before replacing data, download a current backup. In Settings, choose the
compressed archive and explicitly confirm the selected project. The dashboard
reloads after success. If a connection drops, inspect the project before retrying:
the server may already have committed the restore.

The explicit operator equivalent is:

```sh
docker compose exec worker python -m mnemonic_backup restore \
  --project PROJECT_UUID --confirm-project PROJECT_UUID \
  --file /backups/PROJECT_UUID/ARCHIVE.json.bz2
```

This data-only project restore does not migrate or downgrade schemas. Historical
recovery instructions in [the historical archive](upgrade-history.md) refer to the
whole-database archives and matching binaries of those historical releases.
Periodically rehearse current project restores in throwaway containers. The
isolated `scripts/test-e2e.sh` runner exercises HTTP and browser backup/restore,
retention, project isolation, corrupt files, limits, concurrency and storage
failures without using production database or backup paths.

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
Recovery requires an operator-approved restore at the documented project
database backup boundary. Checkpoint, event, evidence, and receipt rows and private
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

<a id="external-records-release-0021-to-0022"></a>

The [external records 0021-to-0022 cutover](upgrade-history.md#external-records-release-0021-to-0022)
retains its original preflight, audit, and recovery requirements.
