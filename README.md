# Mnemonic

A durable home for the work an AI session leaves behind. Save complete hand-off
prompts, carry progress forward through append-only comments, and preserve what a
completing session changed and verified. Work can span sessions without
reconstructing its history.

Mnemonic is a standalone, local-first application: **PostgreSQL + FastAPI +
Next.js**, with an **MCP server that calls the REST API**. It does not modify
Claude's memory subsystem. Claude Code is the first client; the API, metadata,
and MCP interface do not depend on a particular LLM provider.

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
project selector, then connect your agent to save hand-offs. The application
starts empty; there are no fabricated prompts or session IDs.

| Service | `.env` variable | Default local address |
| --- | --- | --- |
| Dashboard | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000) |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp` |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so
change one there if it collides with something already running on the host, and
substitute your own value wherever this README shows a port. `python
scripts/check-stack.py` resolves all three from `.env` and checks that the stack
answers on them.

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

Copy the three folders under [`skills/`](skills/) into the target project's
`.claude/skills/` directory, or into `~/.claude/skills/` for personal use:

- **`mnemonic-save`** searches for duplicates, writes a complete hand-off, and
  saves its real originating client, session ID, and relevant metadata.
- **`mnemonic-search`** finds compact leads within the chosen project, normally
  restricted to open work.
- **`mnemonic-recall`** retrieves the full prompt and progress log, checks
  provenance and cited state, records useful session progress, and saves a work
  summary when authorized work is complete.

Invoke `/mnemonic-save`, `/mnemonic-search`, or `/mnemonic-recall`, or ask Claude
in natural language. The skills require the connected `mnemonic` MCP server.
You can copy the selected project's ID from the dashboard, or use
`list_projects`. Session IDs are opaque text (often UUIDs), not integers, and
refer to the originating LLM conversation, not the MCP transport session.

See [`docs/agents.md`](docs/agents.md) for the workflow and client boundaries.

## What the MVP does

- Separates prompts by project, with a project dropdown and project creation.
- Stores full prompt text, retrieval summary, tags, source client/session/model,
  optional session URL, branch, checked commit, custom metadata, and timestamps.
- Searches PostgreSQL full-text indexes and literal identifiers by default.
  An opt-in Semantic dashboard toggle and `search_handoffs` argument add hybrid
  similarity ranking from a local embedding model; both default to disabled.
  The model runs offline and needs no hosted embedding service or model API key.
  Comment text participates in lexical and semantic retrieval. Search returns
  compact summaries; recall loads the complete prompt and progress timeline.
- Lets a user view, edit, delete, and copy prompts from the dashboard, append
  progress comments, and complete work with a required summary. Concurrent edits
  and completions are detected instead of silently overwriting changes.
- Keeps `done`, `wont-do`, and `promoted` prompts out of the default open queue,
  while retaining them under explicit filters. Deleted prompts are hidden from
  ordinary reads but retained in the database for recovery.
- Saves a PostgreSQL backup at startup and daily, retaining earlier dumps.

It does **not** automatically execute prompts, create GitHub issues, inject
memory hooks, infer missing session IDs, or provide a general-purpose multi-user
human issue tracker. Mnemonic is deliberately LLM-centric: comments are durable
session checkpoints, and completion summaries record an agent's claims rather
than server-verified proof. Prompt quality and freshness remain agent workflow
obligations; storing a commit ID is not proof the service verified anything.

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
