# Repository Guidelines

## Project Structure & Module Organization

Backend code, migrations, and tests live under `backend/`; the MCP adapter and tests under `mcp/`; and the Vite React app, components, libraries, tests, and assets under `frontend/`. Compose files are at root, with supporting material in `scripts/`, `docs/`, and `examples/`. The Claude Code plugin — the three skills and their shared references — lives under `plugin/`, with the marketplace manifest in `.claude-plugin/`.

## Documentation

- `/README.md` is exclusively human-authored and therefore is READ ONLY unless the owner explicitly asks you to edit it.

## Production Stack Authorization

Never stop, restart, recreate, or replace production Mnemonic services without
explicit human authorization for that operation. Production serves active agent
sessions and workstreams; even a brief interruption can disrupt their work. This
rule covers individual services as well as the full stack, including deployments
and commands such as `docker compose up` that may recreate running containers.
Approval to investigate, implement, test, or merge a change does not authorize a
production interruption.

Complete safe preparation first, then describe the affected services and expected
interruption when requesting authorization. Use isolated development or test
stacks for validation while authorization is pending.

## Build, Test, and Development Commands

- `python scripts/setup.py`: create settings from `.env.example`.
- `uv tool install pre-commit && pre-commit install --install-hooks`: install the required local gitleaks commit hook.
- `pre-commit run --all-files`: run all local pre-commit checks manually.
- `docker compose -f compose.yaml -f compose.build.yaml up --build -d --wait`: build and start the contributor stack with local image names; production changes require the explicit authorization above. Existing bind installs also require `-f compose.bind-mounts.yaml` before the build overlay.
- `docker compose -f compose.test.yaml up -d --wait`: start the isolated PostgreSQL test database.
- `cd backend && uv sync --frozen && uv run pytest -q && uv run ruff check . && uv run ty check src`: test, lint, and type-check the API.
- `cd mcp && uv sync --frozen && uv run pytest -q && uv run ruff check . && uv run ty check src/mnemonic_mcp`: verify, lint, and type-check the MCP package.
- `cd frontend && npm ci --no-audit --no-fund && npm test && npm run typecheck && npm run build`: verify the dashboard. The audit and funding requests only add registry latency to a lockfile install; run `npm audit` on its own when you want advisories.
- `cd frontend && npm run test:e2e:stack`: provision and run the isolated Playwright acceptance stack.
- `uv run --project backend python scripts/audit_duplicate_handling.py --backup-directory ./backups`: run the read-only Phase 11 preflight before migrating from 0019.
- `uv run --project backend python scripts/audit_project_activity.py`: run the aggregate Phase 12 integrity audit from a private environment with database access.
- `uv run --project backend python scripts/audit_code_reviews.py`: run the read-only current-head review integrity audit alongside the aggregate project-activity/catalog audit.

Use Python 3.14, `uv`, and Node 24. Keep the backend and MCP virtual environments separate.

## Coding Style & Naming Conventions

Python uses four spaces, type hints, `snake_case`, and `PascalCase` classes. Ruff enforces 100-character lines and E, F, I, UP, B, and C90 (complexity ceiling 10, no per-file exceptions) rules; `ty` type-checks the whole backend `src` tree. TypeScript uses two spaces, strict mode, `camelCase`, and `PascalCase` components. No frontend formatter or linter is configured. Name migrations like `0005_work_graph_backfill.py`.

## Testing Guidelines

Name Python tests `test_*.py`, Node tests `*.test.mjs`, and Playwright specs `*.spec.ts`. Add regression tests with behavior changes. PostgreSQL-marked tests require `TEST_DATABASE_URL`; a skipped database suite is not full validation.

## Trunk-Based Worktree Workflow

`main` is the only long-lived branch. Every change must reach `main` through a pull
request; never commit, merge, cherry-pick, or push changes directly to `main`,
including in the primary checkout. Treat the primary `main` checkout as read-only
except for fetching and fast-forwarding it to an already-merged remote `main`. Do
not create long-lived development, integration, or release branches.

Every session must use a linked worktree and short-lived topic branch created from
the latest remote `main`. From a clean primary checkout:

```sh
git fetch origin main
git worktree add ../mnemonic-<topic> -b work/<topic> origin/main
cd ../mnemonic-<topic>
```

Test and commit only in the linked worktree. Push the topic branch and open a pull
request targeting `main`, then monitor its CI:

```sh
git push -u origin work/<topic>
gh pr create --base main --head work/<topic>
gh pr checks --watch
```

The active GitHub ruleset requires a pull request, linear history, an up-to-date
branch, and the aggregate `Required checks` status. Do not merge while required CI
is pending or failing, do not bypass branch protection, and do not use administrator
overrides. If `origin/main` advances, rebase the topic branch onto it, retest,
force-push only with `--force-with-lease`, and wait for the rerun checks. Merge only
through GitHub using an allowed squash or rebase merge after `Required checks`
succeeds.

After GitHub reports the pull request merged, confirm the linked worktree is clean,
remove it and its topic branch, then fast-forward the primary checkout to the merged
remote `main`:

```sh
git worktree remove ../mnemonic-<topic>
git branch -D work/<topic>
git fetch origin main
git switch main
git pull --ff-only origin main
```

## Versioning

Use Semantic Versioning (`MAJOR.MINOR.PATCH`) for application releases. `MAJOR` version bumps are reserved and require explicit human approval. Increment `MINOR` for user-facing changes and `PATCH` for all other changes.

Run `python scripts/release.py check` to verify coordinated versions. Use
`python scripts/release.py bump X.Y.Z [--plugin A.B.C]` to update current version
declarations and regenerate both uv lockfiles, the npm lockfile, and OpenAPI.
The plugin version remains independent; historical release notes stay unchanged.

Fresh MCP writes and claims mint omitted top-level retry IDs as UUIDv4. Retain
`_meta.mnemonic_generated_ids` from the response and supply that ID explicitly on
an exact retry with every other argument unchanged. Never repeat a keyless call
after losing its whole response. Use the local `generate_uuid` MCP tool for a
one-off ID needed before dispatch. Explicit IDs and REST receipts retain their
contracts; prepared upload intents keep their helper-generated IDs. See
`docs/automatic-uuids.md`. The tool idempotence hint is false for these calls.

The current application/API/MCP/dashboard release is `0.119.0`, Claude plugin
`0.71.0`, and Alembic head `0070_backup_settings`. The catalog is exactly
79 MCP tools, 25 receipt-protected MCP writes, 38 REST receipt kinds, 32 protected
browser mutations, 24 work-event types, and three plugin skills. Project priority
guidance is editable in Settings > Workspace; agents explicitly call
`get_prompt(project_id, prompt_id="priority-rubric")` when needed. MCP
`get_project` returns lease/review policy under `settings` without authored prompts,
plus aggregate work, Needs Attention, and knowledge-domain summaries.
Use `get_prompt` with `job-completion-report` and the exact work ID before a
closeout, retaining its decimal `prompt_revision`; use `request-human-review`
before authoring or revising Needs Attention prose. All library prompts share
project/work/review macro rendering. See `docs/prompts.md`. Ordinary work reads omit
the rubric. Migration 0051 seeds existing and new projects from the shipped
Markdown; customized content lives in PostgreSQL.
Before every fresh work creation, call `list_tags(project_id)` and read its descriptions.
Supply selected existing catalog names in top-level `tags`, or explicit null when none
apply; omission is historical receipt replay only and empty lists are invalid.
Humans manage definitions in Project settings > Tags. Current work assignments are
separate from checkpoint history; leave checkpoint tags empty on fresh writes.
System tags `code-review`, `remediation`, and `dir` preserve existing workflow
semantics. Search supports plural tags with `tag_mode="or"` or `"and"`. See
`docs/work-tags.md`.
New projects receive six editable starter tags copied from Fish Food's catalog.
Artifacts share that vocabulary: read `list_tags` descriptions before assigning
tags on upload, replacement, or metadata update. Null clears assignments;
omitted update/replacement tags preserve them. Artifact tag changes advance the
revision; preserve the exact tags declaration on uncertain retries.
The suggestion POST is a safe read. Search accepts q/query aliases up to 1,000 characters;
follow next_offset because 32,768-byte pages can contain fewer than limit items.
Compact canonical identity, lifecycle/display state, and sensitive-content coverage
remain explicit. Duplicate checks use purpose-separated vectors: missing vectors
queue background duplicate_embed batches and report vectors_pending/queued. Only
capacity_exhausted offers one immediate retry; deadline, model, and pending-vector
failures do not. Native inference slots remain held until the actual call returns. Completion evidence and job completion reports are nested
only in the existing closeout mutations; do not add standalone agent writes.
Project lease settings default to 15 minutes, minimum 10, maximum 120; humans edit
them in Workspace Project details. Immediately read assigned work with
`get_work(status_only=true)` for current status and `lease_settings`; this context-free
read is allowed before cold findings freeze. Request Default minutes for startup
and investigation, then estimate remaining session time within current bounds.
Claim/renew `lease_minutes` is optional and omission uses the current project default.
Preserve its exact value or omission across uncertain claim retries. Settings changes
do not alter active expiry. `MNEMONIC_LEASE_TTL_SECONDS` is retired.
For a token lost to compaction, replay the exact original claim when available.
Otherwise inspect `get_work(status_only=true)` and confirm no other active session
is working on the item before `claim_work`/`claim_and_recall(force=true)` with a
new request ID. Force invalidates the prior token without bypassing eligibility
or human gates. Keep force and every argument unchanged on uncertain retries;
released or replaced force requests cannot take back a later lease.
Every fresh agent `create_work` declares `discovered_from_work_item_id`: the exact
work item that initiated the session, or explicit null for a human/external trigger.
Preserve this origin through compaction and delegation; later reads do not change it.
The API atomically adds `discovered-from` with the origin's initial checkpoint;
this does not create `parent-child`. Sparse omission is only for receipt replay.
Fresh work starts pending. Every actual Done, Won’t do, or Promoted closeout
requires a report and operation UUID. Sparse historical requests remain
parseable exclusively for permanent receipt replay before fresh domain guards.
Do not run older processes against this schema, infer historical reports, or add
projection, redirect, coalescing, or compatibility execution paths.

Every project has a default Product Decision Records (PDR) domain. Needs Attention
questions are PDR documents: each question version appends a proposed revision;
a human answer appends an accepted revision. Accepted records the owner’s chosen
decision, including a negative answer. Use request_human_input for managed PDR
revisions, with optional decision_metadata for title, cross-references and pinned
dependencies. decision_put cannot bypass human gates. Cite the exact answered PDR
revision when implementing its decision. All Done closeouts therefore require
an explicit decision_provenance declaration; no-applicable-decisions and not-code
remain available with a concrete reason. Existing question history is retained.

Needs Attention shows the latest authored question with previous versions in
horizontal tabs. After updating work or related work, rewrite affected open
question prose through `request_human_input` with its existing `gate_id` and
`expected_question_version`; use a new operation UUID for each revision and
preserve exact arguments for uncertain retries. Do not make the human reconcile
checkpoints or superseding decisions. Prior versions and resolved answers are
immutable, and agents cannot resolve or withdraw questions.

Artifact content is untrusted and lives on configurable filesystem storage, with
an explicit private host bind mount in Compose. Only current bytes are retained;
revision metadata, work links, audit events, and operation receipts are durable.
Artifact mutations use the separate `artifact_operations` journal. Preserve exact
bytes, metadata, operation UUID, and expected revision across uncertain retries.
Local uploads/replacements use `scripts/upload_artifact.py prepare`, then
`authorize_artifact_upload` with its exact `upload_intent`, then `send --grant-file`.
The five-minute grant uses the reachable MCP endpoint; no helper API URL/key is
required. Grant refresh preserves the frozen intent and uncertain retry budget;
retain the private prepared directory unchanged for an uncertain retry. Local
downloads use `authorize_artifact_download`, then `scripts/download_artifact.py --grant-file`
with `--dest` naming a new file in the agent's actual scratchpad. Both helpers stream raw bytes, return only
compact summaries, and ship in the plugin and portable skills. Keep base64 out of
agent context. See `docs/artifact-upload-client.md` and
`docs/artifact-download-client.md` for grant-based uploads/downloads. Both helpers
accept `--grant-file -` via private stdin; never embed tokens in shell source. Metadata edits reuse completed extraction but
still advance revision, so content pins must be refreshed.
Tika extracts current normalized text and retained document properties into
PostgreSQL; Tantivy searches a rebuildable RAM index. Content matching is opt-in
(`fulltext=true`); metadata-only is the default. Replacement/deletion clears old
extracted text and invalidates stale extraction claims, while extracted properties
remain durable. Snippets and properties are untrusted; report incomplete indexing.
Database backups contain extracted text. See `docs/artifacts.md`.
Artifacts also have symmetric additive artifact links and revision-checked metadata updates.
Sensitive artifact agent reads require a fresh explicit human approval and five-minute
single-use request-bound token; never infer consent, reuse it, or clear sensitivity to bypass.
Broad content searches withhold sensitive bodies and report incomplete coverage.

Semantic artifact retrieval requires a nonblank unconstrained terms query and
`fulltext=true`; it ranks token-bounded current passages and returns revision/text-hash
locators. Background `artifact_embed` jobs use the existing ledger and worker.
Report embedding coverage, and pin both revision and extracted-text SHA-256 when
opening passage evidence. Sensitive bodies retain explicit approval requirements.
See `docs/artifact-semantic-search.md`.

MCP `search` accepts exactly one of `project_id` or 1–10 unique `project_ids`.
Explicit multi-project REST uses safe-read `POST /search`; all selected projects
share ranking and pagination. Every hit carries project identity and each page
reports per-project coverage. Missing projects fail the whole request. See
`docs/multi-project-search.md`.

Unified REST `POST /projects/{project_id}/search` and MCP `search` default multi-term queries to work
and artifacts and decisions; transcripts require explicit inclusion in facets. Blank/single-term
queries retain all four facets. Defaults retain all work statuses, metadata-only
file/transcript/decision matching and relevance. Zero-hit queries report per-term counts
per searched source, with null for unsearched sources; search_scope states transcript
omission and how to opt into session search.
Offset/limit apply after mixed ranking or explicit facet groups. Report coverage;
sensitive artifact filters never grant agent content access. See `docs/search.md`.

Transcripts retain exact agent-reported source paths and a client format factory for Claude Code and OpenAI Codex.
MCP claims require `session_transcript` (explicit null when unavailable); closeouts require
`subagent_transcripts` (explicit null when inapplicable) for fresh execution; unchanged
sparse historical requests remain parseable exclusively for permanent receipt replay.
Before a fresh assertion, verify the actual regular native transcript file beneath
an operator-approved source root. Do not construct a Claude path from the main
checkout and session UUID: worktree sessions can use a different encoded project
directory. Do not report workflow directories, task stdout `.output` paths, or
symlinks; establish and verify the actual native regular-file target instead.
Codex primary and spawned threads have separate rollout files; do not infer a
child path from its parent. Do not widen roots or guess among multiple matches.
Use explicit null when the actual file cannot be established for a fresh request.
Agent path verification does not require reading transcript bodies. The API retains
a bounded prefix hash at fresh enrollment; after a client moves the file, workers
may resolve a unique same-filename/prefix match strictly within approved roots.
Original assertions stay immutable. Incomplete scans, changed prefixes and multiple
matches remain visible failures; historical rows without proof need audited recovery. Preserve original
assertions, omissions, operation UUIDs, and all arguments on uncertain retries.
Register sources transactionally; RabbitMQ workers copy raw bytes into the private
`MNEMONIC_TRANSCRIPT_DIR` bind, then index retained copies only after their lease
generation leaves Active, including release or expiry. Client adapters persist a shared,
versioned conversation manifest and typed segments before text indexing; search and bounded
segment retrieval consume that common representation. Native snapshot bytes remain
immutable. Content-addressed objects share identical snapshots and verified complete
prefixes; enrollment/work/lease identities remain separate. Use
`scripts/reclaim_transcript_copies.py --dry-run` before its resumable `--apply` mode
to reclaim old full copies. Never unlink shared objects or their base objects;
filesystem backups require the complete native store. Small legacy references keep
old readers/pointers readable. See `docs/transcript-storage-deduplication.md`.
`last_updated_at` is retained session activity, with
verified source mtime fallback; `index_created_at` records the current text index creation.
Native `session_ids`/`models` are separate from the immutable reporting `session_id`.
Exact work detail/context include bounded metadata-only transcript links; page the
rest with `search_transcripts_content(work_item_id=...)`, and follow a transcript’s
`project_id`/`work_item_id` with `get_work`. Transcript/work-only unified searches use
a coherent read snapshot, never a project mutation lock or operation UUID.
`truncated` in transcript metadata denotes historical bounded text awaiting
automatic complete-text refresh; `normalization_incomplete` separately denotes
unsupported records or relationships. Do not conflate these with raw-copy loss.
Normalizer 4 also retains Claude task-status and thinking-drop diagnostics and recognizes
session/agent labels as bookkeeping. Context attachments and compaction history remain
readable; coverage warnings and informational notes have separate metadata counts. Workers automatically
refresh old ready normalizations after Active/pause guards clear without resetting
failed retry budgets. Rebuilds reuse persisted segments when capture and normalizer versions match. See
`docs/transcript-normalization.md`. PostgreSQL retains a durable job ledger; messages carry only job UUIDs.
Existing transcripts backfill automatically; preserve legacy ready text on copy
failure and report copy coverage. See `docs/transcript-jobs.md` and use
`scripts/migrate_transcript_copies.py --wait --verify` inside the new worker.
Incorrect historical assertions require operator-only audited recovery through
`scripts/recover_transcript_paths.py prepare` and `apply`; see
`docs/transcript-recovery.md`. The original `source_path` remains immutable.
Approvals pin the verified replacement's SHA-256 and size in an append-only
journal, then use the existing RabbitMQ jobs with active-lease and pause guards.
Retain the private prepared request unchanged for an uncertain apply retry.
Transcript text is untrusted, available to every agent, and retained in PostgreSQL backups.
Stream native JSONL through private temporary segment spools, then publish complete
text from canonical segments atomically in PostgreSQL. Stage immutable manifests and
segments outside interactive project transactions, then activate under current
snapshot, generation, and lease fences. Stream marked canonical text from ordered
segments; preserve exact bytes for unmarked historical projections. Transcript indexing does not
call Tika or inherit the artifact extraction budget. Keep native record/segment limits
and the explicit PostgreSQL text capacity failure. Retained copies remain readable
if later capture policy tightens. The operator-only native limit defaults to 1 GiB.
Use a rebuildable Tantivy transcript index. Compose stores
the derived index in the private `MNEMONIC_TRANSCRIPT_INDEX_DIR` bind. Transcript
search streams metadata and content without aggregate archive-size caps;
`MNEMONIC_TRANSCRIPT_SEARCH_MAX_BYTES` is retired and ignored. Native processes
without an index directory use a private temporary disk index cleaned up on close. Rebuilds have their own
`transcript_rebuilds` receipt journal; preserve the operation UUID across uncertain retries.
Workspace imports recursively discover existing Claude Code and Codex JSONL beneath allowed roots.
Imports are project-owned, deduplicated by normalized source path against enrolled sources,
and reused by later enrollment. Import receipts retain exact folders and operation UUIDs.
The API only reads regular files beneath operator-configured allowed roots. Corrected
roots automatically retry earlier path-not-allowed failures while retaining lease
and pause guards. Base Compose mounts the configured transcript sources read-only in
API and worker at
its original absolute path; the source supplies the default allowlist. API startup
retains dashboard access when a configured source is unavailable; transcript health
reports exact path/permission failures and worker observations. Conflicting explicit
allowlists remain invalid. Fresh assertions require a readable native regular file
inside approved roots; receipt replays bypass fresh filesystem checks. Environmental
copy/index failures, oversized captures and changing sources recheck every five minutes
without rebuilding, preserving lease/pause guards. Recognized task journals are
rejected at fresh enrollment and skipped by imports. Transcript settings live on `/transcripts` with a project enable switch, server folder
browser, and editable source rows. Disabling prevents fresh imports, captures,
normalization, and indexing while retaining readable history. Capture size is
configured only through `MNEMONIC_TRANSCRIPT_MAX_BYTES`. Column sorting happens
before pagination. Access warnings are source-row scoped and cannot be dismissed. Health reports all-project
allocated native/index bytes, their separate volume free space, and database usage;
partial/unavailable measurements remain explicit. The
shared worker also queues scheduled/manual project backups; there is no dedicated
backup container. Raw transcript copies require filesystem backups. See
`docs/transcripts.md` for the read-only shared-filesystem mount and workspace settings.

Humans can earmark work with `update_work.request_code_review=true`; Done work queues
immediately and other work queues at completion. Preserve human requester provenance.
Unscoped manual reviews require a verified handoff with their first warm claim;
preserve that handoff and all claim arguments on uncertain retries. Later claims
omit it. Cold review requires an already pinned scope.
Summary cards request warm reviews or collect a cold review scope. Optional
manual-request mode is immutable and claims must match it. The originating
completion checkpoint protects summary requests from later completion episodes.
Reviews belong to original Done work and require purpose-bound review leases.
Dashboard humans can defer, close, or return the review episode to To review through
`update_work.review_decision`; its append-only history preserves implementation Done.
Deferred or manually closed episodes cannot be claimed or answered. Manual Active
is unavailable for every work item.
Optional closeout questions are durable originating-session follow-ups, not
human gates. Cold reviewers must not load context before freezing findings.
Fresh token-bearing `append_event` and `add_checkpoint` writes renew an active
implementation lease atomically using its last granted duration, clamped to current
project minimum/maximum; exact receipt replays
and token-free writes do not renew it. Review leases still use `renew_claim`.
New remediation summaries describe finding count, primary file, and finding titles.
One completed review creates zero or one remediation containing all findings;
immutable depth 2 can never be reviewed, and remediation cannot be merged.

## Commit & Pull Request Guidelines

Use short, specific, sentence-case subjects instead of generic `Updates`. Keep commits scoped. PRs should target `main`, remain short-lived, and explain behavior and migration/config impact, link relevant work, list checks run, and include screenshots for visual changes. The `Required checks` GitHub Actions status must pass before merge; never bypass the gitleaks hook with `--no-verify`. Never commit `.env`, keys, test output, backups, or database volumes.

`CLAUDE.md` is an intentionally local, ignored operator/client note rather than a
tracked source of truth. Refresh any local copy when a phase changes the tool
catalog, migration head, shipped-phase status, client retry rules, or error codes.

Knowledge decisions live in PostgreSQL with immutable text and metadata revisions.
Use `decision_get` to read a pinned revision, `decision_put` to create/revise a
document with explicit state, and `decision_scope_append` after implementing code
based on it. The full resulting commit hash and consulted revision are mandatory;
include work item ID and native agent session ID when available. Domain prefixes
are fixed and unique within a project. Search includes `decisions`; narrow with
`filters.decisions.domain_ids` and opt into history using `all_revisions=true`.
See `docs/knowledge-graph.md`.

Knowledge revisions may declare `depends_on` with exact target revisions and reasons;
related links do not establish causality. Use `decision_impact` before amending a
decision and inspect coverage, then page the immutable assessment episodes and
record version-checked dispositions with truthful actor identity. Assessments do
not change decision state or grant execution authority. Every project has a PDR
domain, so fresh Done requires `job_completion_report.decision_provenance`: exact
citations and repository/commit identities, or an explicit reason for
`no-applicable-decisions`/`not-code`. Register actual scope as pull requests merge;
Done still declares complete actual citations and reuses exact existing
decision/revision/repository/commit scope. Missing repository remains unknown,
not a match for an explicit identity. Fresh writes canonicalize supported GitHub
slugs and HTTP(S) URLs; historical rows and exact retry arguments keep their
original spelling. Lookup accepts supported aliases, while reconciliation requires
the exact stored spelling returned by lookup. Preserve original commit records and use
`decision_scope_reconcile` only for an inspected integration mapping. Historical
missing provenance remains unknown. The bundled decision_provenance.py helper
verifies local commit existence and prepares declarations without network or writes.

Automatic decision impact reviews are configured independently in Knowledge graph >
Manage domains. Each domain defaults to off with priority 40; fresh settings accept
on/off and a local decision_review_priority from 0 to 100. There is no global
switch or priority. Migration preserves each existing domain's effective policy.
The changed record domain governs new reviews; settings do not backfill work or
cancel existing obligations. Explicit changes
own one ordinary review with a
snapshot of the editable decision-impact-review prompt. Related revisions extend
its assessment scope; read decision_change_get and page decision_assessments with
change_id, then acknowledge the exact current scope before Done. Fresh agent
decision_put declares source_work_item_id (or explicit null for an external trigger).
Changes default to substantive; record-existing names its governing change and exact
revision, and maintenance requires a reason. Follow-ups inherit lineage. Closed
cycles require explicit dashboard admission; managed owner answers remain retained
while awaiting admission. Adoption binds named episodes into an ordinary combined
investigation. Investigate only; create separate work for remediation. Impact and
assessment reads default to kind=code-impact for the
origin and reverse depends_on dependents; kind=context separately pages
prerequisites and related references. Only code-impact candidates require verdicts;
every recorded change-required result still requires remediation. Classify full
text changes as rule/scope/reference changes, inspect named missing-commit warnings,
and use commit messages, decision-declared files and source-ID searches for gaps.
Revision author metadata and timed active-work observations are evidence, not
proof of historical ownership. See docs/knowledge-graph.md#automatic-decision-impact-reviews.

Declared unchanged supersessions can reuse an immutable prior disposition reference
for a required candidate only when its captured evidence remains equal except
`is_current`, with complete matching coverage except `snapshot_at`. New or changed
evidence and unknown/incomplete snapshots need fresh review. Keep every candidate;
read `reused_from` and use the new assessment version for local writes.
`decision_change_get.lineage_work` pages other work in the change, including source
work and code-review remediation. Inspect relevant open work before conclusions and
again before completion; shared lineage alone proves no defect.

Incoming related-only citations are required code-impact candidates with captured
`related_direction="incoming"`, while causality remains unknown. Outward references
and prerequisites stay context. Historical snapshots without direction retain
their optional related-only disposition policy; never infer direction retroactively.
