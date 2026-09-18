# Mnemonic in Codex

When using Mnemonic, load the installed `mnemonic-recall`, `mnemonic-save`, or
`mnemonic-search` skill and its linked transcript reference. The portable names
are the Codex equivalents of Claude examples such as `mnemonic:mnemonic-recall`.
Use `client=codex` and the actual native thread identity.

Before a fresh claim, verify this thread's exact native `rollout-*.jsonl` within
an operator-approved source root. Use the client-provided thread ID (including
`CODEX_THREAD_ID` when supplied), and confirm the initial `session_meta.payload.id`
matches. Verify a readable regular file without symlink components; do not infer
a path from the repository, pick the newest file, or use `history.jsonl`.
Supply `session_transcript={"client":"codex","path":"/verified/absolute/rollout.jsonl"}`;
use explicit null only when the actual file cannot be established, and report it.

Verify each spawned thread's own rollout independently and supply those locations
in `subagent_transcripts` on release or closeout, or explicit null if unavailable.
Keep assertions, operation IDs, and all arguments unchanged on uncertain retries.
Copying and indexing occur after the work lease ends. The MCP connection alone
does not register sessions; do not import a mixed-project directory into one project.
Check `list_transcripts` and `get_transcript` for copy/index status and coverage.
