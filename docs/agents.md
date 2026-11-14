# Using Mnemonic as an agent

Connect an MCP client using [the client setup guide](mcp-clients.md). Command
contracts and project configuration come from the connected server; local skills
are optional shortcuts and helper packages.

## Orient the conversation

1. Call `session_init()` when its orientation is missing from the current
   conversation. It takes no arguments and returns vocabulary, command routes,
   and a command catalog. It does not register a session or select a project.
2. If the project is not already established, call `list_projects` and select it
   from the user's context. An empty list is valid; do not guess an identity.
3. Call `get_project(project_id, settings_only=true)` for the selected project's
   current identity and runtime settings. Use the full overview only when the
   task needs project summaries. Authored guidance is retrieved separately with
   `get_prompt`; consult its help when needed.
4. Read `help(topic="COMMAND usage")` before using the relevant command. Follow
   its links to requirements and examples. `help(topic="COMMAND")` lists fields,
   `help(topic="COMMAND FIELD")` narrows a field, and `help(topic="COMMAND schema")`
   provides the exact input schema. `COMMAND details` pages the complete contract;
   follow every returned Next link needed for the task.

Orientation is a safe reference read. Repeat it after losing context or when a
newly connected server is unfamiliar. It grants no execution authority and does
not start transcript capture.

## Choose a command

| Task | Start with help for |
| --- | --- |
| Bounded changes or status updates | `query_activity usage` |
| Find saved work and evidence | `search usage` |
| Read selected work | `recall_work usage` |
| Claim authorized implementation or a review | `claim_work usage` |
| Save a new objective or progress | `create_work usage`, `add_checkpoint usage`, or `append_event usage` |
| Read or revise decisions | `decision_get usage` or `decision_put usage` |
| Ask for a durable human decision | `request_human_input usage` |
| Finish work | `complete_work usage` |

Use command help for current retry rules, required declarations, review modes,
coverage, and closeout requirements. For status reporting, `query_activity` bounds
results on the server; its help explains filters, pagination, polling checkpoints,
and historical windows. Do not confuse a polling checkpoint with a work checkpoint.

## Create or continue work

Choose work within the user's authorized task. Read claim help before loading
work context when execution or review is involved: review mode can restrict which
context is available. Reads, leases, and saved records do not grant permission to
execute a task.

Use the acting agent's actual client and native session identity.
`(client, session_id)` identifies the conversation; MCP connection metadata does not. Each
independently acting child keeps its own identity. When a host provides no native
ID, retain one generated session identity for that agent. Keep it separate from
operation retry IDs. Help describes required transcript declarations and exact
retry handling.

## Optional local skills and helpers

The three skills are thin routes to orientation and command help:

- `mnemonic-recall`: selected work, with local transcript verification,
  repository assessment, and decision-provenance preparation when relevant.
- `mnemonic-save`: requested writes, with local upload and provenance helpers.
- `mnemonic-search`: search and bounded activity, with a local download helper.

Load only the relevant skill and linked resource. Portable exports contain each
skill's reachable resources, so each folder can move independently. Helpers keep
file bytes and credentials out of model context. Resolve their paths from the
installed skill; the server cannot know a client's installation directory. See
[installation](mcp-clients.md#install-the-complete-workflow) and the
[Codex native transcript example](../examples/codex-AGENTS.md).

## Optional project workflows

Owners may select modules from [workflow starters](../examples/agent-workflows/README.md),
review them, and copy the chosen text into their repository-owned guidance. These
examples express team preferences, not additional API requirements. Nothing
installs, enables, or updates them automatically. Keep only modules the project
wants and review upstream changes before adopting them. Current command help and
project settings remain the source for API behavior.
