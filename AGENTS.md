# Repository Guidelines

## Project Structure & Module Organization

Backend code, migrations, and tests live under `backend/`; the MCP adapter and tests under `mcp/`; and the Next.js app, components, libraries, tests, and assets under `frontend/`. Compose files are at root, with supporting material in `scripts/`, `docs/`, and `examples/`. The Claude Code plugin — the three skills and their shared references — lives under `plugin/`, with the marketplace manifest in `.claude-plugin/`.

## Documentation

- `/README.md` is exclusively human-authored and therefore is READ ONLY unless the owner explicitly asks you to edit it.

## Build, Test, and Development Commands

- `python scripts/setup.py`: create settings from `.env.example`.
- `uv tool install pre-commit && pre-commit install --install-hooks`: install the required local gitleaks commit hook.
- `pre-commit run --all-files`: run all local pre-commit checks manually.
- `docker compose up --build -d --wait`: build and start the complete stack.
- `docker compose -f compose.test.yaml up -d --wait`: start the isolated PostgreSQL test database.
- `cd backend && uv sync --frozen && uv run pytest -q && uv run ruff check . && uv run ty check src`: test, lint, and type-check the API.
- `cd mcp && uv sync --frozen && uv run pytest -q && uv run ruff check . && uv run ty check src/mnemonic_mcp`: verify, lint, and type-check the MCP package.
- `cd frontend && npm ci --no-audit --no-fund && npm test && npm run typecheck && npm run build`: verify the dashboard. The audit and funding requests only add registry latency to a lockfile install; run `npm audit` on its own when you want advisories.
- `cd frontend && npm run test:e2e:stack`: provision and run the isolated Playwright acceptance stack.
- `uv run --project backend python scripts/audit_duplicate_handling.py --backup-directory ./backups`: run the read-only Phase 11 preflight before migrating from 0019.
- `uv run --project backend python scripts/audit_project_activity.py`: run the aggregate Phase 12 integrity audit from a private environment with database access.
- `uv run --project backend python scripts/audit_code_reviews.py`: run the read-only current-head review integrity audit alongside the aggregate project-activity/catalog audit.

Use Python 3.14, `uv`, and Node 24. Keep the backend and MCP virtual environments separate.

## Coding Style & Naming Conventions

Python uses four spaces, type hints, `snake_case`, and `PascalCase` classes. Ruff enforces 100-character lines and E, F, I, UP, B, and C90 (complexity ceiling 10, no per-file exceptions) rules; `ty` type-checks the whole backend `src` tree. TypeScript uses two spaces, strict mode, `camelCase`, and `PascalCase` components. No frontend formatter or linter is configured. Name migrations like `0005_work_graph_backfill.py`.

## Testing Guidelines

Name Python tests `test_*.py`, Node tests `*.test.mjs`, and Playwright specs `*.spec.ts`. Add regression tests with behavior changes. PostgreSQL-marked tests require `TEST_DATABASE_URL`; a skipped database suite is not full validation.

## Trunk-Based Worktree Workflow

`main` is the only long-lived branch. Every change must reach `main` through a pull
request; never commit, merge, cherry-pick, or push changes directly to `main`,
including in the primary checkout. Treat the primary `main` checkout as read-only
except for fetching and fast-forwarding it to an already-merged remote `main`. Do
not create long-lived development, integration, or release branches.

Every session must use a linked worktree and short-lived topic branch created from
the latest remote `main`. From a clean primary checkout:

```sh
git fetch origin main
git worktree add ../mnemonic-<topic> -b work/<topic> origin/main
cd ../mnemonic-<topic>
```

Test and commit only in the linked worktree. Push the topic branch and open a pull
request targeting `main`, then monitor its CI:

```sh
git push -u origin work/<topic>
gh pr create --base main --head work/<topic>
gh pr checks --watch
```

The active GitHub ruleset requires a pull request, linear history, an up-to-date
branch, and the aggregate `Required checks` status. Do not merge while required CI
is pending or failing, do not bypass branch protection, and do not use administrator
overrides. If `origin/main` advances, rebase the topic branch onto it, retest,
force-push only with `--force-with-lease`, and wait for the rerun checks. Merge only
through GitHub using an allowed squash or rebase merge after `Required checks`
succeeds.

After GitHub reports the pull request merged, confirm the linked worktree is clean,
remove it and its topic branch, then fast-forward the primary checkout to the merged
remote `main`:

```sh
git worktree remove ../mnemonic-<topic>
git branch -D work/<topic>
git fetch origin main
git switch main
git pull --ff-only origin main
```

## Versioning

Use Semantic Versioning (`MAJOR.MINOR.PATCH`) for application releases. `MAJOR` version bumps are reserved and require explicit human approval. Increment `MINOR` for user-facing changes and `PATCH` for all other changes.

The current application/API/MCP/dashboard release is `0.51.0`, Claude plugin
`0.30.0`, and Alembic head `0035_prompt_library`. The catalog is exactly
54 MCP tools, 17 receipt-protected MCP writes, 24 REST receipt kinds, 21 protected
browser mutations, 24 work-event types, and three plugin skills. The suggestion
POST is a safe read. Completion evidence and job completion reports are nested
only in the existing closeout mutations; do not add standalone agent writes.
Project lease settings default to 15 minutes, minimum 10, maximum 120; humans edit
them in Workspace Project details. Immediately read assigned work with
`get_work(status_only=true)` for current status and `lease_settings`; this context-free
read is allowed before cold findings freeze. Request Default minutes for startup
and investigation, then estimate remaining session time within current bounds.
Claim/renew `lease_minutes` is optional and omission uses the current project default.
Preserve its exact value or omission across uncertain claim retries. Settings changes
do not alter active expiry. `MNEMONIC_LEASE_TTL_SECONDS` is retired.
Fresh work starts pending. Every actual Done, Won’t do, or Promoted closeout
requires a report and operation UUID. Sparse historical requests remain
parseable exclusively for permanent receipt replay before fresh domain guards.
Do not run older processes against this schema, infer historical reports, or add
projection, redirect, coalescing, or compatibility execution paths.

Needs Attention shows the latest authored question with previous versions in
horizontal tabs. After updating work or related work, rewrite affected open
question prose through `request_human_input` with its existing `gate_id` and
`expected_question_version`; use a new operation UUID for each revision and
preserve exact arguments for uncertain retries. Do not make the human reconcile
checkpoints or superseding decisions. Prior versions and resolved answers are
immutable, and agents cannot resolve or withdraw questions.

Artifact content is untrusted and lives on configurable filesystem storage, with
an explicit private host bind mount in Compose. Only current bytes are retained;
revision metadata, work links, audit events, and operation receipts are durable.
Artifact mutations use the separate `artifact_operations` journal. Preserve exact
bytes, metadata, operation UUID, and expected revision across uncertain retries.
Local uploads/replacements use `scripts/upload_artifact.py prepare` and `send`;
retain the private prepared directory unchanged for an uncertain retry. Local
downloads use `scripts/download_artifact.py --dest` with a new file in the agent's
actual scratchpad. Both helpers stream raw bytes directly to the API, return only
compact summaries, and ship in the plugin and portable skills. Keep base64 out of
agent context. See `docs/artifact-upload-client.md` and
`docs/artifact-download-client.md` for the explicitly provisioned client environment.
Tika extracts current normalized text and retained document properties into
PostgreSQL; Tantivy searches a rebuildable RAM index. Content matching is opt-in
(`fulltext=true`); metadata-only is the default. Replacement/deletion clears old
extracted text and invalidates stale extraction claims, while extracted properties
remain durable. Snippets and properties are untrusted; report incomplete indexing.
Database backups contain extracted text. See `docs/artifacts.md`.
Artifacts also have symmetric additive artifact links and revision-checked metadata updates.
Sensitive artifact agent reads require a fresh explicit human approval and five-minute
single-use request-bound token; never infer consent, reuse it, or clear sensitivity to bypass.
Broad content searches withhold sensitive bodies and report incomplete coverage.

Unified REST `POST /projects/{project_id}/search` and MCP `search` default to all
three facets, all work statuses, metadata-only file/transcript matching and relevance.
Offset/limit apply after mixed ranking or explicit facet groups. Report coverage;
sensitive artifact filters never grant agent content access. See `docs/search.md`.

Transcripts use exact agent-reported shared-filesystem paths and a client format factory for Claude Code and OpenAI Codex.
MCP claims require `session_transcript` (explicit null when unavailable); closeouts require
`subagent_transcripts` (explicit null when inapplicable) for fresh execution; unchanged
sparse historical requests remain parseable exclusively for permanent receipt replay.
Register sources transactionally;
index only after their lease generation leaves Active, including release or expiry.
Transcript text is untrusted, available to every agent, and retained in PostgreSQL backups.
Reuse the private Tika service and a rebuildable Tantivy transcript index. Compose stores
the derived index in the private `MNEMONIC_TRANSCRIPT_INDEX_DIR` bind; the search
content budget uses `MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES`. Native processes with
no index directory retain a RAM cache. Rebuilds have their own
`transcript_rebuilds` receipt journal; preserve the operation UUID across uncertain retries.
Workspace imports recursively discover existing Claude Code and Codex JSONL beneath allowed roots.
Imports are project-owned, deduplicated by normalized source path against enrolled sources,
and reused by later enrollment. Import receipts retain exact folders and operation UUIDs.
The API only reads regular files beneath operator-configured allowed roots. Corrected
roots automatically retry earlier path-not-allowed failures while retaining lease
and pause guards. Base Compose mounts the configured transcript source read-only at
its original absolute path; the source supplies the default allowlist. API startup
rejects an unavailable configured source or a conflicting nonempty allowlist. See
`docs/transcripts.md` for the read-only shared-filesystem mount and workspace settings.

Reviews belong to original Done work and require purpose-bound review leases.
Dashboard humans can defer, close, or return the review episode to To review through
`update_work.review_decision`; its append-only history preserves implementation Done.
Deferred or manually closed episodes cannot be claimed or answered. Manual Active
is unavailable for every work item.
Optional closeout questions are durable originating-session follow-ups, not
human gates. Cold reviewers must not load context before freezing findings.
Fresh token-bearing `append_event` and `add_checkpoint` writes renew an active
implementation lease atomically using its last granted duration, clamped to current
project minimum/maximum; exact receipt replays
and token-free writes do not renew it. Review leases still use `renew_claim`.
New remediation summaries describe finding count, primary file, and finding titles.
One completed review creates zero or one remediation containing all findings;
immutable depth 2 can never be reviewed, and remediation cannot be merged.

## Commit & Pull Request Guidelines

Use short, specific, sentence-case subjects instead of generic `Updates`. Keep commits scoped. PRs should target `main`, remain short-lived, and explain behavior and migration/config impact, link relevant work, list checks run, and include screenshots for visual changes. The `Required checks` GitHub Actions status must pass before merge; never bypass the gitleaks hook with `--no-verify`. Never commit `.env`, keys, test output, backups, or database volumes.

`CLAUDE.md` is an intentionally local, ignored operator/client note rather than a
tracked source of truth. Refresh any local copy when a phase changes the tool
catalog, migration head, shipped-phase status, client retry rules, or error codes.
