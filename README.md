# mnemonic

> durable coordination across coding agent sessions -- without littering your repo with random Markdown docs or blowing up your issue tracker.

**`mnemonic`** is a self-hosted coordination plane for ephemeral LLM coding agents. Its core thesis: agent sessions are temporary and failure-prone, so durable work should live in a *work graph* that survives sessions, rather than in Markdown scratch files, suggested task chips, or an issue tracker flooded with AI-generated tickets.

The project is a Docker Compose stack that combines a durable backend (*PostgreSQL*) and a RESTful API (*FastAPI*). The API has two consumers: a human-facing, web browser-based dashboard (*Next.js*) and a LLM-facing MCP server. The MCP server ships with preconfigured agent skills so your agent can automatically discover how to interact with `mnemonic`. It is designed for a single, local (human) user and supports multiple, concurrent development projects.

Tested with Claude Code, OpenAI Codex, and OpenCode. Probably works with any similar platform with a MCP client (Cursor, etc).

## Is `mnemonic` right for your project?

- You build with Claude Code, OpenAI Codex, OpenCode or similar MCP client.

- Important FYIs and follow-up tasks are getting overlooked because they're buried under verbose LLM output.

- Dozens or hundreds of transient Markdown documents are cluttering your repo. Or, your issue tracker has so many AI-written issues that it's become nearly unusable.

- Concurrent agents occasionally cause merge conflicts or reduplicate work, slowing progress and wasting tokens.

- You don't want the complexity of a full orchestration platform like OpenClaw and/or prefer a strong human-in-the-loop workflow.

## Basic concepts

The included agent skills encourage the LLM to default to using `mnemonic` to save hand-off prompts and self-discovered follow-up tasks. Markdown docs and your bug/issue tracker (if specified) are reserved for durable human-facing information. "Suggested task chips" are explicitly discouraged here since they live only in the ephemeral client and are easily lost.

Upon discovering something worth doing, but is out-of-scope of the current task, the agent will first search `mnemonic` for related work items using PostgreSQL keyword matching or semantic search (embeddings). If no matches are found, the agent opens a new work item in a "pending" state.

The human (you, presumably) then click the "Copy recall pointer" button of the task card and paste the copied prompt into a fresh session. The LLM will then retrieve the work item and validate the stated premises. If the facts check-out, it requests a "work lease" of 15 minutes and then begins working. The lease is periodically renewed until the task is complete and then work item is marked as *Done*.

The "human-required" copy-and-paste step is deliberate. It allows you to balance your weekly usage quota or API costs between your normal development work and working through the `mnemonic` backlog. If an agent hits a human-needed decision, the work is parked in *Needs Attention* and returns only after a person records an answer in the dashboard.

## Other features
 - **Code review agents** -- Projects can also require or invite an adversarial code review at configurable priority thresholds (both default to Never). Reviewers lease the original Done item and perform either a warm or cold code review. See [code reviews](docs/code-reviews.md) for the complete workflow.

- **Cross-platform coordination** -- Claude Code, OpenAI Codex, OpenCode, et al. can all be used simultaneously in the same project and intelligently coordinate amongst themselves.

- **Plain English work summaries** -- The "Summaries" inbox provides an easy-to-read, durable record of what each session did so you don't have to slog through every dense transcript.

- **External trackers and duplicate comparison** -- Automatically finds duplicate tasks in your repo's GitHub Issues (or similar) and includes them by reference. Avoids two, conflicting homes for agent-to-agent coordination.

## Run it

### Copy and paste into your LLM to have it handle this part.

```text
Install and configure mnemonic for me from https://github.com/RightBrain-Networks/mnemonic. Clone the repository, then follow AGENT-README.md exactly. Use the default local-only Docker Compose configuration unless I state otherwise, preserve any existing data and configuration, connect my MCP-capable LLM client if one is available, and continue until the documented acceptance checks pass or you need a specific decision or credential from me.
```

### Or, if you prefer, old school human instructions...

Requirements: Docker with Linux containers and Docker Compose. The optional settings helper uses Python 3.10 or newer; no host Node or Python is needed to run the containers after `.env` is configured.

```sh
python scripts/setup.py
docker compose up --build -d --wait
```

`sh ./up_mnemonic.sh` starts the stack with the TLS allowlists from
`compose.tls.yaml`.

On macOS/Linux the Python command may be `python3`. Alternatively, copy `.env.example` to `.env` and supply two different random secrets yourself: a URL-safe PostgreSQL password and an API key of at least 32 characters. The
example deliberately contains no usable credentials. Never commit `.env`.

Open `mnemonic`. Create your first project using the project selector, then connect your agent to create or continue durable work. The application starts empty; there are no fabricated objectives, checkpoints, or session IDs.

| Service                | `.env` variable     | Default local address                             |
| ---------------------- | ------------------- | ------------------------------------------------- |
| Dashboard              | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000)           |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP    | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp`                       |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so change one there if it collides with something already running on the host, and substitute your own value wherever this README shows a port. With `uv` installed, `uv run --project mcp python scripts/check-stack.py` resolves all three from `.env` and performs the read-only live-stack checks. See [`docs/development.md`](docs/development.md) before opting into its write path.

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
alternative and OpenCode, live in [`examples/`](examples/); they show the default ports and need the same substitution if yours differ. [`work.json`](examples/work.json) is the canonical work body. Copyable workflows are [`duplicate-suggestion.json`](examples/duplicate-suggestion.json), [`discovered-work.json`](examples/discovered-work.json), [`human-gate-request.json`](examples/human-gate-request.json), [`human-gate-resolution.json`](examples/human-gate-resolution.json), and the irreversible [`merge-work.json`](examples/merge-work.json) request. Replace every placeholder and read both exact work contexts before using the merge example.
The atomic completion examples are
[`completion-with-evidence.json`](examples/completion-with-evidence.json) and
[`completion-without-evidence.json`](examples/completion-without-evidence.json),
with a two-episode response in
[`completion-evidence-history.json`](examples/completion-evidence-history.json).
Replace every provenance, version, UUID, result, and artifact placeholder with
facts from the exact attempt; never copy example evidence as if it were
observed.

The three skills ship as a Claude Code plugin. Register this repository as a marketplace once, then enable the plugin in any project that should have them:

```bash
claude plugin marketplace add /path/to/mnemonic
claude plugin install mnemonic@mnemonic
```

To make it automatic for everyone who clones a consuming repository, commit this to that repository's `.claude/settings.json` instead:

```json
{
  "extraKnownMarketplaces": {
    "mnemonic": { "source": { "source": "directory", "path": "/path/to/mnemonic" } }
  },
  "enabledPlugins": { "mnemonic@mnemonic": true }
}
```

Replace the directory source with `{ "source": "github", "repo": "<owner>/mnemonic" }` once the repository is
reachable remotely.

Installing copies the plugin into `~/.claude/plugins/cache/` at its manifest version, so editing a skill in place does not change an installed copy. `claude plugin marketplace update mnemonic` refreshes the marketplace listing,
not the installed files. After a published plugin version changes, run `claude plugin marketplace update mnemonic`, then `claude plugin update mnemonic@mnemonic`, and restart Claude Code.

The current application/API/MCP release is `0.16.0`, with plugin version
`0.16.0` and database head `0025_cross_project_relationships`. Deploy those
surfaces together after backup and writer quiescence. Downgrade to 0024 only
when every retained relationship has both current
endpoints in its immutable authority project and no immutable
relationship/dependency event history for an edge spans projects. Removing a
cross-project edge does not erase that history or restore eligibility. Otherwise,
fix forward or restore the full pre-0025 backup.
Its repository-freshness helper requires Bash 3.2 or newer and Git 2.45 or newer in the explicitly selected local workspace. It provides:

- **`mnemonic-save`** searches for existing work, explicitly compares a stable
  draft with grouped duplicate candidates while preserving Create anyway,
  creates a durable objective with its initial checkpoint and explicit atomic links, appends corrective
  resume context, records concise progress events, and records a human gate
  only for a concrete decision a person must make, after checking for an open
  question and writing the supporting checkpoint first. Checkpoints may declare
  ordered repository dependency patterns only with a commit the author actually
  inspected; omission means the dependency scope is unknown.
- **`mnemonic-search`** finds compact work-item leads within the chosen project,
  normally restricted to canonical pending work, identifies the exact member
  that supplied a grouped match, interprets categorical duplicate-suggestion
  signals without treating them as authority, supports explicit alias audit scopes,
  assigns deliberate priorities using a shared [consequence rubric](plugin/reference/priority.md),
  separately lists priority-ordered ready candidates, and pages the Needs
  Attention queue without treating any read as authority.
  A compact search pointer never carries repository dependency scope, so any
  checkpoint used for execution is recalled in full first.
- **`mnemonic-recall`** loads bounded current context, pages older checkpoints
  or events when needed, atomically claims already-authorized execution, renews
  or releases that expiring lease, inspects immediate typed relationships,
  reads unresolved and answered human questions with their drift flags, asks a
  person mid-execution through the save procedure, records concise progress
  events, pages complete paired gate history when bounded context omits an
  older decision, distinguishes an alias's exact retained history from its
  canonical continuation, reviews both context revisions before any permanent
  merge, and saves an atomic completion checkpoint with optional structured
  verification and artifact evidence when the work is complete. It can page
  the exact completion history later without treating caller-reported evidence
  as proof, authority, or executable instructions.
  Before relying on a governing checkpoint for repository work, it invokes the
  packaged read-only Git helper from the user-selected workspace and reports
  `unchanged`, `changed`, or `indeterminate` evidence without treating any result
  as semantic proof or execution authority.

Invoke `/mnemonic-save`, `/mnemonic-search`, or `/mnemonic-recall`, or ask Claude in natural language. The skills require the connected `mnemonic` MCP server. You can copy the selected project's ID from the dashboard, or use
`list_projects`. Session IDs are opaque text (often UUIDs), not integers, and refer to the originating LLM conversation, not the MCP transport session. 

See [`docs/agents.md`](docs/agents.md) for the workflow and client boundaries.

## Operate and develop

```sh
docker compose ps
docker compose logs --tail=100 api mcp web backup
docker compose stop
docker compose up -d --wait
```

Normal stop/restart preserves the database. **Do not use `docker compose down -v` on your working stack:** it removes the data volume. Backups are written to `./backups` by default and are not committed. Copy them off the machine and
monitor available disk space; a local dump alone does not protect against disk loss. Restore commands and security limits are in [`docs/operations.md`](docs/operations.md).
