# Historical Mnemonic upgrades

These notes preserve release-specific prerequisites, audits, restore rehearsals, and
old deployment commands. They describe the named historical versions; they are not
the current startup procedure. Begin with [Upgrade from any release](operations.md#upgrade-from-any-release),
then apply the prerequisites for each boundary your database crosses. No command here
authorizes a production interruption or a downgrade.

## Force claims with unresolved human gates (0.123.0)

Upgrade API, worker, MCP and dashboard together with plugin 0.73.0 and migration
`0072_force_claim_gates`. The migration updates the database acquisition guard to
accept an explicit force-claim journal entry matching the request and new lease
generation. It adds no tables or columns and rewrites no existing questions,
leases, or receipts. No configuration change is required.

`force=true` on either claim route can recover a lease while human questions
remain unresolved. Ordinary claims still reject gated work. Force preserves
Waiting and Needs Attention, and does not permit gated completion or other
terminal actions. All other eligibility and exact-retry rules still apply.

Downgrade restores the previous guard without deleting retained history or
invalidating existing leases. Further gated force acquisitions then require an
upgrade; existing leases can still be renewed or released.

## Automatic review cycles (0.121.0)

Upgrade API, worker, MCP and dashboard together with migration
`0071_automatic_review_cycles`. Back up PostgreSQL and the native artifact/transcript
stores using the normal coordinated upgrade procedure. The migration permits
system-attributed successors and enforces one successor per parent, preserving
historical dashboard attributions, completed scope and permanent receipts.
Downgrade refuses to discard automatic successor provenance.

Decision saves no longer stop for owner admission. Existing open successors are
reused through any number of generations; later obligations create an open
successor when necessary. The changed decision domain still controls whether a
review is queued. The worker automatically processes previously held owner answers
in bounded transactions; original classifications remain immutable. Its next
maintenance sweeps may therefore queue reviews for retained answers in enabled
domains. The old admission mutation accepts only exact historical receipt replays.

The shipped review prompt is refreshed only when its stored hash matches a known
shipped default. Customized prompt text remains unchanged; owners can remove any
old admission instructions from their customized review prompt.

## Release notes through 0.115.0

Release 0.115.0 rebuilds transcript management with a server folder browser, editable
project source rows, and a project-wide enable switch. Disabled projects stop new
imports, capture, normalization, and indexing while retaining readable history.
Migration `0069_transcript_directories` seeds managed folders from prior imports and historical access failures.
The capture limit moves to `MNEMONIC_TRANSCRIPT_MAX_BYTES`, default 1073741824
(1 GiB); existing explicit environment values remain operator-controlled. Normalizer
4 recognizes newer Claude and Codex record types and refreshes retained snapshots.
Upgrade API, worker, MCP, and dashboard together after backing up PostgreSQL and
stopping old application writers. Plugin 0.71.0 and tool/receipt counts are unchanged.

Release 0.114.0 places work-item tags above the state and sort controls, with a
subtle divider between the rows. Plugin 0.71.0, schema 0068, and tool contracts
are unchanged.

Release 0.113.0 aligns code-review cards with work-item cards and routes the
code-review copy confirmation through the dashboard toast. API, worker, MCP,
and dashboard share the release version; plugin 0.71.0 and Alembic
`0068_artifact_tags` are unchanged. No schema, configuration, or tool-contract
changes are required.

Release 0.112.0 and plugin 0.71.0 extend project tags to artifacts and seed six
editable starter tags in new projects from Fish Food's October 27, 2026 catalog.
Deploy API, worker, MCP, and dashboard together at Alembic `0068_artifact_tags`.
Existing artifact assignments start null. Existing projects retain their catalog,
except for the requested system `dir` description. Metadata edits and catalog
renames/deletions retain artifact revisions and existing mutation receipts.
Back up the database before migration; artifact files are unchanged. The tool,
receipt, browser-mutation, and work-event counts remain unchanged from 0.110.0.
See [project tags](work-tags.md) for assignment and starter-set behavior.

Release 0.111.0 and plugin 0.70.0 rename the MCP `get_project_settings` tool to
`get_project`. Its existing policy values move under `settings`; the response
also includes work counts by display state and tag, all Active work metadata,
the open Needs Attention count, and knowledge-domain document counts. Update
agent integrations to the new tool name and nested settings shape. API and MCP
must be upgraded together for the new `GET /projects/{project_id}/overview`
read. No migration or configuration change is required; Alembic remains
`0067_project_tags`, and the catalog remains 79 tools. The dashboard's existing
project and settings endpoints retain their contracts. See
[project overview](api-contract.md#project-overview) for the response fields.

Release 0.110.0 and plugin 0.69.0 replace decorative tags with project tag
catalogs and current work assignments. Deploy API, worker, MCP, and dashboard
together at Alembic `0067_project_tags`. The migration intentionally deletes all
legacy checkpoint tags and initializes existing work assignments to null. It
seeds immutable `code-review`, `remediation`, and `dir` definitions for each
project while preserving review lifecycle and decision lineage. Back up before
migration; lost legacy labels cannot be recovered by a schema downgrade.

The dashboard manages definitions at `/settings/tags`, displays code reviews in
Work items, and supports All/OR/AND tag filtering. Agents query `list_tags` and
must declare selected catalog names or explicit null on fresh `create_work`.
There are 79 MCP tools, 25 protected MCP writes, 38 REST receipt kinds,
32 protected browser mutations, and 24 work-event types. No MCP tool mutates tag
definitions. See [work tags](work-tags.md) for the interface and migration details.
Production interruption still requires explicit operator authorization.

Release 0.109.2 updates the decision-impact-review default for a measured
60-minute workflow pilot: grouped investigation, concise evidenced conclusions,
conservative reuse, and checkpoint/release with unfinished obligations retained.
Reviewers report time, findings, scope growth and remaining work. Existing
completion guards, owner gates, decision policy and retained review snapshots are
unchanged. Exact untouched prior defaults refresh on read after installing the
updated API package; customized templates remain intact. A project prompt can be
updated immediately through Settings > Prompts or the documented locked external
edit without restarting services. No migration or API/MCP contract change is
required; plugin 0.68.0 and Alembic `0066_candidate_reuse` remain unchanged.

Release 0.109.1 restores MCP assessment list and detail reads when no candidate
conclusions were reused. The API omits a zero `reused_candidate_count`, matching
the strict adapter contract; positive counts and pinned source dispositions remain
present. Deploy the API image to apply this fix. No migration, configuration change,
or client update is required; plugin 0.68.0 and Alembic `0066_candidate_reuse` remain
unchanged.

Release 0.109.0 adds collapsible inline help beside the Manage domains and
Decision documents page descriptions. Each container remembers its expanded or
collapsed state in browser localStorage; new preferences default to collapsed.
Deploy only the web image against the existing API, MCP, and worker services.
There is no migration or configuration change; source package versions advance
together, while plugin 0.68.0 and Alembic `0066_candidate_reuse` remain unchanged.

Release 0.108.0 replaces the Knowledge graph menu icon with three nodes of
varied sizes connected by two edges of an obtuse triangle. Deploy only the web
image against the existing API, MCP, and worker services. There is no migration
or configuration change; source package versions advance together, while plugin
0.68.0 and Alembic `0066_candidate_reuse` remain unchanged.

Release 0.107.0 updates dashboard navigation, decision and domain directories,
artifact ordering, and summary disclosures. It adds no migration, API contract,
MCP tool, or configuration change. The web image can be updated independently
against the existing 0.106.1 API, MCP, and worker services; the source package
versions advance together. Plugin 0.68.0 and Alembic `0066_candidate_reuse`
remain unchanged.

Release 0.106.1 adds the independently reproduced PostgreSQL 17 dump/restore
forms of the two CHECK constraints recreated by migration 0065 to the frozen
0065 and 0066 audit catalogs. The allowed operation kinds and domain review
settings are unchanged; obsolete guards and changed values remain rejected.
This repairs audit failures on healthy databases restored after 0065, including
their subsequent upgrade to 0066. It adds no migration or configuration change;
plugin 0.68.0 and Alembic `0066_candidate_reuse` remain unchanged.

Release 0.106.0 and plugin 0.68.0 add bounded reuse of reviewed candidate
conclusions for explicitly unchanged supersessions, plus paginated work lineage
and grouped-navigation guidance. Migration `0066_candidate_reuse` stores immutable
references to original dispositions without rewriting historical episodes. Deploy
API, worker, MCP and dashboard together; downgrade is refused while reuse links
remain. No new services or environment variables are required. Unmodified shipped
review prompts refresh automatically; customized prompts remain unchanged.

Release 0.105.1 aligns completion-history audits and project backup validation
with the legacy eventless completion shape explicitly preserved by migration
0019. Historical records remain unchanged; inconsistent modern completions
remain invalid. The 0065 audit catalog also retains previously accepted definitions
for schema objects that migration 0065 does not change. This patch adds no migration
or configuration change. Plugin
0.67.0 and Alembic `0065_knowledge_management` are unchanged.

Release 0.105.0 and plugin 0.67.0 split Knowledge graph into Decision documents
and Manage domains. Migration `0065_knowledge_management` adds domain-local review
priorities, deletion tombstones and an immutable archive of removed graph edges.
Existing domain review settings retain their effective behavior; newly created
domains default to off with priority 40. Project-wide controls are retired.
Human document revisions use optimistic concurrency and never overwrite a stale
revision. Domain exports include current Markdown documents and JSON sidecars.
Deletion is available only through the dashboard, with a fresh relationship preview
and conditional acknowledgement. Built-in PDR records remain managed by Needs Attention.
Back up before upgrading API, worker, MCP and dashboard together. Deploying these
changes requires explicit production-operation authorization. Downgrade refuses
retained tombstones, deletion receipts or nondefault local review settings.

Release 0.104.0 repairs event-history pages containing review leases, code reviews
and post-completion follow-ups. The list query now returns the same stored
reference fields as recall. Validation remains strict, with no migration,
backfill, configuration change or plugin update. Alembic remains at
`0064_work_change_inheritance`.

Release 0.103.0 and plugin 0.66.0 let newly created work exclude its declared
discovery edge from future decision-change inheritance with an explicit reason.
Migration `0064_work_change_inheritance` adds an immutable declaration table and
creation-receipt guards. Existing discovery edges and memberships remain intact;
alternate discovery paths and explicit adoption still apply. No backfill or new
configuration is required. Downgrade refuses retained exclusions.

Release 0.102.0 and plugin 0.65.0 retain optional shared finding IDs and explicit
remediation targets in assessment dispositions. Migration `0063_impact_findings`
adds nullable columns and constraints without rewriting historical receipts.
Downgrade refuses to discard authored metadata. Review summaries distinguish
named findings from unlabelled rows; every candidate obligation remains intact.
No new configuration is required.

Release 0.101.0 and plugin 0.64.0 canonicalize explicit GitHub repository identities
on fresh scope appends and completion reports. Lookup accepts supported aliases
while returning the stored spelling; reconciliation still requires that exact
stored spelling. Historical rows, snapshots and retry intents are unchanged.
No new configuration, backfill or migration is required beyond
`0062_review_scope_notices`.

Release 0.100.0 and plugin 0.63.0 add supersession comparisons between successive
revisions of the replacing document, plus explicit warnings for missing captured
extent/reason. These are read projections; retained assessment evidence and old
cross-document diffs are unchanged. `include_diff=false` omits both ordinary and
supersession prose. No new configuration or migration is required beyond
`0062_review_scope_notices`.

Release 0.99.0 and plugin 0.62.0 add a read-only grouped view of required decision
impact candidates. It distinguishes stable documents, captured revisions and
per-episode obligations, with bounded pagination and a coherent scope snapshot.
No new configuration or migration is required beyond `0062_review_scope_notices`.

Release 0.98.0 and plugin 0.61.0 add opt-in citation excerpts to impact candidate
reads. Excerpts use exact pinned text and compose with compact projections;
stored snapshots and default response shapes are unchanged. No new configuration
or migration is required beyond `0062_review_scope_notices`.

Release 0.97.0 and plugin 0.60.0 add persistent review-scope growth notices and
readable scope-extension checkpoints. Migration `0062_review_scope_notices`
records the exact order of newly attached episodes; historical unknown boundaries
remain explicit. Downgrade refuses to discard recorded attachment versions.
Fresh token-bearing disposition writes also return notices; receipt replays retain
their original response. No new configuration is required. Deploy aligned services
only after separately authorized backup and migration.

Use [unified search](search.md) to retrieve work, artifacts, and transcripts in one
ranked, filtered, paginated read through REST or MCP.

Release 0.96.0 and plugin 0.59.0 add atomic assessment disposition batches and
optional renewal of the associated review's active implementation lease during
fresh disposition writes. Deploy aligned API, worker, MCP and dashboard after
an authorized backup and migration to `0061_batch_dispositions`. The migration
extends the receipt-kind constraint; it does not rewrite existing dispositions
or receipts. Downgrade is refused once batch receipts exist. No new service or
configuration is required. Token-free writes and exact replays do not renew
leases. Production deployment remains a separately authorized operation.

Release 0.95.0 and plugin 0.58.0 add opt-in compact decision impact reads. No new
migration or configuration is required beyond `0060_supersession_reuse`. Existing
read defaults and stored evidence remain unchanged. Untouched default review
prompts upgrade to describe the compact options; authored custom prompts remain
unchanged. Production deployment remains a separately authorized operation.

Release 0.94.0 adds explicit, auditable unchanged-supersession reuse with migration
`0060_supersession_reuse` and plugin 0.57.0. Deploy aligned API, worker, MCP and
dashboard only after an authorized backup and migration. Once reuse declarations
exist, downgrade is refused to preserve their provenance. Existing declarations
and assessment episodes remain intact.
The preceding release 0.93.0 adds per-domain automatic decision review overrides (`inherit`,
`on`, `off`) in Workspace settings, REST and MCP. Deploy aligned API, worker, MCP
and dashboard with plugin 0.56.0 and migration `0059_domain_decision_reviews`
after an authorized backup and migration. Existing domains migrate to `inherit`,
preserving behavior. Priority remains project-wide. No new service, environment
variable or review backlog is introduced. Backups retain the domain override;
downgrade requires resetting every override to `inherit` to avoid losing policy.

Release 0.92.0 adds explicit decision-change grouping and a human admission boundary
for recursive review-derived changes. Deploy API, worker, MCP and dashboard together,
with plugin 0.55.0 and migration `0058_decision_changes`, after the usual authorized
backup and migration. Existing review markers receive exact one-to-one groups;
historical closeouts and dispositions stay intact. Ordinary combined investigations
can explicitly adopt named assessments. Review scope changes require acknowledgement
before Done. Managed owner answers remain readable while awaiting review admission.
Customized prompts and historical work prompts are preserved; untouched shipped
investigation prompts refresh to the current bounded-review instructions.

Release 0.91.1 removes quadratic joins from duplicate-suggestion candidate selection
for large projects, with no migration or configuration changes.

Release 0.91.0 includes incoming related citations in required decision impact
reviews without claiming a causal dependency. New snapshots capture link direction;
historical snapshots retain their existing completion requirements. Outward links
and prerequisites remain optional context.
Deploy API, worker, MCP, and dashboard together with plugin 0.54.0; the Alembic
head remains `0057_automatic_decision_reviews`, with no new migration or service.
Untouched 0.89/0.90 shipped `decision-impact-review` prompt content refreshes on its
next read under the prompt lock. Customized prompts and prior investigation
snapshots remain unchanged. See [prompt updates](prompts.md#automatic-decision-impact-reviews-0890)
and [knowledge graph](knowledge-graph.md) for pagination and agent behavior.

Release 0.89.0 adds opt-in [automatic decision impact reviews](knowledge-graph.md#automatic-decision-impact-reviews).
Migration `0057_automatic_decision_reviews` defaults existing and new projects to
disabled with priority 40; it adds an immutable work marker without backfilling
investigations. Deploy API, worker, MCP, and dashboard together after authorized
backup and migration. Plugin 0.52.0 remains compatible.

Release 0.88.0 adds confirmed **Clear all** dismissal to the dashboard's
[Summaries inbox](project-activity-and-reports.md#settings-and-summaries).
The coordinated application/API/MCP/dashboard version is 0.88.0; plugin 0.52.0
and migration `0056_product_decisions` are unchanged. This release uses the
existing report dismissal API and requires no new migration or configuration.

For release 0.87.0 (plugin 0.52.0), see the [PDR deployment notes](knowledge-graph.md#dashboard-and-deployment).
Migration `0056_product_decisions` creates a default PDR domain for every project
and imports existing Needs Attention questions and answers as immutable decision
revisions. It reuses existing PDR domains and preserves their document numbers.
Deploy aligned API, worker, MCP, and dashboard versions after the normal authorized
backup and migration procedure; old writers cannot create the required PDR records.
The aggregate integrity audit and project backup format include the new bindings.
No additional service, environment variable, or external integration is required.

For the coordinated 0.13.0 code-review release (plugin 0.14.0, migration 0024),
see [code-review deployment and recovery](code-reviews.md#recovery-and-deployment).
Back up and quiesce old writers; do not deploy older processes against the new
schema or force a downgrade after review facts/settings changes. Defaults stay
Never/Never/off. The integrity audit is read-only and never repairs ancestry.

## Current coordinated cutover

The current coordinated boundary is API/MCP/dashboard `0.60.0`, plugin `0.38.0`,
and Alembic `0041_artifact_passages`. Follow [the transcript/job migration](transcript-jobs.md)
to provision RabbitMQ, the shared worker, retained copies, and backup ownership.
Migration `0041_artifact_passages` adds rebuildable artifact passage caches; follow
[semantic artifact upgrade and backfill](artifact-semantic-search.md#coordinated-upgrade).
Explicit [multi-project search](multi-project-search.md) shares one bounded corpus
and reports coverage for every selected project.
Inventory exactly 79 MCP tools,
25 protected MCP writes, 38 REST receipt kinds, 32 protected browser mutations,
and 24 work-event types. Keep older writers stopped: fresh closeouts still
require a report and operation UUID, fresh work starts Pending, settings use
revision checks, and relationship endpoint identity, adjacency, graph guards,
event attribution, and move eligibility now span projects. Permanent historical
receipts remain recoverable with their exact old request; do not manufacture missing reports or evidence for historical work.

Release `0.53.0` adds MCP-authorized local uploads without a schema migration.
Refresh the installed skills and MCP catalog; reverse proxies and stdio adapters
must follow [the upload endpoint configuration](artifact-upload-client.md).

Migration `0035_prompt_library` exports editable project prompt templates
to the private prompt bind directory. Immutable report history stays in PostgreSQL. Create
`MNEMONIC_PROMPT_DIR` with the API owner before upgrading and back it up together
with PostgreSQL. See [prompt storage and migration](prompts.md).

Migration `0034_variable_work_leases` adds per-project lease settings with Default
15, Minimum 10, and Maximum 120 minutes. Configure them in Workspace → Project
details. Existing active leases keep their expiry; new claims and renewals use
the project policy. Token-bearing progress renewals retain the last granted
duration within current bounds. Remove the retired `MNEMONIC_LEASE_TTL_SECONDS` environment
setting. Upgrade API, MCP, dashboard and skills together; agents immediately read
work status and policy, request Default during startup, and estimate remaining
time on later requests. The migration retains claim duration assertions for exact
lost-response recovery. Project backups retain the settings and lease metadata.

Transcript migration `0032_agent_transcripts` follows `0031_review_decisions`. Stop
older API/MCP/dashboard writers before upgrading together. Configure the read-only
shared source mount and allowed roots in [transcript deployment](transcripts.md#filesystem-deployment);
the existing Tika service is reused. Workspace rebuilds now read retained copies;
source access is needed for new captures. Backups include retained transcript text,
provenance, settings, rebuild receipts and import receipts. Migration
`0033_transcript_imports` adds project-owned imports without invented work leases;
it preserves existing enrollment records and rejects downgrades with import data. Populated transcript state cannot be
downgraded without data loss; the migration rejects that downgrade.

Before the artifact upgrade, create the private host bind directory and configure
the shared API/dashboard upload limit as described in [artifact deployment](artifacts.md#deployment).
Artifact receipts add four journal-backed kinds to the existing 18 REST kinds.
Artifact content is outside PostgreSQL and outside the database backup job;
replacement and deletion preserve metadata only. Migration 0027 adds current
normalized extracted text and durable Tika document properties; existing current
files are queued automatically. Upgrade the private Tika service with the API,
MCP and dashboard. Database backups now contain extracted text and must be treated
as sensitive document copies. See [extraction configuration and retention](artifacts.md).

`MNEMONIC_ARTIFACT_MAX_BYTES` in `.env` defaults to `67108864` (64 MiB); set it
to `0` to disable all artifact operations without deleting retained files or
history. Recreate API/web after changes. Authenticated `/api/v1/artifacts/status`
and all artifact MCP tools explicitly report availability/limits. Positive values
up to 1 GiB are supported by the binary API/dashboard; MCP retains a separate
64 MiB decoded transfer ceiling. Disabled mode pauses maintenance and exact
receipt replay too: preserve any uncertain operation UUID and bytes for reenabling.

For 0025, take a verified backup with every writer stopped, then migrate and
deploy all coordinated surfaces. The migration takes bounded exclusive locks
while it changes relationship endpoint keys, global natural identity, and the
move/event/duplicate guards; do not run 0.15.0 processes against that schema.
Run both `scripts/audit_project_activity.py` and
`scripts/audit_code_reviews.py` against the exact new head, rehearse restore,
and verify cross-project add/list/remove, blocker readiness, endpoint-local
activity, and a move that retains its edges before reopening traffic.

Downgrade from 0025 to 0024 is allowed only when every retained relationship
still has both current endpoints in its immutable authority project and no
immutable relationship/dependency event history for an edge spans projects.
Both checks run under exclusive locks and fail before DDL. Removing a
cross-project edge does not erase its events or restore eligibility. Otherwise,
fix forward or restore the full pre-0025 backup with matching binaries.
Further downgrade past 0024 remains subject to the code-review history guards.

### Historical move boundary: 0022 to 0023

The following intermediate-boundary commands remain useful for preflight and
restore rehearsal at those explicit heads; they are not a 0025 certification.

Migration 0023 enables identity-preserving cross-project moves. Quiesce every
writer before upgrading because historical work-owned foreign keys and event
guards change together. After upgrade, verify that each move has one immutable
move row, exactly one source and one target event, and one corresponding activity
entry in each project. A move preserves the UUID, lifecycle/completion state, and
expired retained lease; it never rewrites the project recorded on older facts.

Migration 0024 adds review policy and history that cannot move between projects.
For its final cutover use the [code-review deployment rules](code-reviews.md#recovery-and-deployment)
and both current-head audits. Older-head activity-audit commands below remain
valid at their explicit preflight/restore heads, not as a 0025 certification.

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

For the initial transition from the dedicated backup container to release
0.52.0, follow [the transcript-copy migration runbook](transcript-jobs.md) first;
the commands below use the new shared worker service.

1. Confirm the current stack is healthy, then create fresh compressed project
   database archives while it is still serving:

   ```sh
   docker compose ps
   docker compose exec worker python -m mnemonic_backup once
   docker compose logs --tail=20 worker
   ```

   Download and copy the completed project archives to another device or
   backed-up location. Project archives restore only into a matching schema;
   before a schema-changing upgrade, also arrange a separate PostgreSQL recovery
   snapshot and matching binaries. Artifact files need their separate backup.
2. Quiesce every writer. Stop the bundled entry points and stop any direct REST
   clients before proceeding:

   ```sh
   docker compose stop web mcp api worker
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

### Upgrading the backup service

Release 0.52.0 replaces the dedicated backup container with the shared worker.
Follow [the coordinated migration](transcript-jobs.md#deployment-and-existing-transcript-migration)
for RabbitMQ credentials, storage ownership, and transcript backfill.

Add a fresh independently generated `MNEMONIC_BACKUP_TOKEN` of at least 32
characters to the private `.env`, plus
`MNEMONIC_BACKUP_RETENTION_COUNT=7`. New installations get the token from
`python scripts/setup.py`; that command leaves an existing `.env` unchanged.
Never reuse or distribute the application API key as the backup token.

Stop the legacy backup container before removing legacy archives. Inspect the
exact configured directory and delete only the intended old archives. The new
service never executes or imports old `.dump` files and does not automatically
delete arbitrary files in its root. Prepare the configured bind directory with
the configured API/worker UID/GID and mode 0700; for the default identity and path:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./backups
docker compose up -d --build --wait worker web
```

The compressed upload/archive limit defaults to 67108864 bytes (64 MiB);
`MNEMONIC_BACKUP_EXPANDED_MAX_BYTES` defaults to 268435456 bytes (256 MiB).
Both may be configured up to 1 GiB. Expanded records are held in memory during
validation, so allow additional process memory when raising limits.
For host nginx, install the updated `/api/backups/` location and match its
`client_max_body_size` to `MNEMONIC_BACKUP_MAX_BYTES`.

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
