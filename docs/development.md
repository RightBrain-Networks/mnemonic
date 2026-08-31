# Development and validation

The Python services are independent packages with separate `pyproject.toml` and
`uv.lock` files. Do not combine their environments: the API and MCP SDK can
require different Starlette versions. Docker builds use frozen lockfiles. The
dashboard uses `package-lock.json` and `npm ci`.

Use Python 3.13, uv, and Node 24 for native development. Docker-only users do
not need these tools to run Mnemonic.

## Phase 1 backend verification

The database suite needs a real PostgreSQL instance because Phase 1 depends on
PostgreSQL search, locking, triggers, and Alembic behavior. Start the isolated
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

The Phase 1 backend suite verifies:

- the `0004` expansion and populated `0005` backfill, including exact legacy
  text/provenance parity, preserved IDs, migration markers, and frozen legacy
  tables;
- atomic work-plus-initial-checkpoint creation, pointer-only grouped search,
  literal and full-text retrieval, optional hybrid search, and cache invalidation;
- identity-only work reads, bounded context assembly, deterministic checkpoint
  pagination, newest-context selection, and terminal clarification checkpoints;
- append-only checkpoint enforcement in both the API and database, concurrent
  appenders, and checkpoint appends that do not consume the work version;
- version-protected identity/lifecycle edits, typed application errors, atomic
  completion checkpoints, default-open filtering, and soft deletion;
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

## Phase 1 MCP verification

Run from `mcp`:

```sh
uv sync --frozen
uv run pytest -q
```

The MCP suite verifies the exact canonical-plus-compatibility catalog, nested
checkpoint request bodies, strict response shapes, pointer-only search, bounded
recall, deterministic checkpoint pagination, versioned update/completion/delete
receipts, and the `resume_work` prompt and work-item resource. It also exercises
the REST HTTP boundary, sanitized typed and legacy errors, unknown write
outcomes, project scoping, host/origin/key checks, body limits, Streamable HTTP,
and a real stdio subprocess handshake. It uses an HTTP mock and needs no live
database.

The MCP package currently does not declare Ruff in its own development group.
After syncing `backend`, run the repository's available Ruff binary from the
repository root over the MCP and live-check code:

```sh
uv run --project backend ruff check mcp/src/mnemonic_mcp mcp/tests scripts/check-stack.py
```

## Phase 1 dashboard verification

Run from `frontend`:

```sh
npm ci
npm test
npm run typecheck
npm run build
```

The Node tests cover canonical work search parameters, compact recall pointers,
checkpoint display normalization, sanitized typed errors, same-origin/host
enforcement, and the exact Phase 1 proxy allowlist. Mutation-policy tests prove
that unknown mutation fields are rejected rather than silently stripped.
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

Read-only mode verifies REST/MCP health, authentication, the exact 19-tool Phase
1 MCP catalog, REST-backed project listing, the dashboard proxy's host/origin
boundary, server-side key isolation, and the shipped WOFF2 font assets. It does
not create, edit, complete, or delete records.

Writes require the explicit `--project-id` opt-in. Use a dedicated validation
project whose contents may safely include one synthetic, soft-deleted record:

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID
```

The write path performs only the Phase 1 lifecycle:

1. atomically creates one uniquely marked work item and initial checkpoint;
2. verifies one compact pointer-only search result and bounded recall;
3. checks the canonical resource and `resume_work` prompt;
4. appends and pages an immutable progress checkpoint without changing version;
5. edits through the dashboard proxy and proves a stale REST edit returns the
   typed `version_conflict` error;
6. completes with an atomic completion checkpoint;
7. proves default-open search excludes the completed item while `status=all`
   still finds it;
8. resolves the preserved ID through deprecated search/recall/timeline,
   resource, and prompt compatibility paths;
9. soft-deletes through the canonical action and checks both canonical and
   compatibility reads return `404`.

The script registers cleanup as soon as the run marker exists. Its `finally`
path deletes the exact returned work ID, or searches for the marker plus exact
synthetic session provenance if creation committed but its MCP response was
lost. Cleanup refuses records that lack the run's marker. Soft deletion remains
in database history by design; the script never deletes the project or touches
unrelated work.

Add `--other-project-id` to prove the new ID cannot be read through a second
project. Do not pass either project option without authorization to write in the
named project. Prefer a disposable full stack for automated write-path checks.

## Manual Phase 1 browser pass

Exercise the project empty state and switching, open/all lifecycle filters,
lexical search and explicit Semantic opt-in, work selection, bounded context,
checkpoint timeline, prompt copy, cancel/save identity edits, progress/context
checkpoint creation, completion with a completion checkpoint, deletion
confirmation, and stale-version recovery. At a narrow viewport, confirm lists,
detail panes, dialogs, and long IDs remain usable.

With a nonblank search, Semantic must start disabled. Enabling it should perform
a hybrid request; disabling it should restore lexical retrieval. Repeat the
enabled query once to exercise cache reuse. Never leave fabricated open work in
a user's project after manual verification.
