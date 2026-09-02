# Development and validation

The Python services are independent packages with separate `pyproject.toml` and
`uv.lock` files. Do not combine their environments: the API and MCP SDK can
require different Starlette versions. Docker builds use frozen lockfiles. The
dashboard uses `package-lock.json` and `npm ci`.

Use Python 3.13, uv, and Node 24 for native development. Docker-only users do
not need these tools to run Mnemonic.

## Phases 5–8 backend verification

The database suite needs a real PostgreSQL instance because the system depends
on PostgreSQL search, row locking, database time, receipt reservation/waiting,
triggers, and Alembic behavior. Start the isolated
test database from the repository root:

```sh
docker compose -f compose.test.yaml up -d --wait
```

It listens only on `127.0.0.1:55432`, stores its data in a disposable tmpfs, and
does not share the working application's database. Then run from `backend`:

```sh
uv sync --frozen
export TEST_DATABASE_URL=postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test
uv run pytest -q
uv run ruff check .
```

In PowerShell, replace the export with:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test'
```

The retained Phase 5 backend coverage verifies:

- the `0004` expansion and populated `0005` backfill, including exact legacy
  text/provenance parity, preserved IDs, migration markers, and frozen legacy
  tables;
- the `0006` contract boundary and `0007` lease schema, including model parity,
  constraints, and indexes after physical legacy-table removal;
- the `0008` typed-relationship schema, composite project scoping, normalized
  identity, context ownership, one-parent constraint, and lookup indexes;
- `0009` ready-order/normalized-tag indexes and one-statement ready pages,
  including blocker/lease/filter/order/pagination parity with fresh claims;
- populated `0009 -> 0010` upgrade/backfill/downgrade, ORM model parity, lease
  generations/release markers, exact conservative counts/order, actor fallback,
  typed references, metadata checks, source/deferred guards, and immutability;
- atomic work-plus-initial-checkpoint creation, pointer-only grouped search,
  literal and full-text retrieval, optional hybrid search, and cache invalidation;
- identity-only work reads, bounded context assembly, deterministic checkpoint
  pagination, newest-context selection, and terminal clarification checkpoints;
- append-only checkpoint enforcement in both the API and database, concurrent
  appenders, and checkpoint appends that do not consume the work version;
- version-protected identity/lifecycle edits, typed application errors, atomic
  completion checkpoints, default-Pending filtering, human-only deferral, and
  soft deletion;
- exclusive concurrent acquisition, identical-request replay, expiry takeover,
  renewal/release precision, safe public projections, and claim-and-context
  atomicity;
- lease-token enforcement and removal on completion, retirement, promotion,
  and deletion, without changing work version/activity during lease operations;
- five relationship types, idempotent add/remove, project-serialized cycle and
  parent checks, atomic initial links, blocker-driven readiness/claimability,
  active-plus-blocked recovery, and relationship-protected deletion;
- pointer-only immediate adjacency and exact directional counts in bounded
  recall, plus root/child hierarchy filtering and flat-search breadcrumbs;
- atomic event emission for every canonical mutation, replay/no-op suppression,
  rollback fault injection, deterministic endpoint events, and actor provenance;
- progress-only public append, work-before-lease locking, monotonic activity,
  exact text/bounds, request-known secret rejection, and capability-free errors;
- one-statement event pages in both orders with exact filtered totals and the
  unfiltered partial-history flag, plus chronological bounded events in recall;
- event update/delete rejection at PostgreSQL, soft-delete read isolation, and
  direct REST omission recorded honestly as unattributed history;
- cross-project isolation, hostile/unknown input rejection, and
  pagination/filter totals.

The Phase 6 additions verify:

- `0013_idempotent_mutations` chaining from
  `0012_pending_deferred_statuses`, including fresh installs and populated
  upgrades that
  preserve existing work/event content and legacy metadata, model/DDL parity,
  the request-only progress-metadata boundary, and guarded downgrade behavior;
- the private `client_operations` receipt invariants, exact
  `(project_id, client_operation_id)` uniqueness, salted canonical request
  fingerprints, completed-row immutability, permanent response snapshots, and
  absence of resource foreign keys or public receipt lookup;
- reservation/finalization rollback, bounded waiter timeouts, concurrent
  same-key execution, mismatch conflicts, and exact replay before current
  work/relationship/lease guards;
- first execution and historical replay across all ten registered REST operations,
  including natural true/false no-ops, later edit/reopen/delete/remove/lease
  replacement, and no duplicate work, checkpoints, relationships, leases, or
  domain events;
- optional keyed REST versus unprotected keyless REST, keyed actor requirements,
  secret/control echo rejection, sanitized stable errors, response-model and
  size failure rollback, and strict exclusion of project/claim/renew writes;
- outcome-aware live invalidation: applied originals and applied replays publish
  a data-free refresh hint, while no-ops and failures do not.

The combined Phases 7–8 additions verify:

- `0014_human_gates` fresh and populated upgrades, exact Phase 1–6 row/function
  preservation, ORM/DDL parity, gate/event immutability and deferred
  completeness, fail-closed lease/work triggers, the exact twelve-kind receipt
  check, guarded empty downgrade, populated refusal, and downgrade/write races;
- atomic request/resolution and exact receipt replay under commit/response/
  rendering faults, the disabled-by-default first-execution fence, multiple
  gates, immutable current/resolved revision anchors, drift acknowledgement,
  sanitized errors, source-event coherence, and no duplicate durable effect;
- waiting readiness/lifecycle matrices, active capability replay/renew/release
  after gating, fresh/replacement claim rejection, completion/terminal/delete
  guards, and deterministic request/resolve races;
- immutable-cursor attention and gate-history pages, bounded context gate slices
  with exact omission counts, soft-deleted decision audit, and typed event
  history; and
- one-statement hierarchy pages with exact branch presentation counts,
  subtree-aware filter flags, discovery labels, cycle/depth bounds, pagination,
  current database-time lease facts, and measured query plans.

For a focused Phases 7–8 iteration, run the real gate/migration/readiness suites;
the complete `pytest` command above remains the release gate:

```sh
uv run pytest -q \
  tests/test_phase78_migration_postgres.py \
  tests/test_human_gates_postgres.py \
  tests/test_ready_work_postgres.py \
  tests/test_work_items_postgres.py
```

For a focused Phase 6 iteration, run the receipt suites explicitly; the complete
`pytest` command above remains the release gate:

```sh
uv run pytest -q \
  tests/test_client_operations.py \
  tests/test_client_operations_postgres.py \
  tests/test_idempotent_mutations_postgres.py \
  tests/test_phase6_live_sync.py \
  tests/test_phase6_migration_postgres.py
```

Tests create an isolated random schema, apply the real Alembic chain, and remove
the schema afterward. Semantic cases use a deterministic local test embedder and
do not contact a model service.

Without `TEST_DATABASE_URL`, PostgreSQL tests explicitly skip. Such a run still
checks pure validation helpers but is not proof of migration, transaction,
trigger, concurrency, receipt replay, or PostgreSQL retrieval behavior. Any
skipped PostgreSQL-marked test makes the Phases 7–8 release gate incomplete.

Stop the disposable database afterward from the repository root:

```sh
docker compose -f compose.test.yaml down
```

## Phases 5–8 MCP verification

Run from `mcp`:

```sh
uv sync --frozen
uv run pytest -q
```

The MCP suite verifies the exact 25-tool canonical catalog, strict unknown-field
rejection, nested checkpoint request bodies, pointer-only search/ready results,
bounded recall, deterministic checkpoint/event pagination, versioned mutation
receipts, typed graph and lease behavior, the `resume_work` prompt, and the
work-item resource across direct, Streamable HTTP, and real stdio transports.

Exactly ten mutation tools require a canonical `client_operation_id` and
advertise truthful idempotency: `create_work`, `add_checkpoint`, `append_event`,
`add_relationship`, `update_work`, `complete_work`, `delete_work`,
`remove_relationship`, `release_claim`, and `request_human_input`. Tests prove
exact one-attempt forwarding, strict coherent response decoding, sanitized
same-key recovery guidance, and local rejection on excluded tools. Project
creation, claim, claim-and-recall, and renewal retain separate non-idempotent
contracts.

Phase 7–8 assertions cover exact request arguments and revision projections,
the disabled-request error, unknown-outcome guidance, cursor-safe
`list_human_attention` and `list_work_gates`, waiting readiness and bounded gate
slices in search/ready/context/resource/prompt models, and sanitized gate errors.
They assert that no MCP resolution tool exists and that tool descriptions tell
agents not to infer or self-supply an answer. HTTP and stdio expose the same 25
names; `get_activity`, `resolve_human_input`, and removed hand-off surfaces
remain absent.

The MCP package currently does not declare Ruff in its own development group.

The inner plugin manifest is `0.5.0`. Before release, parse the marketplace
and inner plugin manifests, then exercise a disposable fresh `0.5.0` install
plus a sequential `0.4.0 -> 0.5.0` marketplace update. Use an isolated
`CLAUDE_CONFIG_DIR`; a marketplace refresh alone does not prove that the cached
installed skill bytes changed, and a compatibility copy of the old prerelease
tool schema is not a valid substitute.

After syncing `backend`, run the repository's available Ruff binary from the
repository root over the MCP and live-check code:

```sh
uv run --project backend ruff check mcp/src/mnemonic_mcp mcp/tests scripts/check-stack.py
```

## Phases 5–8 dashboard verification

Run from `frontend`:

```sh
npm ci
npm test
npm run typecheck
npm run build
```

The Node tests cover canonical work/root/child query construction, compact
recall pointers, checkpoint normalization, Ready/Active/Blocked formatting,
expiry refresh scheduling, relationship labels/direction previews, graph
conflict/depth helpers, sanitized typed errors, same-origin/host enforcement,
and the retained Phase 5 proxy route boundary. Event tests cover strict runtime
decoding,
deterministic labels and relationship direction, attributed/unattributed/
backfilled states, safe text rendering, newest-page reset after live
invalidation, actor request construction, progress composer errors, pagination/
filtering, and the partial-history notice at desktop and narrow viewports. The
ready endpoint remains intentionally proxy-denied.

Mutation tests cover all ten browser writes, one UUID and exact frozen serialized
body per intent, in-flight coalescing, same-document recovery after component or
view unmount, exact manual retry after ambiguous outcomes, and a non-discardable
safety state for key conflicts. Strict per-operation decoders require the
expected status, exact shape, and path/result coherence before clearing
recovery. Proxy-policy tests admit the top-level UUID only on those ten routes,
including gate resolution, and reject invalid, nested, query/header/cookie,
secret-equal, and excluded-route IDs without echoing them. Gate creation, lease
paths, and token-bearing browser mutations remain denied.

Phase 7–8 tests additionally cover attention count/cursor paging, literal
question/answer rendering, one-snapshot reviewed revisions, drift rejection and
new-intent preparation, gate detail/history slices and omission counts, waiting
terminal-action guards, live convergence, default-collapsed branches, exact
hierarchy count/discovery labels, filter-hidden explanations, child paging,
depth/cycle fallbacks, keyboard/ARIA behavior, and passive descendant lease-
expiry refresh. No gate question, answer, UUID, or frozen body enters browser
storage.

`typecheck` verifies component and API model alignment; the production build
catches server/client boundary and asset issues. Backend and Playwright suites
cover subtree retention, breadcrumbs, lazy expansion, relationship-editor
behavior, and the event UI.

### Automated browser acceptance

Install the pinned Chromium build once on a development or CI host:

```sh
npx playwright install --with-deps chromium
```

From `frontend`, run the complete isolated acceptance path with:

```sh
npm run test:e2e:stack
```

The wrapper generates a uniquely scoped Compose project, API key, and available
loopback ports. Its disposable API explicitly enables human-gate requests; the
production Compose default remains fenced. It builds API and dashboard images,
runs PostgreSQL on tmpfs, and exercises both desktop and narrow Chromium
layouts. On success, failure, or
interruption it tears down that exact generated project with volumes and orphan
containers included; it never targets the working application stack. The
scoped teardown shape is:

```sh
docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$repo_root/compose.e2e.yaml" down -v --remove-orphans
```

`npm run test:e2e` runs Playwright only. Use it when the same disposable stack
is already running and `MNEMONIC_E2E_WEB_URL`, `MNEMONIC_E2E_API_URL`, and
`MNEMONIC_E2E_API_KEY` point to that stack; it does not provision or tear down
services itself.

For native dashboard development, keep the container API running, stop the
`web` container, and create an untracked `frontend/.env.local` containing:

```text
MNEMONIC_API_URL=http://127.0.0.1:8000
MNEMONIC_API_KEY=<the same private key as .env>
```

Run `npm run dev`. The default allowed origins accept localhost and 127.0.0.1
on port 3000; update the configured origins when changing the port. Browser
requests always use `/api/mnemonic`; the upstream API address and bearer key
remain server-only.

## Full running-stack check

After starting current images with:

```sh
docker compose up --build -d --wait
```

Run the read-only live check from the repository root with the MCP environment:

```sh
uv run --project mcp python scripts/check-stack.py
```

Read-only mode verifies REST/MCP health, authentication, the exact 25-tool
catalog, the exact ten protected schemas and annotations, the absence of an MCP
resolution tool, REST-backed project listing, the dashboard proxy's host/origin
boundary, server-side key isolation, and the shipped WOFF2 font assets. It does
not create, gate, edit, relate, claim, append events, resolve, complete, or
delete records.

Writes require the explicit `--project-id` opt-in and an API with human-gate
requests enabled. Run this only against a disposable stack: set
`MNEMONIC_HUMAN_GATE_REQUESTS_ENABLED=true` for its API, while the production
Compose default remains false. Use a dedicated validation project whose contents
may safely include five synthetic, soft-deleted records:

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID
```

The write path performs the combined canonical lifecycle:

1. prepares and retains one UUID plus exact arguments for every protected call,
   deliberately discards the first create result, and recovers the original IDs
   through exact same-key replay without duplicate events;
2. proves pointer-only search, bounded recall, resource/prompt behavior, and
   request-known credential/capability rejection;
3. claims one candidate, discards a completed `request_human_input` response,
   and recovers the same unresolved gate through exact MCP replay;
4. proves active-plus-waiting context, text-free attention count, attention and
   gate-history reads, hierarchy gate aggregate, ready/fresh-claim exclusion,
   and preserved active claim replay/renew/release;
5. resolves through the dashboard proxy, repeats the frozen UUID/body, and
   proves byte-equivalent historical result plus attention/history/context/
   event/readiness convergence with no MCP resolution tool;
6. exercises blocker and lease exclusions, exact relationship/release/removal
   replays, natural false no-op replay, and deterministic ready ordering;
7. creates a child/discovered item with both relationships atomically and
   verifies direct-parent ready filtering plus hierarchy branch presentation;
8. completes/reopens work, verifies immutable event history, removes graph
   edges, resolves any interrupted run-owned gate during cleanup, then
   soft-deletes all five synthetic records and confirms ordinary reads return
   `404`.

The checker prints its synthetic run UUID before its first write. Retain that
line until cleanup succeeds. If the process is interrupted, recover only that
run's exact marker data with:

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID --cleanup-run-id PRINTED_RUN_UUID
```

Cleanup retains each protected body and UUID through one exact retry after an
ambiguous transport or server response. If an interrupted run lost an active
lease token, wait for the lease to expire before running cleanup recovery.

The script registers cleanup as soon as the run marker exists. Its `finally`
path recovers exact synthetic IDs by marker plus exact synthetic session
provenance when a response may have been lost. It removes only relationships
created by that run and then deletes only marker-bearing records. Soft deletion
remains in database history; the script never deletes the project or touches
unrelated work.

Add `--other-project-id` to prove the new ID cannot be read through a second
project. Do not pass either project option without authorization to write in the
named project. Prefer a disposable full stack for automated write-path checks.

## Phases 7–8 release gate

The combined human-oversight release is incomplete until all of these pass
together:

1. the full backend suite against isolated PostgreSQL with no database skips,
   then backend Ruff;
2. the full MCP suite in its separate frozen environment, then repository Ruff
   for MCP and `scripts/check-stack.py`;
3. frontend unit tests, typecheck, production build, and the isolated Playwright
   stack whose disposable API enables gate requests;
4. fresh head plus populated `0013_idempotent_mutations -> 0014_human_gates`
   preservation, exact legacy validator/non-gate event parity, guarded empty
   downgrade/re-upgrade, populated downgrade refusal/race, and old-backend
   fail-closed claim/terminal probes;
5. backup/restore with resolved and unresolved gates, attention sequence,
   paired events, and exact request/resolution receipt replay without another
   domain effect;
6. representative hierarchy `EXPLAIN (ANALYZE, BUFFERS)` fixtures proving one
   statement per page with exact branch aggregate/filter behavior;
7. plugin manifest parsing plus disposable fresh `0.5.0` and sequential
   `0.4.0 -> 0.5.0` installation;
8. read-only and authorized writable stack checks against a disposable project,
   including the exact 25-tool/ten-protected contract and gate lifecycle; and
9. `git diff --check`, repository Markdown link/path validation, schema/tool
   snapshots, and cold adversarial review.

Do not treat a focused suite, PostgreSQL-skipped run, read-only smoke, stale
Phase 6 validation count, or marketplace refresh by itself as a Phases 7–8
release result. Do not enable gate requests in a serving pool until backend,
MCP/plugin, dashboard/proxy, and operational rollback guidance are deployed
together.

## Manual Phases 5–8 browser pass

Exercise project empty state and switching, root browsing, lazy child expansion,
subtree-aware filters, flat-search breadcrumbs, Pending/Active/Dropped/Deferred
lifecycle filters,
lexical search and explicit Semantic opt-in, work selection, bounded context,
grouped pointer-only relationships, checkpoint timeline, immutable activity
timeline paging, progress-event creation, prompt copy, identity editing,
checkpoint creation, completion, deletion, and stale-version recovery. At a
narrow viewport, confirm lists, hierarchy, detail panes, editors, dialogs, long
IDs, and defensive depth/cycle fallbacks remain usable.

Add and remove every relationship type through the editor with exact stored
direction and truthful provenance. Confirm duplicate adds are harmless and the
editor prevents self-links or invalid context selection. Use REST or MCP in the
test project to verify cycle, second-parent, missing-context, and invalid-context
requests return sanitized actionable errors. Confirm work with active
relationships cannot be deleted. A nonmatching ancestor should
remain visible only when a matching descendant needs navigation scaffolding.

Use an API or MCP client—not the browser—to claim a visible work item, then add
an unresolved incoming blocker. Confirm lifecycle, `Active`, and `Blocked` stay
distinct; the retained lease remains visible through safe holder/timestamps but
no token appears in browser state or network payloads. Release it through the
client, verify new claims remain blocked, complete the blocker, and verify
readiness recovers. Confirm no claim or force-release UI exists.

With the request fence enabled only in the disposable environment, use MCP to
request human input on claimed work. Confirm `Active`, `Blocked`, and `Waiting`
remain independent, waiting wins only the display label, the item leaves ready
and rejects a fresh claim, and exact active claim replay/renew/release still
work. Verify Needs Attention count/paging, literal question rendering,
breadcrumbs, detail and event history, and an inclusive hierarchy gate count.
Confirm the browser cannot create a gate and MCP exposes no resolution tool.

Resolve through the dashboard. When context has changed, review the focused
one-snapshot context and submit its exact revision with acknowledgement; mutate
again before submit and confirm `gate_context_changed` forces a new review and
UUID. Interrupt a resolution response and retry only the frozen body/key.
Confirm one resolution/event, immediate queue removal, ready recovery only when
lease/blocker/lifecycle also permit it, retained paired history, and no gate
text or mutation material in browser storage or data-free live frames.

Expand planned and discovered branches and verify collapsed-by-default display,
direct/descendant and blocked/active/completed/discovered counts, discovery
labels, gate counts, filter-hidden explanations, child paging, defensive
cycle/depth fallbacks, and passive refresh when a descendant lease expires.

With a nonblank search, Semantic starts disabled. Enabling it performs a hybrid
request; disabling it restores lexical retrieval. Repeat the enabled query once
to exercise cache reuse. Never leave synthetic relationships or pending work in a
user's project after manual verification.

For the Phase 6 mutation pass, interrupt one of each browser action after
sending and before accepting its response. Confirm the same-document dashboard
retains the frozen call across modal closure/component unmount, blocks
intersecting actions, warns before unloading, and retries the identical method,
path, UUID, and serialized body. Verify a coherent original or replayed response
reconciles current state and heals live invalidation, while malformed `2xx`,
`5xx`, and transport loss remain unresolved and an exact-call key conflict
remains blocked for investigation. Finally, reload an unresolved document and
confirm the UI makes no recovery promise: no UUID or body was written to local
storage, session storage, cookies, a URL, or a header, and a replacement UUID is
not presented as a safe retry.
