# Installation

Requirements: Docker with Linux containers and Docker Compose v2 (`docker compose`).
The commands below use a POSIX shell, such as Linux, macOS, or WSL. Published-image
installation needs no host Python, Node, application environment, or source build.

For a published release that includes an install bundle, download its `mnemonic-X.Y.Z.tar.gz`
and checksum from [GitHub Releases](https://github.com/RightBrain-Networks/mnemonic/releases),
verify the checksum, and extract it. From the extracted directory, run the following,
replacing `X.Y.Z` with that same published version:

```sh
docker run --rm --pull always --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$(pwd),target=/release" --entrypoint python \
  ghcr.io/rightbrain-networks/mnemonic-api:X.Y.Z /release/setup.py
docker compose config --quiet
docker compose pull
docker compose up -d --wait
```

The first command runs setup inside the selected API image and creates a private
`.env` owned by your host user, without sudo. It does not start the application.
If Python 3.10 or newer is already installed, `python3 setup.py` is an optional
replacement for that first command. Existing `.env` files remain unchanged.

Use an actually published release and its matching bundle; a version mentioned on `main`
does not imply that its images are available. Until that release is published, or when
working on the source, use the contributor overlay:

```sh
git clone https://github.com/RightBrain-Networks/mnemonic.git
cd mnemonic
python3 scripts/setup.py
docker compose -f compose.yaml -f compose.build.yaml config --quiet
docker compose -f compose.yaml -f compose.build.yaml up --build -d --wait
```

New setup files select private named volumes. The one-shot `init-storage` container prepares
them for the configured service UID/GID without host sudo commands; `migrate` initializes the
database before API and worker startup. Keep the same Compose project name for later runs.
The source overlay builds images with local names, separate from published release tags.

For transcript capture, add `--transcript-source /absolute/claude/projects`,
`--codex-source /absolute/codex/sessions`, or
`--codex-archived-source /absolute/codex/archived_sessions` to the setup command before creating
`.env`. When running setup in Docker, also mount each chosen source read-only at its
identical absolute path in that setup container, using
`--mount type=bind,source=/absolute/source,target=/absolute/source,readonly` before the image
name. Alternatively, run the setup command with host Python 3.10 or newer.
Use only existing operator-approved directories. The helper selects their owner's
UID/GID, and API and worker read those paths through read-only binds. Docker Desktop must be
allowed to share configured source directories. Without sources, capture stays disabled.
See [transcript setup](transcripts.md#filesystem-deployment).

**Existing installs:** setup leaves `.env` unchanged. Legacy bind installations must preserve
all old storage paths and use `-f compose.yaml -f compose.bind-mounts.yaml` on every Compose
command, adding the build or TLS overlay when needed. Existing named-volume installations
retain their volumes and Compose project name without the bind overlay. Base Compose
intentionally refuses an implicit legacy storage mode.
Follow [Upgrade from any release](operations.md#upgrade-from-any-release) before starting
new images against existing data. That procedure stops old writers and backs up before migration.

`sh ./up_mnemonic.sh` starts published images with the TLS allowlists from `compose.tls.yaml`;
use it only after configuring `MNEMONIC_TLS_HOST` and the nginx TLS proxy. Existing bind
installs and contributor builds should use explicit `-f` arguments for all their overlays.

On macOS/Linux the Python command may be `python3`; use `python` where that is Python 3.
Alternatively, copy `.env.example` to `.env` and supply four independent random secrets:
URL-safe `POSTGRES_PASSWORD` and `MNEMONIC_RABBITMQ_PASSWORD`, plus `MNEMONIC_API_KEY` and
`MNEMONIC_BACKUP_TOKEN` of at least 32 characters each. The example contains no usable
credentials. Keep `.env` private (`chmod 600 .env` on POSIX systems), and never commit it.
New configurations use UTC for dashboard times; explicit existing `TIMEZONE` values are preserved.

Open `mnemonic`. Create your first project using the project selector, then connect your agent to create or continue durable work. The application starts empty; there are no fabricated objectives, checkpoints, or session IDs.

| Service                | `.env` variable     | Default local address                             |
| ---------------------- | ------------------- | ------------------------------------------------- |
| Dashboard              | `MNEMONIC_WEB_PORT` | [localhost:3000](http://localhost:3000)           |
| REST API documentation | `MNEMONIC_API_PORT` | [localhost:8000/docs](http://localhost:8000/docs) |
| MCP Streamable HTTP    | `MNEMONIC_MCP_PORT` | `http://127.0.0.1:8001/mcp`                       |

Those addresses are defaults, not fixed values. Each port is set in `.env`, so change one there if it collides with something already running on the host, and substitute your own value wherever this guide shows a port. If the web port changes, update `MNEMONIC_DASHBOARD_ORIGINS` to match both `http://localhost:<port>` and `http://127.0.0.1:<port>`.

Run the read-only stack check inside the installed MCP container, which supplies the required
Python dependencies and API key. These service addresses use internal ports even when host
ports have changed; retain your installation's Compose overlays:

```sh
docker compose exec -T mcp python - \
  --api-url http://api:8000 \
  --mcp-url http://mcp:8001/mcp \
  --web-url http://web:3000 --web-origin http://localhost:3000 < scripts/check-stack.py
```

Set `--web-origin` to a dashboard origin configured in `MNEMONIC_DASHBOARD_ORIGINS`;
substitute the actual port or HTTPS origin when using changed ports or TLS.
Do not add `--project-id` to an installation check; that enables durable test mutations in
the named project. See [`docs/development.md`](development.md) for disposable test usage.

The seven running services are `postgres`, `rabbitmq`, `api`, `tika`, `mcp`, `web`, and `worker`; one-shot `init-storage` and `migrate` services complete before application startup; the shared worker handles background jobs and project backups. PostgreSQL, RabbitMQ, Tika, and the worker have no published ports. Dashboard, REST, and MCP ports bind to loopback only. For LAN access through your existing nginx TLS proxy, use the
[nginx configuration and setup guide](../deploy/nginx/README.md).

## Connect Claude Code

Set `MNEMONIC_API_KEY` in the terminal environment to the key from your private `.env` file. Set `MNEMONIC_MCP_PORT` as well if you changed it from the default. Then register the HTTP MCP endpoint:

```sh
claude mcp add --transport http --scope user mnemonic "http://127.0.0.1:${MNEMONIC_MCP_PORT:-8001}/mcp" --header "Authorization: Bearer ${MNEMONIC_API_KEY}"
```

Registering the endpoint does not connect it to a running session. Claude Code loads MCP servers at startup, so start a new session afterwards.

In PowerShell, the URL and header expressions are `"http://127.0.0.1:$env:MNEMONIC_MCP_PORT/mcp"` and
`"Authorization: Bearer $env:MNEMONIC_API_KEY"`; PowerShell has no `:-` default, so set the port variable explicitly there. Do not paste the real key into tracked project configuration. Configuration examples, including a Docker stdio
alternative and OpenCode, live in [`examples/`](../examples/); they show the default ports and need the same substitution if yours differ. [`work.json`](../examples/work.json) is the canonical work body. Copyable workflows are [`duplicate-suggestion.json`](../examples/duplicate-suggestion.json), [`discovered-work.json`](../examples/discovered-work.json), [`human-gate-request.json`](../examples/human-gate-request.json), [`human-gate-resolution.json`](../examples/human-gate-resolution.json), and the irreversible [`merge-work.json`](../examples/merge-work.json) request. Replace every placeholder and read both exact work contexts before using the merge example.
The atomic completion examples are
[`completion-with-evidence.json`](../examples/completion-with-evidence.json) and
[`completion-without-evidence.json`](../examples/completion-without-evidence.json),
with a two-episode response in
[`completion-evidence-history.json`](../examples/completion-evidence-history.json).
Replace every provenance, version, UUID, result, and artifact placeholder with
facts from the exact attempt; never copy example evidence as if it were
observed.

Optional local skills ship as a Claude Code plugin. For a published server release, register its
version-pinned marketplace and enable the matching plugin. From the extracted, checksum-verified
release directory, copy its catalog into a persistent marketplace directory:

```bash
mnemonic_catalog_dir="$HOME/.local/share/mnemonic/marketplace"
mkdir -p "$mnemonic_catalog_dir/.claude-plugin"
cp marketplace.json "$mnemonic_catalog_dir/.claude-plugin/marketplace.json"
claude plugin marketplace add "$mnemonic_catalog_dir"
claude plugin install mnemonic@mnemonic
```

Keep that directory for future plugin refreshes. Claude Code 2.1.303 interprets the GitHub
release download URL as a Git repository when passed directly to `marketplace add`; the local
directory avoids that failure. Its catalog still pins the release tag and full source commit.
The release's compatibility table records the independent plugin version.

The registration stores a directory source in your Claude settings. If you manage those
settings yourself, use the actual absolute path to that persistent directory:

```json
{
  "extraKnownMarketplaces": {
    "mnemonic": { "source": { "source": "directory", "path": "/absolute/path/to/mnemonic/marketplace" } }
  },
  "enabledPlugins": { "mnemonic@mnemonic": true }
}
```

Contributors can register a local checkout with `claude plugin marketplace add /path/to/mnemonic`,
or use `{ "source": "directory", "path": "/absolute/path/to/mnemonic" }` in their settings.
That local catalog follows the checkout. Published catalogs pin both a release tag and its source commit.

Before upgrading the server, replace the local catalog with `marketplace.json` from the chosen
verified release and check its plugin
compatibility entry in [the release](https://github.com/RightBrain-Networks/mnemonic/releases). See
[release installation and verification](releases.md) for checksums and provenance.

Installing copies the plugin into `~/.claude/plugins/cache/` at its manifest version, so editing a skill in place does not change an installed copy. `claude plugin marketplace update mnemonic` refreshes the marketplace listing,
not the installed files. After a published plugin version changes, run `claude plugin marketplace update mnemonic`, then `claude plugin update mnemonic@mnemonic`, and restart Claude Code.

Use a matching release of the application, worker, and compatible plugin. See
[GitHub Releases](https://github.com/RightBrain-Networks/mnemonic/releases) and
[the coordinated upgrade procedure](operations.md#upgrade-from-any-release) before
upgrading an existing database; older processes must not run against the new schema.
After connecting, call `session_init()` when its response is absent from the
conversation. Resolve the project with `list_projects` when needed, then read
`get_project(project_id, settings_only=true)`. Use `help(topic="COMMAND usage")`
for current requirements, examples, and links to detailed help.

The optional skills are thin command-help shortcuts with focused local resources:

- **`mnemonic-save`** routes requested writes and provides local artifact upload
  and decision-provenance helpers.
- **`mnemonic-search`** routes search and bounded project activity updates and
  provides a local artifact download helper.
- **`mnemonic-recall`** routes selected work and claims, with native transcript
  verification, repository assessment, and decision-provenance helper guidance.

Invoke `/mnemonic:mnemonic-save`, `/mnemonic:mnemonic-search`, or
`/mnemonic:mnemonic-recall`, or ask Claude in natural language. Skills require the
connected MCP server; command help remains available without skills. The optional
repository helper requires Bash 3.2 or newer and Git 2.45 or newer in the selected
workspace; Python helpers require Python 3.10 or newer.

See [agent onboarding](agents.md) for help navigation and owner-selected
[workflow starters](../examples/agent-workflows/README.md). These starters are
never enabled or updated automatically.

## Connect Codex or OpenCode

Merge [`examples/codex.config.toml`](../examples/codex.config.toml) or [`examples/opencode.json`](../examples/opencode.json) into the client's configuration, substitute your MCP port, and export `MNEMONIC_API_KEY` in the environment that launches the client. Codex uses `bearer_token_env_var`; OpenCode uses `{env:MNEMONIC_API_KEY}` with `oauth: false`.

After connecting, verify `session_init()` and resolve the project with
`list_projects` when needed; an empty list is valid. Read
`get_project(project_id, settings_only=true)` and the task's command help.

Optionally export local skills with [`scripts/export_agent_skills.py`](../scripts/export_agent_skills.py)
and follow the [installation instructions](mcp-clients.md#install-the-complete-workflow).
Install only the skill folders you need. Connecting MCP does not install them.
For Codex native transcript verification, review and merge the
[AGENTS example](../examples/codex-AGENTS.md) with existing instructions.

## Operate and develop

```sh
docker compose ps
docker compose logs --tail=100 api mcp web worker rabbitmq
docker compose stop
docker compose up -d --wait
```

Use the same explicit `-f` overlays as at installation for these commands. Normal stop/restart preserves the database. Production stop/restart or recreation requires explicit operator authorization. **Do not use `docker compose down -v` on your working stack:** it removes the database, RabbitMQ, and application data volumes. Project backups are written as `.json.bz2` archives under `/backups/<project UUID>/` in the worker: the private backup named volume for new installs, or `MNEMONIC_BACKUP_DIR` for bind installs. The worker schedules them every 24 hours by default and retains seven successful archives per project. No archive is expected before a real project exists. Verify a backup in **Settings → Project backups** after creating your first project. Copy them off the machine and
monitor available disk space; a local archive alone does not protect against disk loss. Artifact files, editable prompt files, and retained native transcripts require separate filesystem backups. Restore commands and security limits are in [`docs/operations.md`](operations.md).
