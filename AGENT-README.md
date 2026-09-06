# Mnemonic installation handoff for an LLM

## Objective

Install and configure the current `main` branch of `mnemonic` for one user. Completion MUST
produce a healthy Docker Compose stack, a private persistent PostgreSQL database, a usable web
dashboard, a ready REST API, a ready Streamable HTTP MCP endpoint, and a verified backup service.
If the user's LLM client supports MCP, completion MUST also connect that client to `mnemonic` and
verify that it can list the server's tools or projects.

Use the local-only deployment unless the user explicitly requests LAN or remote access. Do not stop
after writing configuration: start the stack and perform the validation in this document.

## Vocabulary and observed current state

- `MNEMONIC_ROOT` means the absolute path of the cloned repository. Every repository command in
  this document MUST run from `MNEMONIC_ROOT` unless stated otherwise.
- The work graph is Mnemonic's durable store of project-placed work items, immutable
  checkpoints, events, leases, cross-project typed relationships, and human-decision gates.
- [`compose.yaml`](compose.yaml) is the authoritative local deployment. It defines `postgres`,
  `api`, `mcp`, `web`, and `backup`; the destructive `restore` service is disabled behind the
  `maintenance` profile. The `api` service runs database migrations during startup.
- [`.env.example`](.env.example) is the authoritative configuration-key inventory.
  [`scripts/setup.py`](scripts/setup.py) creates `.env` with two independent random secrets,
  refuses to replace an existing `.env`, and does not print either secret.
- [`docs/operations.md`](docs/operations.md) is authoritative for configuration, backup, upgrade,
  recovery, and security behavior. [`README.md`](README.md) is authoritative for supported client
  registration. [`examples/`](examples/) contains client configuration examples.
- The default dashboard, REST API documentation, and MCP endpoint are respectively
  `http://127.0.0.1:3000`, `http://127.0.0.1:8000/docs`, and
  `http://127.0.0.1:8001/mcp`. `localhost` is also allowed where documented.
- All published ports bind to `127.0.0.1`. PostgreSQL has no published host port. The dashboard
  keeps `MNEMONIC_API_KEY` server-side and proxies permitted browser operations to the API.
- Mnemonic is a single-user application. The dashboard has no application login. One bearer API
  key authorizes every API and MCP operation. Stored prompts and backups can contain sensitive
  user content.
- The first image build needs network access for GitHub, container images, application packages,
  and the bundled local embedding model. Semantic search runs locally after the image is built and
  does not require a hosted model API key.

## Desired state

- The expected GitHub repository exists at a known absolute `MNEMONIC_ROOT`.
- `MNEMONIC_ROOT/.env` is private, untracked, valid, and configured for the user's host.
- The default Compose project owns one persistent `mnemonic_data` volume and five healthy runtime
  services. The maintenance-only `restore` service is not running.
- Loopback health endpoints, the dashboard, database-backed API readiness, and startup backup all
  pass their observable checks.
- One supported MCP client is connected with a private bearer-key reference when such a client is
  available. A new installation remains empty until the user creates a real project.

## Scope

Included:

1. Verify host prerequisites and choose collision-free local ports.
2. Clone `https://github.com/RightBrain-Networks/mnemonic.git` or safely reuse an existing clone.
3. Create and configure a private `MNEMONIC_ROOT/.env`.
4. Build, start, and validate the complete default Compose stack.
5. Connect one available MCP client when the user identifies or already has that client.
6. Report exact non-secret configuration, validation results, and any remaining user action.

Out of scope unless the user explicitly requests it:

- source-code changes, development dependencies, test databases, or contributor setup;
- public Internet exposure, multi-user authentication, OAuth, or cloud deployment;
- restoring a backup, deleting Docker volumes, or rotating an initialized database password;
- importing fabricated example projects, work items, checkpoints, or session identifiers.

## Constraints and invariants

- The installation MUST use Docker with Linux containers and Docker Compose v2 (`docker compose`).
  It MUST NOT substitute `docker-compose` v1.
- `POSTGRES_PASSWORD` and `MNEMONIC_API_KEY` MUST be different cryptographically random values.
  `POSTGRES_PASSWORD` MUST contain only letters, digits, hyphens, or underscores because it is
  interpolated into a database URL. `MNEMONIC_API_KEY` MUST contain at least 32 ASCII characters
  without spaces. The repository helper generates 64-character hexadecimal values satisfying both
  rules.
- The installer MUST NOT change a populated installation's `POSTGRES_PASSWORD` only in `.env`.
  PostgreSQL does not update the initialized role from that change. Follow
  [`docs/operations.md`](docs/operations.md) for coordinated credential rotation.
- The default port bindings in [`compose.yaml`](compose.yaml) MUST remain on `127.0.0.1`. The
  installer MUST NOT change them to `0.0.0.0`, publish PostgreSQL, or bypass MCP Host/Origin checks.
- The default installation MUST use the complete compatible Compose stack. Database migration is
  automatic during `api` startup; no manual migration or compatibility layer is required for a
  new database.
- LAN access MUST use host-managed TLS and the checked-in nginx pattern.

## Negative requirements

- The installer MUST preserve an existing clone, `.env`, backup directory, and Docker volume. It
  MUST NOT overwrite local changes, print secrets, commit `.env`, or store an API key in a tracked
  client configuration.
- The installer MUST NOT run `docker compose down -v`, `docker volume rm`, the `restore` profile,
  or another command that can erase or replace stored work. Normal `stop`, `up`, and `down`
  without `-v` preserve the named database volume.
- The installer MUST NOT display expanded Compose configuration because it contains secrets. Use
  `docker compose config --quiet`, never plain `docker compose config`, for validation.
- The installer MUST NOT weaken health checks, authentication, origin allowlists, container
  security options, or service dependencies to make startup succeed. Diagnose the underlying
  configuration or runtime failure.
- Plain HTTP with a bearer key MUST NOT traverse a LAN or the public Internet.

## Decision rules

Apply these rules in order:

1. **Checkout:** If no checkout exists, clone into an empty `mnemonic` directory. If the target is
   already a Git checkout whose `origin` is the expected GitHub repository, preserve it. Update an
   existing clone with `git pull --ff-only` only when it is on `main` and has no local changes. If
   the target exists but is not that repository, select a different empty path; MUST NOT delete or
   repurpose the existing directory.
2. **Access mode:** Default to local-only Compose. If the user explicitly requests access from
   another machine, stop the local procedure only where it diverges and follow
   [`deploy/nginx/README.md`](deploy/nginx/README.md), including DNS, a trusted certificate,
   source-network restrictions, `MNEMONIC_TLS_HOST`, and both Compose files.
3. **Secrets:** Prefer `scripts/setup.py`. If Python 3.10 or newer is unavailable, copy
   `.env.example` to `.env` and use an operating-system cryptographic random generator. Never use
   a memorable password, a reused credential, a UUID alone, `$RANDOM`, or an LLM-generated value.
4. **Ports:** Keep `3000`, `8000`, and `8001` unless one is unavailable. On collision, choose an
   unused unprivileged port and update only the matching `.env` key. If `MNEMONIC_WEB_PORT`
   changes, also set `MNEMONIC_DASHBOARD_ORIGINS` to the exact comma-separated browser origins with
   the new port, normally both `http://localhost:<port>` and `http://127.0.0.1:<port>`.
5. **Other settings:** Use the user's IANA time-zone name for `TIMEZONE` when known; otherwise keep
   `America/Detroit`. Keep the lease duration, client-operation wait, backup interval, and backup
   directory defaults unless the user states an operational requirement.
6. **Client:** Prefer an already-installed client the user names. Use Streamable HTTP at
   `http://127.0.0.1:<MNEMONIC_MCP_PORT>/mcp` with
   `Authorization: Bearer <MNEMONIC_API_KEY>`. If no MCP-capable client is available, finish and
   validate the dashboard stack, then report client integration as the only unresolved item.

## Implementation plan

### 1. Verify prerequisites

Run:

```sh
git --version
docker --version
docker compose version
docker info
```

All four commands MUST succeed. Docker Desktop on Windows MUST be running Linux containers. If Git,
Docker, or Compose v2 is missing, obtain approval for host package, repository, account, or service
changes and follow the current official installation instructions for the detected operating
system. If the Docker daemon is unavailable, start Docker and repeat `docker info`; do not
continue to Compose startup. Missing administrator access is a blocker, not permission to use an
untrusted installer.

The repository specifies no minimum CPU, memory, or disk capacity. The installer MUST retain enough
free disk space for container images, the PostgreSQL volume, the local embedding model, and
non-pruned backups; report resource exhaustion instead of weakening the stack.

Python 3.10 or newer is recommended only for the safe settings helper and optional live-stack
checker. Host Node.js, `uv`, and a host PostgreSQL installation are not required to run Mnemonic.

### 2. Clone or select the repository

For a new installation:

```sh
git clone https://github.com/RightBrain-Networks/mnemonic.git
cd mnemonic
git remote get-url origin
git status --short --branch
```

`git remote get-url origin` MUST identify
`https://github.com/RightBrain-Networks/mnemonic.git` or the equivalent authenticated URL. Record
the resulting absolute directory as `MNEMONIC_ROOT`. The branch SHOULD be `main`, and a fresh
clone MUST have no tracked or untracked changes.

For an existing clone, run `git remote get-url origin` and `git status --short --branch` before
any update. Preserve all local changes. If the clone is clean and on `main`, update it with:

```sh
git pull --ff-only
```

If the existing clone is dirty, detached, or on another branch, do not switch, reset, clean, or
pull it. Ask the user whether to use its current revision or create a separate fresh clone.

### 3. Create `.env`

From `MNEMONIC_ROOT`, first test whether `.env` exists. For a new configuration on macOS or
Linux, run the first available Python command:

```sh
python3 scripts/setup.py
```

Use `python scripts/setup.py` where `python3` is not the Python 3 command. Expected output begins
with `Created .env with new local secrets.` If `.env` already exists, the helper reports that it
was left unchanged; this is required behavior, not an error.

When no suitable Python exists, copy `.env.example` to `.env`, create two independent secrets
with an OS cryptographic random generator, and insert them without printing them in chat or logs.
Apply owner-only permissions where the host supports POSIX modes:

```sh
chmod 600 .env
```

On Windows, the installer MUST instead verify that the file is protected by account-private
filesystem access controls.

Review `.env` locally without reproducing secret values in output. These keys MUST be present:

| Key | Required value or default decision |
| --- | --- |
| `POSTGRES_PASSWORD` | Required independent URL-safe random secret. |
| `MNEMONIC_API_KEY` | Required independent random ASCII secret, at least 32 characters, no spaces. |
| `MNEMONIC_WEB_PORT` | Dashboard host port; default `3000`. |
| `MNEMONIC_API_PORT` | REST/API-docs host port; default `8000`. |
| `MNEMONIC_MCP_PORT` | MCP host port; default `8001`. |
| `MNEMONIC_DASHBOARD_ORIGINS` | Exact dashboard browser origins; MUST match the web port. |
| `MNEMONIC_LEASE_TTL_SECONDS` | `60` through `3600`; default `900`. |
| `MNEMONIC_CLIENT_OPERATION_WAIT_SECONDS` | `1` through `10`; default `10`. |
| `MNEMONIC_BACKUP_DIR` | Private host directory; default `./backups`. |
| `MNEMONIC_BACKUP_INTERVAL_SECONDS` | Seconds between dumps; default `86400`. No automatic deletion. |
| `TIMEZONE` | IANA time-zone name used by the dashboard; default `America/Detroit`. |

`MNEMONIC_TLS_HOST` MUST remain commented or unset for local-only use. Before startup, ensure the
three selected host ports are free. Do not resolve a collision by stopping an unrelated service
unless the user explicitly authorizes that action.

### 4. Validate and start Compose

From `MNEMONIC_ROOT`, run:

```sh
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
```

`docker compose config --quiet` MUST exit zero without printing expanded configuration.
`docker compose up --build -d --wait` MUST exit zero. On first startup, image download/build and
the local embedding-model download can take several minutes. Compose MUST report `postgres`,
`api`, `mcp`, `web`, and `backup` running and healthy. The `restore` service MUST NOT run.

If startup fails, inspect bounded logs without changing configuration safeguards:

```sh
docker compose ps
docker compose logs --tail=100 postgres api mcp web backup
```

Correct the specific error, rerun `docker compose config --quiet`, and repeat
`docker compose up --build -d --wait`.

### 5. Validate the running application

Substitute the configured ports without revealing either secret:

```sh
curl --fail --silent --show-error http://127.0.0.1:8000/healthz
curl --fail --silent --show-error http://127.0.0.1:8000/readyz
curl --fail --silent --show-error http://127.0.0.1:8001/healthz
curl --fail --silent --show-error http://127.0.0.1:3000/healthz
```

Expected JSON is `{"status":"ok"}` for each `/healthz` endpoint and
`{"status":"ready"}` for the API `/readyz` endpoint. Replace `8000`, `8001`, and `3000`
with `MNEMONIC_API_PORT`, `MNEMONIC_MCP_PORT`, and `MNEMONIC_WEB_PORT` when customized.
PowerShell MAY use `Invoke-RestMethod` for the same URLs.

Open `http://127.0.0.1:<MNEMONIC_WEB_PORT>` in a browser. The page MUST load without an
application error. A new installation is intentionally empty. The user SHOULD create the first
real project in the project selector; the installer MUST NOT invent a project name, repository
URL, work item, or session ID merely to make the dashboard nonempty.

The `backup` service creates and verifies a PostgreSQL custom-format dump at startup and then at
the configured interval. Confirm that `docker compose ps` reports it healthy and that
`MNEMONIC_BACKUP_DIR` contains a new nonempty `.dump` file. Do not print or upload dump contents.

With `uv` installed, the following additional read-only check SHOULD pass:

```sh
uv run --project mcp python scripts/check-stack.py
```

Do not add `--project-id` unless the user authorizes writes to a disposable project; that option
creates synthetic durable history.

### 6. Connect the user's MCP client

Read `MNEMONIC_API_KEY` into the process environment without echoing it. On a POSIX shell,
sourcing the private file avoids copying the key into a tracked file:

```sh
set -a
. ./.env
set +a
```

For Claude Code, register the local HTTP endpoint, then restart Claude Code because MCP servers are
loaded at session startup:

```sh
claude mcp add --transport http --scope user mnemonic "http://127.0.0.1:$MNEMONIC_MCP_PORT/mcp" --header "Authorization: Bearer $MNEMONIC_API_KEY"
```

For the complete supported Claude Code workflow, register the local checkout as a plugin
marketplace and install the three Mnemonic skills:

```sh
claude plugin marketplace add "$PWD"
claude plugin install mnemonic@mnemonic
```

If the marketplace or plugin already exists, inspect the installed entry before changing it. The
plugin is copied into Claude's cache, so a repository update does not update an installed plugin;
follow the update commands in [`README.md`](README.md) after a new Mnemonic release.

For OpenCode, adapt [`examples/opencode.json`](examples/opencode.json) in the user's untracked or
user-level configuration. For another MCP client, adapt
[`examples/claude-code.mcp.json`](examples/claude-code.mcp.json) to that client's documented
Streamable HTTP schema. Client environment-variable interpolation syntax is client-specific and
MUST be verified; `${MNEMONIC_API_KEY}` is not universally expanded. The effective request MUST
send the bearer header, but the raw key MUST remain in a private user-level configuration,
environment, or secret store.

After client restart, verify that the client can discover Mnemonic tools and call `list_projects`.
An empty project list is valid on a new installation. HTTP `401` means the bearer key is absent
or wrong. HTTP `421` usually means the request Host is not allowed. Do not disable authentication
or host validation to resolve either response.

## TLS/LAN branch

Use this branch only after explicit user direction. The installer MUST obtain the real hostname,
DNS state, trusted certificate and private-key paths, allowed client subnet, nginx host details,
and desired dashboard authentication before changing the deployment. Then follow
[`deploy/nginx/README.md`](deploy/nginx/README.md) exactly.

At minimum, set the bare public hostname in `MNEMONIC_TLS_HOST`, update the literal nginx
hostname, certificate paths, network allowlist, checkout path, and any nondefault upstream ports,
and start with both Compose files:

```sh
docker compose -f compose.yaml -f compose.tls.yaml config --quiet
docker compose -f compose.yaml -f compose.tls.yaml up --build -d --wait
```

The same two `-f` arguments MUST be used for later operations. Keep all Compose bindings on
loopback. nginx and Docker MUST run on the same host. The installer MUST run `nginx -t` before an
nginx reload and MUST verify HTTPS without disabling certificate validation. A remote MCP client
MUST use `https://<MNEMONIC_TLS_HOST>/mcp` directly; it MUST NOT send its bearer key to an HTTP URL
and rely on a redirect.

## Failure guidance

| Symptom | Required diagnosis |
| --- | --- |
| Compose reports a required variable is empty | Run `scripts/setup.py` only if `.env` is absent; otherwise repair the existing private `.env` without replacing unrelated settings. |
| A published port is already allocated | Select a free host port in `.env`; if it is the web port, update both exact dashboard origins. |
| `api` is unhealthy | Inspect `postgres` and `api` logs first; API readiness includes a database query and migrations run before readiness. |
| `mcp` or `web` does not start | Establish API health first because both services depend on it. |
| Dashboard API requests return `403` | Make `MNEMONIC_DASHBOARD_ORIGINS` exactly match the browser scheme, hostname, and web port; recreate `api` and `web`. |
| MCP returns `401` | Correct the client's private bearer header from the same `.env`; never expose the key while debugging. |
| MCP returns `421` | Correct the request hostname or the documented TLS allowlist; do not use a wildcard. |
| Image build cannot download dependencies | Verify DNS, proxy, registry, and outbound network access; do not remove pinned dependencies or offline runtime safeguards. |
| `backup` is unhealthy | Inspect its bounded logs and directory permissions. A same-disk dump is not disaster recovery; copy verified dumps to protected storage. |

## Acceptance criteria

The task is complete only when all applicable statements are true:

- [ ] The expected GitHub repository exists at a known absolute `MNEMONIC_ROOT`; no preexisting
      user files or changes were overwritten.
- [ ] `MNEMONIC_ROOT/.env` exists, is private, contains distinct valid secrets, and contains the
      selected non-secret ports, exact dashboard origins, time zone, and backup settings.
- [ ] `docker compose config --quiet` and `docker compose up --build -d --wait` exit zero.
- [ ] `postgres`, `api`, `mcp`, `web`, and `backup` are running and healthy; `restore` is
      absent.
- [ ] API readiness returns `{"status":"ready"}`; API, MCP, and dashboard health endpoints return
      `{"status":"ok"}` on their configured loopback ports.
- [ ] The dashboard renders in a browser, and its project selector permits creation of the user's
      first real project.
- [ ] A new verified, nonempty `.dump` exists in `MNEMONIC_BACKUP_DIR`.
- [ ] If an MCP-capable client is in scope, its user-level configuration retains the key privately,
      survives restart, discovers the Mnemonic tools, and successfully calls `list_projects`.
- [ ] The user receives the dashboard URL, MCP URL, backup path, operational commands, and any
      unresolved conditional work, but no secret values.

## Handoff to the user

Report these commands for routine operation:

```sh
docker compose ps
docker compose logs --tail=100 api mcp web backup
docker compose stop
docker compose up -d --wait
```

State that normal stop/restart preserves data, `docker compose down -v` destroys the working data
volume, and backups are never automatically pruned. Provide the configured dashboard and MCP URLs
and the absolute backup directory. Do not include `POSTGRES_PASSWORD`, `MNEMONIC_API_KEY`,
expanded Compose output, or backup contents.

## Unresolved items

There are no unresolved product decisions for a new local installation when the documented
defaults and prerequisites are available. The installer MUST report each applicable conditional
item separately:

- Missing prerequisite installation requires operating-system details and authorization for
  privileged host changes.
- A nondefault client requires its exact private MCP configuration location and environment or
  secret interpolation rules.
- LAN/TLS requires the hostname, DNS state, certificate paths, nginx host details, source-network
  allowlist, and dashboard-authentication decision.
- An existing dirty checkout requires a decision to use its current revision or create a separate
  clone.
- An existing populated installation requires the upgrade or credential-rotation procedure in
  `docs/operations.md`; it MUST NOT be treated as a new installation.

## Current cross-project relationship release boundary

Application/API/MCP/dashboard 0.16.0, plugin 0.16.0 and Alembic
`0025_cross_project_relationships` ship together: 38 MCP tools, 13
receipt-protected MCP writes,
18 REST receipt kinds, 15 protected browser mutations, 24 event types and three
plugin skills. Existing projects default to Never/Never/off review settings;
do not infer historical review requests. Quiesce old writers, take a verified
backup, migrate, and deploy every coordinated surface together. Run both
read-only `scripts/audit_project_activity.py` and
`scripts/audit_code_reviews.py` at 0025; the activity audit also supports its
explicit historical-head preflights.

Migration 0025 gives relationship endpoints global identity while retaining the
creation project as immutable edge authority. It preserves incident edges when
work moves, records relationship events in the current project of each
endpoint, and keeps hierarchy presentation local to colocated endpoints.
Downgrade to 0024 is allowed only when every retained edge still has both
current endpoints in its immutable authority project and no immutable
relationship/dependency event history for an edge spans projects. Removing a
cross-project edge does not restore eligibility. Otherwise, fix forward or
restore the full pre-0025 backup. Further downgrade remains subject to the
code-review history guards. See
[operations](docs/operations.md#current-coordinated-cutover) for cutover details.

Migration 0023 introduced movement of one stable work-item identity between
projects without changing its lifecycle status. It leaves historical facts at
their original project and records paired `work_moved` activity in the source
and target. At current head 0025, relationships remain attached to that stable
identity and may span projects after a move. An active lease, unresolved gate,
duplicate membership or alias, or unsealed terminal history blocks a fresh move.
Move is a REST/dashboard action; review-policy/history or remediation ancestry also blocks a move in
this release. There is no MCP write or plugin skill for it. Permanent
source-scoped receipts remain the authority for exact unknown-outcome retries after the item has moved.
