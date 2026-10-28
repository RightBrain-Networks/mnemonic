# Contributing to Mnemonic

Read [AGENTS.md](AGENTS.md) for the repository workflow, coding conventions,
versioning policy, and required checks. [Development documentation](docs/development.md)
covers the local environment and test suites.

Use Python 3.14, uv, and Node 24. Keep the backend and MCP virtual environments
separate. Create a linked worktree and a short-lived topic branch from the latest
remote `main`; submit changes through a pull request targeting `main`. Install
the required pre-commit hooks and keep commits scoped to one change.

Describe the problem and resulting behavior in the pull request, including
configuration or migration effects and the checks you ran. Add regression tests
for behavior changes and screenshots for visual changes. Required CI checks
must pass before merge.

Use an isolated test stack for development. Never interrupt a production stack
without its operator's explicit authorization. The root README is
human-authored; follow the editing restriction in AGENTS.md.

Report vulnerabilities through the process in [SECURITY.md](SECURITY.md), rather
than the public bug form.
