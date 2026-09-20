"""Claude context attachments and records that are not authored conversation turns.

Only enumerated native formats are accepted. Context is retained as typed JSON and
readable JSON text, including prompts, hooks, file excerpts and tool definitions.
New record/attachment types continue to report unsupported coverage.
"""

BOOKKEEPING_RECORDS = frozenset({
    "queue-operation", "file-history-snapshot", "progress", "last-prompt", "atis-latch",
    "bridge-session", "relocated", "worktree-state", "pr-link", "file-history-delta",
    "ai-title", "mode", "frame-link", "history-suppression", "artifact-comment-monitor",
    "artifact-autoreact-ledger", "permission-mode", "custom-title", "agent-name",
})
CONTEXT_ATTACHMENTS = frozenset({
    "total_tokens_reminder", "batching_reminder_sent", "deferred_tools_delta", "queued_command",
    "hook_additional_context", "skill_listing", "task_reminder", "mcp_instructions_delta",
    "remote_session_change", "command_permissions", "auto_mode", "date", "deferred_tools_record",
    "edited_text_file", "session_context", "instructions", "prompt_snapshot",
    "silent_turn_reminder",
    "structured_output", "environment", "agent_listing_delta", "hook_system_message", "model",
    "hook_blocking_error", "hook_success", "ultra_effort_enter", "read_truncation_notice",
    "nested_memory", "file", "compact_file_reference", "date_change", "hook_cancelled",
    "invoked_skills", "auto_mode_exit", "thinking_stripped", "ultra_effort_exit", "dynamic_skill",
    "task_status", "thinking_drop",
})
SYSTEM_RECORDS = frozenset({"stop_hook_summary", "api_error", "turn_duration"})


def context_payload(value: object, codes: set[str]) -> object:
    # Validation is performed before this projection, so the depth is bounded.
    if isinstance(value, list):
        return [context_payload(item, codes) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("type") == "base64" and "data" in value:
        codes.add("attachment_content_not_searchable")
        return {key: item for key, item in value.items() if key != "data"}
    return {key: context_payload(item, codes) for key, item in value.items()}
