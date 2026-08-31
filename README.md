# Mnemonic

A durable home for the work an AI session leaves behind. Save complete hand-off
prompts, find them by project, and pick them up in a fresh session without
reconstructing the context.

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

| Service | Local address |
| --- | --- |
| Dashboard | [localhost:3000](http://localhost:3000) |
| REST API documentation | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP | `http://127.0.0.1:8001/mcp` |

The four application services run alongside a small backup container. PostgreSQL
has no published port. Dashboard, REST, and MCP ports bind to loopback only.

For LAN access through your existing nginx TLS proxy, use the
[nginx configuration and setup guide](deploy/nginx/README.md).

## Connect Claude Code

Set `MNEMONIC_API_KEY` in the terminal environment to the key from your private
`.env` file, then register the HTTP MCP endpoint:

```sh
claude mcp add --transport http --scope user mnemonic http://127.0.0.1:8001/mcp --header "Authorization: Bearer ${MNEMONIC_API_KEY}"
```

In PowerShell, the header expression is
`"Authorization: Bearer $env:MNEMONIC_API_KEY"`. Do not paste the real key into
tracked project configuration. Configuration examples, including a Docker stdio
alternative and OpenCode, live in [`examples/`](examples/).

Copy the three folders under [`skills/`](skills/) into the target project's
`.claude/skills/` directory, or into `~/.claude/skills/` for personal use:

- **`mnemonic-save`** searches for duplicates, writes a complete hand-off, and
  saves its real originating client, session ID, and relevant metadata.
- **`mnemonic-search`** finds compact leads within the chosen project, normally
  restricted to open work.
- **`mnemonic-recall`** retrieves the full prompt and checks its provenance and
  cited state before continuing work the user has authorized.

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
- Searches PostgreSQL full-text indexes and literal identifiers without an
  embedding service or a model API key. Search returns summaries; recall loads
  the complete prompt.
- Lets a user view, edit, delete, and copy prompts from the dashboard. Concurrent
  edits are detected instead of silently overwriting changes.
- Keeps `done`, `wont-do`, and `promoted` prompts out of the default open queue,
  while retaining them under explicit filters. Deleted prompts are hidden from
  ordinary reads but retained in the database for recovery.
- Saves a PostgreSQL backup at startup and daily, retaining earlier dumps.

It does **not** automatically execute prompts, create GitHub issues, inject
memory hooks, infer missing session IDs, provide semantic embeddings, or act as
a multi-user issue tracker. Prompt quality and freshness are agent workflow
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
