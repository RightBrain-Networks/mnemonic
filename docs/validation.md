# Mnemonic validation record

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
