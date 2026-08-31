# Development and validation

The Python services are independent packages, each with its own `pyproject.toml`
and `uv.lock`. Do not combine their environments: the API and MCP SDK may require
different Starlette versions. Docker builds use frozen lockfiles. The dashboard
uses `package-lock.json` and `npm ci`.

Use Python 3.13, uv, and Node 24 for native development. Docker-only users do not
need these tools to run Mnemonic.

## API tests against real PostgreSQL

Start the separate disposable test database:

```sh
docker compose -f compose.test.yaml up -d --wait
```

It listens on loopback port 55432, stores data in a disposable tmpfs, and does
not expose or share the working application's database. From `backend`:

```sh
uv sync --frozen
export TEST_DATABASE_URL=postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test
uv run pytest -q
uv run ruff check .
```

In PowerShell replace the export line with:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test'
```

Tests apply the real Alembic migration in an isolated random schema, then remove
that schema. They cover authentication, project isolation, full-text ranking and
stemming, literal identifier/path matching, lifecycle filtering, pagination,
soft deletion, concurrent version checks, and hostile JSON validation. Without
`TEST_DATABASE_URL`, database integration tests explicitly skip; do not treat a
unit-only run as proof of PostgreSQL behavior.

Stop the disposable database after testing (from the repository root):

```sh
docker compose -f compose.test.yaml down
```

## MCP tests

From `mcp`:

```sh
uv sync --frozen
uv run pytest -q
```

Tests exercise typed tools, the REST HTTP boundary, safe error propagation,
host/origin/key checks, body limits, SDK Streamable HTTP initialization/calls,
and a real stdio subprocess handshake. They do not need a live database.

## Dashboard

From `frontend`:

```sh
npm ci
npm test
npm run typecheck
npm run build
```

For development, keep the container API running, stop the `web` container, and
create an untracked `frontend/.env.local` with `MNEMONIC_API_URL` set to
`http://127.0.0.1:8000` and `MNEMONIC_API_KEY` set to the same private key as `.env`.
Then run `npm run dev`. The default dashboard origins accept localhost and
127.0.0.1 on port 3000; update origins when changing that port. Client requests
always use `/api/mnemonic`; the API address and bearer key remain server-only.

## Full running stack

After `docker compose up --build -d --wait`, use the MCP environment to run the
read-only live checks from the repository root:

```sh
uv run --project mcp python scripts/check-stack.py
```

To exercise the complete write path, create a dedicated validation project and
pass its UUID. This explicitly authorizes one synthetic prompt to be created,
edited, completed, and soft-deleted. Its data remains in the database's deletion
history; the script does not touch other prompts or remove the project.

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID
```

Supply `--other-project-id` for a cross-project negative check. The script tests
actual MCP → REST → PostgreSQL communication, the dashboard server proxy,
authentication, exact prompt/provenance preservation, compact search, MCP
resources/prompts, conflicting edits, completion filtering, and deletion.

For a browser pass, exercise the first-project empty state, project switching,
search, status filters, prompt copy, cancel/save edits, deletion confirmation,
and recovery from a stale version. Confirm a narrow viewport remains usable.
Do not leave fabricated hand-offs in a user's working queue after verification.
