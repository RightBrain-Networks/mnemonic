# MCP clients and cooperating agents

Mnemonic exposes the same 38 tools over Streamable HTTP and stdio to every MCP
client. Claude Code retains its native plugin, including skill discovery, session
substitution, and repository freshness helper. Codex, OpenCode, and other agents
can use the complete workflow through portable skills. No vendor factory is used.

## Connect

Use one transport configuration for each client:

| Client | Streamable HTTP | Local stdio |
| --- | --- | --- |
| Claude Code | [HTTP JSON](../examples/claude-code.mcp.json) | [uv JSON](../examples/claude-code-stdio.mcp.json), [Docker JSON](../examples/claude-code-docker-stdio.mcp.json) |
| Codex | [TOML](../examples/codex.config.toml) | [TOML](../examples/codex-stdio.config.toml) |
| OpenCode | [JSON](../examples/opencode.json) | [JSON](../examples/opencode-stdio.json) |

Merge the example into the client's chosen configuration. Set the endpoint to
the host's actual port: HTTP uses `MNEMONIC_MCP_PORT` and `/mcp`; a local stdio
adapter uses the REST API origin at `MNEMONIC_API_PORT`. The sample ports 8001
and 8000 are placeholders. Stdio needs Python 3.14, uv, and an absolute checkout
path. Export `MNEMONIC_API_KEY` in the environment that launches the client.
Keep credentials out of tracked configuration.

Codex supports `bearer_token_env_var` for HTTP and `env_vars` for stdio.
See the [official Codex MCP configuration](https://developers.openai.com/codex/mcp/).
OpenCode uses `headers` with `{env:MNEMONIC_API_KEY}` and `oauth: false`
for Mnemonic's API-key authentication; local servers use `command` and
`environment`. See [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers/).

After connecting, discover the tools and call `list_projects`. An empty list is
valid. Mnemonic also exposes an optional work resource and resume prompt; every
domain operation is available as a tool, so clients without resource or prompt
interfaces retain the full workflow. Client-specific tool prefixes are presentation;
use the discovered tool corresponding to the canonical name in these instructions.

## Install the complete workflow

For Claude Code, keep the marketplace/plugin installation documented in
[agent setup](../AGENT-README.md#6-connect-the-users-mcp-client).
Native plugin paths continue to use Claude's supported
[plugin-root expansion](https://code.claude.com/docs/en/plugins-reference).

For a portable installation, run from the Mnemonic checkout:

```sh
python3 scripts/export_agent_skills.py /absolute/path/to/new-skill-export
```

The destination must not exist, its parent directory must already exist, and its
path must not traverse symlinks. The exporter creates exactly three skill folders,
each containing its own references and executable repository helper. It resolves
Claude plugin paths into relative paths and requires no Claude environment.
The output can be moved independently of this checkout. It never modifies client
configuration or overwrites an installed skill.

Place the three complete folders in your chosen skill discovery directory.
Both [Codex](https://developers.openai.com/codex/skills/) and
[OpenCode](https://opencode.ai/docs/skills/) support `.agents/skills` at project
or user scope. OpenCode also supports `.opencode/skills`. If the desired
`.agents/skills` directory does not yet exist, it can be the export destination
directly. For an existing directory, export elsewhere, inspect the three folders,
and install them without overwriting unrelated or locally modified skills.
For updates, produce a fresh export and review replacements explicitly; the native
Claude plugin continues to use its own update mechanism.

Clients without skill discovery can explicitly load an exported `SKILL.md` and
its linked references. Instructions resolve paths relative to the loaded file,
not the repository working directory. The repository helper requires Bash and Git;
where unavailable, inspect repository facts directly and preserve uncertainty.
No helper output proves correctness or grants authority.

## Identity and coordination

Use the [stable per-agent identity contract](agents.md#create-or-continue-work).
The tuple `(client, session_id)` identifies an acting session. Client names are
open strings, not a vendor allowlist. Model attribution is optional and may change
within a session. A host's MCP `clientInfo` describes software, and an HTTP
connection is transport state; neither is a reliable originating agent identity.
The server therefore never replaces explicit provenance with inferred identity.

For example, Claude Code and Codex can claim separate Pending work in one project
while an OpenCode agent reviews original Done work:

1. Each implementation agent resolves the project and selects work with
   `list_ready_work`, then calls `claim_and_recall` with its own client/session
   identity. A shared project does not mean shared claim tokens or operation UUIDs.
2. Outside cold review, inspect `search_work(status=all, view=full)` pages and
   `summary.readiness.active_lease` to find current collaborators. This includes
   leases on Done work under review. For an exact related item, `get_work`
   returns the public lease without checkpoint bodies. `source_client` and
   `source_session_id` search filters find historical checkpoint authors, not
   necessarily current holders. Expired leases are not current ownership.
3. A contention error identifies the holding client, session, expiry, and lease
   purpose, with review ID/mode for review leases. Treat these as observed,
   asserted coordination facts, never a credential or permission to impersonate.
   Choose other eligible work or coordinate through the originating workflow.
4. Record dependencies with `blocks` edges, durable continuation context with
   checkpoints, and progress with `append_event`. Read the affected work's
   history when coordination requires it. Mnemonic is a shared work ledger; it
   does not dispatch agents or deliver private messages.
5. The OpenCode reviewer receives the exact project/work/review IDs and immutable
   repository scope through its assignment. Before cold findings are frozen,
   use only the minimal review claim/renew/release protocol; do not browse the
   project, recall checkpoints, load handoff, or read the author's reports.
   Claim the original work with `purpose=code_review`, `mode=cold`, and its
   exact `code_review_id`. See the [review protocol](code-reviews.md).
6. Implementation closeouts and review results retain their own actor identity.
   Only the originating client/session answers its optional review recommendation.
   An independent reviewer cannot take over that identity. Each review produces
   zero or one remediation containing all findings.

Generated fallback session UUIDs are retained once per independently acting agent
when the host exposes no suitable native ID. They are distinct from the UUID for
each protected mutation. Retain unresolved mutation intents privately and replay
their exact arguments; never rebuild an unknown-outcome retry with a new session.

## Survey and design decision

| Code or artifact | Finding and disposition |
| --- | --- |
| [MCP server](../mcp/src/mnemonic_mcp/server.py) | Stateless HTTP, stdio, tool results, resources, and prompts are already shared. Extend identity guidance without choosing behavior by vendor. |
| [API schemas](../backend/src/mnemonic_api/schemas.py), [leases](../backend/src/mnemonic_api/services/leases.py), [reviews](../backend/src/mnemonic_api/services/code_reviews.py) | Open client/session fields and purpose-bound capabilities already distinguish independent agents; preserve their validation and receipt semantics. |
| [Native skills](../plugin/skills/), [shared provenance](../plugin/reference/authority-and-provenance.md) | Claude path/session expansion and native-ID-only instructions prevent independent portable use. Preserve native paths, generalize identity instructions, and export complete relocatable bundles. |
| [API errors](../backend/src/mnemonic_api/errors.py), [MCP errors](../mcp/src/mnemonic_mcp/api.py) | Contention hid the holder's session. Expose bounded public session/purpose/review facts through both boundaries. |
| [Setup](../scripts/setup.py), [repository helper](../plugin/bin/mnemonic-repository-freshness) | Already independent of client APIs. Keep setup and helper behavior shared. |
| [Dashboard client labels](../frontend/components/work-item-card.tsx) | Familiar names have display labels, and unknown clients display their supplied name. There is no runtime vendor restriction. |

The survey found vendor-neutral bounded identity fields, lease arbitration,
receipt replay, review capabilities, and model-optional provenance in the backend.
The MCP server already supports stateless HTTP and stdio with structured and text
tool results. Setup and the local Bash/Git assessment helper do not call vendor APIs.
Public work views already identify current holders by client and session.

The specific coupling was in the Claude plugin's path expansion, its session
substitution examples, missing Codex setup, instructions that rejected clients
without a native conversation ID, and contention messages that hid the holder's
session. Portable packaging and an explicit identity contract resolve these gaps.
The native plugin remains the maintained source; exports reuse it rather than
creating a second set of workflow instructions.

A runtime factory would currently duplicate the same protocol and could incorrectly
bind one server process or transport session to one agent. The shared design keeps
all tools available and adds no schema migration, registry, vendor enum, or capability
intersection. If future functionality actually requires a vendor API, isolate that
integration at its boundary and select it by an explicit supported capability;
do not infer the agent or reduce other clients' tool access.
