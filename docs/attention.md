# Needs Attention

Questions show the latest authored prose by default. A horizontal tab strip above
it opens every previous version. Returning to the current version enables the
answer form; viewing history does not change or resolve the question.

The agent updating work is responsible for keeping affected open questions
current, including questions on related work. After saving relevant work changes,
call the existing `request_human_input` tool with the existing `gate_id`, the
most recently read `question_version` as `expected_question_version`, rewritten
`question` prose, requester provenance, and a new `client_operation_id`. Supply a
complete question covering the current facts, options, and recommendation.
Mnemonic does not run a background language model to invent a revised question.

The REST equivalent is the existing POST to the work item's `/gates` collection
with those same optional revision fields. Both target and expected version must
be supplied together. Omitting both creates a separate question. Rewriting keeps
the question's ID, creation time, queue position, and unresolved state. All
previous prose, authorship, timestamps, and context anchors remain durable.
An open question can also be revised while its work is deferred; the hold stays
in place. Resolved questions cannot be rewritten. No new MCP tool or receipt kind is added.

Reads expose `question_version` and chronological `previous_questions`, alongside
the latest question and requester. The original request event remains immutable;
the gate history is the authoritative record of question versions.

Answers include `expected_question_version` (default 1) and the current context
revision. Conflicts preserve the browser draft and refresh the question; no
checkpoint or relationship review is required. The human answers the current
question. Agents cannot answer or withdraw it, including when later evidence
makes it moot: rewrite it to explain the change and ask the human to close it.

Every revision uses a new operation UUID. Uncertain retries preserve that UUID
and every argument exactly. Historical receipts replay their original result,
even after further rewrites or resolution. A stale question version returns
`409 gate_question_changed`; reread before submitting a new intent.

Upgrade API, MCP, and dashboard together to 0.38.0, plugin 0.24.0, and Alembic
`0030_question_versions`. The migration gives existing questions version 1,
preserves original prose and events, and adds append-only revision storage.
Back up first and stop old writers before migrating. Downgrade is allowed only before any revised question or new gate receipt exists;
after use, restore a pre-upgrade backup. There are no new configuration settings.
