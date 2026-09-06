# Repository Guidelines

## Project Structure & Module Organization

Backend code, migrations, and tests live under `backend/`; the MCP adapter and tests under `mcp/`; and the Next.js app, components, libraries, tests, and assets under `frontend/`. Compose files are at root, with supporting material in `scripts/`, `docs/`, and `examples/`. The Claude Code plugin — the three skills and their shared references — lives under `plugin/`, with the marketplace manifest in `.claude-plugin/`.

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

The current application/API/MCP/dashboard release is `0.12.0`, Claude plugin
`0.13.0`, and Alembic head `0023_work_item_moves`. The catalog is exactly
32 MCP tools, 11 receipt-protected MCP writes, 16 REST receipt kinds, 14 protected
browser mutations, 18 work-event types, and three plugin skills. The suggestion
POST is a safe read. Completion evidence and job completion reports are nested
only in the existing closeout mutations; do not add standalone agent writes.
Fresh work starts pending. Every actual Done, Won’t do, or Promoted closeout
requires a report and operation UUID. Sparse historical requests remain
parseable exclusively for permanent receipt replay before fresh domain guards.
Do not run older processes against this schema, infer historical reports, or add
projection, redirect, coalescing, or compatibility execution paths.

## Commit & Pull Request Guidelines

Use short, specific, sentence-case subjects instead of generic `Updates`. Keep commits scoped. PRs should target `main`, remain short-lived, and explain behavior and migration/config impact, link relevant work, list checks run, and include screenshots for visual changes. The `Required checks` GitHub Actions status must pass before merge; never bypass the gitleaks hook with `--no-verify`. Never commit `.env`, keys, test output, backups, or database volumes.

`CLAUDE.md` is an intentionally local, ignored operator/client note rather than a
tracked source of truth. Refresh any local copy when a phase changes the tool
catalog, migration head, shipped-phase status, client retry rules, or error codes.
