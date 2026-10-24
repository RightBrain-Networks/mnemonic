# Needs Attention

Each Needs Attention item is a Product Decision Record (PDR) in the knowledge
graph. New projects receive a Product Decision Records domain with prefix `PDR`;
existing projects receive a designated product decision domain during upgrade.
The queue keeps its human decision workflow while storing questions and answers
as ordinary immutable knowledge revisions, with links, metadata, hashes,
searchability and implementation provenance.

Agents fetch `get_prompt(project_id, prompt_id="request-human-review", work_item_id=...)`
before authoring or revising a question. Humans edit these instructions and their
placeholder macros in Settings > Prompts > Request human review. The agent follows
the returned content, then submits its question with `request_human_input`, its
actual native requester session ID and the authoring model ID. Fresh agent
requests and rewrites require the model; missing historical identities remain
unknown and unchanged.
See [Prompt library](prompts.md) for the shared retrieval contract.

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

An optional `decision_metadata` object supplies `title`, `related_decision_ids`,
`related_work_item_ids`, `related_artifact_ids`, `depends_on`, `supersedes`, and
`supersession_details`. New questions default to the work title and no optional
links. On rewrite, omitted fields retain the existing value; explicit empty lists
clear optional links. New questions retain their primary work item link. Read the
current decision metadata before changing it. Cross-domain references and exact
revision dependencies follow the ordinary [knowledge graph](knowledge-graph.md)
rules; supersession is restricted to the same domain.
Retained `supersession_details` also establish supersedes links, so removing a
supersession requires updating both lists.

The REST equivalent is the existing POST to the work item's `/gates` collection
with those same optional revision fields. Fresh requests and answers require a
`client_operation_id`; the MCP client supplies an automatic UUID when omitted
and returns it for exact retries. Both target and expected version must
be supplied together. Omitting both creates a separate question. Rewriting keeps
the question's ID, creation time, queue position, and unresolved state. All
previous prose, authorship, timestamps, and context anchors remain durable.
An open question can also be revised while its work is deferred; the hold stays
in place. Resolved questions cannot be rewritten. No new MCP tool or receipt kind is added.

Reads expose `question_version` and chronological `previous_questions`, alongside
the latest question and requester. The gate's `decision` includes its canonical
document/domain IDs, stable decision ID, revision-qualified reference, lifecycle
state, content SHA-256 and full metadata. Question version N corresponds to
proposed decision revision N-1. The original request event remains immutable;
all question versions are retained as knowledge revisions.

Answers include `expected_question_version` (default 1) and the current context
revision. Conflicts preserve the browser draft and refresh the question; no
checkpoint or relationship review is required. The human answers the current
question. Agents cannot answer or withdraw it, including when later evidence
makes it moot: rewrite it to explain the change and ask the human to close it.

Answering appends an accepted human-authored revision containing both the current
question and the answer. Its decision revision equals the final question version.
Accepted records the owner's decision, including "No" or "No longer needed";
it is not an automatic approval of the proposed action. The original requester,
question revisions, human resolver and reviewed context remain auditable.

`decision_get` includes `human_gate` so a document discovered through search can
be traced back to its dedicated queue/work surface. Use `human_gate.work_project_id`
when opening the originating work. A historical resolved question whose work had
already moved projects before this migration retains its PDR in the original
project and reports `decision.originating_work_project_id`; it does not create an
invalid cross-project ordinary work link. Work with managed PDRs cannot move again.
Decision search finds PDR
titles and metadata by default; `fulltext=true` searches questions and answers,
and `all_revisions=true` includes earlier versions. Read the exact answered
revision before implementing a choice based on it and register the resulting
commit through completion provenance or `decision_scope_append` outside closeout.

Generic `decision_put` cannot update a managed PDR or bypass human resolution.
Resolved questions cannot be reopened or rewritten through knowledge tools.
A subsequent product choice is a new question, explicitly linked to the earlier
PDR. Ordinary decisions in the PDR domain do not automatically become queue items.

Every revision uses a new operation UUID. Uncertain retries preserve that UUID
and every argument exactly. Historical receipts replay their original result,
even after further rewrites or resolution. A stale question version returns
`409 gate_question_changed`; reread before submitting a new intent.

Upgrade API, MCP, and dashboard together to 0.92.0, plugin 0.55.0, and Alembic
`0058_decision_changes`; old writers cannot maintain the PDR contract. Back up
first and stop old writers before migrating. Existing question
versions and answers are imported into immutable PDR revisions with their retained
timestamps, authorship and context. Existing question IDs, queue positions, event
history and exact mutation receipts remain intact. Historical receipts keep their
original response shape; read current state after replay to obtain the PDR link.
No configuration setting or additional MCP tool is required.

When a project already has a `PDR` domain, the migration reuses its ID, existing
name, ordinals and documents. If another prefix already owns the name Product
Decision Records, the new PDR domain uses Product Decision Records (PDR), adding a
numeric suffix if needed. Existing domains are never renamed to make room.
