# Development and validation

The Python services are independent packages with separate `pyproject.toml` and
`uv.lock` files. Do not combine their environments: the API and MCP SDK can
require different Starlette versions. Docker builds use frozen lockfiles. The
dashboard uses `package-lock.json` and `npm ci`.

Use Python 3.13, uv, and Node 24 for native development. Docker-only users do
not need these tools to run Mnemonic.

## Phase 3 backend verification

The database suite needs a real PostgreSQL instance because Phases 1 through 3
depend on PostgreSQL search, row locking, database time, triggers, and Alembic
behavior. Start the isolated
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

The Phase 3 backend suite verifies:

- the `0004` expansion and populated `0005` backfill, including exact legacy
  text/provenance parity, preserved IDs, migration markers, and frozen legacy
  tables;
- the `0006` contract boundary and `0007` lease schema, including model parity,
  constraints, indexes, and compatibility aliases after physical legacy-table
  removal;
- the `0008` typed-relationship schema, composite project scoping, normalized
  identity, context ownership, one-parent constraint, and lookup indexes;
- atomic work-plus-initial-checkpoint creation, pointer-only grouped search,
  literal and full-text retrieval, optional hybrid search, and cache invalidation;
- identity-only work reads, bounded context assembly, deterministic checkpoint
  pagination, newest-context selection, and terminal clarification checkpoints;
- append-only checkpoint enforcement in both the API and database, concurrent
  appenders, and checkpoint appends that do not consume the work version;
- version-protected identity/lifecycle edits, typed application errors, atomic
  completion checkpoints, default-open filtering, and soft deletion;
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
- cross-project isolation, hostile/unknown input rejection, pagination/filter
  totals, and deprecated handoff projections backed by canonical rows.

Tests create an isolated random schema, apply the real Alembic chain, and remove
the schema afterward. Semantic cases use a deterministic local test embedder and
do not contact a model service.

Without `TEST_DATABASE_URL`, PostgreSQL tests explicitly skip. Such a run still
checks pure validation helpers but is not proof of migration, transaction,
trigger, concurrency, or PostgreSQL retrieval behavior.

Stop the disposable database afterward from the repository root:

```sh
docker compose -f compose.test.yaml down
```

## Phase 3 MCP verification

Run from `mcp`:

```sh
uv sync --frozen
uv run pytest -q
```

The MCP suite verifies the exact canonical-plus-compatibility catalog, nested
checkpoint request bodies, strict response shapes, pointer-only search, bounded
recall, deterministic checkpoint pagination, versioned update/completion/delete
receipts, atomic initial-relationship serialization, relationship add/get/list/
remove tools, and the `resume_work` prompt and work-item resource. It also
exercises claim/replay/renew/release body serialization and annotations, token
redaction, claim-specific unknown-outcome recovery, pointer-only counterparts,
typed graph errors, strict unknown-field rejection, and sanitized local
validation across direct, Streamable HTTP, and real stdio transports. REST
boundary, generic unknown write outcomes, project scoping, host/origin/key
checks, and body limits are covered with an HTTP mock and need no live database.

The MCP package currently does not declare Ruff in its own development group.
After syncing `backend`, run the repository's available Ruff binary from the
repository root over the MCP and live-check code:

```sh
uv run --project backend ruff check mcp/src/mnemonic_mcp mcp/tests scripts/check-stack.py
```

## Phase 3 dashboard verification

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
and the exact Phase 3 proxy allowlist. Mutation-policy tests prove lease paths
and token-bearing browser mutations are rejected rather than stripped or
forwarded while relationship and hierarchy routes remain narrowly admitted.
Backend and Playwright suites cover subtree retention, breadcrumbs, lazy
expansion, and relationship-editor behavior.

`typecheck` verifies component and API model alignment; the production build
catches server/client boundary and asset issues.

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
loopback ports. It builds API and dashboard images, runs PostgreSQL on tmpfs,
and exercises both desktop and narrow Chromium layouts. On success, failure, or
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

run the read-only live check from the repository root with the MCP environment:

```sh
uv run --project mcp python scripts/check-stack.py
```

Read-only mode verifies REST/MCP health, authentication, the exact 27-tool Phase
3 MCP catalog, REST-backed project listing, the dashboard proxy's host/origin
boundary, server-side key isolation, and the shipped WOFF2 font assets. It does
not create, edit, relate, claim, complete, or delete records.

Writes require the explicit `--project-id` opt-in. Use a dedicated validation
project whose contents may safely include three synthetic, soft-deleted records:

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID
```

The write path performs the canonical Phase 3 lifecycle:

1. creates uniquely marked work and its initial checkpoint atomically;
2. finds one compact pointer-only result through search;
3. recalls bounded context and checks the resource and `resume_work` prompt;
4. appends and pages a second immutable checkpoint without changing version;
5. edits through the proxy and proves a stale edit returns `version_conflict`;
6. claims and recalls atomically, then replays the exact request without
   extending expiry or exposing request ID/token through ordinary reads;
7. adds an incoming blocker, proves readiness and claim eligibility change,
   releases the retained active claim, removes the blocker, and claims again;
8. creates a child/discovered item with both relationships in its creation
   transaction and verifies pointer-only adjacency plus hierarchy navigation;
9. completes the primary work with a completion checkpoint and lease token;
10. verifies default-open filtering plus deprecated ID/resource/tool aliases;
11. removes every synthetic relationship before soft-deleting all synthetic
    work, then confirms canonical and compatibility reads return `404`.

The script registers cleanup as soon as the run marker exists. Its `finally`
path recovers exact synthetic IDs by marker plus exact synthetic session
provenance when a response may have been lost. It removes only relationships
created by that run and then deletes only marker-bearing records. Soft deletion
remains in database history; the script never deletes the project or touches
unrelated work.

Add `--other-project-id` to prove the new ID cannot be read through a second
project. Do not pass either project option without authorization to write in the
named project. Prefer a disposable full stack for automated write-path checks.

## Manual Phase 3 browser pass

Exercise project empty state and switching, root browsing, lazy child expansion,
subtree-aware filters, flat-search breadcrumbs, open/all lifecycle filters,
lexical search and explicit Semantic opt-in, work selection, bounded context,
grouped pointer-only relationships, checkpoint timeline, prompt copy, identity
editing, checkpoint creation, completion, deletion, and stale-version recovery.
At a narrow viewport, confirm lists, hierarchy, detail panes, editors, dialogs,
long IDs, and defensive depth/cycle fallbacks remain usable.

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

With a nonblank search, Semantic starts disabled. Enabling it performs a hybrid
request; disabling it restores lexical retrieval. Repeat the enabled query once
to exercise cache reuse. Never leave synthetic relationships or open work in a
user's project after manual verification.
