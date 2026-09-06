# mnemonic

> Lightweight coordination across coding agent sessions -- without littering your repo with random Markdown docs or blowing up your issue tracker.

**`mnemonic`** is a self-hosted coordination layer for ephemeral LLM coding agents. Its core thesis: agent sessions are temporary and failure-prone, so durable work should live in a *work graph* that survives sessions, rather than in Markdown scratch files, suggested task chips, or an issue tracker flooded with AI-generated tickets.

The project is a Docker Compose stack that combines a durable backend (*PostgreSQL*) and a RESTful API (*FastAPI*). The API has two consumers: a human-facing, web browser-based dashboard (*Next.js*) and a LLM-facing MCP server. The MCP server ships with preconfigured agent skills so your agent can automatically discover how to interact with `mnemonic`. It is designed for a single, local (human) user and supports multiple, concurrent development projects.

It does not modify any client's memory subsystem. Claude Code is the first client, but the API, metadata, and MCP interface do not depend on one LLM provider.

## Is `mnemonic` right for your project?

- You build with Claude Code or another MCP client such as OpenCode.

- Important FYIs and follow-up tasks are getting overlooked because they're buried under verbose LLM output.

- Dozens or hundreds of transient Markdown documents are cluttering your repo. Or, your issue tracker has so many AI-written issues that it's become nearly unusable.

- Concurrent agents occasionally cause merge conflicts or reduplicate work, slowing progress and wasting tokens.

- You don't want the complexity of a full orchestration platform like OpenClaw and/or prefer a strong human-in-the-loop workflow.

## Basic concepts

The included agent skills encourage the LLM to default to using `mnemonic` to save hand-off prompts and self-discovered follow-up tasks. Markdown docs and your bug/issue tracker (if specified) are reserved for durable human-facing information. Claude's "suggested task chips" are explicitly discouraged here since they live only in the ephemeral client and are easily lost.

Upon discovering something worth doing, but is out-of-scope of the current task, the agent will first search `mnemonic` for related work items using PostgreSQL keyword matching or semantic search (embeddings). If no matches are found, the agent opens a new work item in a "pending" state.

The human (you, presumably) then click the "Copy recall pointer" button of the task card and paste the copied prompt into a fresh session. Claude (or similar) will then retrieve the work item and validate the stated premises. If the facts check-out, it requests a "work lease" of 15 minutes and then begins working. The lease is periodically renewed until the task is complete and then work item is marked as "done".

The "human-required" copy-and-paste step is deliberate. It allows you to balance your weekly usage quota or API costs between your normal development work and working through the `mnemonic` backlog. If an agent hits a human-needed decision, the work is parked in *Needs Attention* and returns only after a person records an answer in the dashboard.

Projects can also require or invite an adversarial code review at configurable
priority thresholds (both default to Never). Reviewers lease the original Done
item; the dashboard offers a minimal **Cold review** prompt or a contextual warm
recall pointer. All actionable findings create a single linked remediation item.
Remediation reviews are off by default and structurally limited to one additional
generation. See [code reviews](docs/code-reviews.md) for the complete workflow.

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
not the installed files. After a published plugin version changes, run `claude plugin marketplace update mnemonic`, then `claude plugin update mnemonic@mnemonic`, and restart Claude Code. The current application/API/MCP release is `0.13.0`, with plugin version `0.14.0` and database head `0024_code_reviews`. Its repository-freshness helper requires Bash 3.2 or newer and Git 2.45 or newer in the explicitly selected local workspace. It provides:

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

## What Mnemonic currently does

- Separates durable work by project, with a project selector and project
  creation. One work-item card can represent checkpoints from many sessions.
- Stores immutable checkpoint text, tags, source client/session/model, optional
  session URL, caller-declared branch and checked commit, an ordered declared
  repository dependency scope, custom metadata, and timestamps. Empty scope is
  unknown; it is omitted from canonical responses to preserve historical
  receipts.
- Keeps title, retrieval summary, priority, lifecycle, and optimistic version on
  the small mutable work item rather than rewriting historical session context.
- Searches PostgreSQL full-text indexes and literal identifiers by default.
  An opt-in Semantic dashboard toggle and `search_work` argument add hybrid
  similarity ranking from a local embedding model; both default to disabled.
  The model runs offline and needs no hosted embedding service or model API key.
  Checkpoint text participates in lexical and semantic retrieval. Search
  returns one full summary hit per canonical group by default and identifies
  the exact matching member; recall returns bounded current context, and older
  history is explicitly paginated.
- Compares a complete creation draft on explicit action against exact-title,
  lexical, and optional local semantic lanes. The response groups aliases under
  one canonical candidate, identifies the matching member, exposes coverage and
  categorical signals rather than raw scores, persists neither draft nor result,
  and never disables independent creation.
- Lets a user edit work identity, append immutable context/progress checkpoints,
  complete work with a required completion checkpoint and optional structured
  verification results and artifact references, copy current context, and
  soft-delete work. Evidence is immutable, caller-reported, inert history bound
  to that exact completion episode; stored commands are never executed and
  artifact locators are never fetched automatically. Concurrent
  edits and completions are detected rather than silently overwriting changes;
  independent checkpoint appenders can both succeed.
- Keeps `deferred`, `done`, `wont-do`, and `promoted` work out of the default
  pending view while retaining them under explicit filters. The work-detail
  Defer button is a split control whose menu offers Pending, Active, Done,
  Won’t Do, and Promote while omitting the current state. Every selection is
  recorded with dashboard human provenance; manual Done also creates a
  decision-only completion checkpoint and a truthful closeout report without
  inventing implementation or verification evidence. Deleted work and its
  checkpoints are hidden from ordinary reads but retained for recovery.
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
  undirected. A historical `duplicate-of` mark is descriptive evidence only;
  fresh marks are created only as part of an authoritative merge.
- Records an authoritative duplicate merge as one immutable
  `source --duplicate-of--> direct destination` decision. The source becomes a
  retained, non-actionable alias while keeping its lifecycle, checkpoints,
  gates, events, provenance, receipts, and relationships. Exact alias reads
  remain source-owned and separately point through a bounded path to the
  current canonical root; Mnemonic never redirects the supplied ID, blends
  contexts, transfers relationships or leases, or coalesces content.
- Makes only unresolved incoming `blocks` edges affect readiness and claim
  eligibility. `done` resolves a blocker; `wont-do` and `promoted` do not.
  Active work may become blocked without revoking its existing lease.
- Exposes dedicated `ready-work` REST and `list_ready_work` MCP reads. Ready
  items are pending, visible, unblocked, unleased, and have no unresolved human
  gate and are not duplicate aliases at one database-time snapshot, ordered by priority descending, then
  creation time and UUID. The
  compact result is advisory: `claim_and_recall` revalidates before execution.
- Stores append-only, actor-attributed work events for creation, work changes,
  claims/releases, checkpoints, relationships, completion/reopen, deletion,
  explicit concise progress, human-gate requests/resolutions, and paired
  `work_merged` and `work_moved` audit facts. Authoritative
  events commit with the mutation they describe; canonical idempotent replays
  and natural no-ops do not fabricate duplicates.
- Moves one stable work UUID between projects through the REST/dashboard human
  control plane while preserving its lifecycle status and project-at-fact
  history. Active leases, unresolved gates, relationships, duplicate
  membership, and unsealed terminal history block a fresh move.
- Provides first-class human gates with exact question/answer history, asserted
  requester/resolver provenance, immutable request and resolution revisions,
  drift flags and a required reviewed revision, a cursor-paged Needs Attention queue, per-work history,
  and bounded gate slices in recall. Unresolved gates block fresh claims,
  completion, terminal transitions, and deletion without revoking exact active
  claim replay, renewal, release, checkpoints, or progress. Resolution is a
  direct REST/dashboard human action; MCP intentionally has no resolve tool.
- Makes retries safe for eighteen project-scoped REST mutations with
  caller-generated `client_operation_id` values and durable typed success
  receipts. Exactly thirteen of the 38 canonical MCP tools require the UUID,
  including `request_human_input` and `merge_work`; the dashboard retains frozen
  same-document requests for its fifteen non-capability mutations, including
  deferral, move, gate resolution, merge, report dismissal, and report follow-ups.
  Every fresh closeout, merge, dismissal, report follow-up, and dashboard move
  requires an operation UUID. Exact retries return the original historical result
  before fresh domain guards, including previously acknowledged report-free
  closeouts and source-scoped completed moves.
- Requires a concise human summary and optional FYI bullets on each fresh Done,
  Won’t do, or Promoted closeout. Agents author the report assuming the reader
  has read no other LLM output. Projects ship a sensible authoring prompt,
  editable independently of recall pointer content at `/settings`.
- Provides a top-level Summaries inbox. Dismissal hides a report from the default
  inbox while retaining it in the API. Create Follow-up opens manually reviewed
  pending work linked to both the report and its original work item; it neither
  dismisses the report nor assigns an agent.
- Exposes a durable, commit-ordered project activity cursor. The dashboard uses
  authenticated polling plus data-free socket hints to catch up after missed
  notifications. Historical imports contain recorded work events only. SSE,
  webhooks, and arbitrary resource reservations remain future work.
- Keeps checkpoints and events separate. A checkpoint is substantial resume
  context; a progress event is a short historical fact. Recall includes at most
  20 recent events, while the dashboard pages the complete per-work Activity
  timeline and labels reconstructed pre-Phase-5 history honestly.
- Packages a bounded, filter-free, read-only Git assessor for the Claude plugin.
  It compares the exact governing checkpoint baseline and declared scope with
  committed, staged, unmerged, raw worktree, and nonignored-untracked evidence.
  It fails closed on unmatched patterns, unsupported index/configuration state,
  repository movement, ambiguous objects, and command/resource failures. It
  never clones, fetches, mutates the repository, runs configured filters, or
  sends a project URL to Git. The backend, MCP adapter, and browser only
  transport/display declarations and never perform this assessment.
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
  merged duplicate count, discovery labels, and the next active descendant lease expiry. Subtree-aware
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
relationships or merges from semantic similarity, run duplicate comparison on
each keystroke, suppress creation, repair/unmerge a mistaken
merge, persist repository-freshness results, or reserve repository resources.
Every merge is an explicit permanent
operation; correction requires a whole-database restore that discards later
writes or a future append-only correction design. Mnemonic is deliberately
LLM-centric: checkpoints and relationship context record an agent's claims
rather than server-verified proof. “No relevant Git change observed” is a
bounded point-in-time comparison, not proof that a checkpoint is correct,
current, or safe; the browser and service never claim otherwise.

## Operate and develop

```sh
docker compose ps
docker compose logs --tail=100 api mcp web backup
docker compose stop
docker compose up -d --wait
```

Normal stop/restart preserves the database. **Do not use `docker compose down -v` on your working stack:** it removes the data volume. Backups are written to `./backups` by default and are not committed. Copy them off the machine and
monitor available disk space; a local dump alone does not protect against disk loss. Restore commands and security limits are in [`docs/operations.md`](docs/operations.md).

## External trackers and duplicate comparison

Work items can retain up to ten ordered external references. “Tracked by” means
the record tracks the objective; “Reference” means supporting context. Ready-work
rows show these links and caller-observed state/time before selection, even when
a summary is stale. References are mutable context, separate from immutable
completion evidence. Editing replaces the complete list; clearing sends `[]`.
A closed issue hint never changes readiness or closes Mnemonic work automatically.

Keep park-then-file and attach the actual stable credential-free URL afterward.
Use the [reference update example](examples/external-reference-update.json), then
reread versions before a separately authorized report-required closeout. Exact
inverse lookup includes URL case, query and fragment: search `view=full`,
`status=all`, `duplicate_scope=all`, and paginate all matches. Normal lookup/claim
practice cannot coordinate an external worker that never consults Mnemonic.

The explicit duplicate comparison supports a separate list of up to 64 supplied
external records alongside internal matches. The dashboard accepts manual fields;
the plugin can gather during explicit comparison through existing provider access
with a repository URL and bounded reads. Mnemonic stores no provider credentials
and fetches no provider data. Failed comparison still allows Create anyway.
See the [agent workflow](docs/agents.md#external-first-and-park-then-file-workflows)
and [offline frame example](examples/external-candidate-frame.py). The complete
MCP frame has a 1 MiB limit, including envelope and text escaping.
