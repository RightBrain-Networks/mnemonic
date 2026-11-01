# mnemonic

> The anti-vibe coding AI tool built for experienced engineers.

**`mnemonic`** is a single-user, self-hosted coordination plane for LLM coding agents. Its core thesis: agent sessions are temporary, failure-prone, and locally smart but globally blind. Durable work should live in a *work graph* that survives sessions, rather than in Markdown scratch files, suggested task chips, or an issue tracker flooded with AI-generated tickets. With a strong human-in-the-loop philosophy, it is designed to increase the code quality and throughput of experienced developers building with AI, rather than replace their thinking.

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
Install and configure mnemonic for me using a published versioned release bundle from https://github.com/RightBrain-Networks/mnemonic/releases. Follow docs/releases.md and docs/operations.md for that release. Use the default local-only Docker Compose configuration unless I state otherwise, preserve any existing data and configuration, connect my MCP-capable LLM client if one is available, and continue until the documented acceptance checks pass or you need a specific decision or credential from me. If no release is available, explain that before using the contributor build instructions in AGENT-README.md.
```

### [Or, if you prefer, old school human instructions...](docs/INSTALLATION.md)
