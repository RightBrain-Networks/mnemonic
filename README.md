# mnemonic

> A lightweight coordination system that coding agents can use to communicate
> amongst themselves -- without littering your repo with random Markdown docs
> or blowing up your issue tracker.

**`mnemonic`** is a standalone, local-first application: **PostgreSQL + FastAPI +
Next.js**, with an **MCP server that calls the REST API**. It does not modify
Claude's memory subsystem. Claude Code is the first client; the API, metadata,
and MCP interface do not depend on a particular LLM provider.

A durable home for objectives that outlive any one AI session. Save one work
item, carry it forward through immutable session-attributed checkpoints, and
preserve what each session knew, changed, and verified. Many sessions can
continue the same objective without multiplying its human-visible identity.

## Is `mnemonic` right for you?

- You build with Claude Code (*support for additional platforms coming soon*).

- Important context and follow-up tasks are often overlooked because they 
get buried under overly verbose LLM output.

- Dozens or hundreds of transient Markdown documents are cluttering 
your repo, or your issue tracker has so many agent-to-agent issues that it's 
become painful for a human user.

- Multiple agents are concurrently working on the same project and occasionally 
clobber each other.

- You prefer a managing a locally-hosted Docker stack rather than paying for a 
SaaS product.

- You don't want the complexity of a full orchestration platform like Openclaw.

## Run it

Requirements: Docker with Linux containers and Docker Compose. The optional
settings helper uses Python 3.10 or newer; no host Node or Python is needed to
run the containers after `.env` is configured.

```sh
python scripts/setup.py
docker compose up --build -d --wait
```

On macOS/Linux the Python command may be `python3`. Alternatively, copy
`.env.example` to `.env` and supply two different random secrets yourself: a
URL-safe PostgreSQL password and an API key of at least 32 characters. The
example deliberately contains no usable credentials. Never commit `.env`.

Open [Mnemonic](http://localhost:3000). Create your first project using the
project selector, then connect your agent to create or continue durable work.
The application starts empty; there are no fabricated objectives, checkpoints,
or session IDs.

| Service | `.env` variable | Default local address |
| --- | --- | --- |
| Dashboard | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000) |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp` |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so
change one there if it collides with something already running on the host, and
substitute your own value wherever this README shows a port. With `uv`
installed, `uv run --project mcp python scripts/check-stack.py` resolves all
three from `.env` and performs the read-only live-stack checks. See
[`docs/development.md`](docs/development.md) before opting into its write path.

The four application services run alongside a small backup container. PostgreSQL
has no published port. Dashboard, REST, and MCP ports bind to loopback only.

For LAN access through your existing nginx TLS proxy, use the
[nginx configuration and setup guide](deploy/nginx/README.md).

## Connect Claude Code

Set `MNEMONIC_API_KEY` in the terminal environment to the key from your private
`.env` file. Set `MNEMONIC_MCP_PORT` as well if you changed it from the default.
Then register the HTTP MCP endpoint:

```sh
claude mcp add --transport http --scope user mnemonic "http://127.0.0.1:${MNEMONIC_MCP_PORT:-8001}/mcp" --header "Authorization: Bearer ${MNEMONIC_API_KEY}"
```

Registering the endpoint does not connect it to a running session. Claude Code
loads MCP servers at startup, so start a new session afterwards.

In PowerShell, the URL and header expressions are
`"http://127.0.0.1:$env:MNEMONIC_MCP_PORT/mcp"` and
`"Authorization: Bearer $env:MNEMONIC_API_KEY"`; PowerShell has no `:-` default,
so set the port variable explicitly there. Do not paste the real key into
tracked project configuration. Configuration examples, including a Docker stdio
alternative and OpenCode, live in [`examples/`](examples/); they show the
default ports and need the same substitution if yours differ.
[`work.json`](examples/work.json) is the canonical example.

The three skills ship as a Claude Code plugin. Register this repository as a
marketplace once, then enable the plugin in any project that should have them:

```bash
claude plugin marketplace add /srv/mnemonic
claude plugin install mnemonic@mnemonic
```

To make it automatic for everyone who clones a consuming repository, commit this
to that repository's `.claude/settings.json` instead:

```json
{
  "extraKnownMarketplaces": {
    "mnemonic": { "source": { "source": "local", "path": "/srv/mnemonic" } }
  },
  "enabledPlugins": { "mnemonic@mnemonic": true }
}
```

Replace the local path with `{ "source": "github", "repo": "<owner>/mnemonic" }`
once the repository is reachable remotely; `claude plugin marketplace update
mnemonic` then pulls changes. The plugin provides:

- **`mnemonic-save`** searches for existing work, creates a durable objective
  with its initial checkpoint and explicit atomic links, or appends corrective
  context to an existing one.
- **`mnemonic-search`** finds compact work-item leads within the chosen project,
  normally restricted to open work, with lifecycle/readiness pointers.
- **`mnemonic-recall`** loads bounded current context, pages older checkpoints
  when needed, atomically claims already-authorized execution, renews or
  releases that expiring lease, inspects immediate typed relationships, appends
  useful progress, and records an atomic completion checkpoint when the work is
  complete.

Invoke `/mnemonic-save`, `/mnemonic-search`, or `/mnemonic-recall`, or ask Claude
in natural language. The skills require the connected `mnemonic` MCP server.
You can copy the selected project's ID from the dashboard, or use
`list_projects`. Session IDs are opaque text (often UUIDs), not integers, and
refer to the originating LLM conversation, not the MCP transport session.

See [`docs/agents.md`](docs/agents.md) for the workflow and client boundaries.

## What Phase 3 does

- Separates durable work by project, with a project selector and project
  creation. One work-item card can represent checkpoints from many sessions.
- Stores immutable checkpoint text, tags, source client/session/model, optional
  session URL, branch, checked commit, custom metadata, and timestamps.
- Keeps title, retrieval summary, priority, lifecycle, and optimistic version on
  the small mutable work item rather than rewriting historical session context.
- Searches PostgreSQL full-text indexes and literal identifiers by default.
  An opt-in Semantic dashboard toggle and `search_work` argument add hybrid
  similarity ranking from a local embedding model; both default to disabled.
  The model runs offline and needs no hosted embedding service or model API key.
  Checkpoint text participates in lexical and semantic retrieval. Search returns
  one compact result per work item; recall returns bounded current context, and
  older history is explicitly paginated.
- Lets a user edit work identity, append immutable context/progress checkpoints,
  complete work with a required completion checkpoint, copy current context, and
  soft-delete work. Concurrent edits and completions are detected rather than
  silently overwriting changes; independent checkpoint appenders can both
  succeed.
- Keeps `done`, `wont-do`, and `promoted` work out of the default open view
  while retaining it under explicit filters. Deleted work and its checkpoints
  are hidden from ordinary reads but retained for recovery.
- Lets one cooperative agent session claim an open work item through an atomic,
  server-timed lease. A client request ID recovers an active claim receipt after
  an unknown response, renewal extends responsibility, release hands unfinished
  work back, and expiry restores claimability without operator repair.
- Derives `Ready`, `Active`, and `Blocked` independently from lifecycle. Search,
  recall, and the dashboard expose only safe holder/session/timing details; the
  capability token appears only in MCP/API claim receipts and JSON mutation
  bodies, never browser data, URLs, errors, or ordinary responses.
- Stores explicit project-local `blocks`, `parent-child`, `discovered-from`,
  `duplicate-of`, and `related` relationships. All directed edges use
  `source --type--> target`; `related` is normalized and presented as
  undirected.
- Makes only unresolved incoming `blocks` edges affect readiness and claim
  eligibility. `done` resolves a blocker; `wont-do` and `promoted` do not.
  Active work may become blocked without revoking its existing lease.
- Creates a new objective, initial checkpoint, and up to ten explicit typed
  relationships atomically. A `discovered-from` link must cite a
  checkpoint on the originating target work item.
- Browses collapsible structural roots and lazily loaded children in the
  dashboard. Subtree-aware filters keep matching descendants visible beneath
  muted ancestors, while free-text search returns direct hits with bounded
  ancestor breadcrumbs.
- Requires the matching lease token for completion, retirement, promotion, or
  deletion while work has an active lease. Checkpoint append remains open and
  lease operations do not alter work version or activity time.
- Saves a PostgreSQL backup at startup and daily, retaining earlier dumps.

It does **not** automatically execute checkpoints, grant authority by claiming,
create GitHub issues, inject memory hooks, infer missing session IDs, schedule
the next ready item, infer relationships from semantic similarity, merge
duplicates, or reserve repository resources. Mnemonic is deliberately
LLM-centric: checkpoints and relationship context record an agent's claims
rather than server-verified proof. Context quality and freshness remain agent
workflow obligations; storing a commit ID is not proof the service verified
anything.

## Operate and develop

```sh
docker compose ps
docker compose logs --tail=100 api mcp web backup
docker compose stop
docker compose up -d --wait
```

Normal stop/restart preserves the database. **Do not use `docker compose down
-v` on your working stack:** it removes the data volume. Backups are written to
`./backups` by default and are not committed. Copy them off the machine and
monitor available disk space; a local dump alone does not protect against disk
loss. Restore commands and security limits are in
[`docs/operations.md`](docs/operations.md).

For local development and tests, see [`docs/development.md`](docs/development.md).
Completed checks are recorded in [`docs/validation.md`](docs/validation.md).
The revised design is recorded in [`docs/architecture.md`](docs/architecture.md),
with the wire contract in [`docs/api-contract.md`](docs/api-contract.md).
[`ADR.md`](ADR.md) is preserved as the original memory-subsystem proposal; the
standalone architecture supersedes that implementation scope.
