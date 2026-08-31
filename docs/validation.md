# MVP validation record

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
