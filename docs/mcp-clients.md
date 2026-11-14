# MCP clients and cooperating agents

Every MCP client uses the same Mnemonic command contracts over Streamable HTTP
or stdio. Start with server orientation and command help. The Claude plugin and
portable skills are optional shortcuts that also package local file helpers.

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

After connecting, call `session_init()` when its response is absent from the
conversation, resolve the project with `list_projects` if needed, then call
`get_project(project_id, settings_only=true)`. Read `help(topic="COMMAND usage")`
for the task. See [agent onboarding](agents.md) for help navigation. Client tool
prefixes are presentation: use the discovered tool matching the canonical name.
An empty project list is valid. Optional MCP resources and prompts are convenient
entry points; the commands remain available without those interfaces.

<a id="install-the-complete-workflow"></a>

## Install optional skills

Skills are optional; this section installs their local helper resources. Command
instructions come from the connected server's help.

For Claude Code, keep the marketplace/plugin installation documented in
[agent setup](../AGENT-README.md#6-connect-the-users-mcp-client).
Native plugin paths continue to use Claude's supported
[plugin-root expansion](https://code.claude.com/docs/en/plugins-reference).

For a portable installation, run from the extracted release bundle or Mnemonic checkout:

```sh
python3 scripts/export_agent_skills.py /absolute/path/to/new-skill-export
```

The destination must not exist, its parent must exist, and its path must not
traverse symlinks. The exporter creates the three skill folders and copies only
the references and helpers reachable from each skill. It rewrites
Claude plugin paths to relative paths. Each folder can move independently of
the checkout, release bundle, and other skills. Export does not change client
configuration or overwrite installed files.

Install whichever skill folders you need in your client's discovery directory.
[Codex](https://developers.openai.com/codex/skills/) and
[OpenCode](https://opencode.ai/docs/skills/) support `.agents/skills` at project
or user scope; OpenCode also supports `.opencode/skills`. A new discovery directory
can be the export destination. Otherwise export elsewhere and review the selected
folders before copying them, preserving unrelated or locally modified skills.
Review updates explicitly; the native Claude plugin uses its own update mechanism.

Clients without skill discovery can load the exported `SKILL.md` and its relevant
links directly. Resolve resource paths relative to that installed file. The
repository helper requires Bash and Git; the Python helpers require Python 3.10
or newer. Read the linked helper guide for invocation and local requirements.

## Codex transcript setup

Merge the [Codex AGENTS example](../examples/codex-AGENTS.md) into the user's
chosen instruction file after review. Do not overwrite existing instructions.
The example keeps orientation and command rules in server help while retaining
Codex's local native-path verification. Use the actual Codex thread identity and
`client=codex`; Claude plugin names have portable equivalents without the prefix.

If using skills, check that the selected folder's `SKILL.md` and its linked
`reference/transcripts.md` are readable. Connecting MCP does not install skills.
Restart the client when necessary to discover changed skills or instructions.
See the official [skill discovery](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)
and [AGENTS discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md#how-codex-discovers-guidance)
rules for client behavior.

Transcript enrollment uses verified native paths on claims and child closeouts;
connecting or reading orientation does not enroll a conversation. Read claim and
closeout help for declarations, retry rules, and coverage checks. Never import a
mixed-project sessions directory into a single project.

<a id="survey-and-design-decision"></a>

## Identity and coordination

Use the [per-agent identity guidance](agents.md#create-or-continue-work). Each
independent agent retains its own client/session identity and private claim
receipt. Read `help(topic="claim_work usage")` for claim eligibility, contention,
review modes, and the boundaries on context before a cold review. Read renew,
release, and closeout help when those actions are needed. Runtime policy comes
from the selected project's settings; a lease does not authorize execution.

Optional [project workflow starters](../examples/agent-workflows/README.md) can
express team preferences about reporting or review methodology. Owners select,
copy, and maintain them; connecting Mnemonic or installing a skill enables none.

## Local artifact transfers

Read `help(topic="authorize_artifact_upload usage")` or
`help(topic="authorize_artifact_download usage")` for the current grant contract.
The [upload helper](artifact-upload-client.md) ships with `mnemonic-save`; the
[download helper](artifact-download-client.md) ships with `mnemonic-search` and
`mnemonic-save`. Each helper accepts a private, short-lived MCP grant without a
standing API key or API origin in its environment. Keep grant tokens out of shell
source, command arguments, logs, and model context. Server artifact availability,
size limits, and sensitive-content rules still apply; see [configuration](artifacts.md).

Resolve helper paths through the installed skill's resource links. The server
cannot supply a remote client's absolute installation path. Do not guess plugin
cache versions. For bounded text reads, use `get_artifact_text` help; to search
all occurrences in original bytes, download a pinned revision to a new scratch
file and use a local reader. Raw file offsets differ from normalized text offsets.
