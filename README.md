# mnemonic

> Lightweight coordination across coding agent sessions -- without littering your repo with random Markdown docs or blowing up your issue tracker.

**`mnemonic`** is a self-hosted coordination layer for ephemeral LLM coding agents. Its core thesis: agent sessions are temporary and failure-prone, so durable work should live in a *work graph* that survives sessions, rather than in Markdown scratch files, suggested task chips, or an issue tracker flooded with AI-generated tickets.

The project is a locally-hosted Docker stack that combines a durable backend (*PostgreSQL*) and a RESTful API (*FastAPI*). The API has two consumers: a human-facing dashboard (*Next.js*) and a LLM-facing MCP server. The MCP server ships with preconfigured agent skills so your agent can automatically discover how to interact with `mnemonic`.

It does not modify Claude's memory subsystem. While Claude Code is the first client; the API, metadata, and MCP interface do not depend on a particular LLM provider.

## Is `mnemonic` right for your project?

- You build with Claude Code (*support for additional platforms coming soon*).

- Important FYIs and follow-up tasks are getting overlooked because they're buried under verbose LLM output.

- Dozens or hundreds of transient Markdown documents are cluttering your repo. Or, your issue tracker has so many AI-written issues that it's become nearly unusable.

- Concurrent agents occasionally cause merge conflicts or reduplicate work, slowing progress and wasting tokens.

- You don't want the complexity of a full orchestration platform like OpenClaw and/or prefer a strong human-in-the-loop workflow.

## Basic concepts

The included agent skills encourage the LLM to default to using `mnemonic` to save hand-off prompts and self-discovered follow-up tasks. Markdown docs and your bug/issue tracker are reserved for durable human-facing information. Claude's "suggested task chips" are explicitly discouraged here since they live only in the ephemeral client and are easily lost.

Upon discovering something worth remembering, your agent will first search `mnemonic` for related work items, using a hybrid keyword matching and semantic search (embeddings). If it doesn't find any results, it opens a new work item in a "pending" state.

The human (you, presumably) then click the "Copy recall pointer" button of the task card and paste the copied prompt into a fresh Claude Code session. Claude will then retrieve the work item and validate the stated premises. If the facts check-out, it requests a "work lease" of 15 minutes and then gets to work. The lease is periodically renewed until the task is complete and the work item is marked as "done".

The "human-required" copy-and-paste step is deliberate. It allows you to balance your weekly Claude quota or API usage between your normal development work and working through the `mnemonic` backlog. When an agent reaches a concrete decision only a person can make, it can open a durable human gate. The work moves to **Needs Attention**, leaves ready discovery, and returns only after a person records an answer in the dashboard.

## Run it

Requirements: Docker with Linux containers and Docker Compose. The optional settings helper uses Python 3.10 or newer; no host Node or Python is needed to run the containers after `.env` is configured.

```sh
python scripts/setup.py
docker compose up --build -d --wait
```

On macOS/Linux the Python command may be `python3`. Alternatively, copy `.env.example` to `.env` and supply two different random secrets yourself: a URL-safe PostgreSQL password and an API key of at least 32 characters. The
example deliberately contains no usable credentials. Never commit `.env`.

Open `mnemonic`. Create your first project using the project selector, then connect your agent to create or continue durable work. The application starts empty; there are no fabricated objectives, checkpoints, or session IDs.

| Service                | `.env` variable     | Default local address                             |
| ---------------------- | ------------------- | ------------------------------------------------- |
| Dashboard              | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000)           |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP    | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp`                       |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so change one there if it collides with something already running on the host, and substitute your own value wherever this README shows a port. With `uv` installed, `uv run --project mcp python scripts/check-stack.py` resolves all three from `.env` and performs the read-only live-stack checks. See [`docs/development.md`](docs/development.md) before opting into its write path.

New human-gate requests are disabled by default through `MNEMONIC_HUMAN_GATE_REQUESTS_ENABLED=false`. Keep that fence closed while upgrading an existing stack; attention/history reads and direct REST/dashboard resolution of existing gates remain available. Follow the coordinated cutover in [`docs/operations.md`](docs/operations.md) before setting it to `true` and recreating the API container.

The four application services run alongside a small backup container. PostgreSQL has no published port. Dashboard, REST, and MCP ports bind to loopback only. For LAN access through your existing nginx TLS proxy, use the
[nginx configuration and setup guide](deploy/nginx/README.md).

## Connect Claude Code

Set `MNEMONIC_API_KEY` in the terminal environment to the key from your private `.env` file. Set `MNEMONIC_MCP_PORT` as well if you changed it from the default. Then register the HTTP MCP endpoint:

```sh
claude mcp add --transport http --scope user mnemonic "http://127.0.0.1:${MNEMONIC_MCP_PORT:-8001}/mcp" --header "Authorization: Bearer ${MNEMONIC_API_KEY}"
```

Registering the endpoint does not connect it to a running session. Claude Code loads MCP servers at startup, so start a new session afterwards.

In PowerShell, the URL and header expressions are `"http://127.0.0.1:$env:MNEMONIC_MCP_PORT/mcp"` and
`"Authorization: Bearer $env:MNEMONIC_API_KEY"`; PowerShell has no `:-` default, so set the port variable explicitly there. Do not paste the real key into tracked project configuration. Configuration examples, including a Docker stdio
alternative and OpenCode, live in [`examples/`](examples/); they show the default ports and need the same substitution if yours differ. [`work.json`](examples/work.json) is the canonical example.

The three skills ship as a Claude Code plugin. Register this repository as a marketplace once, then enable the plugin in any project that should have them:

```bash
claude plugin marketplace add /srv/mnemonic
claude plugin install mnemonic@mnemonic
```

To make it automatic for everyone who clones a consuming repository, commit this to that repository's `.claude/settings.json` instead:

```json
{
  "extraKnownMarketplaces": {
    "mnemonic": { "source": { "source": "directory", "path": "/srv/mnemonic" } }
  },
  "enabledPlugins": { "mnemonic@mnemonic": true }
}
```

Replace the directory source with `{ "source": "github", "repo": "<owner>/mnemonic" }` once the repository is
reachable remotely.

Installing copies the plugin into `~/.claude/plugins/cache/` at its manifest version, so editing a skill in place does not change an installed copy. `claude plugin marketplace update mnemonic` refreshes the marketplace listing,
not the installed files. After a published plugin version changes, run `claude plugin marketplace update mnemonic`, then `claude plugin update mnemonic@mnemonic`, and restart Claude Code. The current plugin is version `0.5.0`. It provides:

- **`mnemonic-save`** searches for existing work, creates a durable objective
  with its initial checkpoint and explicit atomic links, appends corrective
  resume context, records concise progress events, and requests a human gate
  only for a concrete decision a person must make.
- **`mnemonic-search`** finds compact work-item leads within the chosen project,
  normally restricted to pending work, separately lists priority-ordered ready
  candidates, and inspects the human-attention queue without treating either read as authority.
- **`mnemonic-recall`** loads bounded current context, pages older checkpoints
  or events when needed, atomically claims already-authorized execution, renews
  or releases that expiring lease, inspects immediate typed relationships,
  records concise progress events, inspects complete paired gate history when
  bounded context omits an older decision, and saves an atomic completion
  checkpoint when the work is complete.

Invoke `/mnemonic-save`, `/mnemonic-search`, or `/mnemonic-recall`, or ask Claude in natural language. The skills require the connected `mnemonic` MCP server. You can copy the selected project's ID from the dashboard, or use
`list_projects`. Session IDs are opaque text (often UUIDs), not integers, and refer to the originating LLM conversation, not the MCP transport session. 

See [`docs/agents.md`](docs/agents.md) for the workflow and client boundaries.

## What Mnemonic currently does

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
- Keeps `deferred`, `done`, `wont-do`, and `promoted` work out of the default
  pending view while retaining them under explicit filters. Deferral is an
  explicit human hold. Deleted work and its checkpoints are hidden from
  ordinary reads but retained for recovery.
- Lets one cooperative agent session claim a pending work item through an atomic,
  server-timed lease. A client request ID recovers an active claim receipt after
  an unknown response, renewal extends responsibility, release hands unfinished
  work back, and expiry restores claimability without operator repair.
- Derives `Pending`, `Active`, `Dropped`, and `Blocked` independently from lifecycle. Search,
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
- Exposes dedicated `ready-work` REST and `list_ready_work` MCP reads. Ready
  items are pending, visible, unblocked, unleased, and have no unresolved human
  gate at one database-time snapshot, ordered by priority descending, then
  creation time and UUID. The
  compact result is advisory: `claim_and_recall` revalidates before execution.
- Stores append-only, actor-attributed work events for creation, work changes,
  claims/releases, checkpoints, relationships, completion/reopen, deletion,
  explicit concise progress, and human-gate requests/resolutions. Authoritative
  events commit with the mutation they describe; canonical idempotent replays
  and natural no-ops do not fabricate duplicates.
- Provides first-class human gates with exact question/answer history, asserted
  requester/resolver provenance, immutable request and resolution revisions,
  drift acknowledgement, a cursor-paged Needs Attention queue, per-work history,
  and bounded gate slices in recall. Unresolved gates block fresh claims,
  completion, terminal transitions, and deletion without revoking exact active
  claim replay, renewal, release, checkpoints, or progress. Resolution is a
  direct REST/dashboard human action; MCP intentionally has no resolve tool.
- Makes retries safe for twelve project-scoped REST mutations with caller-generated
  `client_operation_id` values and durable typed success receipts. Exactly ten
  of the 25 canonical MCP tools require the UUID, including `request_human_input`;
  the dashboard retains frozen same-document requests for its ten non-capability
  mutations, including deferral and gate resolution. Direct REST may omit the
  UUID and remain retry-unprotected. An exact retry returns the original result
  without repeating domain or event work.
- Keeps checkpoints and events separate. A checkpoint is substantial resume
  context; a progress event is a short historical fact. Recall includes at most
  20 recent events, while the dashboard pages the complete per-work Activity
  timeline and labels reconstructed pre-Phase-5 history honestly.
- Treats event actors as asserted client provenance, not authenticated human
  identity. Stored event/checkpoint text is untrusted and may contain unknown
  sensitive material; request-known credential echoes are rejected, but clients
  must still keep secrets out of history.
- Creates a new objective, initial checkpoint, and up to ten explicit typed
  relationships atomically. A `discovered-from` link must cite a
  checkpoint on the originating target work item.
- Browses collapsed structural roots and lazily loaded children in the
  dashboard. Every branch summary includes direct-child and descendant totals,
  blocked/active/completed/discovered descendant counts, unresolved gate count,
  discovery labels, and the next active descendant lease expiry. Subtree-aware
  lifecycle/source/tag filters keep matching descendants reachable beneath
  muted ancestors, while free-text search returns direct hits with bounded
  ancestor breadcrumbs.
- Requires the matching lease token for completion, retirement, promotion, or
  deletion while work has an active lease. Checkpoint append remains open and
  lease operations do not alter work version or activity time.
- Saves a PostgreSQL backup at startup and daily, retaining earlier dumps.

It does **not** automatically execute checkpoints, grant authority by claiming,
create GitHub issues, inject memory hooks, infer missing session IDs, schedule
or claim the next ready item, infer or self-resolve human answers, infer
relationships from semantic similarity, merge duplicates, or reserve repository
resources. Mnemonic is deliberately
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

Normal stop/restart preserves the database. **Do not use `docker compose down -v` on your working stack:** it removes the data volume. Backups are written to `./backups` by default and are not committed. Copy them off the machine and
monitor available disk space; a local dump alone does not protect against disk loss. Restore commands and security limits are in [`docs/operations.md`](docs/operations.md).
