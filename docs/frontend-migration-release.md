# Frontend migration parity and release

This checklist records parity verification for migrations 1–8: FastAPI owns the
browser API, Vite builds the static React dashboard, TanStack Router owns routes,
and TanStack Query owns decoded server reads. The existing mutation registry and
resource-specific executors still own frozen writes, operation UUIDs and uncertain retries.
No Query mutation path replaces them.

Work: `8a86b2b9-c001-49ec-87b0-2bc414cea9a9` in project
`d906cd7c-880c-45c3-8b3d-f5668d0b94fd`. The validation baseline is
`d78c2f78eeb651ead14d779eb72a63c9c1b34e7c` (application `0.83.8`, Alembic
`0051_priority_rubrics`). The release candidate is application/API/MCP/dashboard
`0.83.9`; plugin `0.49.0` and the schema head are unchanged. This verification
also corrects a browser request-cancellation defect found during acceptance.
It adds no database migration or browser feature. Release preparation does not
authorize production replacement or reload.

## Acceptance matrix

Bare `*.spec.ts` filenames are under `frontend/tests/e2e/`, bare `*.test.mjs`
filenames under `frontend/tests/`, and bare `test_*.py` filenames under
`backend/tests/`. Other paths are repository-relative. The matrix maps required
behavior to retained executable coverage; observed results and their limits
follow separately.

| Boundary | Required behavior | Coverage |
| --- | --- | --- |
| Browser admission | Closed method/path/query inventory; exact Host/Origin/fetch metadata; caller credentials and controls cannot gain machine or worker authority | `backend/tests/test_browser_policy.py`, `test_browser_boundary.py`, `test_browser_artifacts.py`, `test_browser_transcripts.py`, `test_browser_backups.py`; `browser-transport-ingress.spec.ts` |
| Bodies and responses | Inclusive JSON/raw limits, streamed/chunked bounds, safe error responses, coding rejection before body parsing, exact evidence identity marker | `backend/tests/test_browser_responses.py`, `test_browser_file_transport.py`, `test_browser_backups_stream.py`; `scripts/test-nginx-e2e.sh` |
| Cancellation and cleanup | A timed-out/disconnected read keeps its session until synchronous native work returns, then releases its transaction; asynchronous cleanup runs even when the default thread pool is full | `backend/tests/test_browser_session_lifetime.py`, `test_browser_boundary.py`, `test_browser_file_transport.py` |
| Protected writes | Lost or malformed committed responses keep the frozen body and UUID; exact manual retry recovers once; conflict scope and navigation locks survive route changes | `frontend/tests/mutation-intent.test.mjs`, `mutation-responses.test.mjs`, `mutation-scope.test.mjs`, `work-mutation-queries.test.mjs`; `phase6-idempotent-mutations.spec.ts`, `work-detail-query.spec.ts` |
| Work actions | Create/edit/checkpoint, move, relationships/merge, gates, completion reports and review decisions retain domain guards and recovery | `phase1-work-checkpoints.spec.ts`, `phase3-work-graph.spec.ts`, `phase7-8-human-gates.spec.ts`, `phase9-duplicate-handling.spec.ts`, `phase11-completion-evidence.spec.ts`, `phase12-project-activity.spec.ts`, `code-reviews.spec.ts`, `dashboard-actions.spec.ts` |
| Query identity and freshness | Project/filter changes abort obsolete reads; late responses stay isolated; background reads preserve visible pages and editor baselines; pagination follows server cursors/offsets | `frontend/tests/*queries.test.mjs`, `work-fresh-reads.test.mjs`, `search-pagination.test.mjs`; `core-query.spec.ts`, `work-detail-query.spec.ts`, `work-library-surface.spec.ts` |
| Live state | WebSocket reconnect, activity catch-up, focus refresh and lease expiry converge without overwriting drafts or repeating mutations | `frontend/tests/live-sync.test.mjs`, `project-activity.test.mjs`, `lease-refresh.test.mjs`; `phase12-project-activity.spec.ts`, `phase2-lease-visibility.spec.ts`, `dashboard-refresh-motion.spec.ts` |
| Artifacts | Revision/text-hash pins, sensitivity, semantic inference admission, coverage, sorting, replacement/deletion and raw upload/download/preview remain distinct | `artifacts.spec.ts`, `artifact-links-sensitive.spec.ts`, `artifact-semantic.spec.ts`, `artifact-search-surface.spec.ts`, `artifact-sort.spec.ts`, `artifact-preview.spec.ts`; browser artifact and file-transport backend suites |
| Transcripts | Metadata/text/segments retain locators and coverage; imports, settings and rebuilds keep exact uncertain requests; health polling and downloads remain bounded | `transcripts.spec.ts`; `frontend/tests/transcript-queries.test.mjs`, `transcript-segments.test.mjs`, `transcript-coverage.test.mjs`; browser transcript backend suites |
| Prompts and secondary views | Prompt/rubric drafts keep reviewed revisions; action rendering is fresh; attention/report counts and lists reconcile; review views retain selection | `prompt-library.spec.ts`, `priority-rubric.spec.ts`, `tasks-dashboard.spec.ts`, `code-review-surface.spec.ts`, `phase12-project-activity.spec.ts`; `frontend/tests/secondary-queries.test.mjs`, `rendered-prompt-queries.test.mjs` |
| Backups | Catalog/job polling, create/download/restore, project isolation, compressed/expanded limits, uncertain outcomes and operation locks remain intact | `project-backups.spec.ts`; `scripts/test_project_backups.py`; browser backup backend suites |
| Routes and presentation | Direct loads/refresh, saved links/repeated parameters, Back/Forward and missing routes; theme, reduced motion, scroll, narrow layout and keyboard controls | `vite-routing.spec.ts`, `sidebar-navigation.spec.ts`, `settings-navigation.spec.ts`, `resources-navigation.spec.ts`, `dashboard-preferences.spec.ts`, `theme-preference.spec.ts`, `dark-theme-contrast.spec.ts`, `dashboard-refresh-motion.spec.ts`, `work-library-surface.spec.ts` |
| Static delivery | Runtime public overrides; secret-free image without Node; real API routing and sync upgrade; private-path rejection; security headers; HTML/assets cache separation | `frontend/tests/runtime-config.test.mjs`, `vite-boundary.test.mjs`; `vite-routing.spec.ts`, `browser-transport-ingress.spec.ts`; `scripts/test-nginx-e2e.sh` |

The complete Playwright runner exercises desktop and narrow Chromium plus the
configured Firefox motion/navigation subset. Tests that use controlled responses
prove client/transport contracts, not external model performance. Existing
keyboard cases are targeted controls rather than a complete accessibility audit.

## Recorded validation

Initial baseline on 2026-10-16 used Node `24.22.0`: frozen frontend installation,
all **545** Node tests, TypeScript checking and the production build passed.
Vite reported JavaScript **1,095.43 kB / 329.03 kB gzip** and CSS
**153.95 kB / 29.32 kB gzip**. Its existing warning for a chunk above 500 kB
remains. One build reported 335 ms; this is a local observation, not a latency
target or a browser performance comparison.

Final Node 24 validation passed **541** tests with zero failures/skips, the frozen
install, TypeScript and build. Four tests for unused pre-Query pagination helpers
were removed with those helpers; `work-queries.test.mjs` retains behavior coverage
for uneven page boundaries, retained prefixes, failed appends and cancellation.
No active Next dependency, proxy or second JavaScript server remains. Bootstrap,
activity/WebSocket delivery and raw transfers are the documented non-Query reads.

Representative parity captures were inspected from the full browser run:
[desktop dark](images/frontend-migration-parity-desktop.png) and
[narrow light](images/frontend-migration-parity-narrow.png). The bundle sizes are
unchanged from the baseline; final build time was 637 ms. HTML, JavaScript and CSS
were byte-identical in the baseline browser image, final local build and the
retained candidate web image. This provides parity evidence, not a claimed speedup.

The evidence below separates complete browser invocations from focused reruns and
the standalone backup harness. It does not claim one green 411-case invocation.

- The complete browser run after the cleanup correction recorded **405 passed,
  5 intentionally skipped narrow-layout cases, 1 failed** in 19.8 minutes.
  The skips cover desktop-only sidebar, filter and divider interactions.
  Its sole failure was the sidebar navigation observer being installed during
  the initial live-ready project-catalog refresh, which temporarily disables
  the project picker. The same disabled behavior existed before Query at
  `02ec05f^`. The trace contained no mutations, and all seven recorded observations
  preceded the first navigation settling. The test now holds and releases that
  actual paginated catalog refetch before installing its unchanged reset observer;
  a controlled WebSocket ready frame orders bootstrap deterministically. The test
  still checks a single live connection, persistent sidebar, no document navigation
  and no resets. Dedicated integration tests retain real WebSocket coverage.
  No UI behavior changed.
- The entire `sidebar-navigation.spec.ts` then passed three repeats across desktop
  Chromium, narrow Chromium and the configured Firefox project: **33 passed,
  3 intentional narrow-layout skips, zero failures**, in 59.4 seconds.
  Only test setup changed after the complete run; production code and browser
  assets were unchanged. This resolves the remaining failed case without
  reclassifying the preceding complete invocation as successful.
- The first full browser run recorded
  **403 passed, 5 skipped, 3 failed** in 21.7 minutes. Two failures were the old
  Phase 5 expectation that failed activity refresh empties the view; migration 6
  intentionally retains Query snapshots. The adapted case now checks the exact
  cached head, correct offset/count, absence of the unobserved event, and recovery
  with the new event and cleared alert. The third was a Chromium `Target crashed`
  while opening an attention page. All four affected desktop/narrow cases passed
  on focused rerun and the corrected complete run, with no product assertion
  change for the crash. Browser temporary storage is now on the disk-backed scratch.
- The second full browser run recorded **402 passed, 5 skipped, 4 failed** in
  19.5 minutes. All four failures were API fixture HTTP 503 responses before UI
  assertions: three desktop work-library cases clustered with transaction lock
  timeouts; one narrow dashboard-action case coincided with a confirmed scheduled
  backup deadlock. They were retained as failures and investigated rather than
  weakened into accepted UI outcomes.
- That investigation deterministically reproduced a migration regression:
  `asyncio.Task.cancel()` could close the request's SQLAlchemy session while a
  synchronous handler was still running. The handler could reopen a transaction
  after cleanup and retain its lock. JSON and file transports now cancel an AnyIO
  scope, preserving the native worker's cancellation shield until it returns.
  The request session dependency performs shielded cleanup in a separate thread
  capacity limiter so an exhausted default worker pool cannot prevent rollback.
  **144** focused tests passed, including six new cleanup cases covering both
  transports, timeouts/disconnects, actual PostgreSQL lock release and exhausted
  default worker capacity. The corrected complete browser result is recorded above.
- In **270 point-in-time database snapshots across nine separate 30-second
  windows**, no open transaction was older than one second or blocked. The
  monitor included active and idle-in-transaction sessions, excluded its own
  connection and had gaps between windows; it does not prove continuous absence
  of contention or a maximum transaction duration. The observations are retained
  in `/tmp/mnemonic-final-fixed-lock-observations.log`. The corrected browser run
  had no fixture 503 or lock-timeout failures, and its reviewed logs showed no
  early-session-cleanup diagnostics. PostgreSQL regressions independently prove
  lock release after native work returns; the recovered backup deadlock below
  remains part of the record.
- The earlier four-case focused runner's backup phases exposed `backup_database_busy`
  response during restore while the shared worker remained active. The harness
  now permits at most three identical-byte attempts only for that exact HTTP 409
  rollback result, verifying unchanged target/other project responses before
  retry. Transport uncertainty and every other failure still stop immediately;
  final restored-data checks remain. Fourteen focused regressions verify these
  boundaries. The separate backup-only harness passed all six phases with exit
  status zero, recorded in `/tmp/mnemonic-final-backup-only.log`: authentication
  and API isolation; download/retention/concurrency; storage-permission recovery;
  invalid restore inputs and limits; actual PostgreSQL restoration with another
  project preserved; and lock-timeout retry recovery. During the corrected full
  browser run, the shared worker's existing automatic retry recovered one backup
  deadlock on attempt two; all 195 backup jobs in the follow-up snapshot had succeeded.
- Before the cleanup fix, outer nginx acceptance passed with `TIMEZONE=UTC` and
  summary limit `384`, and with empty overrides resolved to
  `America/Detroit` / `2048`. The corrected source passed the complete custom
  `UTC` / `384` run and the empty-override `America/Detroit` / `2048` run again,
  both with exit status zero.
  Actual asset/runtime bytes excluded the isolated API and backup credentials;
  runtime JSON exposed exactly four public fields. Deep links, private-route
  rejection, security/cache headers, WebSocket upgrade, early raw-stream/upload
  delivery, real worker backup/restore, active Brotli isolation, and inclusive
  3 MiB response / 1 MiB completion limits passed. The harness exports defaults
  so local `.env` files cannot disagree with its test oracle.
- MCP: **2,156** passed, zero skipped; **78** transport/version/OpenAPI/stack-check
  cases passed again after the metadata bump. Backend/MCP Ruff and ty passed,
  as did **72** repository-freshness tests (including installed Linux helpers),
  operational-script Ruff and pre-commit/Gitleaks.
- Before the cleanup fix, the backend full run passed **4,917** tests with zero
  failures/skips in 315.08 seconds with real PostgreSQL/RabbitMQ, including the
  fourteen new harness regressions. The full run after the cleanup fix passed
  **4,923 tests, zero failures/skips**, in 318.30 seconds, including all six new
  session-lifetime regressions.
  Two preceding runs each had **4,902** passes
  and one capacity-test failure. Diagnostic instrumentation confirmed real
  `EDQUOT` (disk quota exceeded) from `os.write` staging the 200 MiB transcript
  under `/tmp`, despite reported free space. The same case passed twice in
  isolation. The successful full rerun used a new disk-backed temporary directory;
  the capacity test's assertions were unchanged.
- Diff and obsolete-path audits passed. The PR must pass the aggregate
  `Required checks` before merge; its final result and exact head belong in the
  associated work completion record.

Run frontend checks from `frontend` with Node 24, then run both isolated harnesses
from the repository root:

```sh
npm ci --no-audit --no-fund
npm test
npm run typecheck
npm run build
```

```sh
./scripts/test-e2e.sh
./scripts/test-nginx-e2e.sh
```

These harnesses create uniquely named test projects with disposable databases,
private scratch storage and random credentials. Their cleanup targets only that
test project. A skipped PostgreSQL suite is not full backend validation. Preserve
test failures and rerun evidence honestly; a successful retry does not erase the
first failure. Check the PR's aggregate `Required checks` before merging.

## Configuration and rollout boundary

Browser traffic follows optional host TLS nginx → static `web:3000` →
`api:8000`. Only `/api/mnemonic`, `/api/artifacts`, `/api/transcripts` and
`/api/backups` are forwarded; only `/api/mnemonic/sync` upgrades to WebSocket.
The API calls the private backup worker. MCP remains on its separate private
listener and the host's `/mcp` route.

| Setting | Owner and release check |
| --- | --- |
| `TIMEZONE`, `MNEMONIC_WORK_SUMMARY_MAX_CHARS`, `MNEMONIC_ARTIFACT_MAX_BYTES`, `MNEMONIC_BACKUP_MAX_BYTES` | API exposes only their four public runtime fields; apply API configuration and reload tabs to read changed settings. No web asset rebuild is needed. Keep worker backup limits and the edge restore limit consistent. |
| `MNEMONIC_DASHBOARD_ORIGINS` | API exact browser origin allowlist, including a customized local web port. Forwarded headers do not grant trust. |
| `MNEMONIC_TLS_HOST` | `compose.tls.yaml` adds the public origin/MCP host. The host nginx file still needs the same literal hostname and certificate paths. |
| `MNEMONIC_WEB_PORT`, `MNEMONIC_API_PORT`, `MNEMONIC_MCP_PORT` | Preserve loopback bindings and match host `proxy_pass` addresses. |
| `MNEMONIC_API_KEY`, `MNEMONIC_BACKUP_TOKEN`, `MNEMONIC_BACKUP_URL`, database/broker/storage settings | Private service configuration only; retain existing values and mounts. The static web image receives none of these. |
| `MNEMONIC_DEV_API_URL` | Local Vite development proxy only. Never place private settings in `VITE_*`. |

For an installation already on a compatible API/schema, a static dashboard
change normally replaces web and, when its browser boundary/configuration
changed, API. API replacement briefly interrupts browser reads/writes,
WebSockets and API-backed MCP calls; web replacement briefly interrupts dashboard
delivery. Host nginx configuration changes need a separately authorized reload.
Running workers and active agent sessions make even a short API interruption an
operator decision.

The inspected installation is not assumed to meet that narrow case. On
2026-10-16 its API reported package `0.82.1` while the database reported
`0051_priority_rubrics`. The `0.83.0` prompt release requires coordinated
API/MCP/dashboard/skill compatibility. Reconcile the running code, worker,
installed client skills and schema before choosing affected services. Do not
replace only API/web or call an old image a safe rollback merely from its
package version or the presence of a schema head.

## Operator preparation, before any interruption

1. Identify the exact running Compose project, working directory and ordered
   overlay list from container labels. Retain every active overlay, including
   local transcript mounts and image overrides; the repository's base/TLS pair
   alone may not reproduce production. Record service image IDs, image references,
   code/package versions, published ports and database Alembic revision. Read
   only selected inspection fields; do not print full container environments or
   expanded `docker compose config` containing secrets.
2. Create a private release record outside Git, with directory mode 0700 and
   file mode 0600. Save exact Compose files and `.env`, host nginx configuration
   and included snippets, their SHA-256 hashes, and the prior image references
   plus immutable IDs. Preserve custom TLS/ACL/Basic-auth/log settings. Keep
   certificate/key locations rather than copying secrets into the repository.
   Record the prior HTML asset URLs and the services approved for replacement.
3. Retain previous images by local rollback tags or image archives; verify each
   tag resolves to its recorded ID. Build candidate images from the reviewed
   commit without starting them against production. Match configured API UID/GID
   when building backend images. Save candidate image IDs, Compose hashes, tested
   commit and test results beside the prior record. Do not prune either set.
4. Compare both candidate and rollback code/schema requirements with the actual
   database. API startup runs `alembic upgrade head`. If revisions or required
   coordinated services differ, use the applicable migration runbook, rehearsed
   backups/restores and a revised interruption scope. Never test an old API
   against production to discover compatibility, and never issue an Alembic
   downgrade as a frontend rollback. The absence of a new migration in this PR
   does not certify a historical image against the current schema.
5. Validate the exact candidate configuration with `docker compose ... config
   --quiet`, using the recorded project directory, environment file, project name
   and complete ordered overlay list. Rehearse on an isolated stack. Verify origin,
   runtime limits, static routes, raw transfers and old/new browser behavior.
   If compatibility cannot be demonstrated, prepare a fix forward or an explicit
   coordinated restore; do not label the previous deployment rollback-ready.
6. Request authorization naming the exact services/configuration to replace,
   expected interruption, treatment of active work and old tabs, smoke checks,
   rollback trigger and verified recovery choice. No build, test, PR approval or
   work-item closeout substitutes for production authorization.

Use the actual recorded project identifiers for read-only inspection. For
example, after assigning `production_project` from the existing container label:

```sh
docker ps --filter "label=com.docker.compose.project=$production_project" \
  --format '{{.ID}} {{.Names}} {{.Image}}'
docker inspect --format '{{.Name}} {{.Image}} {{index .Config.Labels "com.docker.compose.project.working_dir"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  "$production_api_container" "$production_web_container"
```

The read-only inspection record is
`/tmp/mnemonic-final-release-manifest.json` (mode 0600), created on 2026-10-16.
It records immutable running image IDs and SHA-256 hashes for the six ordered
Compose files, `.env`, host `nginx.conf`, the Mnemonic virtual host and its API
policy snippet. It contains no secret environment values. These hashes identify
the observed configuration; they are not configuration backups. Move the record
to durable private storage and capture the actual files before any cutover.
The observed Compose order is `compose.yaml`, `compose.tls.yaml`,
`compose.transcripts.yaml`, `compose.ws01.yaml`, `compose.codex-native.yaml`, then
`compose.claim-fix-images.yaml`. The last four are installation-specific; use
their saved bytes rather than substituting repository examples.

The corrected complete browser run retained these candidate images locally. Its
405/5/1 result, focused reruns and separate backup verification are recorded above:

| Service | Local reference | Immutable image ID |
| --- | --- | --- |
| Web | `mnemonic-migration-release-web:0.83.9` | `sha256:e5375b3331aa711e6dcf1ab51a9b08ba59d8c70073b2c2f1ba5a89ee00dfafde` |
| API | `mnemonic-migration-release-api:0.83.9` | `sha256:e772ab303a46706abd0f7bb99fc8507288afd4900365c65d0848413794c22545` |

The separate corrected nginx build used web image
`sha256:7676db99b3fcb2f0f60b55b1ec885b628b323e5a7400937bbf62cfe4084630ac`
and API image
`sha256:6fb2314e7b5d7f118487812e0372bdbeac1c086b234615fdbc18ad618d69385b`.
The completed custom run is recorded in `/tmp/mnemonic-final-nginx-fixed.log`;
the completed empty-override default run is recorded in
`/tmp/mnemonic-final-nginx-fixed-defaults.log`. The latter built web image
`sha256:b24571344f844b94b347ff6f4209bc9beb1995512a7a4e48a6c7a06e2ae1263e`
and API image
`sha256:7f7efa06f1a4ce5268bb630b02622e6cf8998d7bde34f1713e26cf7bdfda9fd6`.
Those images were built from the same corrected source. The post-fix asset hash
comparison used the retained candidate browser web image listed above; image IDs
are recorded separately rather than treating different builds as the same artifact.

The corrected nginx candidate was tested with this PR's `compose.e2e.yaml`,
`compose.nginx-e2e.yaml` and `scripts/test-nginx-e2e.sh`: unique loopback ports,
random temporary credentials, isolated PostgreSQL/RabbitMQ/Tika/worker,
`UTC` / summary limit `384` and empty overrides resolving to
`America/Detroit` / `2048`, artifact limit `67108864`, backup limit `2097152`,
and the stock plus Brotli-enabled nginx fixtures. This is a test configuration, not a
production override. Candidate API changes from the baseline include the JSON/file
cancellation and session-cleanup correction above. Rebuild from the merged commit
with the approved production UID/GID
and record new IDs if those build arguments differ.

The tested `0.83.8` source baseline also has locally retained fallback images:

| Service | Local reference | Immutable image ID |
| --- | --- | --- |
| Web | `mnemonic-migration-rollback-web:0.83.8-d78c2f7` | `sha256:02ef43fc9ada780085468795efaefdc5a7dfdf55b053b092b04474537b8199dc` |
| API | `mnemonic-migration-rollback-api:0.83.8-d78c2f7` | `sha256:9ee8dcee41ecf1bf96bf98284a5714e3c9146db868c886477822442374fba3f4` |

Their matching source configuration is `compose.yaml`, `compose.tls.yaml`,
`compose.e2e.yaml`, and `compose.nginx-e2e.yaml` at the immutable baseline commit
`d78c2f78eeb651ead14d779eb72a63c9c1b34e7c`, together with that commit's frontend
nginx files and shared policy snippets. The baseline isolated browser run used
`America/Detroit`, summary limit `2048`, artifact limit `67108864` and backup
limit `2097152`; its test runner generated its own loopback origin and credentials.

These are the migration-7 baseline on `0051_priority_rubrics`, not the older
production `0.82.1` images. They already contain the static dashboard and Query
migration and remain compatible with the unchanged schema and their saved source
configuration. However, the fallback API contains the confirmed request-session
cancellation defect: rolling back to it reintroduces the lock-retention risk.
Prefer a fix forward when that defect is the rollback trigger; this pair is not
a defect-free recovery choice. It does not restore the former Next topology.
Preserve matching reviewed Compose files
and coordinated API/MCP/worker compatibility with the release record; local tags
alone do not make a deployable recovery plan. A return to pre-migration behavior
still needs its own demonstrated code/schema compatibility or a separately
authorized coordinated restore.

## Authorized cutover and smoke checks

The following is a command shape, not a command to run before authorization.
Set `production_dir` and `production_project` to the verified existing values,
and `candidate_overlay` to the absolute path of the prepared image-only overlay.
That final overlay pins each affected service to its retained candidate image.
Confirm the tags still resolve to the approved IDs. Set the Bash array
`approved_services` to precisely the authorized compatible set; it may include
MCP/worker for a coordinated release. For the observed six-file installation:

```sh
set -euo pipefail
test "${#approved_services[@]}" -gt 0
release_compose=(docker compose
  --project-directory "$production_dir"
  --env-file "$production_dir/.env"
  --project-name "$production_project"
  -f "$production_dir/compose.yaml"
  -f "$production_dir/compose.tls.yaml"
  -f "$production_dir/compose.transcripts.yaml"
  -f "$production_dir/compose.ws01.yaml"
  -f "$production_dir/compose.codex-native.yaml"
  -f "$production_dir/compose.claim-fix-images.yaml"
  -f "$candidate_overlay")
"${release_compose[@]}" config --quiet
"${release_compose[@]}" up -d --no-deps --no-build --pull never --wait "${approved_services[@]}"
```

`--no-deps` prevents an incidental dependency recreation. Never substitute an
unscoped `up`, `down`, `restart` or full-stack rebuild. If the authorization also
covers host nginx changes, install the prepared files with the recorded ownership
and permissions, run `nginx -t`, and reload only after that succeeds. Container
builds do not install host nginx files.

Before reopening normal use, verify service health, the configured HTTPS origin,
`/api/mnemonic/runtime-config` public fields, root and direct secondary routes,
current hashed asset responses, private/API/asset 404s, cache/security headers,
live sync and one read from each resource family. Verify MCP's authenticated
health through the existing client. Use a designated disposable project for any
authorized write/transfer smoke test. Record exact images, timings and outcomes;
do not infer successful protected writes from a healthy container.

## Old tabs, assets and rollback

Query data, drafts and pending protected intents are in document memory. There
is no persistent Query cache or service worker to migrate. An old tab continues
executing its loaded bundle after a release; a refresh loads the new HTML and
bootstrap configuration but discards that document's unresolved intents. Ask
operators to finish protected requests, resolve unknown outcomes using their
existing recovery controls, and preserve unsaved drafts before the cutover.
Do not force reload a tab with an unresolved mutation or generate a replacement
UUID to make it proceed.

HTML and unfingerprinted public files use `Cache-Control: no-cache`; fingerprinted
`/assets/*` use one-year immutable caching. API responses remain no-store and
no-transform. Images contain their own HTML and assets; a replacement does not
keep the old image's asset files available. Do not purge fingerprinted browser
assets or mix HTML from one image with another image's asset directory. A missing
old asset must remain a 404, never SPA HTML. After resolving old tab state and
passing smoke checks, reload tabs deliberately. Runtime values are read at
document bootstrap, so already-open tabs do not automatically adopt a changed
runtime-config limit.

If an authorized rollback is triggered, first apply the same old-tab and
unknown-outcome rules. Verify the saved recovery images remain schema-compatible,
restore their exact Compose/environment/host-configuration pair, pin the recorded
image IDs, and replace only the authorized services using the same scoped command
shape. Syntax-check any restored nginx files before an authorized reload. Repeat
the smoke checks against the recovered images and reload resolved tabs. Never
roll back data or discard operation receipts to repair a frontend problem. If
the old code cannot run on the retained schema, use the prepared fix forward or
separately authorized full restore instead of starting it.

Production cutover and rollback were not executed as part of this verification.

## Human-authored README handoff

The root `README.md` is unchanged. Suggested replacement architecture wording for
its owner:

> The dashboard is a Vite-built React SPA using TanStack Router and TanStack Query.
> An unprivileged static nginx image serves it; FastAPI owns the same-origin
> browser API. See `docs/frontend-delivery.md` for development and
> `docs/frontend-migration-release.md` for validation and operator rollout/rollback.
