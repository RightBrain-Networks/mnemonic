# mnemonic

> The single-user, multi-agent coordination plane built for experienced engineers rather vibe coders.

**`mnemonic`** is a single-user, self-hosted coordination plane for LLM coding agents. Its core thesis: agent sessions are temporary, failure-prone, and locally smart but globally blind. Durable work should live in a *work graph* that survives sessions, rather than in Markdown scratch files, suggested task chips, or an issue tracker flooded with AI-generated tickets. With a strong human-in-the-loop philosophy, it is designed to increase the code quality and throughput of experienced developers using AI, rather than replace their thinking.

Additionally, `mnemonic` maintains a *knowledge graph* containing a versioned history of decisions made by both the human operator and agents. When a decision changes, any previously written code that was downstream of those decision is automatically discoverable. No more "grepping and hoping" when product requirements shift, even on a large codebase.

Tested with Claude Code, OpenAI Codex, and OpenCode. Probably works with any similar platform with a MCP client (Cursor, etc). It is designed for a single, local (human) user and supports multiple, concurrent development projects.

### Tech stack
The project is a Docker Compose stack that combines a durable backend (*PostgreSQL*) and a RESTful API (*FastAPI*). The API has two consumers: a human-facing, web browser-based dashboard (*React*) and a LLM-facing MCP server. The MCP server ships with preconfigured agent skills so your agent can automatically discover how to interact with `mnemonic`. 

## Is `mnemonic` right for your project?

- You build with Claude Code, OpenAI Codex, OpenCode and/or similar MCP client and, optionally, would like for them to coordinate work across platforms.

- Important FYIs and follow-up tasks are getting overlooked because they're buried under verbose LLM output.

- Dozens of transient Markdown documents are cluttering your repo. Or, your issue tracker has so many AI-written issues that it's become nearly unusable.

- You struggle to mentally hold onto the big picture of what your AI agents are building and why.

- Concurrent agents occasionally cause merge conflicts or reduplicate work, slowing progress and wasting tokens.

- You don't want the complexity of a full orchestration platform like OpenClaw and/or prefer a strong human-in-the-loop workflow.

### Why it might **not** be right for you

- **The human is the harness.** `mnemonic` requires an attentive human operator. Work is delegated out to agents by manually copying and pasting pre-written prompts into your MCP client/IDE and you have to be willing to be a part of the decision loop. If vibe coding is more your speed, where you're comfortable with nearly all decisions being made by the clankers, then `mnemonic` is going to feel like a lot of work. **The upside though, is that you understand what's being built and why** and you use your existing Claude Code/Codex/OpenCode, et al UI/TUI rather than an unobservable, headless harness.

- **It's not exactly lightweight.** `mnemonic` imports, normalizes, and indexes agent transcripts and creates embeddings of the text. This doesn't require a GPU, but it's not likely to run well on a Raspberry Pi and can consume quite a bit (tens of GB) of diskspace. Plus the Docker Compose stack is 6-7 containers (depending on your config). And: the dashboard UX take a while fully master and there are over 60+ MCP tools. **`mnemonic` was designed to help bring engineering rigor to AI-enabled development workflows** but doesn't pretend that cost is free. It's a tool for experienced developers.

- **`mnemonic` is overkill if you only run one session at a time.** The platform was designed (and has been tested) to juggle 3 to 15 concurrent agents. It will **dramatically** accelerate work throughput at that scale. However, the operational overhead of the MCP tool calls will nerf efficiency gains if you routinely use it with only one or two simultaneous sessions.

## Basic concepts

The included agent skills encourage the LLM to default to using `mnemonic` to save hand-off prompts and self-discovered follow-up tasks, rather than your bug/issue tracker, Markdown docs, or ephemeral "Suggested task chips". 

Upon discovering something worth doing, but is out-of-scope of the current task, the agent will first search `mnemonic` for related work items using sophisticated full-text matching. If no matches are found, the agent opens a new *Pending* work item in the `mnemonic` dashboard -- where it waits until you tell Claude Code/Codex/OpenCode, etc. to pick it up and get to work.

### Just copy-and-paste the prompt

When you decide to have your agent work on the task, you **click the "Copy recall pointer" button of the task card and paste the copied prompt into a fresh session** and... *that's pretty much it*. Everything else happens automatically.

The LLM will then start a new session, retrieve the work item, and validate the stated premises. If the facts check-out, it requests a temporary "work lease" and then begins working. The lease is periodically renewed until the task is complete and then work item is marked as *Done*. If the agent dies before completing the task, the lease expires and another session can pick it up again. A healthy agent though will periodically update the task with detailed checkpoints and, optionally, request a code review once done.

### "I need a human!"

**The human-required copy-and-paste step is deliberate.** It allows you to balance your weekly usage quota or API costs between your normal development work and working through the `mnemonic` backlog. And it keeps you working inside your already familiar UI.

If an agent hits a blocker that needs a human-needed decision, the work is parked in *Needs Attention* and returns only after you record an answer in the dashboard. These decisions are recorded and become Product Decision Records (PDRs) in the project knowledge graph (see *"Project-scoped knowledge graph"*, below). 

For non-blocking FYIs, the *"Summaries"* list contain simplified, plain-language descriptions of what the agent accomplished once it marks a work item as done.

## Other key features

 - **Project-scoped knowledge graph** -- Decisions (both human and clanker) are recorded in Markdown *decision documents* that are centralized, versioned, and saved in the database. Every PR and work item cites the decisions it relied upon when authoring code. This allows you (and agents) to bidirectionally trace the provenance of decisions up and down the graph: 

   - *"What information did the agent rely upon to decide to implement X instead of Y here?"* 
   - *"If I were to change my mind and decide this app needed to support multiple users, what downstream parts of the architecture needs to be revisited?"*

   This feature is especially handy to precisely determine the blast radius of a mistake made dozens of PRs or weeks earlier.

 - **Code review hand-offs** -- Projects can also require or invite an adversarial code review at configurable priority thresholds (both default to Never). Reviewers lease the original *Done* item and perform either a warm or cold code review. See [code reviews](docs/code-reviews.md) for the complete workflow.

- **Cross-platform coordination** -- Claude Code, OpenAI Codex, OpenCode, et al. can all be used simultaneously in the same project and intelligently coordinate amongst themselves.

- **Plain English work summaries** -- The "Summaries" inbox provides an easy-to-read, durable record of what each session did so you don't have to slog through every dense transcript.

- **External trackers and duplicate comparison** -- Automatically finds duplicate tasks in your repo's GitHub Issues (or similar) and includes them by reference. Mitigates two, conflicting homes for agent-to-agent coordination.

- **Project artifact library** -- Some content shouldn't be committed in your repo (docs with PII, large binaries, etc). The project artifact library allows both agents and humans to upload, download, search, and reference these files stored in your local filesystem. The artifact library is a lightweight alternative to a traditional document management system (DMS) and can be optionally disabled in the project environment.

- **Session transcript archival** -- Once an agent transitions a work item to *Done*, `mnemonic` copies that transcript (and any subagent transcripts) into the mnemonic project store. Each transcript is normalized to a common, cross-vendor format and indexed. This allows Codex agents to search and read Claude Code agents' past work activity and vice versa.

- **Unified search** -- Agents can search across work items, artifacts, decision documents, and session transcripts to retrieve relevant information in one-shot, with results being ranked using an intelligent, composite score. This saves time, tokens, and agent context space. This hybrid search is powered by a combination of *Apache Tika*, *Tantivy*, and embeddings for semantic search.

---

## Run it

### Copy and paste into your LLM to have it handle this part.

```text
Install and configure mnemonic for me from https://github.com/RightBrain-Networks/mnemonic. Clone the repository, then follow AGENT-README.md exactly. Use the default local-only Docker Compose configuration unless I state otherwise, preserve any existing data and configuration, connect my MCP-capable LLM client if one is available, and continue until the documented acceptance checks pass or you need a specific decision or credential from me.
```

### Or, if you prefer, old school human instructions...

Requirements: Git, Docker with Linux containers, and Docker Compose v2 (`docker compose`). The optional settings helper uses Python 3.10 or newer; no host Node or Python is needed to run the containers after `.env` is configured.

For transcript capture, add `--transcript-source /absolute/claude/projects`, `--codex-source /absolute/codex/sessions`, or `--codex-archived-source /absolute/codex/archived_sessions` to the setup command below before creating `.env`. Use only existing, operator-approved directories; the helper selects their owner's UID/GID. Without configured sources, transcript filesystem access stays disabled. See [transcript setup](docs/transcripts.md#filesystem-deployment).

```sh
git clone https://github.com/RightBrain-Networks/mnemonic.git
cd mnemonic
python scripts/setup.py
```

Before starting Compose, create the five private bind directories shown by the helper. Substitute the configured paths and `MNEMONIC_API_UID`/`MNEMONIC_API_GID` if customized. For the default paths and service UID/GID `10001:10001` on Linux:

```sh
sudo install -d -m 0700 -o 10001 -g 10001 ./artifacts
sudo install -d -m 0700 -o 10001 -g 10001 ./prompts/runtime
sudo install -d -m 0700 -o 10001 -g 10001 ./backups
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/mnemonic/transcript-index
sudo install -d -m 0700 -o 10001 -g 10001 /var/lib/mnemonic/transcripts
docker compose config --quiet
docker compose up --build -d --wait
```

Docker Desktop must be able to share those host directories. After startup, `python scripts/check_transcript_access.py` checks both services' transcript access. The helper leaves an existing `.env` unchanged; use the [upgrade procedure](docs/operations.md#current-coordinated-cutover) for an existing installation.

`sh ./up_mnemonic.sh` starts the stack with the TLS allowlists from
`compose.tls.yaml`; use it only after configuring `MNEMONIC_TLS_HOST` and the nginx TLS proxy below.

On macOS/Linux the Python command may be `python3`. Alternatively, copy `.env.example` to `.env` and supply four independent random secrets yourself: URL-safe `POSTGRES_PASSWORD` and `MNEMONIC_RABBITMQ_PASSWORD` values, plus `MNEMONIC_API_KEY` and `MNEMONIC_BACKUP_TOKEN` values of at least 32 characters each. The
example deliberately contains no usable credentials. Keep `.env` private (`chmod 600 .env` on POSIX systems). Never commit `.env`.

Open `mnemonic`. Create your first project using the project selector, then connect your agent to create or continue durable work. The application starts empty; there are no fabricated objectives, checkpoints, or session IDs.

| Service                | `.env` variable     | Default local address                             |
| ---------------------- | ------------------- | ------------------------------------------------- |
| Dashboard              | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000)           |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP    | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp`                       |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so change one there if it collides with something already running on the host, and substitute your own value wherever this README shows a port. If the web port changes, update `MNEMONIC_DASHBOARD_ORIGINS` to match both `http://localhost:<port>` and `http://127.0.0.1:<port>`. With `uv` and Python 3.14 available, `uv run --project mcp python scripts/check-stack.py` resolves all three from `.env` and performs the read-only live-stack checks. See [`docs/development.md`](docs/development.md) before opting into its write path.

The seven services are `postgres`, `rabbitmq`, `api`, `tika`, `mcp`, `web`, and `worker`; the shared worker handles background jobs and project backups. PostgreSQL, RabbitMQ, Tika, and the worker have no published ports. Dashboard, REST, and MCP ports bind to loopback only. For LAN access through your existing nginx TLS proxy, use the
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
    "mnemonic": { "source": { "source": "github", "repo": "RightBrain-Networks/mnemonic" } }
  },
  "enabledPlugins": { "mnemonic@mnemonic": true }
}
```

For a local marketplace instead, use `{ "source": "directory", "path": "/absolute/path/to/mnemonic" }`.

Installing copies the plugin into `~/.claude/plugins/cache/` at its manifest version, so editing a skill in place does not change an installed copy. `claude plugin marketplace update mnemonic` refreshes the marketplace listing,
not the installed files. After a published plugin version changes, run `claude plugin marketplace update mnemonic`, then `claude plugin update mnemonic@mnemonic`, and restart Claude Code.

The current application/API/MCP/dashboard release is `0.87.0`, with plugin version
`0.52.0` and database head `0056_product_decisions`. Deploy those
surfaces and the worker together after backup and authorized writer quiescence.
See [the coordinated upgrade procedure](docs/operations.md#current-coordinated-cutover)
before upgrading an existing database; older processes must not run against the new schema.
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

Invoke `/mnemonic:mnemonic-save`, `/mnemonic:mnemonic-search`, or `/mnemonic:mnemonic-recall`, or ask Claude in natural language. The skills require the connected `mnemonic` MCP server. You can copy the selected project's ID from the dashboard, or use
`list_projects`. Session IDs are opaque text (often UUIDs), not integers, and refer to the originating LLM conversation, not the MCP transport session. 

See [`docs/agents.md`](docs/agents.md) for the workflow and client boundaries.

## Connect Codex or OpenCode

Merge [`examples/codex.config.toml`](examples/codex.config.toml) or [`examples/opencode.json`](examples/opencode.json) into the client's configuration, substitute your MCP port, and export `MNEMONIC_API_KEY` in the environment that launches the client. Codex uses `bearer_token_env_var`; OpenCode uses `{env:MNEMONIC_API_KEY}` with `oauth: false`.

Install the three complete portable skill folders using [`scripts/export_agent_skills.py`](scripts/export_agent_skills.py) and the [multi-client setup instructions](docs/mcp-clients.md#install-the-complete-workflow). Connecting MCP does not install the skills. For Codex, also merge the [AGENTS guidance](examples/codex-AGENTS.md) into existing instructions. Restart the client and verify tool discovery and `list_projects`; an empty list is valid.

## Operate and develop

```sh
docker compose ps
docker compose logs --tail=100 api mcp web worker rabbitmq
docker compose stop
docker compose up -d --wait
```

Normal stop/restart preserves the database. Production stop/restart or recreation requires explicit operator authorization. **Do not use `docker compose down -v` on your working stack:** it removes the database and RabbitMQ volumes. Backups are written to `./backups/<project UUID>/` by default as `.json.bz2` archives and are not committed. The worker schedules them every 24 hours by default and retains seven successful archives per project. No archive is expected before a real project exists. Verify a backup in **Settings → Project backups** after creating your first project. Copy them off the machine and
monitor available disk space; a local archive alone does not protect against disk loss. Artifact files, editable prompt files, and retained native transcripts require separate filesystem backups. Restore commands and security limits are in [`docs/operations.md`](docs/operations.md).
