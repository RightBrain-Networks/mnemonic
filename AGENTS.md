# Repository Guidelines

## Project Structure & Module Organization

Backend code, migrations, and tests live under `backend/`; the MCP adapter and tests under `mcp/`; and the Next.js app, components, libraries, tests, and assets under `frontend/`. Compose files are at root, with supporting material in `scripts/`, `docs/`, `examples/`, and `skills/`.

## Build, Test, and Development Commands

- `python scripts/setup.py`: create settings from `.env.example`.
- `docker compose up --build -d --wait`: build and start the complete stack.
- `docker compose -f compose.test.yaml up -d --wait`: start the isolated PostgreSQL test database.
- `cd backend && uv sync --frozen && uv run pytest -q && uv run ruff check .`: test and lint the API.
- `cd mcp && uv sync --frozen && uv run pytest -q`: verify the MCP package.
- `cd frontend && npm ci && npm test && npm run typecheck && npm run build`: verify the dashboard.
- `cd frontend && npm run test:e2e:stack`: provision and run the isolated Playwright acceptance stack.

Use Python 3.13, `uv`, and Node 24. Keep the backend and MCP virtual environments separate.

## Coding Style & Naming Conventions

Python uses four spaces, type hints, `snake_case`, and `PascalCase` classes. Ruff enforces 100-character lines and E, F, I, UP, and B rules. TypeScript uses two spaces, strict mode, `camelCase`, and `PascalCase` components. No frontend formatter or linter is configured. Name migrations like `0005_work_graph_backfill.py`.

## Testing Guidelines

Name Python tests `test_*.py`, Node tests `*.test.mjs`, and Playwright specs `*.spec.ts`. Add regression tests with behavior changes. PostgreSQL-marked tests require `TEST_DATABASE_URL`; a skipped database suite is not full validation.

## Isolated Worktree Workflow

Every session must use a linked worktree and topic branch; never develop in the primary `main` checkout. From a clean checkout:

```sh
git worktree add ../mnemonic-<topic> -b work/<topic> main
cd ../mnemonic-<topic>
```

Test and commit there. Return to the primary checkout, confirm it is clean and on `main`, then integrate and clean up:

```sh
git merge --ff-only work/<topic>
git worktree remove ../mnemonic-<topic>
git branch -d work/<topic>
```

If `main` advanced, rebase the topic branch, retest, then fast-forward. Remove only after the commit is on `main` and the worktree is clean.

## Commit & Pull Request Guidelines

Use short, specific, sentence-case subjects instead of generic `Updates`. Keep commits scoped. PRs should explain behavior and migration/config impact, link relevant work, list checks run, and include screenshots for visual changes. Never commit `.env`, keys, test output, backups, or database volumes.
