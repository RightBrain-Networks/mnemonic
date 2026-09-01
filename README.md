mnemonic

Lightweight coordination across coding agent sessions -- without littering your repo with random Markdown docs or blowing up your issue tracker.

mnemonic is a locally-hosted Docker stack that combines a durable backend (PostgreSQL) + RESTful API (FastAPI). The API has two consumers: a human-facing dashboard (Next.js) and a LLM-facing MCP server. The MCP server ships with preconfigured agent skills so your agent can automatically discover how to interact with mnemonic.

It does not modify Claude's memory subsystem. While Claude Code is the first client; the API, metadata, and MCP interface do not depend on a particular LLM provider.

Is mnemonic right for your project?

- You build with Claude Code (support for additional platforms coming soon).
- Important context and follow-up tasks are often overlooked because they get buried under verbose LLM output.
- Dozens or hundreds of transient Markdown documents are cluttering your repo. Or, your issue tracker has so many AI-written issues that it's become painful for a human eyes.
- Multiple agents are concurrently working on the same project and occasionally clobber each other.
- You prefer a managing a locally-hosted Docker stack rather than paying for a SaaS product.
- You don't want the complexity of a full orchestration platform like Openclaw.

Run it

Requirements: Docker with Linux containers and Docker Compose. The optional settings helper uses Python 3.10 or newer; no host Node or Python is needed to run the containers after .env is configured.

    python scripts/setup.py
    docker compose up --build -d --wait

On macOS/Linux the Python command may be python3. Alternatively, copy .env.example to .env and supply two different random secrets yourself: a URL-safe PostgreSQL password and an API key of at least 32 characters. The
example deliberately contains no usable credentials. Never commit .env.

Open mnemonic. Create your first project using the project selector, then connect your agent to create or continue durable work. The application starts empty; there are no fabricated objectives, checkpoints, or session IDs.

  Service               	.env variable    	Default local address    
  Dashboard             	MNEMONIC_WEB_PORT	localhost:3000           
  REST API documentation	MNEMONIC_API_PORT	localhost:8000/docs      
  MCP Streamable HTTP   	MNEMONIC_MCP_PORT	http://127.0.0.1:8001/mcp

Those addresses are defaults, not fixed values. Each port is set in .env, so change one there if it collides with something already running on the host, and substitute your own value wherever this README shows a port. With uv installed, uv run --project mcp python scripts/check-stack.py resolves all three from .env and performs the read-only live-stack checks. See docs/development.md before opting into its write path.

The four application services run alongside a small backup container. PostgreSQL has no published port. Dashboard, REST, and MCP ports bind to loopback only. For LAN access through your existing nginx TLS proxy, use the
nginx configuration and setup guide.

Connect Claude Code

Set MNEMONIC_API_KEY in the terminal environment to the key from your private .env file. Set MNEMONIC_MCP_PORT as well if you changed it from the default. Then register the HTTP MCP endpoint:

    claude mcp add --transport http --scope user mnemonic "http://127.0.0.1:${MNEMONIC_MCP_PORT:-8001}/mcp" --header "Authorization: Bearer ${MNEMONIC_API_KEY}"

Registering the endpoint does not connect it to a running session. Claude Code loads MCP servers at startup, so start a new session afterwards.

In PowerShell, the URL and header expressions are "http://127.0.0.1:$env:MNEMONIC_MCP_PORT/mcp" and
"Authorization: Bearer $env:MNEMONIC_API_KEY"; PowerShell has no :- default, so set the port variable explicitly there. Do not paste the real key into tracked project configuration. Configuration examples, including a Docker stdio
alternative and OpenCode, live in examples/; they show the default ports and need the same substitution if yours differ. work.json is the canonical example.

The three skills ship as a Claude Code plugin. Register this repository as a marketplace once, then enable the plugin in any project that should have them:

    claude plugin marketplace add /srv/mnemonic
    claude plugin install mnemonic@mnemonic

To make it automatic for everyone who clones a consuming repository, commit this to that repository's .claude/settings.json instead:

    {
      "extraKnownMarketplaces": {
        "mnemonic": { "source": { "source": "directory", "path": "/srv/mnemonic" } }
      },
      "enabledPlugins": { "mnemonic@mnemonic": true }
    }

Replace the directory source with { "source": "github", "repo": "<owner>/mnemonic" } once the repository is
reachable remotely.

Installing copies the plugin into ~/.claude/plugins/cache/ at its manifest version, so editing a skill in place does not change an installed copy. claude plugin marketplace update mnemonic refreshes the marketplace listing,
not the installed files. After a published plugin version changes, run claude plugin marketplace update mnemonic, then claude plugin update mnemonic@mnemonic, and restart Claude Code. The current plugin is version 0.3.0. It provides:

- mnemonic-save searches for existing work, creates a durable objective
with its initial checkpoint and explicit atomic links, or appends corrective
resume context to an existing one; concise historical progress uses an event.
- mnemonic-search finds compact work-item leads within the chosen project,
normally restricted to open work, and separately lists priority-ordered ready
candidates without treating search as a queue.
- mnemonic-recall loads bounded current context, pages older checkpoints
or events when needed, atomically claims already-authorized execution, renews
or releases that expiring lease, inspects immediate typed relationships,
records concise progress events, and saves an atomic completion checkpoint
when the work is complete.

Invoke /mnemonic-save, /mnemonic-search, or /mnemonic-recall, or ask Claude in natural language. The skills require the connected mnemonic MCP server. You can copy the selected project's ID from the dashboard, or use
list_projects. Session IDs are opaque text (often UUIDs), not integers, and refer to the originating LLM conversation, not the MCP transport session. 

See docs/agents.md for the workflow and client boundaries.

Operate and develop

    docker compose ps
    docker compose logs --tail=100 api mcp web backup
    docker compose stop
    docker compose up -d --wait

Normal stop/restart preserves the database. Do not use docker compose down -v on your working stack: it removes the data volume. Backups are written to ./backups by default and are not committed. Copy them off the machine and
monitor available disk space; a local dump alone does not protect against disk loss. Restore commands and security limits are in docs/operations.md.
