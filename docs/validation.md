# Mnemonic validation record

## Phases 7–8 final integrated validation — 2026-09-01

This is the final combined prerelease release record. Counts below were
observed against the integrated implementation with every PostgreSQL suite
enabled. The capacity observations below are evaluated against explicit local
prerelease release budgets; those budgets are neither production network SLOs
nor runtime limits.

- **The full backend suite passed 396 tests against PostgreSQL 17 with three
  warnings, and backend Ruff passed.** The suite includes the `0013 -> 0014`
  migration, gate persistence/service/receipt behavior, readiness and lifecycle
  enforcement, attention/history/context reads, event coherence, and hierarchy
  presentation. The focused Phase 6/7–8 migration batch passed 11 tests, and
  the separate migration/ORM parity check passed one test.
- **The full MCP suite passed 206 tests, and repository Ruff passed for MCP.**
  Its current contract is exactly 25 tools and exactly ten protected writes;
  `request_human_input`, `list_human_attention`, and `list_work_gates` are
  present, while no MCP resolution tool exists.
- **The frontend passed 107 unit tests, TypeScript checking, and the production
  build.** This covers the ten browser mutation intents, gate review/resolution
  helpers and proxy policy, attention/detail behavior, and hierarchy response
  handling at the unit/type/build layers.
- **Plugin manifest and disposable installation checks passed.** Both a fresh
  `0.5.0` install and sequential `0.4.0 -> 0.5.0` update resolved the shipped
  skill/reference bytes without a compatibility copy.
- **The complete isolated Playwright stack passed 40 executions across desktop
  and narrow Chromium.** This includes committed-response-loss/exact replay,
  a B-to-C work/checkpoint/relationship drift rejection while the outer queue
  projection remains stale at B, branch-local all-descendant filtering, and
  collapsed passive-expiry refresh. The disposable E2E API enabled human-gate
  requests explicitly; production Compose remains fenced by default.
- **The isolated production-shaped five-service stack passed its read-only and
  authorized writable checks.** The checker exercised one lost gate-request
  response, waiting/readiness/claim exclusion, exact dashboard resolution
  replay and one activity advance, 25 later ordinary events without paired
  decision eviction, exact planned/discovered hierarchy aggregates, project
  isolation, and cleanup. Post-cleanup PostgreSQL state was revision 0014,
  five of five synthetic work items hidden, one of one gate resolved, exactly
  two gate events and two completed gate receipts, no pending receipts, and no
  visible unresolved gate. A 143-entry custom archive contained gates, their
  attention sequence, events, and receipts. Across 54 dynamic gate/operation
  identifiers and 41,339 log characters, the API, MCP, web, backup, and
  PostgreSQL logs contained no identifier, gate text, answer, bearer, operation
  field, claim-request field, or lease-token field from the audit set.

### Cold-review release-gate evidence — 2026-09-01

The following checks used synthetic data in the isolated PostgreSQL 17 test
service. Every temporary database and schema was dropped after the measurement;
no application or production data was read. These are observed pre-release
capacity points, not production SLOs or enforced input/graph-size limits.

#### Locked downgrade and writer race

- The focused command
  `TEST_DATABASE_URL=<isolated PostgreSQL URL> uv run pytest -q
  tests/test_phase78_migration_postgres.py` passed **11 tests in 1.78 seconds**
  with one upstream Starlette warning. Backend Ruff also passed. Two new
  deterministic tests exercise the migration's actual PostgreSQL locks rather
  than timing assumptions.
- A new canonical Phase 6 replay regression builds a typed append-event
  response and its real salted request fingerprint at revision
  `0013_idempotent_mutations`, completes the receipt through the same
  pending-to-completed database contract, and establishes the byte baseline
  through the actual REST event route. The response remained byte-identical
  after `0013 -> 0014` and again after downgrade/re-upgrade; both passes left
  exactly two work events, one receipt, no gates, and the work activity
  timestamp unchanged.

- In the writer-first order, a keyed request transaction reserved and completed
  its receipt, locked the focal work, and inserted the gate and request event.
  Downgrade was observed waiting for `ACCESS EXCLUSIVE` on
  `client_operations`; after the writer committed, downgrade refused, left the
  database at `0014_human_gates`, and retained both the gate and completed
  receipt.
- In the downgrade-first order, the migration was paused after its empty-data
  check while holding all four required `ACCESS EXCLUSIVE` locks. An unkeyed
  gate writer was observed waiting for `ROW SHARE` on `work_items`; downgrade
  completed to `0013_idempotent_mutations`, the writer then failed with SQLSTATE
  `42P01` instead of committing into a dropped schema, and re-upgrade restored
  `0014_human_gates`. The pre-existing work remained and the gate table was
  empty. Neither order deadlocked or lost a committed gate/receipt.

#### Custom backup, isolated restore, and exact replay

- A disposable source database was migrated from empty state to
  `0014_human_gates`. Through the real REST service it created one work item and
  **100 keyed gates**, resolved 99, and retained one unresolved gate. The
  fixture therefore contained 199 gate events and 199 completed gate-operation
  receipts, with no pending receipt. One resolved gate used the maximum
  4,000-character question and resolution plus maximum-length provenance.
- A PostgreSQL 17 custom archive was taken and its catalog explicitly checked
  for `work_gates`, the attention identity-sequence state, `work_events`, and
  `client_operations`. Restoring it into a separate empty database preserved
  the full source digest, revision, 100/99/1 gate counts, maximum attention
  sequence and sequence value of 100, all seven gate-table indexes, and all six
  cross-table guard triggers.
- With new gate creation disabled in the restored application, exact receipt
  replays of a resolved gate request, its resolution, and the still-unresolved
  request returned byte-identical response bodies. The complete redacted
  durable digest and counts were unchanged. Ready-work total remained zero,
  text-free attention total remained one, and a fresh claim returned
  `409 work_gated`.
- The archive grew from **136,195 bytes to 199,844 bytes** for this fixture,
  an observed increase of 63,649 bytes. The populated archive took 143.949 ms
  to write and 184.033 ms to restore. Allocated relation growth was 163,840
  bytes for gates, 221,184 for events, and 458,752 for receipts. The maximum-
  text gate plus its two events and two receipts occupied 34,154 row bytes:
  9,002 gate, 9,264 events, and 15,888 receipts.
- In-process API plus local PostgreSQL request latency across 100 gates was
  p50 **16.824 ms**, p95 **25.997 ms**, and p99 **37.633 ms**. Resolution
  latency across 99 gates was p50 **17.487 ms**, p95 **20.318 ms**, and p99
  **28.449 ms**. These figures include validation, persistence, response-model
  rendering, and local transport; they do not establish a network deployment
  latency budget.

#### Representative hierarchy plans

A random schema migrated to `0014_human_gates` held 12,000 work items and
12,000 checkpoints: 120 roots, 11,880 parent-child edges, maximum depth 50,
50-child broad branches, 321 discovery edges, 223 blockers, 282 active and 283
expired leases, and 393 gates split 197 unresolved/196 resolved. A deep-only
tag forced qualification through a deep descendant. Each production service
case received one warm-up and seven timed calls; its exact captured SQL then ran
under PostgreSQL 17.10 with `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` and the
production five-second statement timeout.

| Hierarchy case | Returned / total | Service p50 / p95 | Plan / execution | Shared hits | Recursive rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Root first page | 20 / 120 | 1062.382 / 1072.874 ms | 2.043 / 1064.345 ms | 72,267 | 12,000 |
| Root later page, offset 100 | 20 / 120 | 1073.723 / 1100.170 ms | 1.759 / 1071.097 ms | 72,267 | 12,000 |
| Root deep-tag filter | 1 / 1 | 1143.205 / 1162.721 ms | 1.889 / 1111.282 ms | 71,669 | 12,000 |
| Broad child first page | 20 / 50 | 86.911 / 88.450 ms | 3.542 / 90.604 ms | 3,482 | 99 |
| Broad child later page, offset 40 | 10 / 50 | 84.275 / 88.174 ms | 1.703 / 84.037 ms | 3,323 | 99 |
| Deep child tag filter | 1 / 1 | 19.843 / 25.311 ms | 1.843 / 15.995 ms | 3,134 | 99 |

All hierarchy plans had zero shared reads/writes and zero temporary blocks;
sorts stayed in 25–55 KB. They used the unresolved-gate, checkpoint, lease,
work-item, and relationship indexes, with expected sequential scans for
full-project root aggregation. Recursive traversal itself took about
15.6 ms. PostgreSQL JIT consumed roughly 994–1,040 ms of root execution and
67–73 ms of broad-child execution, while the selective deep-child plan did not
trigger JIT. This identifies JIT configuration/query cost, not a missing index,
as the first measured optimization target. No case timed out, spilled, or
returned duplicate rows.

#### High-degree focused human review

A second random schema migrated to head created 501 work items, 500 current
`related` edges and their canonical events, and one unresolved human gate. An
ordinary context response reported all 500 in `relationship_counts` but returned
its normal 50-edge slice: 58,704 response bytes with p50/p95 16.280/17.155 ms.
The valid focused-gate review returned **all 500 edges** in one statement:
485,206 bytes with p50/p95 51.224/51.994 ms. The exact focused SQL received one
EXPLAIN warm-up and three measured `ANALYZE, BUFFERS` runs; median planning was
2.512 ms, median execution 37.820 ms, and the final plan used 16,138 shared
buffer hits with zero reads, writes, dirtied blocks, temporary blocks, or JIT.
It stayed within the five-second statement timeout.

The focused review deliberately has no enforced edge-count maximum, because it
must return every relationship fact bound to the unresolved gate review. The
500-edge fixture is therefore an observed capacity point, not a cap or proof
for arbitrarily high degree; response size and latency grow with focal degree.

#### Ready-work, fresh-claim, and attention density

A third random schema on PostgreSQL 17.10 held 5,000 Pending work items,
1,000 unresolved gates (20 percent density), 500 active leases, 500 retained
expired leases, and 100 work items having both an active lease and a gate.
The expected ready union was therefore 3,600 items. The fixture was vacuum-
analyzed before measurement and every connection used the production
five-second statement timeout. In-process REST timings below used one warm-up
and seven measured calls per page. Exact SQL captured from each endpoint then
received one EXPLAIN warm-up and three measured
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` bundles.

| Read case | Items / total | Service p50 / p95 | Plan / execution | Shared hits | Response bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ready first page | 50 / 3,600 | 33.289 / 34.545 ms | 0.549 / 28.277 ms | 34,035 | 11,296 |
| Ready later page, offset 3,500 | 50 / 3,600 | 33.021 / 33.907 ms | 0.515 / 28.014 ms | 34,036 | 11,299 |
| Attention first page | 50 / 1,000 | 28.063 / 36.747 ms | 1.875 / 5.448 ms | 2,442 | 118,329 |
| Attention after sequence 500 | 50 / 1,000 | 28.759 / 37.800 ms | 1.785 / 5.010 ms | 2,420 | 109,018 |

The ready bundle contained two statements and used 15.408/15.146 ms of JIT in
the first/later final measured runs. The attention bundle contained eleven
statements and used no JIT. All four final bundles had zero shared reads,
writes, or dirtied blocks and zero temporary reads or writes. The later
attention cursor was obtained by traversing ten 50-item pages, rather than by
inventing a sequence token.

Fresh-claim behavior was then sampled on 100 distinct gated targets and 100
distinct otherwise eligible targets. Every gated request returned
`409 work_gated`; every eligible request returned 200 and acquired its lease.
Gated service latency was p50 **10.550 ms** and p95 **15.871 ms**; eligible
latency was p50 **11.839 ms** and p95 **16.507 ms**. Each captured request had
four read statements. After one warm-up and across three measured EXPLAIN
bundles, gated median planning/execution was 0.587/0.212 ms and eligible was
0.476/0.184 ms. Both final bundles used 20 shared hits with zero shared reads,
writes, dirtied or temporary blocks and no JIT. To avoid duplicating durable
effects, EXPLAIN replay covered only the captured `SELECT`/`WITH` statements;
the successful lease/event DML is evidenced by the 100 real service calls, not
re-executed under EXPLAIN.

These are small local samples and observed capacity points. In particular,
seven-call p95 values are descriptive only; they are not production network
SLOs.

#### Passive active-descendant lease expiry

The same production-shaped schema added one 500-child branch with 100 active
descendant leases. One lease expired three seconds after the initial read and
the other 99 expired fifteen minutes later. The first hierarchy response took
109.070 ms, reported 100 active descendants, and returned the earliest boundary.
A read started 100.077 ms after that boundary; seven post-boundary reads were
p50 **108.456 ms** and p95 **113.398 ms**, reported 99 active descendants, and
advanced the returned boundary to the later expiry. Time passage performed zero
database writes and no server background polling; the corrected count appeared
on the next read.

The authoritative browser scheduler command
`node --test tests/lease-refresh.test.mjs` passed **5/5 tests in 71.435 ms**.
Under fake time it selects the earliest valid displayed expiry, fires exactly
at that boundary, retries an unchanged already-due boundary every 65 seconds,
and stops after cancellation. That bounds a persistently stale mounted
scheduler to about 0.923 retry callbacks per minute after its first boundary
callback. The dashboard refreshes its list, attention total, and open context;
each expanded hierarchy branch schedules from its returned earliest child
boundary. Rate therefore scales with mounted views/expanded branches and has
no server-global enforced maximum. The full 38-execution Playwright result
above includes the active-lease browser expiry path. These observations are
behavior and rate evidence, not a browser/network latency SLO.

#### Prerelease capacity acceptance budgets

The Phase 3 hierarchy baseline used 2,000 work items and reported 12.28 ms root
pagination and 1.48 ms child expansion before full-branch presentation facts
existed. The Phase 7-8 fixture deliberately increased the project sixfold,
required every returned row and aggregate to share one statement snapshot, and
added gates, discovery, blockers, lease overlap, depth 50, and deep-only
qualification. Using that historical baseline and the production-shaped
measurements above, the following conservative local release budgets were fixed
before final acceptance. They are regression gates for the named PostgreSQL
17.10 fixtures and five-second statement timeout, not promises for arbitrary
hardware, graph size, focal degree, or network latency.

| Capacity case | Local prerelease budget | Observed worst named value | Result |
| --- | --- | ---: | --- |
| Gate request/resolution, 100-gate fixture | p95 <= 50 ms and p99 <= 100 ms | 25.997 / 37.633 ms | pass |
| Maximum-text gate plus two events/receipts | <= 64 KiB row bytes | 34,154 bytes | pass |
| Ready and attention first/later pages, 5,000 work / 20% gate density | p95 <= 100 ms, no temporary spill | 37.800 ms, zero temp blocks | pass |
| Fresh gated/eligible claims | p95 <= 50 ms with exact 409/200 outcomes | 16.507 ms | pass |
| 12,000-work hierarchy root pages/deep filter | p95 <= 1.5 s, one statement, no spill, under timeout | 1.163 s, zero temp blocks | pass |
| Broad/deep hierarchy child pages | p95 <= 150 ms, no spill | 88.450 / 25.311 ms | pass |
| Focused 500-edge human review | service p95 <= 100 ms, SQL execution <= 75 ms, no spill | 51.994 / 37.820 ms | pass |
| Passive 500-child expiry correction | first post-boundary read <= 250 ms and advances exact count/boundary | 113.398 ms | pass |
| 100-gate archive growth and restore | archive growth <= 128 KiB; dump and restore each <= 1 s | 63,649 bytes; 143.949 / 184.033 ms | pass |

The root budget intentionally exposes JIT as the current optimization target:
the measured root statements spend roughly one second compiling, while the
recursive traversal itself is about 15.6 ms. Crossing a budget is a release
investigation, not permission to add a cache, counter table, closure table, or
index without representative plan evidence.

#### Extracted Phase 6 process against revision 0014

No historical Phase 6 container image was available or run. A safe disposable
source/process drill instead archived immutable commit
`7f2a3215853873d19cdffe5c7b096bce4e4403d0`, verified that its backend source
had no `work_gates` or human-gate references, and launched that extracted
source in a separate operating-system Python process using the locked backend
environment. Its TestClient targeted a random PostgreSQL 17.10 schema already
at `0014_human_gates` with a five-second statement timeout.

The gate-aware service created two gated Pending targets: one without a lease
and one with a retained expired lease. Its ready total was zero. The old process
stale-listed both as ready, proving the documented read hazard, but its fresh
claim, expired-lease replacement claim, completion, and deletion attempts each
returned `503 database_unavailable`. After all attempts, the schema remained
at `0014_human_gates` with two gates, two gate events, zero lease rows for the
fresh target, the one original expired lease still retained, both work items
Pending at version 1 and undeleted, and their two original checkpoints. Thus
the database backstops failed closed without a partial domain change.

This was a real separate old-source application process, but not a historical
container-image/package/startup drill. Coordinated image inventory, routing
drain, and zero old database connections remain mandatory at deployment; this
source-level evidence does not replace them.

The final root validation also passed backend and MCP Ruff, frontend typecheck
and production build, production Compose rendering, checker compile/lint,
repository whitespace checks, and relative Markdown-link validation. A fresh
adversarial review of the implementation and the added gate-idempotency,
readiness/lifecycle, hierarchy, writable-stack, and browser evidence found no
remaining blocker or high-severity issue. The ready/claim, attention,
hierarchy, focused-context, passive-expiry, backup/restore, and old-process
observations above cover the accepted plan's prerelease capacity and
compatibility gates without creating production SLOs. Historical Phase 6
counts below remain accurate for that release and are not rewritten as current
Phases 7-8 totals.

## Sidebar artwork and edge SVG serving — 2026-09-01

Checks observed while replacing the sidebar's drawn page stack with the robot
SVG and moving static SVG delivery to the host nginx.

- **73 frontend unit tests, TypeScript checking, and the production build
  passed.** The isolated Node 24 Playwright stack passed all 26 desktop and
  narrow-viewport executions, including a new check that the sidebar image
  resolves to a decoded asset rather than a broken `<img>`.
- **The installed configuration passed `nginx -t` on nginx 1.24.0 and was
  reloaded.** Against the live host: `/img/robot.svg` returned 200 from disk
  with `cache-control: max-age=604800` and gzip encoding, carrying HSTS and all
  six response headers `next.config.ts` sets. `/icon.svg`, which Next.js
  generates outside `public/`, still returned 200 from the dashboard through
  the fallback, and an absent `.svg` returned the dashboard's 404.
- **The routing guards were unchanged by the new location.** `/` returned 200,
  `/mcp` without a bearer token returned 401, `/mcp/foo.svg` returned 404 from
  the `^~` prefix rather than the SVG regex, a traversal sequence resolved
  outside the root and returned 404, and port 80 still returned 308.

These checks also found a pre-existing configuration fault unrelated to the
change: `.env` had never set `MNEMONIC_TLS_HOST`, so `compose.tls.yaml` supplied
its `mnemonic.example.com` placeholder to all three allowlists and the dashboard
answered 403 to its own `/api/mnemonic/*` requests over the public hostname. The
page itself loaded, which is why routing checks passed. Setting the real
hostname and recreating the stack returned 200 with the projects listed, and an
untrusted origin still returned 403.

Not checked: any client outside the address allowlist. No stored prompts were
read or modified.

## Phase 6 final integrated validation — 2026-09-01

Validated against the final integrated source after the semantic rebase. The
Alembic order is `0011_project_settings` -> `0012_pending_deferred_statuses` ->
`0013_idempotent_mutations`; the protected surface is ten REST operations, nine
MCP tools, and nine browser intents.

- **The full backend suite passed 314 tests against PostgreSQL 17, and the full
  backend Ruff check was clean.** The run produced three known warnings.
  Focused migration validation passed four tests, and the deterministic
  receipt-race, deferral, and readiness batches also passed.
- **The full MCP suite passed 186 tests in its separate environment.** The
  protected surface remains exactly nine MCP mutation tools within the exact
  22-tool catalog.
- **The frontend passed 96 unit tests, TypeScript checking, and the production
  build.** The complete isolated Playwright stack then passed 36/36 executions
  in 1.1 minutes across desktop and narrow Chromium. Its disposable stack was
  cleaned after the run.
- **Both plugin manifests validated, and real disposable installation drills
  passed.** A fresh `0.4.0` install and a sequential `0.3.0 -> 0.4.0` upgrade
  both completed successfully.
- **A disposable five-service production stack became healthy.** The read-only
  checker passed health, authentication, dashboard proxy/origin policy, the
  exact 22-tool catalog, all nine protected MCP schemas and annotations, and
  REST-backed project listing.
- **The authorized checker passed its complete canonical lifecycle.** It
  exercised create, search, recall, checkpoints, events, the resource and
  prompt, dashboard editing, stale-version rejection, claim replay, renewal and
  release, pointer/capability isolation, ready-work behavior, event history,
  graph behavior, and cleanup. A post-check ledger query found 25 completed
  receipts across all nine MCP-covered operation kinds, with zero pending rows
  and no completed row missing a response status.
- **A dedicated custom-archive replacement drill preserved idempotent deferral
  replay.** After known create and defer operations, writers were stopped and a
  custom archive was restored through whole-`public`-schema replacement. The
  restored database retained revision `0013_idempotent_mutations`, removed a
  post-backup sentinel, retained the completed defer receipt, and returned the
  same HTTP status and a byte-identical body for an exact same-key deferral
  replay without a second transition.
- **Application-service log review found no runtime failure or operation-key/ID
  leak.** The aggregate PostgreSQL log's only failure was one operator-caused
  diagnostic query against the nonexistent `mutation_receipts` name; it was not
  an application runtime query or failure.

The post-implementation cold adversarial code review found and drove fixes for
two high-severity recovery gaps. Request-only MCP metadata validation had been
reused for historical progress-event reads, so nested legacy
`Client_Operation_ID` metadata could no longer be recalled; request and
historical validators are now separate, with a regression through both event
listing and recall. The browser mutation registry also lacked a client-side
deadline spanning both `fetch` and response-body decoding, so a stalled request
could remain permanently in flight. Every attempt now has a 20-second deadline,
five seconds above the ordinary proxy timeout, with one abort signal kept active
through strict decoding; hung-fetch and hung-body tests prove transition to
unresolved followed by exact UUID/method/path/body retry. Read-only remediation
reviews found no remaining blocker or high-severity issue.

## Phase 6 pre-integration validation checkpoint — 2026-09-01

This checkpoint records genuinely observed results from the Phase 6 branch
before it was semantically rebased onto `0012_pending_deferred_statuses` and
before `defer_work` became the tenth REST and ninth browser operation. Its old
migration name, operation counts, and test counts are retained as historical
performance, contention, restore, and integration evidence only; they are not
the final integrated release result above.

Validated against the then-current pre-integration Phase 6 source in the
isolated Linux worktree with
Python 3.13.15, PostgreSQL 17, the separate locked backend/MCP environments,
and Node 24.20.0 for the frontend unit and type gates. This record contains
only checks and measurements observed during this implementation session.

- **208 backend tests passed against the disposable PostgreSQL service with no
  skips, and the full backend Ruff check passed.** The three warnings were the
  existing upstream Starlette TestClient deprecation and SQLAlchemy reflection
  warnings for the PostgreSQL `NOT VALID` constraint options. The suite covers
  all nine operation kinds, frozen canonical vectors, exact replay,
  cross-project/key conflicts, natural no-ops, rollback, response validation
  and secret rejection, outcome-aware live invalidation, pool saturation, and
  migration/model parity.
- **133 MCP tests passed in its separate environment, and repository Ruff
  passed for MCP plus `scripts/check-stack.py`.** The exact catalog remains 22
  tools. Exactly nine mutation tools require a UUID and advertise
  `idempotentHint=true`; protected transport/malformed-response failures make
  one outbound attempt and retain exact-retry guidance, while excluded writes
  retain their separate contracts.
- **88 frontend unit tests and TypeScript checking passed under Node 24.20.0.**
  The then-current production build also passed in the Node 24 image. The tests
  cover all eight browser mutation intents, exact serialized-body reuse, strict
  response decoding, conflict-key blocking, retained safety conflicts, proxy
  route/body/secret policy, and the no-persistence boundary.
- **The complete isolated Playwright stack passed 28/28 executions in 57.7
  seconds: 14 desktop and 14 narrow Chromium.** The Phase 6 scenarios commit
  before a synthetic lost or malformed response, retry the exact method, path,
  body, and UUID, and prove one durable create/event/relationship/delete
  effect. They also cover natural true-versus-fresh-key-false results, blocked
  ambiguous UI, modal-accessible recovery, healing invalidation, newer-state
  reconciliation, deletion disappearance, and absence of retry material from
  browser storage and rendered content.
- **Migration validation passed for populated `0011_project_settings` to
  `0012_idempotent_mutations`, fresh head creation, empty-ledger downgrade and
  re-upgrade, and completed-ledger downgrade refusal.** Historical nested and
  case-varied `client_operation_id` metadata remained byte-semantically
  unchanged under the separate `NOT VALID` Phase 6 check, and the Phase 5
  metadata function remained unchanged. Two deterministic, no-sleep
  two-connection tests observed the actual PostgreSQL locks: a writer-first
  downgrade waited then refused without losing its completed receipt, while a
  downgrade-first path held `ACCESS EXCLUSIVE` after the empty check through
  drop and forced the blocked writer to fail with SQLSTATE `42P01`.
- **The receipt contention and pool-recovery drill passed four focused
  PostgreSQL tests in 2.79 seconds.** Same-key owner commit produced one replay,
  owner rollback transferred ownership to the waiter, a one-second bounded
  timeout never fallback-executed, and two bounded waiters against a
  pool-size-three/no-overflow engine released all capacity for an unrelated
  query and exact retry.
- **A 1,721-receipt durability/performance fixture completed across four
  representative response shapes.** It held 420 append-event receipts, 1,200
  absent relationship-removal no-ops, 100 larger create-work snapshots, and
  one update-work recovery receipt. Another 400 exact replays of the last
  append key added no receipt. Full in-process API plus local PostgreSQL fresh
  append latency was p50 11.947 ms, p95 21.153 ms, and p99 27.159 ms; replay
  was p50 7.800 ms, p95 11.505 ms, and p99 15.858 ms. Eight workers completed
  1,200 different-key durable no-ops over 64 project-lock partitions in 9.658
  seconds, or 124.3 requests/second.
- **The unique receipt lookup used `uq_client_operations_scope` as a one-row
  index scan.** The observed plan took 0.054 ms to plan and 0.033 ms to execute
  with three shared-buffer hits and no reads. After `VACUUM (ANALYZE)`, 1,721
  rows used 1,515,520 bytes of heap, 1,556,480 bytes of table/TOAST storage,
  204,800 bytes of indexes, and 1,761,280 bytes total: approximately 1,023.4
  physical bytes per receipt. Serialized response snapshots averaged 498.5
  bytes and had p95/max 1,380 bytes.
- **A real custom-format dump and isolated restore preserved retry knowledge.**
  The 413,715-byte archive took 0.172 seconds to dump and 0.307 seconds to
  restore. The restored revision was exactly `0012_idempotent_mutations`;
  project/work/checkpoint/event/relationship/lease/receipt aggregates and the
  dedicated target version matched the source. A real post-restore PATCH with
  the retained UUID and exact body returned the original typed JSON while the
  entire before/after aggregate tuple remained unchanged.
- **A PostgreSQL 17 old-archive-over-new-target replacement drill passed.** A
  real populated `0011_project_settings` custom archive was restored over a
  migrated `0012_idempotent_mutations` target containing a completed private
  receipt. Immediately after restore, `alembic_version` was exactly
  `0011_project_settings`, `to_regclass('public.client_operations')` was null,
  and the historical nested value
  `{"outer":[{"Client_Operation_ID":"historically-legal"}]}` remained
  semantically exact. Migrating that restored database to Phase 6 recreated an
  empty receipt ledger, preserved the legacy value, and installed the reserved
  metadata constraint as deliberately `NOT VALID`. This specifically proves an
  older archive cannot leave future schema objects or receipt data behind.
- **Plugin manifest, inventory, and installation validation passed.** Both JSON
  manifests parsed strictly; the inner version is `0.4.0`; the package
  contains exactly three skills and two shared references. A fresh isolated
  `0.4.0` installation and a sequential `0.3.0 -> 0.4.0` update both
  installed the expected bytes and valid shared links without compatibility
  copies.
- **The pre-integration disposable production-image stack passed.** All five services
  became healthy with `0012_idempotent_mutations` matching the image, running
  API, and database. The read-only checker passed both sections; the authorized
  checker passed all three sections, the 22/22 catalog and 9/9 protected schema
  gates, and a five-item MCP-to-REST-to-PostgreSQL/dashboard-proxy lifecycle.
  Its retained state represented all nine operation kinds: 31/31 receipts were
  completed, zero were pending, all seven work rows were soft-deleted, and no
  relationship remained. All five recognizable misplaced operation-ID headers
  were rejected value-free with no durable state. A bodyless dashboard-proxy
  relationship DELETE was rejected while the edge, both endpoint timelines,
  and receipt count remained unchanged.
- **That pre-integration stack log audit inspected 379 aggregate lines with zero
  tracebacks, severe runtime entries, credential-value hits, operation-ID hits,
  or known body-content hits.** The first smoke cycles exposed only stale
  checker expectations: a keyed stale edit needed its required actor, and keyed
  secret echoes now correctly return `client_operation_secret_echo` before the
  Phase 5 event-only guard. Both checker fixtures were corrected and the full
  writable lifecycle reran successfully.
- **The pre-integration static gates passed.** Both manifests and every repository-local
  Markdown target parse or exist, the checker CLI imports, and
  `git diff --check` is clean. Every disposable benchmark database/schema,
  dump, browser stack, production stack, volume, network, temporary credential
  file, Playwright artifact, and checker artifact was removed. The existing
  `mnemonic` and `mnemonic-test` stacks were not mutated by disposable
  validation.

The performance figures are one warm local tmpfs run, not an SLO or production
capacity claim. They use in-process TestClient rather than network/TLS/proxy,
moderate payloads and four of nine response shapes rather than maximum-size
responses, and durable relationship no-ops rather than applied writes for the
parallel throughput sample. Index bytes are relation-wide, the dump size
includes the entire fixture database, and the exercise did not benchmark a
production-sized migration lock or sustained ten-second contention.

## Phase 4 ready-work and Phase 5 event validation — 2026-09-01

Validated in the local Linux workspace with the locked environments, isolated
PostgreSQL 17 data, and Node 24. This record includes only checks and
measurements observed during this implementation session.

- **140 backend tests passed against disposable PostgreSQL 17, and the full
  Ruff check passed.** The populated migration exercise through
  `0009_ready_work_indexes` and `0010_work_events` passed upgrade, exact
  backfill, ORM model parity, and downgrade checks.
- **122 MCP tests passed.** The package-local environment does not include Ruff;
  MCP and checker lint were covered by the full repository Ruff run above.
- **51 frontend unit tests, TypeScript checking, and the production build
  passed.** The isolated Node 24 Playwright stack passed all 16 desktop and
  narrow-viewport executions.
- **A cold adversarial review found no remaining P0/P1/P2 flaws after fixes.**
  It drove regression fixes for deferred release-marker bypasses, exact retained
  holder values, Unicode tag normalization, strict event references and
  endpoint binding, ready-page lifecycle semantics, dashboard refresh/paging
  recovery, and attacker-controlled validation-location reflection. Every
  affected backend, MCP, frontend, Playwright, restore, and full-stack gate was
  rerun against the final source.
- **Plugin validation and installation drills passed.** The marketplace and
  plugin manifests validated; real disposable installs succeeded for sequential
  `0.1.0 -> 0.2.0 -> 0.3.0` upgrades and fresh `0.2.0` and `0.3.0` installs.
- **The populated `0009 -> 0010` migration completed in 7.35 seconds.** It
  backfilled exactly 52,000 immutable events over 10,000 work items: 10,000
  `work_created`, 20,000 `checkpoint_added`, 4,000 `dependency_added`, 16,000
  `relationship_added`, and 2,000 `work_claimed`. Initial event storage was
  26 MB. Inserting a further 100,000 progress events took 7.16 seconds, leaving
  152,000 table rows and a busiest per-work history of 100,305 events in that
  multi-work migration fixture.
- **Every required ready-work plan passed on the final canonical query.** The
  exact corpus held 10 projects, 10,000 open work items, 30,000 checkpoints,
  10,000 relationships including 2,000 blockers and 1,000 direct-parent edges,
  and 2,000 leases split evenly between active and expired. The default query
  returned the requested project's total 750 plus 30 rows in 3.182 ms with
  4,691 shared-buffer hits. A selective mixed-case tag exercised PostgreSQL
  normalization on both operands and returned total/page 7 in 2.820 ms with
  556 hits using `ix_checkpoints_normalized_tags_gin`. The direct-parent filter
  returned total 100 plus 30 rows in 3.023 ms with 2,691 hits using
  `uq_work_relationships_one_parent`. Offset 500 returned total 750 plus 30 rows
  in 5.921 ms with 4,691 hits. Each query was warmed once; all four reported
  zero shared reads/writes/dirtied blocks and zero temporary I/O. They used
  `ix_work_items_ready_order`, bounded blocker endpoint/source indexes, lease
  primary-key probes, and page-only checkpoint-count probes, with no sequential
  scan, full lease/graph scan, external sort, or spill.
- **Event paging remained bounded in a separate intentionally one-hot fixture.**
  All 152,000 table rows belonged to the queried work item. The list route
  returned the exact per-work total plus a 30-row page in 23.084 ms; bounded
  context returned the same total plus 10 timeline rows in 15.760 ms. Both
  bounded page selections used `ix_work_events_timeline` with no temporary
  spill; their separate exact totals necessarily traversed all matching rows.
- **Both event orders and deep offsets were measured through the exact list
  service statement.** A separate 100,001-event history contained one
  `work_created`, 90,000 `progress`, and 10,000 `work_updated` rows.
  Oldest-first pages at offsets 0, 50,000, and 99,901 ran in 16.664, 21.139, and
  26.451 ms with 2,132, 4,611, and 7,186 shared-buffer hits. Newest-first pages
  at those offsets ran in 16.394, 21.602, and 26.490 ms with 2,131, 3,976, and
  5,716 hits. Every 100-row page used `ix_work_events_timeline`; increasing
  index rows and buffers show the documented offset degradation. All page
  sorts remained in memory at 39–46 kB.
- **Selective event filters used the dedicated indexes.** A 10,000-row
  `work_updated` filter returned 100 rows plus its exact total in 3.426 ms and
  200 shared-buffer hits, using `ix_work_events_timeline_type` for both the
  page and an index-only count. The one-row `work_created` filter used
  `uq_work_events_work_created`, ran in 0.188 ms, and hit 7 buffers. The common
  90,000-row `progress` filter ran in 21.509 ms with 2,572 hits; its page used
  the general timeline index and PostgreSQL rationally chose a sequential
  exact-count aggregate for that majority. The history flag used
  `uq_work_events_work_created` in every plan. No variant spilled to temporary
  storage. The disposable database was dropped afterward.
- **A real custom-format backup and isolated restore drill passed.** Dump took
  0.62 seconds and restore took 7.78 seconds. Source and restored databases
  matched at 152,000 events, maximum event ID 152,000, deterministic checksum
  `e89ae2688fd6393045e7e46f115e3d6b`, and sequence state. The restore retained
  all 11 indexes, the event checks, the immutability function, and all three
  work-event triggers. A post-review repeat against the hardened final schema
  dumped in 0.18 seconds and restored in 0.13 seconds. Its source and restore
  matched at 13 events, maximum ID 13, checksum
  `2844251f5ce317c3128c02beef907911`, sequence state, all 11 indexes, all four
  event/release guards, and the final release-marker function fingerprint.
  Restored event update and delete each failed with SQLSTATE `55000`. Both
  drills removed their disposable databases and archives.

- **The production-image full-stack check passed** against a uniquely named,
  disposable Compose project. All services became healthy, and
  `scripts/check-stack.py` verified authentication, dashboard origin and host
  protection, the exact 22-tool MCP catalog, and the complete authorized Phase
  4/5 ready-work and immutable-event lifecycle through MCP, REST, PostgreSQL,
  and the dashboard proxy. Cleanup succeeded. A 23,486-character scan of API,
  MCP, and web logs contained no bearer value/header, synthetic request body,
  accepted progress-event body, traceback, or unhandled exception. The
  disposable containers, network, and volume were removed afterward.

## Deprecated hand-off surface removal — 2026-08-31

Validated in the local Linux workspace with the locked environments and an
isolated PostgreSQL 17 test stack, after removing the deprecated hand-off tools,
REST routes, resource, and prompt.

- **120 backend tests passed against disposable PostgreSQL 17**, none skipped.
  A control run without `TEST_DATABASE_URL` reported 77 passed and 51 skipped,
  confirming the PostgreSQL tests actually executed. Backend Ruff passed; the
  only warning was the existing upstream Starlette TestClient deprecation.
  Coverage that previously reached canonical behavior through a deprecated route
  was retargeted rather than deleted: weighted full-text ranking, literal and
  wildcard query safety, `search_vector` re-derivation after an edit, combined
  filters with pagination, concurrent-writer version conflict, and the shared
  schema validation cases.
- **86 MCP tests passed**, and MCP Ruff passed. The suite now verifies the exact
  19-tool canonical catalog. The eight deprecated tools were removed, so the
  count fell from the 87 recorded for Phase 3 above.
- **39 frontend unit tests passed under Node 24.** TypeScript checking and the
  Next.js production build passed. Six tests covering deprecated-only helpers
  were removed with those helpers.
- **The production-image full-stack check passed** after `docker compose up
  --build`. `scripts/check-stack.py` read-only mode verified service security and
  the exact 19-tool catalog.
- **Live MCP surface measured against the running stack.** `tools/list` returned
  19 tools with no `*_handoff*` name; the model-visible tool surface fell from
  27,627 to 21,860 bytes. The `handoffs` resource template and the
  `resume_handoff` prompt are absent; the `work-items` resource and `resume_work`
  prompt still resolve. All eight `/projects/{project_id}/handoffs*` REST routes
  return `404` while the canonical routes return `200`.
- **Recall duplication eliminated.** Across the eight live work items, bounded
  recall returned 191,411 bytes with 89,985 bytes (47.0%) of byte-identical
  checkpoint duplication before the change, and 111,357 bytes with 0 duplicated
  bytes after it.
- **Search compaction measured.** The default agent-facing `view=minimal`
  returned 315 bytes per item against 1,824 bytes for the unchanged `view=full`
  dashboard shape.
- **`INSTRUCTIONS` is 581 characters** and leads with the trigger condition.
- **Migration provenance verified against the live database, not from code.**
  All seven checkpoints carrying `migration_origin = 'legacy-handoff-snapshot'`
  retain their `legacy_record_id`, and both fields still surface through the
  canonical context route. The Alembic head remains `0008_work_relationships`
  and no file under `backend/alembic/versions/` was modified.

The "27-tool canonical/compatibility catalog" recorded for Phase 3 below was
observed before this removal and is left as written.

## Phase 3 typed work-relationship validation — 2026-08-31

The complete three-phase program was validated in the local Linux workspace
with the repository's locked environments and isolated PostgreSQL 17 stacks:

- **128 backend tests passed against disposable PostgreSQL 17**. They covered
  migration/model parity through `0008_work_relationships`, all five
  project-local edge types, database constraints, direction and provenance,
  normalization/idempotency, one-parent enforcement, sequential and concurrent
  cycle prevention, blocker readiness and lease overlap, atomic linked creation,
  hierarchy filtering, bounded relationship context, live synchronization, and
  legacy tag compatibility. Backend Ruff passed; the only warning was the
  existing upstream Starlette TestClient deprecation.
- **87 MCP tests passed**, and MCP Ruff passed. HTTP and stdio tests exercised
  the exact 27-tool canonical/compatibility catalog, strict schemas,
  pointer-only counterpart data, typed graph errors, local validation
  sanitization, malformed-envelope log redaction, and claim-response recovery.
- **45 frontend unit tests passed under Node 24**. TypeScript checking and the
  Next.js production build passed. The tests cover hierarchy/search helpers,
  relationship direction and conflict language, stable per-tab provenance,
  strict proxy routes, capability rejection, and empty-stream DELETE handling.
- **10 Playwright test executions passed** across desktop and narrow Chromium.
  They exercised the Phase 1 and 2 scenarios plus collapsed root paging, lazy
  child loading, ancestry breadcrumbs, open descendants below every terminal
  parent status, active-plus-blocked display, relationship add/remove and
  parent conflict behavior, keyboard/dialog use, and narrow-layout containment.
- **The production-image full-stack check passed** against the separately named
  `mnemonic-phase3-validation` Compose project. All five containers became
  healthy at Alembic `0008_work_relationships`. The checker verified service
  security and the exact 27-tool catalog, then exercised create/search/recall,
  immutable checkpoints, stale edits, claim/replay/renew, blocker eligibility,
  atomic child/discovery creation, hierarchy browse, completion, default-open
  filtering, compatibility aliases, graph-first cleanup, and soft deletion
  through MCP → REST → PostgreSQL and the dashboard proxy.
- **All six planned query shapes were inspected with
  `EXPLAIN (ANALYZE, BUFFERS)`** on 2,000 work items, 6,000 checkpoints,
  1,800 hierarchy edges, and 100 blocker edges. Observed execution times were
  0.10 ms for indexed browse, 22.46 ms for complete lexical fallback search,
  0.05 ms for latest checkpoint, 0.08 ms for indexed blocker count, 12.28 ms
  for subtree-aware root pagination, and 1.48 ms for child expansion.
- **A real post-upgrade custom-format backup/restore drill passed**. The archive
  passed `pg_restore --list` and had SHA-256
  `4a04521677d690e70f54e02912162e7f536f3a4058608c8dee80910da567e5b6`.
  Source and restored databases matched at migration head, one project, 2,003
  work items, 6,005 checkpoints, and 1,900 relationships (100 `blocks` and
  1,800 `parent-child`). The restored checkpoint immutability trigger and all
  three relationship indexes were present.
- The disposable API/MCP/web log audit covered 13,288 characters and found no
  API key, bearer header, lease-token field/query, claim request ID, traceback,
  or unhandled exception. All three bundled skills passed the skill-creator
  validator; the examples parsed, documentation links resolved, the full-stack
  checker passed Ruff/compile/help/catalog checks, and the final diff had no
  whitespace or patch artifacts.

The comprehensive Phase 1–3 review also fixed expired claim-request reuse,
mixed-case migrated tag lookup, project-wide graph live synchronization and
open-detail reconciliation, a deletion guard that bypassed project-leading
relationship indexes, context projection work performed before its bound,
possible MCP SDK validation-value logging, non-strict canonical project
responses, a missing typed discovery-context error mapping, bodyless browser
relationship deletion, whitespace-only search mode drift, lifecycle-filtered
hierarchy fallback, and stale/superseded UI reload reporting and recovery. The
disposable E2E and production containers, networks, volumes, restored database,
backup archives, and temporary settings were removed. The user's existing
Mnemonic stack was not modified.

## Phase 2 atomic work-lease validation — 2026-08-31

The Phase 2 work-lease cutover was validated in the local Linux workspace with
the repository's locked environments:

- **118 backend tests passed against disposable PostgreSQL 17**. They covered
  migration/model parity, exact claim replay, expiry takeover, renewal and
  release, holder/session/request isolation, terminal-transition lease
  consumption, lock ordering, concurrent claims, optional checkpoint lease
  validation, query-capability rejection, and redaction. Backend and
  repository-wide Python Ruff checks passed; the suite emitted only its existing
  upstream Starlette TestClient deprecation warning.
- **68 MCP tests passed**. They covered the four typed lease tools, the exact
  23-tool HTTP and stdio catalogs, secret input schemas and representations,
  safe error mapping, and recovery of an unknown claim response without an
  unsafe retry.
- **34 frontend unit tests passed under Node 24**; TypeScript checking and a
  Next.js production build succeeded. The tests cover lifecycle/lease display,
  recursive proxy denial of capability-bearing inputs, typed conflict handling,
  and refresh at the lease-expiry boundary.
- **8 Playwright scenarios passed** across desktop and narrow Chromium. In
  addition to the Phase 1 work/checkpoint and live-update flows, they exercised
  active-lease visibility, expiry refresh, and an external claim arriving during
  a dashboard edit. The UI did not expose claim, renew, release, or token
  controls.
- **The production-image full-stack check passed** against a separately scoped
  disposable Compose project. The API, MCP server, dashboard, backup service,
  and PostgreSQL 17 became healthy; Alembic reported `0007_work_leases`, the
  removed hand-off tables were absent, and the real MCP HTTP catalog contained
  exactly 23 tools. The check exercised canonical work/checkpoint behavior plus
  exact claim replay, renew, cross-project token isolation, leased completion,
  and open-work filtering.
- **A real post-upgrade custom-format backup/restore drill passed**. The archive
  was validated by `pg_restore --list` and had SHA-256
  `f0ef414228a6e64de01583e25b3eaa2c025443bb66e2902bc270a6235c9fa437`.
  Source and restored databases matched at migration head, table counts, lease
  count, capability-token shape, removed-table absence, and deterministic
  canonical-data checksum (`798a8de29db3b8e5eff4c40d54f0b8b4`). A restored
  API rejected replay of an expired request with `claim_request_expired`, then
  allowed takeover and kept ordinary context responses capability-free.
- The updated `mnemonic-recall` skill passed the skill-creator validator. A
  separate final scope audit found no Phase 3 persistence, tools, or UI.

The Playwright, production-stack, restore-API, and test-database resources were
uniquely scoped. Their disposable containers, networks, volumes, restored
database, backup archive, and temporary configuration were removed after the
checks. The user's existing Mnemonic stack was not modified by validation.

## Phase 1 work/checkpoint validation — 2026-08-31

The canonical Phase 1 work-item/checkpoint cutover was validated in the local
Linux workspace with the repository's locked environments:

- **101 backend tests passed against disposable PostgreSQL 17**. Backend Ruff
  and Python compile checks also passed.
- **50 MCP tests passed**. MCP Ruff checks and the bundled skill validations
  also passed.
- **21 frontend unit tests passed**; TypeScript checking and a Next.js
  production build succeeded.
- **2 Playwright acceptance scenarios passed** against a disposable stack: one
  in desktop Chromium and one at the narrow Chromium viewport. They exercised
  work-item grouping, immutable checkpoint history, canonical recall pointers,
  work-only edits, completion, and deletion.
- **The production-image full-stack check passed** against a separately scoped
  disposable Compose project. It exercised the exact 19-tool MCP catalog and a
  canonical create/search/recall/checkpoint/update/complete/delete lifecycle
  through MCP, REST, PostgreSQL, and the dashboard proxy, then verified the
  deprecated aliases, resource, and prompt resolve the same canonical records.
- **A real custom-format backup/restore drill passed on the Phase 1 schema**.
  The production backup and restore scripts both validated the archive with
  `pg_restore --list`; an isolated restored database had the same deterministic
  canonical-data checksum as its source
  (`842b39ac85894777721e2b7f28f70588`). Canonical and deprecated API reads
  preserved the representative work item, both exact checkpoint bodies,
  Unicode, provenance, JSON metadata, IDs, timestamps, lifecycle, and version.
  The restored checkpoint immutability trigger rejected a direct update.
- The migration and running API both reported Alembic head
  `0005_work_graph_backfill`.

The Playwright wrapper used a uniquely scoped Compose project with disposable
PostgreSQL storage. Its success-path cleanup removed the containers and network;
no E2E containers remained after the run. The production-stack and restore
checks used a different narrowly named Compose project and isolated restore
database; their containers, volume, network, and temporary archive were removed
after validation.

## Hand-off progress validation — 2026-08-31

The comment and completion-summary change was validated in the local Linux
workspace with the repository's locked environments:

- **92 API tests passed against disposable PostgreSQL 17**, including Alembic
  model parity, exact comment text/provenance, comment full-text search,
  cross-project isolation, atomic completion, stale-version duplicate prevention,
  lifecycle filtering, and comment-aware embedding invalidation.
- **38 MCP tests passed**, including all ten typed tools, comment pagination and
  writes, completion receipts, timeline-bearing resources/prompts, Streamable
  HTTP, and a real stdio subprocess handshake.
- **13 dashboard tests passed**; TypeScript checking and a Next.js production
  build also succeeded. The tests cover the allowlisted comment/completion proxy
  routes alongside the existing origin and host protections.
- Backend lint, changed-MCP-file lint, and the updated full-stack check script's
  lint and format checks passed.

The disposable PostgreSQL container and network were removed after the run. The
API environment emitted its existing upstream Starlette TestClient deprecation
warning; no test failed.

## Prior MVP validation — 2026-08-30

Validated on 2026-08-30 (America/New_York) using Docker Desktop's Linux engine
on Windows. The production images were built from the repository dependency
lockfiles and run with the shipped Compose configuration.

## Automated checks

- **77 API tests passed** against real PostgreSQL 17, using the API's exact
  locked environment. Includes migrations/schema consistency, weighted GIN
  full-text search, stemming, literal identifiers and paths, safe query escaping,
  validation, authentication, project isolation, lifecycle, pagination, soft
  deletion, and simultaneous writer conflicts.
- **33 MCP tests passed**, including typed tools, HTTP error mapping, bearer and
  Host/Origin protection, SDK Streamable HTTP initialization/calls, and a real
  stdio subprocess handshake.
- **6 dashboard security tests passed**. TypeScript validation and the Next.js
  production Docker build succeeded. Package installation reported no known npm
  advisories at the time of this run.
- All three distributable skills passed the skill-creator validator. A separate
  scenario review checked duplicate handling, unavailable session IDs, stale
  provenance, and authorization boundaries.
- Python lint checks passed for the backend and operator/check scripts.

The API test environment reports one upstream Starlette TestClient deprecation
warning; it does not affect these results or the serving application.

## Running application checks

The live check script passed against the production containers, including
MCP → REST → PostgreSQL writes, compact search, exact recall, resource and prompt
retrieval, dashboard proxy edits, conflicting versions, lifecycle filtering,
cross-project rejection, and deletion. A separate real Docker stdio client
initialized successfully, discovered all seven tools available at that revision,
and listed projects through the API. Container restarts preserved the database contents.

In-browser verification covered:

- First-project creation and project-ID copying.
- Project switching, open/completed filters, and search by a stored tag.
- Full prompt viewing and exact clipboard preservation, including Unicode,
  trailing spaces, and newlines before and after an edit.
- Immutable originating session display.
- An external edit arriving while a browser draft was open: the stale save was
  rejected, the draft stayed intact, and explicit reconciliation preserved both
  the browser's title change and the other session's summary change.
- Canceling deletion and confirming deletion of a synthetic record.
- Usable narrow and desktop layouts, with no horizontal desktop overflow.

Temporary verification projects and records were removed from the application
after testing. Normal startup does not insert demonstration data.

## Backup and restore drill

A real custom-format backup containing two test projects and five test hand-offs
was restored into a new isolated database in the disposable PostgreSQL test
container. Every stored field matched the archive: prompt text, provenance,
metadata, tags, lifecycle, versions, and timestamps. Restored API recall/search,
full-text search, both GIN indexes, and soft-delete isolation worked.

Five invalid restore attempts (missing confirmation, path traversal, absolute
path, wrong extension, and missing file) failed without changing the empty
test database. A damaged archive with a readable table of contents failed during
data restoration; the single transaction rolled back and preserved all prior
test data. The isolated database and copied test files were then removed.

## Boundaries not claimed as validated

The actual Claude Code and OpenCode applications were not configured globally
or launched. Their configuration examples were checked against official docs,
and the underlying MCP transports were exercised with the official SDK.
ChatGPT cloud access, OAuth, public hosting, multi-user authorization, semantic
embedding recall, automatic capture hooks, and an off-machine backup destination
are outside this MVP. See operations guidance before any remote deployment.
