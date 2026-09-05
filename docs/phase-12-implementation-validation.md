# Phase 12 implementation validation

Recorded on 2026-09-05 for application/API/MCP/dashboard `0.8.0`, plugin
`0.11.0`, and Alembic `0021_job_completion_reports`. The implementation was
rebased onto `8887740`, retaining the upstream parallel-CI, legacy completion
history, and definitive unsealed-episode refusal fixes. All database and browser writes used disposable test environments;
no production data was read, migrated, restored, or deployed.

## Completed checks

- Backend: **1,204 passed**, no skips, against PostgreSQL 17 with Python 3.14.6
  and four independent pytest workers. This includes populated migrations,
  legacy shapes and exact historical receipt recovery, database guards and
  parity, all three report closeouts, settings conflicts, human dismissal and
  follow-up provenance, allocator races, deadlines, audit corruption/ACL cases,
  and the combined maximum report/evidence/escaped-checkpoint envelope.
- Backend Ruff and whole-source `ty` passed. Operational-script Ruff checks
  include the new activity audit and historical acceptance fixture helper.
- MCP: **809 passed**, no skips, including the authentic isolated Claude CLI
  fresh `0.11.0` install and `0.10.0 -> 0.11.0` update, exact cached payload and
  executable mode, all 32 tool contracts, resource/prompt behavior, bounded
  malformed-wire handling, report/evidence serialization, and historical retry
  behavior. MCP Ruff and whole-source `ty` passed.
- Dashboard: **301 unit tests passed** under Node 24; typecheck passed. The
  optimized Next.js production build also passed in the disposable acceptance
  stacks. No application key is exposed in the browser bundle or responses.
- Configured Playwright stack: **127 passed, 4 conditional skips**, no failures.
  Desktop and narrow Chromium exercise all Phase 12 flows; Firefox covers the
  existing motion/live-update acceptance lane. The four unchanged skips apply
  only to desktop filter/divider interactions that do not exist in the narrow
  stacked layout. The full run includes the delayed-prompt closeout regression
  and waits for completed receipt recovery before checking preserved drafts.
- Plugin helper: **71 unittest cases and 108 subtests passed**. All three skill
  validators passed; the catalog remains three skills and five shared references.
- The real REST/MCP/dashboard stack checker passed its authenticated lifecycle,
  exact 32-tool/11-write catalog, scope/capability boundaries, settings and
  activity reads, all three authored closeouts, dismissal/replay, and Pending
  follow-up creation with both immutable provenance links. Its temporary stack
  was removed after completion.

Commands were run from the corresponding package directories, with separate
backend and MCP virtual environments. PostgreSQL tests used the disposable
`compose.test.yaml` service and independently named schemas:

```sh
# backend/
uv sync --frozen
uv run pytest -q -n 4
uv run ruff check .
uv run ty check src

# mcp/
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp

# frontend/ under Node 24
npm test
npm run typecheck
npm run build

# repository root under Node 24
bash scripts/test-e2e.sh
pre-commit run --all-files
```

The writable stack check supplied only explicit loopback URLs for a uniquely
named disposable Compose project, two synthetic project IDs, an inspected
commit declaration, and the selected `mcp/**` and `plugin/**` paths. Its API
key stayed in the subprocess environment. The check retained synthetic merge
and report history until that entire disposable stack was removed.

## Independent adversarial review

Two reviewers inspected the code independently of the implementation authors.
The [backend/MCP review](phase-12-cold-code-review-backend.md) and
[frontend review](phase-12-cold-code-review-frontend.md) preserve the original
findings and clearly attribute their closure evidence. Both final dispositions
are ACCEPT. A second independent reviewer also reran the actual restore script
against a new disposable database, verified the preserved report/head and fresh
stream ID, obtained a clean audit, and confirmed both old activity and report
cursors returned `activity_stream_changed`.

Corrections include atomic restore incarnation rotation, audit detection of a
missing report and transition witness, a genuine distinct-work allocator race,
strict malformed cursor handling, report-only draft guards, retained source
context refresh, per-view failed-read retry, unrelated-action retry isolation,
closeout buttons waiting for their loaded prompt revision, and schema-scoped
index-definition evaluation during parallel catalog checks. The final small
prompt-loading correction received an additional cold-review acceptance.

## Performance, restoration, and documentation

[Database performance and restore evidence](phase-12-performance-and-restore-evidence.md)
records 1,000/100,000 activity histories, 10/100 simultaneous writers, 100,000
report inboxes, mostly dismissed and fully dismissed history, bounded query
plans, response bytes, lock timing, hardware, and the actual supported restore
rehearsal. Measurements use local authenticated TestClient requests and tmpfs
PostgreSQL; they are not production latency guarantees.

The current [activity/report contract](project-activity-and-reports.md),
[operations guide](operations.md), API contract, OpenAPI snapshot, agent/plugin
workflows, release inventories, and fresh completion examples were updated.
The three dashboard screenshots use deliberately synthetic font-decision data.

Production-target backup rehearsal, maintenance scheduling, writer quiescence,
coordinated rollout, and post-migration audit remain the explicit operator
procedure. Repository validation does not assert that this cutover happened.
