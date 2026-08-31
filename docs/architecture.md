# Mnemonic MVP architecture

This decision implements the owner's revised standalone-application scope. The
original [`ADR.md`](../ADR.md) remains the historical proposal; its memory-store,
hook, and host integration decisions do not apply here. The optional local
semantic retrieval described below is part of the standalone application.

## Product contract

Mnemonic is a durable home for complete, agent-authored hand-off prompts. A
prompt is enough context for a fresh session to investigate and continue work;
it is not a ticket or an implicit instruction from the owner. Capture includes
context, the intended outcome, durable references, known hazards, and concrete
verification steps. Agent skills enforce this writing discipline. The service
stores prompt and comment text without silently rewriting it or pretending to
verify its content. Each hand-off has an append-only, session-attributed progress
timeline. Completion atomically adds the completing session's work summary and
moves the hand-off to `done`, so lifecycle state retains its supporting context.

Projects partition the store. Originating client and session ID are required;
model, branch, verified commit, tags, and extensible JSON metadata travel with
the prompt. Session IDs are opaque strings, not integers: clients often use
UUIDs or other identifiers. An agent must not invent an originating session ID.

## Services and trust boundaries

```mermaid
flowchart LR
    Agent[Claude Code / other MCP client] --> MCP[MCP adapter :8001]
    MCP --> API[FastAPI :8000]
    User[Browser] --> Web[Next.js :3000]
    Web --> API
    API --> DB[(PostgreSQL)]
```

- PostgreSQL is the only durable application store, on a named Docker volume.
- FastAPI owns validation, project isolation, lifecycle, search, and persistence.
- The MCP service calls the REST API over HTTP; it has no database credentials,
  SQL, or duplicate storage logic. It supports Streamable HTTP and stdio.
- Next.js provides a project selector, search and status filters, full prompt
  viewing, editing, deleting, copy, progress comments, and completion summaries.
  Its same-origin server proxy holds the API key; the browser never receives that key.
- All published ports bind to loopback by default. API and HTTP MCP requests
  require a shared bearer key. The dashboard is for a trusted local user, not
  a multi-user deployment. Its proxy rejects untrusted hosts and cross-origin
  requests. Remote use requires HTTPS and an authentication boundary in front
  of the dashboard. ChatGPT connectivity/OAuth is a later integration, not
  something the local MVP claims to supply.

## Retrieval and lifecycle

PostgreSQL full-text search ranks title and summary ahead of prompt and comment
content; literal matching also finds identifiers, paths, and hand-off or comment
session IDs. That lexical
path remains the default. For a nonblank query, a caller can opt into semantic
search; the API fuses the lexical ranking with similarity from a local embedding
model. Both the dashboard toggle and MCP argument default to disabled, preserving
the existing search behavior unless the user requests hybrid retrieval.

The dense channel embeds each hand-off's title, summary, first 1,500 prompt
characters, and most recent 1,500 comment characters with
`BAAI/bge-small-en-v1.5`; queries use the model's retrieval prefix. Weighted reciprocal-rank fusion (`k=60`) favors the lexical channel 3:1,
retaining exact vocabulary as the stronger signal while adding conceptual matches.

The Docker build downloads the model into `/app/.embedding-cache`. The running
API sets Hugging Face offline mode, so it requires no hosted LLM, embedding
service, or model API key and does not send search text off the host.

The PostgreSQL `handoff_embeddings` table stores one `REAL[]` vector, model/config
tag, and SHA-256 content digest per hand-off. Semantic queries fill stale rows
lazily in batches of 16; a text or model/config change rebuilds that row. These
rows are disposable derived state even though they live in PostgreSQL and may appear
in a dump: canonical hand-off rows alone are sufficient for recovery. Search
still returns compact records without the full prompt. Recall explicitly fetches
one complete record. Agents search before saving to detect possible duplicates;
the database does not automatically merge similar work.

Statuses are `open`, `done`, `wont-do`, and `promoted`. Default searches only
return `open` records. Other statuses remain available through explicit filters.
Ordinary comments append without contending on the mutable hand-off version. A
`done` transition requires an atomic completion operation with that version and
a nonblank work summary; a stale completion cannot append a duplicate summary.
Promotion records an owner's decision; it does not create external issues.
Deletion removes a prompt and its timeline from normal reads and searches, using
a soft-delete timestamp for recovery. Edits, completion, and deletes require the
version that was read to prevent one browser or agent silently overwriting
another's changes.

## Durability and deliberate limits

Schema changes use Alembic migrations. Compose starts the API only after the
database is healthy, and the API runs migrations before serving. Database
backup and explicit restore procedures ship with the application. A small
additional backup container saves a checked PostgreSQL dump on startup and
daily into a private host directory; it does not delete historical dumps.
A persistent volume is not a backup: operators must monitor backup health and
copy dumps off the machine, and manage retention to avoid filling the disk.

There are no assignees, dependencies, due dates, issue synchronization, automatic
execution, memory hooks, or claims of verified freshness.
Skills carry the provenance warning and recheck cited state before execution.
