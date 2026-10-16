# Static dashboard delivery

The dashboard is a React SPA built with Vite and routed by TanStack Router.
The web image runs nginx as an unprivileged user on port 3000. Node 24 is used
only for building and development. This release changes no database schema,
MCP tool, protected mutation protocol, or machine REST contract.

## Local development

Start an isolated API and its dependencies, then run from `frontend`:

```sh
npm ci --no-audit --no-fund
MNEMONIC_DEV_API_URL=http://127.0.0.1:8000 npm run dev
npm test
npm run typecheck
npm run build
```

Vite binds to `127.0.0.1:3000` and keeps the browser Host while proxying the four
browser API namespaces to `MNEMONIC_DEV_API_URL`. The API must admit that exact
browser origin in `MNEMONIC_DASHBOARD_ORIGINS`. WebSocket upgrades use only
`/api/mnemonic/sync`. Do not put private keys, worker tokens or service addresses
in `VITE_*` variables: those variables are eligible for browser bundling.
`npm run preview` is a local asset preview, not a production application server.
Use the isolated Compose stack to validate full delivery and backend behavior.

## Runtime and route ownership

`main.tsx` loads `/api/mnemonic/runtime-config` without caching, validates its four
public fields, and mounts the router only after success. Loading/error/retry
states keep dependent forms unavailable until timezone and limits are known.
The API owns deployment-time values. The web container has no application
credentials or worker address and serves the same built bytes across deployments.

`router.tsx` has a shared dashboard layout and eleven thin leaf routes. That
layout owns the dashboard, mutation registry, preferences and live connection
through sibling navigation. `DashboardLink` and router navigation cover sidebar,
programmatic and browser history transitions; pending actions block before a new
route commits. Internal selection changes replace only the URL without adding
history entries or discarding pending intents. Query parsing preserves string
values and repeated parameters; legacy `/?work=...` links redirect to `/work-items`.
TanStack Query owns decoded dashboard, selected-work, resource and secondary
server reads. Runtime configuration, activity/WebSocket delivery and raw byte
streams retain their transport-specific owners. See
[frontend shared patterns](frontend-shared-patterns.md#core-server-reads-with-tanstack-query-migration-5)
for resource ownership and mutation reconciliation.

Theme, application preferences and library expansion scripts are injected before
paint from the existing shared definitions. The favicon and fonts are static
public assets. The security policy preserves the existing frame/object/form
restrictions and permits these pre-paint scripts.

## Web and edge configuration

The static nginx server proxies only `/api/mnemonic`, `/api/artifacts`,
`/api/transcripts` and `/api/backups`, preserving external Host and Origin.
It clears forwarding headers, disables transfer buffering/compression and proxy
retries, allows backend-enforced body limits, and retains large metadata headers.
Backup idle timeouts exceed the API's 30-minute operation deadline. The optional
outer TLS nginx forwards to this same web listener and retains its port topology.

Known document paths serve the SPA on direct loads and refreshes. Retired
`/settings`, unknown pages, private namespaces, and absent API/assets receive
404s. Hashed build assets are immutable; HTML is revalidated and API responses
remain no-store/no-transform. `/healthz` checks the static listener. The image
runs read-only with a writable temporary directory, without a Node executable.

## Verification ownership

The obsolete Next route handlers and server proxy implementations are removed.
Their policy tests now belong to FastAPI: `test_browser_policy.py` retains the
captured JSON parity cases and route inventory; boundary/response/mutation tests
cover origin, controls, limits, uncertainty and receipts. Artifact, transcript,
backup and shared file-transport suites cover raw bytes, safe headers, disabled
storage, revision pins, cancellation and backpressure. Frontend tests retain
response codecs, frozen mutations, recovery, preferences and URL behavior.

`test-e2e.sh` builds the real web image with disposable services and data.
`test-nginx-e2e.sh` adds the outer edge and wire probes for identity coding,
streamed limits, WebSocket upgrade, private routes, caching, and image credentials.
Both legacy `--browser-transports` flags now select the same default path.

Visual checks captured from the isolated static-web acceptance stack:
[desktop dashboard](images/frontend-vite/dashboard-desktop.png),
[narrow dashboard](images/frontend-vite/dashboard-narrow.png), and
[runtime settings failure](images/frontend-vite/runtime-error-narrow.png).
The failure screenshot uses an intercepted configuration response; the dashboard
screenshots use the seeded project with no data changes.

## Deployment and rollback

Build from the repository root with `docker build -f frontend/Dockerfile .`;
Compose uses that same context to include the shared nginx security snippets.

Use the [frontend migration release runbook](frontend-migration-release.md) to
record exact running images, matching configuration and schema compatibility
before requesting production authorization. API/web replacement and host nginx
reload each require explicit operator authorization. The API owns worker
URL/token and runtime values; the static web image has no private settings.
An API restart also interrupts MCP requests that use it, even if the MCP
container stays running.

Rollback restores a verified compatible image/configuration pair. A historical
Next/API pair is not automatically safe on a newer database: the API starts by
running Alembic, and older processes must never run against an incompatible
schema. This frontend verification adds no migration; that does not waive the
schema check when production is behind. Retain old tabs until protected writes
and unknown outcomes are resolved, then reload them deliberately after the
operator's checks. Receipt requests and their UUIDs must never be reconstructed
or regenerated during a rollout.
