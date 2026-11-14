# Verify a native transcript path

Read `help(topic="claim_work session_transcript")` before enrollment and
`help(topic="complete_work subagent_transcripts")` before closeout. The server
help owns enrollment, retry, capture, and indexing rules. This file covers locating
native files in the client environment.

Use the actual client identity and an operator-approved source root. Verify a
readable regular file with no symlink components; if the client supplied a
symlink, establish and verify its actual target within that approved root.
Do not widen roots or guess among multiple matches. Use explicit null on a fresh
assertion when the actual file cannot be established, and report that limitation.

For Codex, use `client=codex` and the client-provided native thread ID, including
`CODEX_THREAD_ID` when available. Locate its exact `rollout-*.jsonl` and verify
that the first `session_meta.payload.id` matches. `history.jsonl` is not a native
session rollout. Each spawned thread has its own ID and rollout; verify it
independently instead of deriving its path from the parent.

For Claude Code, use `client=claude_code`. Hook input supplies `transcript_path`;
SubagentStop input may supply `agent_transcript_path`. Worktrees can have different
encoded project directories, so do not construct a path from the main checkout
and session UUID. A workflow directory or task stdout `.output` file is not a
native transcript.

Path and first-record identity verification need no transcript-body retrieval.
A later file move does not change frozen retry arguments. For retained copy and
index coverage, consult `help(topic="get_transcript usage")`.
