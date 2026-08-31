# MVP validation record

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
initialized successfully, discovered all seven tools, and listed projects
through the API. Container restarts preserved the database contents.

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
