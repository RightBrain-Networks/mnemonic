# Mnemonic Phase 11 — Structured Completion Evidence Implementation Plan

This document is the implementation contract for Phase 11 and is a planning
artifact only. Its checkboxes describe future work; they do not assert that
implementation or validation has started.

The plan was started on the dedicated
`work/phase11-structured-completion-evidence` worktree at
`a0cc7fc0863b6ddf70f436d1a5ab511bd20c700d`. While planning was in progress,
Phase 10 merged through pull request 17. This worktree was then fast-forwarded
onto the delivered Phase 10 commit
`fe7231595c9009cd46b244ed672a1db06563173d`, then fast-forwarded over two
unrelated follow-ups to current `origin/main`
`97317d8c675e8869cbe1aff684c6cc97dd235c10` before this plan was frozen for
adversarial review. The earlier Phase 10 peer implementation was inspected
read-only at `ed999601e9de23936c88443ac41e00d5ef6ff17b`; it has since been
retired and is not an integration source. The delivered Phase 10 boundary is:

- application, REST API, MCP adapter, and dashboard `0.5.0`;
- Claude Code plugin `0.9.0`;
- Alembic head `0018_repository_freshness`;
- 27 MCP tools, 11 receipt-protected MCP writes, 13 REST receipt kinds, and 11
  protected browser mutations.

Phase 11 implementation must start from this merged Phase 10 boundary (or a
later `origin/main` containing it), repeat the surface and receipt inventories,
and make migration `0019_structured_completion_evidence` revise the delivered
`0018_repository_freshness`. If `origin/main` advances before implementation,
the topic branch must be rebased again and the baseline gate repeated. No file
from the retired peer worktree may be copied or merged around reviewed `main`.

Phase 11 is one coordinated user-facing prerelease release:

- application, REST API, MCP adapter, and dashboard: `0.6.0`;
- Claude Code plugin: `0.10.0`;
- database migration: `0019_structured_completion_evidence`;
- receipt request-fingerprint and response-contract versions: remain `1`;
- MCP catalog: 28 tools;
- receipt-protected MCP writes: remain 11;
- durable REST receipt kinds: remain 13;
- protected browser mutation kinds: remain 11.

The central design decision is intentionally narrow: verification results and
artifact references are accepted only as part of the existing atomic
`complete_work` operation and belong to that exact immutable completion
checkpoint. Phase 11 adds one read-only `list_completion_evidence` MCP tool and
one corresponding REST history endpoint. It does not add an independent
evidence mutation, a new receipt kind, or a second browser write.

This realizes the Phase 6 requirement that verification submission use the
durable receipt mechanism: non-empty evidence conditionally requires
`client_operation_id`, and the submission is inside the already-enrolled
`complete_work` intent, receipt, transaction, and replay response. Existing
direct REST completion without an operation UUID remains valid only when
evidence normalizes to absent. This also preserves the roadmap's
coarse-grained-operation principle. A future need for asynchronous or
post-completion evidence can justify a separately designed append operation;
Phase 11 does not prebuild that lifecycle.

---

## 1. Outcome

After Phase 11 ships:

1. A completion request may carry an ordered, bounded
   `completion_evidence` object containing structured verification results and
   artifact references. A non-empty object requires the existing top-level
   client operation UUID at every public write surface.
2. The completion checkpoint, all supplied evidence rows, the transition to
   `done`, the existing `work_completed` event, and the existing durable
   `complete_work` receipt commit atomically or all roll back.
3. Every evidence row belongs to exactly one immutable completion checkpoint
   on the same work item. Reopening and completing again creates a new
   completion episode with new evidence; it never rewrites or adopts evidence
   from an earlier episode.
4. A verification result records what a caller reports observing. It has a
   discriminated type, a machine-readable outcome, a bounded human summary,
   and optional observation time and repository commit. Command evidence also
   records the inert command text and its observed exit code or lack of one.
5. An artifact reference records a typed, bounded locator such as a commit,
   branch, repository path, pull request, test run, external issue, or build
   artifact. Mnemonic stores the reference; it does not resolve, fetch, open,
   validate, retain, or attest the artifact.
6. Evidence is immutable. There is no update, delete, replace, redact, or
   post-completion append route. A materially wrong completion is corrected by
   durable context plus the existing explicit reopen/new-completion lifecycle,
   leaving the original claim visible.
7. Structured evidence is optional. Historical and new completion episodes
   with no rows are represented honestly as “No structured completion evidence
   recorded,” not as failed, unverified, or implicitly passing.
8. A dedicated page returns every completion episode, including episodes with
   no structured rows, in monotonic `work_completed` event order. Each item
   carries that event identity, a compact completion checkpoint pointer, and
   its ordered result/reference arrays. Page-level work/canonical identity
   distinguishes a current canonical completion from retained alias history.
9. Agents can retrieve the complete structured history without parsing
   checkpoint prose. Humans can inspect the same evidence in a dedicated,
   lazily loaded dashboard tab.
10. Existing `work_completed` events remain the lifecycle timeline fact. No
    `verification_run` event, new event reference column, or duplicated event
    metadata is added; the event's existing completion checkpoint ID is the
    join key.
11. Commands, outcomes, commits, paths, labels, summaries, and URLs remain
    caller assertions and untrusted historical content. The API, MCP adapter,
    browser, and plugin never execute a stored command or automatically contact
    a stored locator.
12. Verification outcomes and artifact presence do not change readiness,
    blockers, gates, claims, work versions, merge authority, or completion
    authorization. They are evidence, not a policy engine.
13. Phase 10 repository-freshness assessments remain ephemeral and
    client-local. They are never copied automatically into Phase 11 evidence
    and never become server verification.
14. Existing production content, every Phase 10 affected-path declaration,
    every event, and every permanent receipt byte remains intact. No evidence
    is inferred from checkpoint text, metadata, tags, events, commits, or local
    repository state.

A representative response item is:

```json
{
  "completion_event_id": "481",
  "completion_checkpoint": {
    "id": "82c3c46c-8665-48ad-a0be-13fb196418be",
    "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
    "kind": "completion",
    "source_client": "codex",
    "source_session_id": "session-42",
    "source_model": "gpt-5",
    "repository_branch": "work/example",
    "verified_against": "7ad62e4",
    "tags": ["backend"],
    "migration_origin": null,
    "legacy_record_id": null,
    "created_at": "2026-09-03T18:04:12.123456Z"
  },
  "verification_results": [
    {
      "id": "a96f13a0-b552-451f-970c-90b2387cc518",
      "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
      "completion_checkpoint_id": "82c3c46c-8665-48ad-a0be-13fb196418be",
      "position": 0,
      "verification_type": "command",
      "name": "Backend test suite",
      "outcome": "passed",
      "summary": "1,284 tests passed; the PostgreSQL suite ran without skips.",
      "command": "uv run pytest -q",
      "exit_code": 0,
      "observed_at": "2026-09-03T18:01:02Z",
      "observed_at_commit": "7ad62e4",
      "created_at": "2026-09-03T18:04:12.123456Z"
    }
  ],
  "artifact_references": [
    {
      "id": "515f3427-0fa5-497e-b95a-47417fb0ba28",
      "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
      "completion_checkpoint_id": "82c3c46c-8665-48ad-a0be-13fb196418be",
      "position": 0,
      "artifact_type": "pull_request",
      "label": "Phase 11 pull request",
      "reference": "https://github.com/example/mnemonic/pull/123",
      "created_at": "2026-09-03T18:04:12.123456Z"
    }
  ]
}
```

The UI and agent guidance must call this **recorded** or **caller-reported**
evidence. A green “verified” badge, “Mnemonic proved this,” or any equivalent
claim is forbidden.

### 1.1 Non-goals

Phase 11 does not:

- execute, retry, parse, normalize, shell-quote, or schedule a stored command;
- store raw stdout, stderr, full test logs, environment dumps, or private
  chain-of-thought;
- clone, mount, identify, fetch, pull, or otherwise inspect a repository;
- persist the Phase 10 helper protocol or treat `unchanged` as completion proof;
- contact CI providers, source hosts, issue trackers, artifact stores, or URLs;
- upload, proxy, mirror, retain, checksum, sign, or attest artifact contents;
- implement SLSA, in-toto, SBOM, provenance signatures, trusted identities, or
  cryptographic verification;
- require structured evidence for completion or infer failure from its absence;
- decide whether reported evidence is sufficient for the objective;
- automatically block or permit completion based on `passed`, `failed`,
  `inconclusive`, or `skipped` values;
- add a post-completion evidence write, artifact-only write, edit, deletion,
  supersession, or correction endpoint;
- add a `verification_run` or `completion_evidence_recorded` work event;
- add evidence to bounded `WorkContext`, checkpoint pointers outside the
  evidence page, search, hierarchy, ready work, gates, relationships,
  embeddings, duplicate suggestions, or cache identity;
- copy evidence from an alias to its canonical destination or group histories;
- create evidence for historical completions during migration;
- implement a project activity cursor, webhook, SSE stream, notification, or
  external orchestration hook from Phase 12;
- introduce an old/new model union, legacy alias, response downgrade projection,
  dual write, or other compatibility shim.

---

## 2. Shipped baseline and constraints

### 2.1 Phase 10 is a hard implementation prerequisite

Phase 11 is planned against Phase 10's delivered contract, even though its
planning worktree necessarily branched before Phase 10 merged. Implementation
must first prove all of the following against merged `main`:

- the Alembic head is exactly `0018_repository_freshness`;
- application/API/MCP/dashboard and plugin versions are `0.5.0` and `0.9.0`;
- full checkpoint input/read models contain sparse ordered `affected_paths`;
- compact `CheckpointPointer` does not contain `affected_paths`;
- all 13 receipt kinds and their response bodies still use contract version 1;
- the catalog is 27 MCP tools/11 protected writes and the browser has 11
  protected mutations;
- `mcp/uv.lock` resolves MCP/FastMCP `1.29.1`; its request ID remains an
  unbounded strict integer-or-string union and its stdio reader iterates
  unbounded decoded text lines, so Phase 11 cannot delegate its new ingress/ID
  ceilings to the SDK;
- the Phase 10 helper remains local, read-only, and separate from persisted
  completion evidence.

If the merged Phase 10 interface differs, the implementation phase must update
this plan's baseline and re-run a cold contract review before adding migration
0019. It must not create an alternate 0019 branch from migration 0017 or copy
unmerged peer-worktree files.

### 2.2 Completion is already an atomic lifecycle mutation

The current `complete_work` flow:

1. locks one exact visible work item;
2. rejects a duplicate alias;
3. requires `status=pending` and the exact `expected_version`;
4. rechecks blockers and unresolved human gates;
5. validates or consumes the active lease capability;
6. appends one immutable `kind=completion` checkpoint;
7. changes the item to `done`, increments its version, and updates activity;
8. writes one source-matched `work_completed` event; and
9. commits those facts with the durable receipt.

Phase 11 extends this aggregate. It does not make a second call whose success
could be lost between “done” and “evidence recorded.”

Work can later move from `done` to `pending` and be completed again. Therefore
`work_item_id` alone is not an evidence identity. The immutable completion
checkpoint is the completion-episode key.

### 2.3 Permanent receipts constrain both request and response models

The existing `complete_work` request and `WorkCompletionRead` response are
permanent replay contracts. A defaulted `completion_evidence: {}` or
`completion_evidence: null` property would change historical fingerprints or
reserialized response bodies even when no evidence exists.

Phase 11 uses a field-local sparse rule:

> An omitted evidence field and an explicitly empty evidence object are one
> no-structured-evidence value. Their canonical request and response
> representation omits `completion_evidence` entirely.

A non-empty evidence object is ordered, serialized, fingerprinted, and returned
normally. Historical request fingerprints and response bodies are never
rewritten. Request-fingerprint version 1 and response-contract version 1 remain
the only versions.

### 2.4 Completion checkpoints already provide provenance

Evidence rows inherit their submitting client, session, model, work item,
completion time, and completion episode from their parent checkpoint. They do
not repeat mutable actor columns or accept a second actor that could disagree.

The completion checkpoint's prompt remains the narrative summary. Structured
evidence does not duplicate it into an aggregate `summary` field. Individual
verification summaries explain only the observation represented by that row.

### 2.5 Duplicate aliases retain exact history

An authoritative merge keeps the source work item, checkpoints, events, and
receipts as a non-actionable audit alias. Phase 11 follows the same ownership
rule:

- evidence recorded before a merge stays linked to the source completion;
- canonical-root reads never blend source evidence into destination evidence;
- exact alias evidence reads remain source-owned and visibly labeled;
- a fresh completion—and therefore fresh evidence—cannot target an alias;
- historical exact `complete_work` receipt replay still succeeds before current
  alias/lifecycle checks.

### 2.6 Evidence is asserted, not authenticated or independently verified

Mnemonic uses a shared bearer boundary and caller-asserted client/session/model
provenance. Phase 11 does not claim that the named model or session ran the
command, that the process ran in a particular directory, that a URL points to
the named artifact, or that an artifact is immutable or available.

Machine-readable means that clients do not need to scrape prose. It does not
mean machine-attested, trusted, sufficient, current, or correct.

### 2.7 Prerelease evolution without shims

Mnemonic is prerelease. Phase 11 updates all first-party clients in one release.
Once a response contains non-empty structured evidence, strict 0.5.x clients
are unsupported and may reject it. That is an explicit coordinated-release
boundary, not a reason to maintain two response models.

Existing database content must be preserved. Public compatibility with older
strict clients after first use is not required. No response filter, feature
flag, shadow table, receipt rewrite, or dual schema is introduced.

---

## 3. Decisions fixed by this plan

### 3.1 Coordinated version and inventory boundary

Phase 11 ships as application/API/MCP/dashboard `0.6.0`, plugin `0.10.0`, and
migration `0019_structured_completion_evidence`.

The plugin changes from `0.9.0` to `0.10.0`; it does not cross to `1.0.0`
because repository policy reserves major releases for explicit human approval.

The catalog changes only by adding `list_completion_evidence`:

| Inventory | Phase 10 | Phase 11 |
| --- | ---: | ---: |
| MCP tools | 27 | 28 |
| Receipt-protected MCP writes | 11 | 11 |
| Durable REST receipt kinds | 13 | 13 |
| Protected browser mutations | 11 | 11 |
| Claude plugin skills | 3 | 3 |
| Claude plugin shared references | 3 | 4 |
| Claude plugin executables | 1 | 1 |

The new REST surface is one safe `GET`. The existing `complete_work` POST is
extended; it is not duplicated or renamed.

### 3.2 Chosen write topology

The only Phase 11 evidence write is:

```text
complete_work(..., checkpoint, completion_evidence?)
```

The evidence is created inside the completion transaction. This is preferred
over the roadmap's tentative standalone `add_verification_result` name because:

- the checkpoint exists as the exact completion-episode key in the same
  transaction;
- required checks cannot be stranded behind an already-committed `done` state;
- one client operation UUID protects the complete semantic intent;
- result and artifact order is frozen together;
- an ambiguous response has one exact retry, not two interdependent retries;
- no result-to-later-completion inference or mutable association is needed;
- no new authoritative event, activity touch, work lock policy, receipt kind,
  or alias mutation path is introduced.

An independent append is not an alias for this operation and will not be added
quietly during implementation. It is a future product decision with its own
lifecycle, correction, replay, and current-completion semantics.

For the roadmap acceptance criterion, “append-only evidence” means rows are
inserted once as new historical facts inside their completion episode and can
never be updated or deleted. It does not require—or authorize—a later append
to an already completed episode.

Because direct REST historically permits an unkeyed completion, the new
cross-field contract conditionally requires `client_operation_id` whenever
`completion_evidence` normalizes to non-empty. Omitted or explicitly empty
evidence retains the historical unkeyed REST form. MCP and browser completion
already require the UUID in every case. This conditional is structural
validation and runs before any receipt reservation or domain write.

### 3.3 Completion episode identity

Every result/reference row stores:

```text
project_id
work_item_id
completion_checkpoint_id
position
created_at
```

The composite work/checkpoint foreign key prevents cross-work attachment. An
insert guard requires the checkpoint's kind to be `completion`. `created_at`
is server record time and must equal the completion checkpoint timestamp.

Rows can be inserted only before the corresponding `work_completed` event is
visible, and a deferred guard requires that exact immutable event. While its
generation is still the work's current generation, the same guard requires
the work to be `done`; after a fully sealed episode has been left through the
non-deferrable departure guard below, a smaller historical generation remains
valid. Consequently the only legal write window is the completion transaction.
Evidence cannot be appended to an already completed historical checkpoint by
API or direct SQL.

The child guard is not the episode-creation guard. Phase 11 adds a database-
managed completion generation to work items and completion checkpoints plus a
private reopen-generation binding on work events. Every pre-0019 completion
checkpoint and reopen event is deterministically placed in its own disjoint
negative namespace by negating that fact's positive event ID. A migrated work
that is currently `done` stores the negative generation of its highest-ID
retained completion event; other existing and all new work starts at
generation zero. This materializes the same current completion fact without
inferring a historical reopen pairing. The first later non-pending-to-pending
transition maps any nonpositive work generation to positive generation one,
and later such transitions increment it. A partial unique
checkpoint index permits only one completion per work/generation, including
while the first checkpoint is still eventless. A separate partial event index
permits only one reopen event for each positive work/generation, while the
event primary key and scalar binding make one event incapable of witnessing
multiple generations.

The completion-checkpoint guard applies whether the completion has twenty
children or none: before any new `kind=completion` checkpoint is inserted, it
locks the same work row, requires a live canonical `pending` item, and assigns
the work's necessarily nonnegative generation. At forced-check time or commit
it requires the checkpoint's one exact live `work_completed` event, its
generation no greater than the work's current generation, `done` state when
the generations are equal, and any required retained reopen witness. A
regular pending-exit guard prevents an eventless checkpoint from leaving its
generation: `pending -> done` requires exactly its current still-eventless
completion checkpoint, a live canonical row in both transition images, and
the exact checked work-version increment, while
`pending -> deferred|wont-do|promoted` requires that no current-generation
completion checkpoint exists. A non-pending state cannot jump directly to
`done`. Evidence children can be inserted only while that exact checkpoint
generation is still the work's current pending generation; the database
therefore freezes checkpoint/children/state/event order as well as the final
aggregate. A regular unsealed-deletion guard also forbids a live work from
becoming soft-deleted while its current-generation completion checkpoint is
still eventless, so clearing the timestamp later in the transaction cannot
erase an invalid abandonment.

Another regular work-side guard revalidates the current completion event and
complete evidence aggregate before any `done -> pending` departure; none of
the regular guards can be discharged with `SET CONSTRAINTS`. A separate
deferred work-transition guard and a symmetric event-side guard bind every
post-cutover non-pending-to-pending generation increment to one exact
immutable live `work_reopened` event, in both directions. A locked insertion
guard
independently requires every new `work_completed` event to target the work's
exact current-generation checkpoint and requires its ID to be positive and
strictly greater than all prior same-work completion-event IDs, including when
direct SQL overrides the identity or resets its sequence. Its typed
`work_version` must also be strictly greater than every prior same-work live
completion version and, for a positive generation, its exact bound reopen
version. That retained bigint identity is the monotonic episode sequence, while
the version predicates prevent a status-preserving direct reset from creating
history that a later re-upgrade cannot accept. History is driven only by
event-backed completion checkpoints. Multiple genuine completion/reopen cycles
may occur in one transaction only in the exact
checkpoint/pending-to-done/event/sealed-departure order; a selectively
discharged or abandoned invalid checkpoint cannot be stranded as history. Any
retained checkpoint/event/generation/ordering disagreement fails migration
preflight, read assembly, and audit closed. A transient invalid ordering or
liveness change that leaves no independently auditable fact is rejected by
its regular or deferred database guard before commit.

### 3.4 Optional evidence and sparse canonical form

The completion request gains:

```text
completion_evidence?: {
  verification_results?: VerificationResultInput[]
  artifact_references?: ArtifactReferenceInput[]
}
```

Rules:

- omission means no structured evidence;
- an object whose two arrays are omitted or empty normalizes to the same value;
- explicit JSON `null` is rejected rather than becoming a third spelling;
- a non-empty object contains between 1 and 20 total child entries;
- a non-empty object requires `client_operation_id`; omission/empty evidence
  does not change the historical optional-ID direct REST contract;
- canonical output omits `completion_evidence` when no rows exist;
- canonical non-empty output always includes both arrays, using `[]` for the
  empty child family;
- a response that contains `completion_evidence: null` or a present object with
  zero total children is noncanonical and rejected by strict response guards.

The outer field uses a field-local exclusion. No global serialization flag is
changed.

Presence and nullability are exact:

| Field family | Accepted request spelling | Canonical request/output |
| --- | --- | --- |
| `client_operation_id` | With absent/empty evidence: omitted, explicit `null`, or valid UUID under the historical transport contract. With non-empty evidence: a present non-null valid UUID. | Control field is stripped before domain fingerprinting; receipt identity remains outside the request fingerprint. |
| `completion_evidence` | omitted, `{}`, omitted arrays, or arrays `[]`; a non-empty object as specified | Empty forms become omission; non-empty output is present. Explicit `null` is rejected. |
| `verification_results`, `artifact_references` | omitted or array; `null` rejected | Empty input families normalize to `[]`; both arrays are required in every non-empty output object. |
| Required result/artifact fields | present, non-null, correct strict type | Always present. Null is rejected; booleans are not integers. |
| `observed_at`, `observed_at_commit` | omitted or a valid non-null value | Omission stays omitted in request fingerprint and output. Explicit `null` is rejected. |
| command-result `command` | required non-null string | Always present for `verification_type=command`. |
| command-result `exit_code` | required integer for `passed`/`failed`; omitted for `inconclusive` | Present integer for determinate outcomes; omitted for inconclusive. Explicit `null` is rejected in every case. |
| observation `command`, `exit_code` | keys forbidden, including with `null` | Absent. |
| server `id`, `work_item_id`, `completion_checkpoint_id`, `position`, `created_at` | forbidden in input | Required in every child read object. |

Thus omission and explicit null are never two fingerprint spellings for an
unknown evidence value: null is invalid. The deliberate fingerprint
equivalences are limited to the empty outer forms above and the explicitly
enumerated `observed_at` offset/fraction spellings in section 3.9. No other
field is trimmed or normalized into an equivalent intent.

### 3.5 Verification-result vocabulary

V1 supports exactly two discriminated verification types:

| `verification_type` | Meaning | Command fields |
| --- | --- | --- |
| `command` | The caller reports observing one process invocation. | `command` required; `exit_code` follows the outcome matrix. |
| `observation` | The caller reports a non-command check, review, inspection, or external observation. | `command` and `exit_code` forbidden. |

Every result has:

```text
name
outcome
summary
observed_at?
observed_at_commit?
```

`outcome` is exactly:

```text
passed | failed | inconclusive | skipped
```

These words are reported result categories. They are never aggregated into an
overall completion score and never trigger a lifecycle change.

V1 uses a strict union, not an arbitrary `metadata` object or a nullable field
bag. A future verification type requires an intentional schema/API migration.

### 3.6 Command and exit-code matrix

For `verification_type=command`:

| Outcome | `command` | `exit_code` |
| --- | --- | --- |
| `passed` | required | exactly `0` |
| `failed` | required | any strict signed 32-bit integer except `0` |
| `inconclusive` | required | absent; use the summary for timeout, signal, unavailable runner, or indeterminate interpretation |
| `skipped` | invalid | invalid; an unexecuted planned command is not a command result |

For `verification_type=observation`, `command` and `exit_code` are absent for
all four outcomes. A `skipped` observation records that a named check was
actually observed to be skipped and its summary explains why; it is not a
placeholder for a check the caller forgot to run.

V1 intentionally uses the conventional process-status mapping: zero is
reported `passed` and nonzero is reported `failed`. A tool whose domain treats
another status as successful must record an `observation` or an
`inconclusive` command with an explanation; it cannot contradict the matrix.
The server validates that structural convention only. It never judges whether
the check is semantically sufficient, whether the summary matches output, or
whether the command ran at all.

### 3.7 Artifact-reference vocabulary

V1 supports exactly:

```text
commit
pull_request
branch
test_run
repository_path
external_issue
build_artifact
```

Each reference has `artifact_type`, `label`, and `reference`. Validation is
type-specific:

| Type | Reference contract |
| --- | --- |
| `commit` | 7–64 lowercase hexadecimal characters; caller-declared and not resolved. |
| `branch` | Exact bounded nonblank Unicode text, no NUL, and no leading/trailing whitespace; mutable by nature. |
| `repository_path` | One exact relative path using the Phase 10 safe path-component grammar without `*` or `**`; no glob meaning. |
| `pull_request`, `test_run`, `external_issue`, `build_artifact` | Absolute ASCII `https://` URL, no userinfo, query, or fragment; IPv6 literals use exact lowercase canonical compressed spelling; exact text is preserved and never fetched automatically. |

The URL restriction intentionally excludes expiring signed URLs and embedded
tokens. A durable canonical page URL should be stored instead. A path that the
V1 ASCII grammar cannot represent remains narrative checkpoint context; the
server does not lossy-normalize it.

Artifact branches use a new exact-preservation validator rather than the
checkpoint `BranchName`, which trims. Edge whitespace is rejected; accepted
internal whitespace and case are preserved and fingerprinted.

Exact duplicate `(artifact_type, reference)` pairs within one completion are
rejected. The same durable artifact may legitimately appear in a later
completion episode. Verification results are not deduplicated because repeated
runs can be distinct observations.

### 3.8 Exact bounds, ordering, and preservation

Bounds are enforced in Python, PostgreSQL, MCP, and TypeScript from one shared
fixture corpus:

- at most 20 total verification results plus artifact references;
- `name` and artifact `label`: 1–200 characters, at most 800 UTF-8 bytes,
  nonblank, no NUL;
- verification `summary`: 1–4,000 characters, at most 16,000 UTF-8 bytes,
  nonblank, no NUL;
- `command`: 1–4,096 characters, at most 16,384 UTF-8 bytes, nonblank, no NUL;
- branch: existing 200-character bound and no NUL;
- repository path: at most 512 ASCII bytes;
- external URL: at most 2,000 ASCII bytes;
- `observed_at`: 20–32 ASCII bytes before parsing, with the exact grammar and
  canonical UTC spelling in section 3.9;
- all stored caller strings in one completion evidence object: at most 32,768
  UTF-8 bytes in aggregate;
- positions are zero-based, contiguous, and unique within each child family.

Array order, string spelling, case, whitespace, and summaries are preserved
except where a type's grammar explicitly requires lowercase commit hex or
lowercase `https://`. `observed_at` preserves the instant but canonicalizes its
wire spelling to UTC as section 3.9 specifies. The server does not trim, sort,
expand, or deduplicate verification results. Evidence order is part of the
protected request fingerprint and response coherence.

“Character” means a Unicode scalar value encoded as valid UTF-8. Python uses
code-point length, PostgreSQL uses `length`, and TypeScript counts code points
with iteration/`Array.from`, never UTF-16 code units; all byte checks use the
actual UTF-8 encoding.

The aggregate byte count is exactly the sum of UTF-8 byte lengths of these
validated, stored input strings:

```text
for each verification result:
  verification_type + name + outcome + summary
  + command when present
  + 32 bytes when observed_at is present
  + observed_at_commit when present

for each artifact reference:
  artifact_type + label + reference
```

The fixed 32-byte charge for a present timestamp is its maximum accepted wire
length and deliberately avoids parser- or serializer-dependent accounting.
The aggregate excludes integer `exit_code`, server IDs/positions, and server
`created_at`. The fixed 32,768-byte aggregate replaces the rejected larger
draft bound. A generated worst-case corpus must prove, with every existing
completion/checkpoint/work field simultaneously at its legal escaping maximum,
that the compact canonical request, canonical fingerprint envelope,
API/receipt response, and PostgreSQL `jsonb::text` remain at or below 896
KiB—below the deployed browser/nginx 1 MiB ingress ceiling and the permanent-
receipt ceiling. A raw deployed REST/browser request remains independently
subject to an inclusive 1,048,576-byte ceiling and may be larger than 896 KiB
because legal JSON permits transport-only whitespace or noncanonical escape
spellings; the 896 KiB claim does not cover that raw transport entity. Direct
backend deployments must preserve an equivalent upstream body limit or accept
that only the post-parse canonical intent is bounded here. The ten-episode
REST history document has a separate 3 MiB serialized-JSON and identity-body
budget enforced by the API and every first-party reader.

That 3 MiB ingress/read bound does not describe the MCP server's emitted tool
result. The locked FastMCP SDK currently represents a typed result twice: as
JSON text in `content` and as the object in `structuredContent`. A generated
maximum-page test must pass the exact typed value through the installed SDK
and serialize the complete JSON-RPC success response with the largest permitted
request ID. The complete application payload—the Streamable HTTP response body
or the stdio record including terminal LF—contains both representations and
the response ID and is at most 12,582,912 UTF-8 bytes (12 MiB). HTTP headers,
transfer framing, TLS, and lower layers are outside this JSON-RPC byte budget.
Exercise both real Streamable HTTP and stdio delivery.

That response guarantee requires a bounded caller-controlled ID and request
frame before FastMCP parses or dispatches it. Phase 11 therefore freezes one
shared inbound MCP transport contract:

- each Streamable HTTP POST entity and each newline-delimited stdio JSON record
  is at most 1,048,576 raw bytes; the stdio delimiter is excluded;
- Streamable HTTP accepts only absent or a single case-insensitive `identity`
  `Content-Encoding` and rejects every other or malformed coding before the
  first body pull;
- the decoded top level is exactly one JSON object, never a scalar, array, or
  JSON-RPC batch;
- a present JSON-RPC `id` is either a strict signed 64-bit integer or a
  1–128-character ASCII string matching `[A-Za-z0-9._:-]+`; `null`, booleans,
  floats, empty strings, other characters, and out-of-range integers are
  invalid; and
- the guard applies to every inbound protocol method, not only tool calls or
  evidence methods. A notification remains ID-less.

The largest permitted string ID occupies exactly 130 bytes as a JSON token.
The maximum-envelope fixture uses that ID. A project-owned ASGI middleware and
bounded binary stdio adapter enforce the raw entity/record limit, top-level
shape, and ID domain before handing a validated/replayed message to the SDK;
they do not monkey patch FastMCP. The HTTP path returns a bounded content-free
error with no caller ID when it cannot safely validate the frame, top level,
or ID. The stdio path treats an over-limit record, invalid UTF-8/JSON, a
non-object top level, or an invalid ID as a terminal protocol violation: it
closes with no response or control-frame bytes for that rejected record,
discards every later buffered record, and never hands any of them to the SDK.
An object that passes those transport checks is handed to the SDK; remaining
JSON-RPC version, method, params, and protocol semantics belong to the SDK and
use only its response writer. Previously completed SDK frames may precede the
close. The adapter never creates a second stdout writer or injects an error
into an SDK frame; transport shutdown may cut an in-flight SDK frame, which is
already an ordinary malformed/EOF unknown outcome rather than a purported
response. An in-flight protected write uses the existing frozen
operation-UUID receipt/retry procedure. Both readers count chunks
incrementally, distrust `Content-Length`, and never copy byte 1,048,577 into a
Mnemonic accumulator. As with the history reader, this does not claim control
over a runtime-owned input chunk. Strict UTF-8 and JSON parsing happen only
after the raw limit succeeds.

If the measured response payload exceeds 12 MiB, lower an unreleased evidence
or page limit through plan review; do not truncate evidence, silently drop one
SDK representation, or raise the ceiling without reviewing client/context
cost. Repeat the measurement and the private transport-seam fixture after any
MCP SDK or serialization change. These ceilings are contract tests; legal
canonical input may not fail late because of response size.

### 3.9 Time and commit semantics

`created_at` is database record time shared with the completion checkpoint. It
does not claim when a check actually ran.

`observed_at` is an optional caller-asserted timezone-aware timestamp. Before a
generic datetime parser runs, every layer requires a JSON string containing
only ASCII and matching this bounded RFC 3339 subset:

```text
YYYY-MM-DD "T" HH:MM:SS [ "." 1*6DIGIT ] ( "Z" / ( "+" / "-" ) HH:MM )
```

The lexical length is 20–32 bytes. Year is `0001..9999`; calendar date must be
real; hour is `00..23`; minute and second are `00..59` (no leap-second `60`);
an offset is at most `14:00`, with minute `00` when hour is `14`; lowercase
`t`/`z`, spaces, omitted timezone, more than six fractional digits, and the
RFC 3339 unknown-offset spelling `-00:00` are rejected. Parsing and conversion
to UTC must not underflow or overflow Python's year `1..9999` range. The
database check enforces the same UTC range on `TIMESTAMPTZ`, so direct SQL
cannot retain a value that the public read model cannot serialize.

Canonical request fingerprints and all output use UTC with uppercase `Z`, no
fraction when microseconds are zero, and exactly six fractional digits
otherwise. Accordingly, different accepted offsets for the same instant,
fraction spellings such as `.1` and `.100000`, and an all-zero fraction versus
no fraction are intentional equivalent intents after canonicalization. A
maximum-length spelling and every boundary/normalization pair are part of the
shared fixture and full-envelope budget generator. The value may be omitted
when the caller cannot state it truthfully; no server clock-skew inference is
made.

Three commit concepts remain distinct:

1. completion checkpoint `verified_against` is the commit the checkpoint author
   says it inspected; with `affected_paths`, it anchors a later local freshness
   assessment;
2. result `observed_at_commit` is the commit the caller says was checked out
   when that verification observation occurred;
3. an artifact reference with `artifact_type=commit` identifies a deliverable
   commit.

No equality among these values is required or inferred. None is resolved by the
server, MCP adapter, or browser.

### 3.10 Advisory effect and forbidden inferences

| Stored fact | Permitted interpretation | Forbidden inference |
| --- | --- | --- |
| `passed` | The caller reported this named check passed. | The objective is complete, correct, safe, or independently verified. |
| `failed` | The caller reported this named check failed. | The work must automatically reopen or every completion claim is false. |
| `inconclusive` | The caller could not report a determinate result. | No problem was observed, so the check passed. |
| `skipped` | The caller observed that this named non-command check was skipped. | The check ran or was unnecessary. |
| artifact reference | The caller recorded this locator as supporting context. | The artifact exists, is immutable, belongs to this project, or is trustworthy. |
| no structured rows | No Phase 11 evidence was recorded for this episode. | Verification failed, verification passed, or no prose evidence exists. |

The backend will accept internally valid failed/inconclusive evidence in a
completion. This preserves the storage/trust boundary. First-party agent
guidance must normally stop before completion when a required check failed or
was inconclusive, unless current user authority explicitly accepts that
limitation and the completion checkpoint says so.

### 3.11 Read surface and stable pagination

Add:

```text
GET /projects/{project_id}/work-items/{work_item_id}/completion-evidence
list_completion_evidence(project_id, work_item_id, limit?, cursor?)
```

The response is:

```text
CompletionEvidencePage
  work_item_id: UUID
  work_version: integer
  lifecycle_status: pending | deferred | done | wont-do | promoted
  is_duplicate: boolean
  canonical_work_item_id: UUID
  current_completion_checkpoint_id: UUID | null
  as_of_completion_event_id: canonical PostgreSQL-bigint decimal string | null
  items: CompletionEvidenceEpisodeRead[]
  total: integer                         # event-backed episodes <= high-water
  structured_completion_total: integer  # those episodes with at least one row
  limit: integer
  next_cursor: string | null
```

An exact-key page with one historical empty episode is:

```json
{
  "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
  "work_version": 9,
  "lifecycle_status": "pending",
  "is_duplicate": false,
  "canonical_work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
  "current_completion_checkpoint_id": null,
  "as_of_completion_event_id": "481",
  "items": [
    {
      "completion_event_id": "481",
      "completion_checkpoint": {
        "id": "82c3c46c-8665-48ad-a0be-13fb196418be",
        "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
        "kind": "completion",
        "source_client": "codex",
        "source_session_id": "session-42",
        "source_model": "gpt-5",
        "repository_branch": "work/example",
        "verified_against": "7ad62e4",
        "tags": ["backend"],
        "migration_origin": null,
        "legacy_record_id": null,
        "created_at": "2026-09-03T18:04:12.123456Z"
      },
      "verification_results": [],
      "artifact_references": []
    }
  ],
  "total": 1,
  "structured_completion_total": 0,
  "limit": 10,
  "next_cursor": null
}
```

`current_completion_checkpoint_id` is the exact checkpoint whose private
generation equals the work's private generation while the exact work is Phase
11-live under section 5.4, canonical, and currently `done`. The migration
assigns that equality to the highest-identity retained legacy completion of a
currently-done work; for new episodes, uniqueness and completion-ID
monotonicity make the match necessarily the highest-identity completion.
Ordinary title/summary/priority edits while done retain the generation and
therefore do not obsolete the episode. The pointer is `null` after reopen,
when done state has no matching completion episode, in every other lifecycle
state, after soft deletion or any retained deletion-event tombstone even if
`deleted_at` was cleared, and for a retained duplicate alias even if that
source's frozen status remains `done`.
`is_duplicate` plus `canonical_work_item_id` makes exact alias history
self-describing without redirecting or blending it. These fields are
read-time conveniences and never enter a receipt.

Each item contains its positive `completion_event_id` as a canonical decimal
string, a compact
`CheckpointPointer` whose kind must be `completion`, and both ordered child
arrays. Every child exposes its server ID, work/checkpoint parent IDs,
position, and `created_at`; the parent IDs must match the wrapper and the child
time must equal the checkpoint/event time. The item does not repeat the
potentially 100-KiB checkpoint prompt or add Phase 10 `affected_paths` to the
compact pointer. A caller that needs the complete checkpoint uses existing
checkpoint history/full recall.

History is driven by the one retained `work_completed` event per completion
checkpoint, never by an unqualified checkpoint scan. Pages are newest episode
first by `work_events.id DESC`. The same-work row lock plus the Phase 11
completion-event insertion guard requires each new ID to be positive and
strictly greater than every prior completion ID for that work, so this bigint
identity is their database-enforced monotonic order even if wall time ties or
moves backward, an identity sequence is reset, or direct SQL uses
`OVERRIDING SYSTEM VALUE`. Migration 0019 adds a partial access index on
`(project_id, work_item_id, id DESC) WHERE event_type='work_completed'`; it
does not alter the event wire model or vocabulary.

Default and maximum limit are 10. The cursor is 1–4,096 ASCII characters with
at most 2,048 decoded bytes. It is unpadded base64url of canonical UTF-8 JSON
(`sort_keys=true`, compact separators) with exactly:

```json
{
  "as_of_completion_event_id": "481",
  "direction": "desc",
  "endpoint": "completion_evidence",
  "last_completion_event_id": "472",
  "project_id": "07fd0090-7be9-4bd3-af85-e5759467d44e",
  "v": 1,
  "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9"
}
```

The endpoint/direction literals, canonical lowercase UUID strings, version,
scope, bounds, and exact key set are revalidated. Both event IDs are canonical
ASCII decimal strings matching `[1-9][0-9]{0,18}`, parse within PostgreSQL's
signed-bigint range but do not exceed the reserved completion-event maximum
`9223372036854775806`, name retained same-work completion events, and satisfy
`last_completion_event_id <=
as_of_completion_event_id`. They are strings on every new REST, MCP,
TypeScript, and cursor surface so JavaScript never rounds a database bigint.
Cursor contents grant no authority and are not a signature. Reuse under
another scope or malformed/nonexistent event identity returns existing `422
invalid_cursor`. There is no offset mode, outcome filter, artifact filter, or
free-text search in V1.

The first page sets `as_of_completion_event_id` to the decimal spelling of the
highest visible
completion event for the exact work, or null when none exists. Continuations
retain that high-water identity and select/count only events at or below it,
so an exhausted traversal made with the exact unchanged chain of
server-issued `next_cursor` values is complete **as of that identity** even
while newer completions commit. The cursor is deliberately opaque rather than
integrity-protected: a caller that decodes, manufactures, or modifies a
syntactically valid cursor can select a different historical subset and must
not claim completeness from that traversal. Total and structured total remain
stable across an unchanged server-issued chain. Page selection occurs before
child aggregation. Page-level work version/status/canonical/current facts
describe the live snapshot of each request and can advance beyond the cursor's
historical high-water mark.

Read assembly validates every selected private generation before projecting it
away. A negative checkpoint generation must be the exact negated legacy event
ID. A nonnegative checkpoint generation cannot exceed the work's current
generation and has its exact preceding positive reopen witness when greater
than zero. If it is below the work generation, it must also precede the exact
successor reopen at generation plus one by typed work version. For a
Phase-11-live canonical done work, exactly one sealed checkpoint/event has
generation equal to the work generation and supplies the current pointer; no
other state exposes a current pointer. Any missing successor, late completion
version, or other mismatch fails the whole page content-free instead of
choosing a merely newest-looking checkpoint.

All metadata, page rows, and children within one response use one explicit
read-only repeatable-read transaction. To claim it has audited the current
history, a long-lived consumer records tuple
`T0=(as-of decimal event identity, work version/status, alias/canonical projection, current
checkpoint)` from its first page, exhausts that traversal, obtains a new first
page and tuple `T1`, and claims current completeness only if `T1 == T0`. If
they differ, that new first page becomes the start of a replacement traversal;
repeat the exhaust-and-compare cycle. Under continuous change it reports that
a stable current audit could not be established rather than claiming
completeness.

The aggregate name `list_completion_evidence` intentionally differs from the
roadmap's tentative `list_verification_results`: it returns result and artifact
families, honest empty completion episodes, and lifecycle identity in one
bounded call.

### 3.12 Correction and late evidence

Evidence rows cannot be corrected in place. If the completion claim itself is
materially wrong, the authorized workflow is:

1. append a context checkpoint identifying the erroneous evidence without
   deleting it;
2. move the work from `done` to `pending` using the current version and truthful
   actor provenance;
3. perform or obtain the current checks; and
4. complete again with a new completion checkpoint and new evidence.

The old episode remains visible and the current pointer moves only after the new
completion. Phase 11 does not treat a late CI result or newly available pull
request as permission to mutate an already frozen completion. Keep the work
pending until required asynchronous evidence is available, or record the late
fact in ordinary checkpoint context. A first-class post-completion evidence
append, if real usage requires one, must be designed later without a hidden
compatibility path.

---

## 4. Requirement identifiers

| ID | Requirement |
| --- | --- |
| SCE-001 | Store typed verification results and artifact references against one exact completion checkpoint. |
| SCE-002 | For non-empty evidence, create checkpoint, evidence, done state, completion event, and required receipt atomically; preserve the historical unkeyed path only for absent evidence. |
| SCE-003 | Keep structured evidence optional and represent absence honestly. |
| SCE-004 | Preserve all historical completion request fingerprints and response bodies byte for byte. |
| SCE-005 | Use one sparse outer evidence field without a legacy/current model union. |
| SCE-006 | Enforce strict verification type/outcome/command/exit-code matrices. |
| SCE-007 | Enforce strict artifact kinds and kind-specific reference grammars. |
| SCE-008 | Enforce count, character, byte, aggregate, ordering, and duplicate bounds at every layer. |
| SCE-009 | Distinguish record time, observed time, checkpoint baseline, observed commit, and commit artifact. |
| SCE-010 | Make every result/reference immutable and forbid post-completion insertion. |
| SCE-011 | Preserve each reopen/recompletion episode and never copy evidence forward. |
| SCE-012 | Return completion episodes with and without structured evidence through a bounded complete history. |
| SCE-013 | Bind cursors to project/work/direction and provide deterministic traversal. |
| SCE-014 | Keep evidence out of normal recall, compact/search/hierarchy/readiness/gate/duplicate/cache surfaces. |
| SCE-015 | Keep existing `work_completed` facts and their public wire shape unchanged and add no verification event. |
| SCE-016 | Keep evidence source-owned across authoritative duplicate merges. |
| SCE-017 | Preserve replay before current lifecycle, alias, visibility, and version checks. |
| SCE-018 | Treat all evidence as caller-reported, untrusted, and non-authoritative. |
| SCE-019 | Never execute commands or fetch/resolve artifact references in any first-party layer. |
| SCE-020 | Keep Phase 10 freshness evidence ephemeral and structurally separate. |
| SCE-021 | Reject request-known operation, bearer, and lease secrets anywhere in nested evidence. |
| SCE-022 | Render untrusted evidence inertly, accessibly, and without automatic network requests. |
| SCE-023 | Preserve all Phase 10 database content and create no inferred/backfilled evidence. |
| SCE-024 | Refuse any downgrade that would discard evidence or evidence-bearing receipts. |
| SCE-025 | Ship exact 0.6.0/0.10.0/0019 and 28/11/13/11 inventory boundaries. |
| SCE-026 | Prove schema/ORM/OpenAPI/MCP/frontend validation and strict response parity. |
| SCE-027 | Prove completion, reopen, merge, receipt, and direct-SQL concurrency behavior. |
| SCE-028 | Give agents a read tool and humans a dedicated evidence view without adding a write tool. |
| SCE-029 | Keep logs, metrics, live-sync frames, search indexes, and routine audits content-free. |
| SCE-030 | Rebase onto the actually merged Phase 10 implementation before writing migration or product code. |
| SCE-031 | Make every completion checkpoint, including an empty episode, correspond one-to-one with its retained monotonic `work_completed` event. |
| SCE-032 | Keep every generated compact request/fingerprint/response/receipt representation at or below 896 KiB, raw deployed REST/browser ingress at or below 1 MiB, and every valid ten-episode REST history document at or below 3 MiB under worst-case JSON escaping. |
| SCE-033 | Require `client_operation_id` structurally whenever normalized evidence is non-empty in runtime validation and executable OpenAPI 3.1 schema. |
| SCE-034 | Preserve a stable high-water event identity in every continuation cursor and describe only an unchanged server-issued cursor chain as complete as of that identity. |
| SCE-035 | Enforce one completion per database-owned lifecycle generation and a unique, immutable, bidirectional reopen-event binding for every post-cutover generation increment. |
| SCE-036 | Bound `observed_at` lexically before parsing, canonically preserve its instant, and enforce the compatible finite microsecond database range. |
| SCE-037 | Block supported `TRUNCATE` of evidence, lifecycle-event, and durable-receipt history in addition to row mutation. |
| SCE-038 | Require identity coding for evidence history at every first-party hop, reject non-identity `Content-Encoding` before body consumption, and enforce the 3 MiB identity-body ceiling before UTF-8 or JSON parsing. |
| SCE-039 | Require every new completion-event ID to be positive, below the terminal bigint value, and strictly same-work monotonic; require its typed work version to advance every prior live completion and its exact positive-generation reopen despite identity or work-version resets. |
| SCE-040 | Measure and cap the complete SDK-emitted MCP JSON-RPC evidence result, including both `content` and `structuredContent`, at 12 MiB across real Streamable HTTP and stdio transports. |
| SCE-041 | Revalidate a fully sealed current episode in a regular non-deferrable departure trigger, so selective `SET CONSTRAINTS IMMEDIATE` cannot strand invalid history while multiple genuine cycles remain legal. |
| SCE-042 | Bound every inbound MCP HTTP entity and stdio record before SDK parsing, require one object and a bounded request ID, return only a bounded non-echoing HTTP rejection, make a rejected stdio record terminal with no response under the SDK-only writer, and prove the complete maximum-ID response remains at most 12 MiB. |
| SCE-043 | Forbid a pending generation with an outstanding completion checkpoint from leaving except through its exact live-canonical, version-incrementing pending-to-done seal, bind the event to that captured transition version, require children before it, and reject every non-pending-to-done jump, unsealed delete/clear or alias, or late prior-generation completion event. |

### 4.1 Requirement traceability

This matrix identifies each requirement's primary design and executable proof.
It is a navigation index, not a substitute for the full invariants. Any
implementation-time requirement change must update its design, proof, gate,
Definition of Done, and this row together.

| Requirement | Primary design | Primary executable proof | Hard closure |
| --- | --- | --- | --- |
| SCE-001–SCE-005 | Sections 3.2–3.4 and 6.1–6.3 | Sections 11.1, 11.4, and 11.5 | Gates 1 and 3; section 18 contract/atomicity |
| SCE-006–SCE-009 | Sections 3.5–3.9 and 6.1 | Sections 11.1 and 11.4 | Gates 1 and 3; section 18 contract |
| SCE-010–SCE-011 | Sections 3.3, 3.12, and 5.5–5.6 | Sections 11.3, 11.5, and 11.6 | Gate 2; section 18 contract/atomicity |
| SCE-012–SCE-013 | Sections 3.11 and 6.4 | Sections 11.4 and 11.7 | Gate 4; section 18 read surfaces |
| SCE-014–SCE-016 | Sections 2.5, 3.10, 6.6–6.7, and 7.4 | Sections 11.4, 11.7, 11.8, and 11.10 | Gates 3–5; section 18 read/security |
| SCE-017 | Sections 2.3 and 6.2–6.3 | Sections 11.5 and 11.6 | Gate 3; section 18 atomicity |
| SCE-018–SCE-020 | Sections 2.6, 3.10, 7.3, and 9 | Sections 11.8–11.11 | Gate 5; section 18 read/security |
| SCE-021–SCE-022 | Sections 6.5, 8.4, and 13 | Sections 11.1, 11.4, 11.8, and 11.9 | Gates 3 and 5; section 18 security |
| SCE-023–SCE-024 | Sections 5.1 and 5.7–5.8 | Section 11.2 | Gate 2; section 18 contract/operations |
| SCE-025 | Sections 3.1, 10.7, and 14 | Sections 11.8–11.12 | Gate 6; section 18 release |
| SCE-026 | Sections 6.1, 7.2, and 8 | Sections 11.1 and 11.4–11.9 | Gates 1 and 5; section 18 read/release |
| SCE-027 | Sections 5.5 and 6.7 | Sections 11.3, 11.5, and 11.6 | Gates 2 and 3; section 18 atomicity |
| SCE-028 | Sections 7.1, 8.3, and 9 | Sections 11.8–11.11 | Gate 5; section 18 read surfaces |
| SCE-029 | Sections 6.6, 7.4, and 13.4 | Sections 11.4, 11.8, and 11.9 | Gate 5; section 18 security |
| SCE-030 | Sections 2.1 and 10.1 | Gate-0 baseline commands and catalog assertions | Gate 0; section 18 release |
| SCE-031 | Sections 3.3, 5.1, and 5.5 | Sections 11.2 and 11.3 | Gate 2; section 18 contract |
| SCE-032 | Sections 3.8 and 13.5 | Sections 11.1, 11.4, and 11.7–11.9 | Gates 1, 4, and 5; section 18 read/security |
| SCE-033 | Sections 3.4 and 6.1 | Sections 11.1, 11.4, and 11.5 | Gates 1 and 3; section 18 atomicity/release |
| SCE-034 | Sections 3.11 and 6.4 | Sections 11.4 and 11.7 | Gate 4; section 18 read surfaces |
| SCE-035 | Sections 3.3 and 5.5 | Sections 11.2, 11.3, and 11.6 | Gate 2; section 18 contract |
| SCE-036 | Sections 3.9 and 6.1 | Sections 11.1, 11.3, and 11.4 | Gates 1 and 2; section 18 contract |
| SCE-037 | Sections 5.6 and 5.8 | Sections 11.2 and 11.3 | Gate 2; section 18 contract/operations |
| SCE-038 | Sections 3.8, 7.2, and 8.5 | Sections 11.7–11.9 and 11.11 | Gates 4 and 5; section 18 read/security |
| SCE-039 | Sections 3.3, 5.1, and 5.5 | Sections 11.2, 11.3, and 11.7 | Gate 2; section 18 contract/read |
| SCE-040 | Sections 3.8 and 7.2 | Sections 11.8 and 11.11 | Gate 5; section 18 read surfaces |
| SCE-041 | Sections 3.3 and 5.5 | Sections 11.3 and 11.6 | Gate 2; section 18 contract |
| SCE-042 | Sections 3.8 and 7.2 | Sections 11.8 and 11.11 | Gate 5; section 18 read surfaces |
| SCE-043 | Sections 3.3 and 5.5 | Sections 11.3, 11.6, and 11.11 | Gate 2; section 18 contract/atomicity |

---

## 5. Persistence and database invariants

### 5.1 Migration `0019_structured_completion_evidence`

Create
`backend/alembic/versions/0019_structured_completion_evidence.py` with:

```python
revision = "0019_structured_completion_evidence"
down_revision = "0018_repository_freshness"
```

The migration creates two initially empty authoritative evidence tables, adds
private generation columns to `work_items`, `checkpoints`, and `work_events`,
creates partial completion/reopen-event indexes, and installs Phase-11-specific
validation/guard functions. It does not alter any pre-existing column value,
the event vocabulary, a Phase 10 function, or a public
checkpoint/event/read/receipt shape.

Before creating those objects, force every queued deferred constraint, require
the deployment writer/direct-DML quiescence from section 12, and acquire
`ACCESS EXCLUSIVE` locks in the exact order `client_operations`, `work_items`,
`checkpoints`, then `work_events`. Under those locks, run a read-only preflight that rejects either
direction of checkpoint/event disagreement: every retained
`kind=completion` checkpoint must have exactly one same-work
`event_type=work_completed` row, and every such event must reference exactly
one same-work completion checkpoint. Existing
`uq_work_events_checkpoint_fact` supplies the uniqueness half; preflight makes
legacy omissions or corruption explicit rather than silently exposing a fake
empty episode. Every retained work whose status is `done`, including a
soft-deleted work or duplicate alias, must have at least one such completion
event; preflight chooses its highest-ID event as the exact legacy current
episode or refuses before DDL. It also requires every retained completion and
reopen event ID
to be in `1..9223372036854775806`; the terminal bigint value is reserved as an
explicit fail-closed exhaustion sentinel, not as a promise of indefinite
headroom. Reaching the reserved completion/reopen ceiling requires a reviewed
fix-forward identity widening or rekeying before another lifecycle event; an
operator must never reseed below retained history to manufacture space. For
each work, every live completion event
must have a unique, strictly increasing typed `metadata.work_version` as its ID
increases, and every live completion ID must be greater than every same-work
backfilled completion ID. Backfilled completion events have `{}` metadata and
are excluded from the work-version comparison. For every retained work whose
status is `done` and whose selected highest-ID current completion is live,
that completion's typed `work_version` must be no greater than the retained
work's version; a metadata-free backfill current event is excluded from this
additional comparison. This is the only retained logical-order preflight
available; it uses no timestamp, rewrites no ID, and refuses an
override/reset-corrupted history rather than guessing. Only after that locked
preflight succeeds, add:

```text
work_items.completion_generation BIGINT NOT NULL DEFAULT 0
checkpoints.completion_generation BIGINT NULL
work_events.reopen_generation BIGINT NULL
```

`work_items.completion_generation` is database-managed. Set each existing
completion checkpoint's generation to the negative of its exact positive
`work_completed.id`; leave every non-completion checkpoint null. For each
existing `done` work, set the work generation to the negative generation of
its highest-ID retained completion event/checkpoint. This applies even when
the row is soft-deleted or a duplicate alias so its private state remains
internally exact; public current selection still requires a live canonical
work. Set every other existing work generation to zero, and force every new
work to generation zero. The negative namespace gives each legacy episode and
the exact still-current legacy episode a deterministic identity without
inferring which historical reopen preceded which completion. A runtime
completion checkpoint is always nonnegative. The first later transition from
any non-pending state to `pending` maps a nonpositive work generation to
positive generation one; later such transitions increment it.

Set each retained `work_reopened` event's new `reopen_generation` to the
negative of its exact positive event ID and leave the new column null on every
other event. Negative reopen bindings identify legacy events only; they do not
claim which historical completion, if any, preceded them. Runtime bindings are
strictly positive.

The historical backfills change only newly added columns. Under the four
exclusive locks, first verify the normalized definitions of
`duplicate_alias_work_mutation_guard`, `checkpoints_immutable`,
`duplicate_alias_checkpoint_guard`, and `events_immutable`; transactionally
disable only those four exact triggers;
perform and count the three scoped checkpoint/work/event backfills; re-enable
them immediately; and
prove their definitions and enabled state are unchanged. This follows the
repository's controlled migration rewrite pattern; no runtime, fixture, or
operator path may disable them.

Add `ck_work_items_completion_generation_range` to exclude values below
`-9223372036854775806`, plus
`ck_checkpoints_completion_generation_kind` for checkpoint generation being
non-null exactly when `kind='completion'`, and
`uq_checkpoints_completion_generation` on `(work_item_id,
completion_generation) WHERE kind='completion'`. A negative work generation is
legal only when migration or an eligible evidence-free re-upgrade assigned it
to that work's exact retained current legacy completion. Inserts force zero;
ordinary updates retain the value; a departure to pending replaces any
nonpositive value with one; and every caller-supplied assignment is rejected.
Thus runtime checkpoint generations never collide with the negative historical
namespace, and pending runtime work cannot retain a negative generation.

Add `ck_work_events_reopen_generation_kind` so
`event_type='work_reopened'` requires a non-null, nonzero
`reopen_generation` and every other event requires null. Add partial unique
index `uq_work_events_reopen_generation` on `(work_item_id,
reopen_generation) WHERE event_type='work_reopened'`. Historical bindings are
negative exact event IDs; the insertion guard creates positive runtime
bindings. The event row's primary key supplies the reverse cardinality: one
event row has exactly one scalar binding and cannot witness another
generation.

While writers and direct event DML are quiescent and `work_events` remains
locked, advance its owned identity sequence to at least the retained global
maximum ID and reject exhaustion at PostgreSQL's bigint maximum. This
availability step prevents a historically advanced explicit ID from causing
ordinary inserts to fail until the sequence catches up. Correctness never
depends on sequence state: the runtime completion-event guard below still
fails closed after any later reset or explicit override.

Use schema-qualified function bodies, fixed `search_path`, deterministic names,
and explicit lock ordering consistent with prior migrations. Keep all three
new generation/binding columns out of every public serializer, receipt, event
schema, MCP model, and browser type: they are reconstructible database
enforcement metadata, not evidence or API compatibility state. Add
`ix_work_events_completion_evidence_history` on
`(project_id, work_item_id, id DESC) WHERE event_type = 'work_completed'` for
the event-identity traversal and locked preceding-ID lookup in sections 3.11
and 5.5. Add
`ix_work_events_live_completion_version_order` on
`(work_item_id, id DESC) WHERE event_type = 'work_completed' AND
origin = 'live'` so the locked event guard can fetch the latest prior typed
completion version in one bounded predecessor lookup.

### 5.2 `verification_results`

Conceptual columns:

```text
id UUID PRIMARY KEY
project_id UUID NOT NULL
work_item_id UUID NOT NULL
completion_checkpoint_id UUID NOT NULL
position SMALLINT NOT NULL
verification_type VARCHAR(20) NOT NULL
name VARCHAR(200) NOT NULL
outcome VARCHAR(20) NOT NULL
summary TEXT NOT NULL
command TEXT NULL
exit_code INTEGER NULL
observed_at TIMESTAMPTZ(6) NULL
observed_at_commit VARCHAR(64) NULL
created_at TIMESTAMPTZ NOT NULL
```

Constraints enforce:

- `verification_type IN ('command', 'observation')`;
- `outcome IN ('passed', 'failed', 'inconclusive', 'skipped')`;
- the complete matrix from section 3.6;
- nonblank/no-NUL character and UTF-8 byte limits;
- nullable lowercase commit grammar;
- named `ck_verification_results_observed_at_range`:
  `observed_at IS NULL OR (isfinite(observed_at) AND observed_at >=
  TIMESTAMPTZ '0001-01-01 00:00:00+00' AND observed_at < TIMESTAMPTZ
  '10000-01-01 00:00:00+00')`, with microsecond storage precision matching
  section 3.9;
- `position BETWEEN 0 AND 19`;
- unique `(work_item_id, completion_checkpoint_id, position)`;
- unique `(work_item_id, id)` for scope-safe references and audits.

Indexes:

- `(work_item_id, completion_checkpoint_id, position)` supports ordered
  hydration and is unique;
- `(completion_checkpoint_id, id)` gives the checkpoint-global operational
  audit a bounded representative/owner probe even when unrelated projects
  contain large evidence inventories;
- no outcome, command, summary, commit, full-text, GIN, or semantic index.

### 5.3 `artifact_references`

Conceptual columns:

```text
id UUID PRIMARY KEY
project_id UUID NOT NULL
work_item_id UUID NOT NULL
completion_checkpoint_id UUID NOT NULL
position SMALLINT NOT NULL
artifact_type VARCHAR(32) NOT NULL
label VARCHAR(200) NOT NULL
reference TEXT NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

Constraints enforce the exact V1 artifact vocabulary, label/reference bounds,
type-specific grammar, `position BETWEEN 0 AND 19`, and:

- unique `(work_item_id, completion_checkpoint_id, position)`;
- unique `(work_item_id, id)`;
- unique
  `(work_item_id, completion_checkpoint_id, artifact_type, reference)` using
  deterministic `C` comparison.

Add `(completion_checkpoint_id, id)` for the same bounded checkpoint-global
audit probe. There is no URL-domain, branch, path, or content index.

### 5.4 Ownership and foreign keys

Both tables have:

- composite `(project_id, work_item_id)` foreign key to the work item;
- composite `(work_item_id, completion_checkpoint_id)` foreign key to the
  existing unique checkpoint identity;
- `ON DELETE RESTRICT` for authoritative history;
- an insert guard that locks the work row, rejects deleted work and duplicate
  aliases, requires a same-work `kind=completion` checkpoint, and requires the
  supplied `created_at` to equal that checkpoint's timestamp.

For every Phase 11 checkpoint, child, pending-to-done, and completion-event
seal, **live** means both `work_items.deleted_at IS NULL` and no retained
same-work `work_deleted` event. The delivered partial unique deletion-event
index makes the second predicate bounded. This intentionally treats a row
whose timestamp was cleared by direct SQL after an immutable deletion event as
deleted history, rather than allowing a fabricated resurrection to complete.

No foreign key points to a project URL, repository, lease, event, receipt, or
external artifact.

### 5.5 Atomic-only insertion and exact completion linkage

The database owns lifecycle generations; application and direct-SQL callers do
not set them. Install `completion_generation_guard` (`BEFORE INSERT OR UPDATE`
on `work_items`) with these rules:

1. every inserted work row is forced to generation `0`;
2. an update that supplies a generation different from `OLD` is rejected;
3. when `OLD.status <> 'pending'` and `NEW.status = 'pending'`, require
   `NEW.version = OLD.version + 1`, reject bigint overflow, and set generation
   to `1` when the old generation is nonpositive, otherwise to
   `OLD.completion_generation + 1`;
4. every other update retains the exact old generation; and
5. a negative retained value is accepted only on the migrated, still-done
   legacy episode described in section 5.1. It can survive ordinary
   title/summary/priority edits or soft deletion while status stays done, but
   it can never reach pending or a runtime completion checkpoint.

Define one side-effect-free, schema-qualified sealed-episode validator reused
by the triggers, reads, and audit. For a supplied work/generation it proves the
exact same-project completion checkpoint/event pair, the event-ID/generation
mapping, required live/backfill origin rules, checkpoint timestamp, child
positions/counts/byte bounds, duplicate-artifact rule, and any positive reopen
witness. For every nonnegative episode, its live completion version must
strictly advance the latest earlier live completion by event ID and, when
generation is positive, its exact same-generation reopen version. For a
nonnegative historical episode at generation `g` below the work's generation,
the validator additionally requires the exact successor `work_reopened` event
bound to `g + 1`, requires that event's typed old status to be `done`, and
requires the completion event's typed `work_version` to be strictly lower than
the successor reopen's typed `work_version`. For the current done episode, the
completion version must be at most the retained work version, so a
status-preserving decrement below the sealed event cannot commit. It permits an
empty aggregate but never a partial, eventless, late-sealed, or out-of-order
one.

Install `completion_state_episode_guard` as a deferrable, initially deferred
`AFTER INSERT OR UPDATE` constraint trigger on `work_items` whenever
`NEW.status='done'`. It validates the captured `NEW.completion_generation`
with that sealed-episode function. For the trigger instance capturing
`OLD.status='pending' AND NEW.status='done'`, it additionally requires the
exact current-generation completion event's typed `work_version` to equal
that captured `NEW.version`, not merely the work's later value at commit.
This binds the event to the checked one-step completion transition and rejects
a status-preserving bump inserted between the transition and event. It
intentionally also queues after an ordinary edit to an already done work:
later version/time changes after a sealed event do not obsolete the episode,
while the exact generation and immutable facts still must exist. A direct
insert to done therefore cannot commit.

Install `completion_pending_exit_guard` as a regular, non-constraint
`BEFORE UPDATE OF status` trigger. It inspects the locked work's exact current
generation before any lifecycle exit:

1. for `OLD.status='pending' AND NEW.status='done'`, require exactly one
   same-project/work completion checkpoint with `migration_origin IS NULL` and
   a nonnegative generation equal to `OLD.completion_generation`, and require
   that checkpoint to have no retained `work_completed` event yet; also
   require both the `OLD` and `NEW` work images to be nondeleted and canonical
   under the delivered duplicate predicate, require no retained
   `work_deleted` event, and
   require the checked integer transition
   `NEW.version = OLD.version + 1`, rejecting an unchanged, decremented,
   jumped, or overflowing version;
2. for `OLD.status='pending'` and `NEW.status` equal to `deferred`, `wont-do`,
   or `promoted`, require no completion checkpoint at
   `OLD.completion_generation`; and
3. reject `NEW.status='done'` from `deferred`, `wont-do`, or `promoted`.

The first rule freezes the delivered completion order—checkpoint, optional
children, pending-to-done update, then event—while the deferred done-state and
checkpoint guards require the event by commit. The second rule makes an
eventless completion checkpoint a synchronous fence on its pending generation
instead of letting that checkpoint become apparently historical. The third
rule prevents a held/terminal detour from returning to done in the same
generation. Ordinary terminal/defer flows with no completion checkpoint remain
legal, as do non-pending-to-pending reopens through the generation/reopen
guards.

Install `completion_unsealed_deletion_guard` as a regular, non-constraint
`BEFORE UPDATE OF deleted_at` trigger. When a live row would change from
`deleted_at IS NULL` to a non-null value, reject if its exact current
nonnegative generation has a non-migration completion checkpoint without a
retained `work_completed` event. This covers both pending work and the
same-transaction done-before-event interval. It does not block soft deletion
after a completion is fully sealed or when no current checkpoint exists.
Deletion before checkpoint creation remains blocked by the checkpoint insert
guard, while a retained `work_deleted` event remains an independent liveness
fence even if direct SQL later clears the timestamp.

Install `completion_episode_departure_guard` as a regular, non-constraint
`BEFORE UPDATE OF status` trigger with a name that sorts before
`completion_generation_guard`. For every `OLD.status='done' AND
NEW.status<>'done'` transition it:

1. requires `NEW.status='pending'`, matching the delivered lifecycle;
2. synchronously validates the fully sealed episode at
   `OLD.completion_generation`, including its complete evidence aggregate; and
3. only then allows `completion_generation_guard` to assign the next positive
   generation.

Because it is a regular immediate trigger and fires on each departure, neither
`SET CONSTRAINTS completion_checkpoint_episode_guard IMMEDIATE` nor
`SET CONSTRAINTS ALL IMMEDIATE`, followed by `SET CONSTRAINTS ... DEFERRED`,
can discharge the only check and strand an invalid earlier episode. Ordinary
done-item metadata edits remain legal because they do not depart or alter the
private generation.

Install `completion_generation_reopen_guard` as a deferrable, initially
deferred `AFTER UPDATE` constraint trigger for every transition in rule 3. At
forced-check time/commit it requires exactly one retained, same-project/work,
`origin='live'`, `event_type='work_reopened'` event whose positive
`reopen_generation` equals that transition's `NEW.completion_generation`,
whose `created_at` equals `NEW.updated_at`, and whose typed metadata has the
exact old status, `to_status='pending'`, matching
`changes.status.before/after`, and `work_version=NEW.version`. It never matches
a witness by mutable-looking shape alone. Missing, duplicate, wrong-generation,
wrong-version, wrong-time, or wrong-status witnesses abort.

Install `mnemonic_guard_completion_lifecycle_event_insert` through the
alphabetically earlier `completion_lifecycle_event_insert_guard` as a
`BEFORE INSERT` trigger on `work_events`, so it runs before the delivered
`work_event_source_fact_guard`:

1. every caller must leave `reopen_generation` null;
2. for `work_reopened`, lock the exact same-project work row `FOR UPDATE`,
   require live canonical nondeleted work already in the post-transition
   `pending` state with positive generation, require an event ID in
   `1..9223372036854775806`, exact `created_at=work.updated_at`, and exact typed
   metadata `work_version=work.version`, then assign the current work
   generation;
3. for every non-reopen event, retain null; and
4. for `work_completed`, under the same work lock require the referenced
   same-project/work completion checkpoint to have
   `migration_origin IS NULL` and a nonnegative generation exactly equal to
   the work's current generation, require the work to remain live, canonical,
   and `done`, require an ID in `1..9223372036854775806` strictly greater than
   the highest retained same-work completion-event ID, and require the event's
   typed `work_version` to be strictly greater than the latest-by-ID prior
   same-work `origin='live'` completion-event version. Migration preflight and
   induction make that a strict advance over all prior live completions. When
   the checkpoint generation is positive, also require that version to be
   strictly greater than the typed `work_version` of its exact same-generation
   `work_reopened` witness.

Identity defaults are materialized before `BEFORE INSERT`, so rule 4 sees both
an ordinary identity value and a caller value supplied with `OVERRIDING SYSTEM
VALUE`. The same-work lock serializes concurrent direct inserts, and
`ix_work_events_completion_evidence_history` and
`ix_work_events_live_completion_version_order` make the preceding-ID and
version lookups bounded. The delivered source-fact guard continues to validate
the completion checkpoint, final state, version, actor, and metadata.

Install `completion_reopen_event_episode_guard` as a second deferrable,
initially deferred constraint trigger, this time `AFTER INSERT` on each new
`work_reopened` event. At forced-check time/commit it requires the exact
positive binding and event to remain unique and immutable, the same project/
work to remain retained, and the work's generation to be greater than or equal
to the event's binding. Equality is deliberately not required because a direct
transaction may contain multiple genuine non-pending-to-pending transitions,
each separated by another non-pending status and each with its own bound event.
Those transitions may also separate multiple genuine completion episodes in
one transaction, provided every departing episode first passes the regular
sealed-episode guard.

The two deferred directions close different bypasses. The work-transition
guard requires its exact keyed event. The event-side guard fires even for a
standalone event insertion. Its immediate binder can assign only the current
positive generation while the locked work is in the exact post-transition
pending/version/time state; generation can advance only through the guarded
work transition; and the unique immutable `(work_item_id, reopen_generation)`
binding can never be reused. A future event inserted before its transition
therefore sees generation zero, a nonpending state, or an already occupied
generation and fails. A later standalone duplicate hits the partial unique
index. Resetting status, version, or timestamps cannot make an old event
witness a strictly larger generation. The `work_events` row and statement
guards make the committed scalar binding permanent within the supported DML/
TRUNCATE boundary.

An earlier completion generation may remain below the work's final generation
at commit, but only after its `done -> pending` departure synchronously
revalidated the exact checkpoint, event, and complete child aggregate. This
allows an intentionally constructed direct transaction to retain multiple
truthful cycles without making transaction boundaries part of public history.
It does not allow two checkpoints in one generation, an eventless episode, a
departure from an incomplete episode, or a selectively discharged invalid
episode.

Every first-party non-pending-to-pending path must explicitly flush the work
update before it stages and flushes `work_reopened`. This is part of the
lifecycle contract, not an ORM-order assumption: the event binder must observe
the post-transition status, generation, version, and timestamp. SQL trace tests
freeze this update-before-event order for every reopen entry point.

The Phase-11 completion-checkpoint guard applies to every newly inserted
`kind=completion` checkpoint, including a completion with zero evidence:

1. `completion_checkpoint_insert_guard` runs `BEFORE INSERT`, rejects a
   caller-supplied generation, locks the referenced work row, requires it to be
   live, canonical, and `pending`, rejects migration provenance on a new
   checkpoint, and assigns the locked work generation; and
2. the unique `(work_item_id, completion_generation)` index immediately rejects
   a second completion in that generation, including an outstanding eventless
   checkpoint; and
3. `completion_checkpoint_episode_guard`, a deferrable initially deferred
   constraint trigger, requires at forced-check time/commit that the same work
   generation is at least the checkpoint's generation, the checkpoint remains
   a completion, and exactly one retained same-project/work live
   `work_completed` event references it with the checkpoint timestamp. When
   the generations are equal, the work must still be `done`; when the work
   generation is greater, the regular departure guard has already sealed this
   historical episode.

For a new checkpoint in generation `>0`, the deferred guard also requires a
retained live `work_reopened` event whose positive `reopen_generation` equals
the checkpoint generation. It does not infer the witness from timestamp or
event-ID ordering. Generation `0` is the only initial/cutover generation and
requires no earlier reopen. New checkpoints can never use a negative
generation; that namespace belongs solely to the migration's immutable
historical mapping.

Together these rules reject these direct-SQL bypasses: an already-
done work cannot be toggled through pending and completed in one transaction
without retaining the exact reopen witness, and two completion checkpoints
cannot share the pending generation before either event exists. If a checkpoint
is inserted, its pending generation cannot exit to a held/other terminal state,
and no prior-generation completion event can be attached after a later reopen.
The only path through done and onward to another generation first causes the
regular departure guard to require that checkpoint's exact event and complete
valid aggregate. The work-row lock is held through commit, so concurrent
lifecycle, completion, and merge transactions serialize.

A separate versioned child guard validates evidence as one aggregate. During
each child insertion it takes/reuses the same work-row lock, enforces section
5.4, requires the work to remain canonical, live, and `pending`, requires the
parent to be a non-migration completion checkpoint whose nonnegative
generation exactly equals the work's current generation, and requires that no
`work_completed` event yet exists for the parent checkpoint. Thus neither a
post-status/pre-event insert nor an append to any prior episode is possible.
At deferred commit it requires:

- the checkpoint-level completion invariant above;
- all evidence rows have the checkpoint timestamp;
- each family has positions exactly `0..count-1`;
- combined child count is `1..20` when either family is present;
- combined caller-text bytes, calculated exactly as section 3.8, are at most
  32,768;
- every child belongs to the same project/work/checkpoint.

The completion service adds and explicitly flushes every child row, then
explicitly flushes the guarded pending-to-done work update, before it adds or
flushes the existing completion event; mere ORM object materialization is not
an ordering guarantee. The deferred checks then prove the final aggregate. A
later transaction sees the completion event and cannot add another child.
Reads and audits enumerate only event-backed checkpoints and fail closed if
any event/checkpoint/generation/binding/ID-order relation is missing or
inconsistent.

This is a database invariant, not merely the absence of a public route.

### 5.6 Immutability

`BEFORE UPDATE OR DELETE FOR EACH ROW` and
`BEFORE TRUNCATE FOR EACH STATEMENT` triggers on both evidence tables raise
SQLSTATE `55000` for every attempt. There is no exception for spelling fixes,
redaction, URL rot, changed branch tips, failed artifacts, or the deployed
application role.

Freeze the trigger names as `verification_results_immutable`,
`verification_results_truncate_guard`, `artifact_references_immutable`, and
`artifact_references_truncate_guard`. The shared Phase-11 rejection functions
must still check `TG_TABLE_SCHEMA`, `TG_TABLE_NAME`, `TG_OP`, and expected
argument count so attaching either function to an unintended trigger fails
closed rather than broadening a trusted path.

Phase 11 also installs named, Phase-11-owned `BEFORE TRUNCATE FOR EACH
STATEMENT` guards on the existing `work_events` and `client_operations`
tables. Row immutability alone does not intercept `TRUNCATE`: losing
`work_events` would make evidence-backed episodes disappear from the
authoritative read, and losing `client_operations` would destroy permanent
unknown-outcome replay. These two guards reject even an empty-table truncate,
are included in catalog hashes/direct-SQL tests, and are removed exactly by an
eligible downgrade. Test isolation must use transaction rollback or disposable
schema/database recreation; it must not teach ordinary fixtures to disable the
guards.

Name those existing-table triggers `work_events_phase11_truncate_guard` and
`client_operations_phase11_truncate_guard`; do not replace or rename the
delivered Phase 10 row-mutation triggers.

A correction is a new checkpoint/recompletion episode. These triggers protect
the supported application and direct-SQL DML/TRUNCATE paths. A database owner
can deliberately drop/disable schema objects, so owner-level DDL is outside
the application immutability claim, is prohibited by operations guidance, and
is detected by catalog audit. Within supported operation, whole-database
restore is the recovery path for removing an erroneous retained fact, with an
explicit whole-database data-loss boundary.

### 5.7 Preservation fixture and evidence-empty migration

Populate migration 0018 with:

- every checkpoint kind and provenance combination, including non-empty Phase
  10 `affected_paths`;
- multiple completed/reopened/completed work items;
- leases, blockers, parent/child/discovery/related relationships;
- unresolved and resolved gates;
- authoritative duplicate marks, merges, source aliases, and destination
  roots;
- semantic embeddings and duplicate suggestion state;
- every work event type and backfill origin;
- all 13 receipt kinds, including historical completion response bodies with
  no `completion_evidence` property.

After upgrade:

- both new evidence tables contain zero rows;
- every historical completion checkpoint has generation
  `-work_completed.id`, every other checkpoint generation is null, each
  existing `done` work has the negative generation of its exact highest-ID
  completion, and every other existing work has generation zero, with no
  historical reopen/completion pairing inferred;
- every historical `work_reopened` event has
  `reopen_generation=-work_events.id`, every other event binding is null, and
  no negative reopen binding is paired with a completion;
- every pre-0019 column value, row count, timestamp, ID, JSON body, digest,
  salt, fingerprint, version, and response body is exact;
- the complete Phase 10 survivor catalog retains its exact incoming raw
  PostgreSQL 17 representation. The audit accepts only the migration-built
  digest
  `5171e0e22b9b6f838277725146ad81ccdcb747a82244fba3dd2aa42bb3cfa8fe`
  or the shipped-backup-restored digest
  `95ac5cede92f756a2132379f9fb38f97148b7c3dd2c817a1844b8ad1facc45fe`;
  it does not generically normalize expressions;
- every Phase 11 object is excluded from that survivor projection by exact
  identity, including the four new constraint-trigger `pg_constraint` rows,
  while Phase 11 custom/RI triggers retain their own exact inventory;
- Phase 10 arrays, constraints, functions, named triggers, indexes, sparse
  serialization behavior, and whichever approved raw Phase 10 survivor-catalog
  representation the database entered with are otherwise unchanged;
- no prompt, source metadata, tag, event body, command-looking text, commit,
  URL, or receipt is interpreted as evidence;
- historical completions are readable with empty arrays only through the new
  completion-evidence page.

### 5.8 Conditional downgrade

Downgrade to 0018 is permitted only before Phase 11 evidence has been used.
Before taking any lock, query, or destructive action, require the current
transaction isolation to be exactly `read committed`; reject repeatable-read
and serializable transactions as Phase 10 does.

Downgrade is an operator-coordinated maintenance operation. Stop and drain all
first-party writers and prohibit direct child/checkpoint DML before invoking
it. The migration cannot prove that an application-role SQL session is absent,
so it must not describe its lock order as universally deadlock-free. Execute
`SET LOCAL lock_timeout = '5s'` before the first lock; any lock timeout or
PostgreSQL deadlock aborts the entire transaction before DDL, and the operator
may retry only after re-establishing quiescence.

Then acquire these relations one at a time in the following first-party writer
order, using `ACCESS EXCLUSIVE` for every relation and holding every lock
through transaction end:

```text
client_operations
work_items
checkpoints
verification_results
artifact_references
work_events
```

This order matches the receipt-first application path. A deliberately issued
direct child insert acquires its target relation before its `BEFORE` trigger
locks `work_items`; a direct event insert likewise acquires `work_events`
before its Phase 11 trigger locks the work. Direct-DML quiescence and
fail-closed retry are therefore mandatory rather than hand-waved away. Only
after all locks are held, execute `SET CONSTRAINTS ALL IMMEDIATE`; any queued
invariant failure aborts the downgrade. Then check for:

- any row in either evidence table;
- any completed `complete_work` receipt whose stored response object has a
  top-level `completion_evidence` key, regardless of whether its value is
  non-empty, empty, null, or otherwise noncanonical;
- any malformed receipt body or impossible catalog/constraint state that
  prevents proving absence.

If any check is nonzero or indeterminate, raise before dropping an object.
There is no `force` option. If all checks are empty, first prove the generation
bindings, completion/reopen identity bounds, backfill-before-live rule, strict
live-completion ID/version order, exact positive-generation reopen/version
chronology, current-done live-completion/work-version order, identity sequence,
and catalogs satisfy the next 0019 preflight.
Drop, in dependency order, the event-side reopen constraint trigger,
work-transition constraint trigger, done-state episode constraint trigger,
event insertion/ID/version guard,
checkpoint guards that query reopen bindings, the regular pending-exit,
unsealed-deletion, and completion-episode departure guards, partial
reopen-generation index, reopen-kind check, and private
`work_events.reopen_generation`; then remove the remaining Phase 11 statement/
row triggers (including both existing-table truncate guards), generation
indexes/checks/columns, both partial completion-event indexes, evidence tables,
and Phase-11-only functions. Restore that database's exact pre-upgrade approved
raw Phase 10 survivor-catalog representation, not a forced conversion between
the two explicitly approved migration-built and shipped-backup-restored
projected forms. Private generation
values are reconstructible enforcement metadata; no-evidence Phase 11 reopens
and completions remain ordinary 0018 event/checkpoint facts and do not
independently make downgrade lossy. Re-upgrade maps all then-retained
completion/reopen events into their exact negative legacy namespaces, maps
each then-`done` work to its highest retained completion generation, and maps
other work to zero. It intentionally does not preserve former positive
generation numbers and never guesses an old reopen/completion pairing. Tests
reproduce Phase 10's non-READ-COMMITTED rejection and its
writer-commits-while-downgrade-waits race; the post-lock query must see the
committed row/receipt and refuse. Separate tests hold a first-party receipt/work
lock and a direct target-table-before-work lock: downgrade either proceeds
after the harmless holder exits or aborts content-free on its bounded timeout,
never runs partial DDL, and succeeds on a fresh quiescent retry when otherwise
eligible.

After first use, recovery is fix-forward or restoration of a complete pre-use
database plus matching 0.5.x binaries, explicitly losing all later writes.

### 5.9 Schema parity and read-only audit

Tests and the operational audit verify:

- exact migration head and revision chain;
- table/column types, nullability, defaults, checks, FKs, and indexes;
- every Phase 11 validator and guard function's exact signature, volatility,
  parallel safety, fixed search path, and normalized body hash;
- exact enabled state, timing, event set, deferrability, condition, function
  binding, and normalized definition hash for
  `completion_generation_guard`,
  `completion_state_episode_guard`,
  `completion_pending_exit_guard`,
  `completion_unsealed_deletion_guard`,
  `completion_episode_departure_guard`,
  `completion_generation_reopen_guard`,
  `completion_lifecycle_event_insert_guard`,
  `completion_reopen_event_episode_guard`, both completion-checkpoint guards,
  the aggregate child guards, all row-immutability guards, both evidence-table
  statement-TRUNCATE guards, and the existing-table `work_events`/
  `client_operations` statement-TRUNCATE guards;
- the event-backed completion one-to-one relation, completion/reopen IDs in the
  reserved range `1..9223372036854775806`, owned-sequence position, and
  both monotonic access indexes;
- the non-tautological completion-order predicate: across every retained
  same-work `origin='live'` completion, typed `metadata.work_version` is unique
  and strictly increases with event ID, and every live ID follows every
  same-work backfill ID; every positive-generation completion version exceeds
  its exact same-generation reopen version; every nonnegative checkpoint
  generation has a positive event ID greater than all same-work negative-
  generation episode IDs; and, for any two nonnegative completion generations
  `g1 < g2`, their IDs satisfy `id1 < id2`;
- exact negative legacy checkpoint generations and the exact negative current
  generation on every migrated/re-upgraded done work; zero on every other
  migrated work and every new work; nonnegative runtime checkpoint
  generations; no negative pending work; and exactly one completion per
  work/generation;
- for every nonnegative completion checkpoint,
  `checkpoint.completion_generation <= work.completion_generation`; equality
  only for the sealed episode of a done work, and strict inequality after its
  non-dischargeable departure; every retained done work has exactly one sealed
  checkpoint/event at its work generation, that current live completion's
  typed work version is no greater than the retained work version, and other
  statuses have no checkpoint at their current generation;
- no pending/deferred/wont-do/promoted work has a completion checkpoint at its
  current nonnegative generation; every nonnegative historical completion at
  generation `g` has an exact successor reopen binding at `g + 1` from done,
  and its completion-event work version is strictly lower than that successor
  reopen-event work version;
- negative exact legacy reopen bindings, exact unique positive reopen bindings
  for every positive generation increment, and—when the work generation is
  positive—bindings forming the complete prefix
  `1..work.completion_generation`;
- no orphan, cross-project, wrong-kind, post-completion, noncontiguous,
  duplicate-artifact, over-count, or over-byte rows;
- exact result/reference counts per completion checkpoint;
- for a retained source alias, exact existing immutable-merge reviewed source
  checkpoint/event counts plus Phase 11 receipt/evidence correspondence;
  runtime work-row serialization and the live-canonical completion-event guard
  prevent insertion after the source becomes an alias. The audit does not
  compare a completion ID with a different event type's ID or pretend
  checkpoint-derived `created_at` proves physical insertion order;
- both directions of correspondence between evidence-bearing receipt bodies
  and retained rows;
- an unchanged whole Phase 10 survivor digest matching exactly one of the two
  approved raw PostgreSQL 17 forms, exact `(relation, constraint)` exclusion of
  Phase 11 constraint triggers, separately inventoried Phase 11/RI objects, and
  unchanged receipt digests.

Whole-catalog equality is asserted only after a clean downgrade to 0018, when
the new custom and RI triggers no longer exist.

The audit emits only IDs needed to locate corrupt rows plus aggregate counts and
error categories. It never prints commands, summaries, labels, paths, URLs, or
artifact contents.

Read assembly applies the same order predicate to every selected episode using
bounded adjacent-generation/predecessor lookups and fails the entire page
closed; it does not turn page access into an unbounded global audit. A
disposable corruption test temporarily defeats guards with owner DDL, creates
each stored ID/generation-order violation, restores the catalog, and proves the
affected public page and aggregate audit reject it. This tests detection; it
does not place owner DDL inside the supported mutation boundary.

---

## 6. Backend, REST, and receipt design

### 6.1 Strict input and read models

Add explicit models for:

```text
WorkCompletionRequest            # control-free domain fields
WorkCompletionCreate             # public transport request + operation UUID
CommandVerificationInput
ObservationVerificationInput
VerificationResultInput = CommandVerificationInput | ObservationVerificationInput
CommandVerificationRead
ObservationVerificationRead
VerificationResultRead = CommandVerificationRead | ObservationVerificationRead
ArtifactReferenceInput
ArtifactReferenceRead
CompletionEvidenceInput
CompletionEvidencePayloadRead
CompletionEvidenceEpisodeRead
CompletionEvidencePage
CompletionEvidenceListQuery
```

Follow the existing `WorkMergeRequest`/`WorkMergeCreate` pattern.
`WorkCompletionRequest` owns `expected_version`, checkpoint, optional
`lease_token`, and optional evidence, but has no client-operation control or
conditional UUID validator. `WorkCompletionCreate` inherits those fields, adds
the optional `client_operation_id`, and alone enforces “non-empty evidence
requires an ID.” Register `complete_work` with
`request_model=WorkCompletionCreate` and
`domain_model=WorkCompletionRequest`.

This split is required by the existing receipt pipeline:
`prepare_client_operation` validates the public transport model, extracts and
strips `client_operation_id`, then revalidates explicitly supplied domain
fields with the registry's control-free model. Reusing the conditional public
model as its own domain model would incorrectly reject every keyed
evidence-bearing request after the ID was stripped.

OpenAPI 3.1 encodes the same conditional as executable JSON Schema 2020-12 on
`WorkCompletionCreate`, rather than relying on prose or an example. The
condition activates exactly when the raw evidence object contains at least one
item in either child array. Its `then` branch requires
`client_operation_id` and narrows the value to a non-null UUID string:

```json
{
  "if": {
    "required": ["completion_evidence"],
    "properties": {
      "completion_evidence": {
        "type": "object",
        "anyOf": [
          {
            "required": ["verification_results"],
            "properties": {
              "verification_results": {
                "type": "array",
                "minItems": 1
              }
            }
          },
          {
            "required": ["artifact_references"],
            "properties": {
              "artifact_references": {
                "type": "array",
                "minItems": 1
              }
            }
          }
        ]
      }
    }
  },
  "then": {
    "required": ["client_operation_id"],
    "properties": {
      "client_operation_id": {
        "type": "string",
        "format": "uuid"
      }
    }
  }
}
```

The base evidence property is object-only and has no null branch. Outside the
conditional, the operation-ID property retains its historical optional/null
schema; intersecting the `then` branch excludes null only when evidence is
non-empty. Omitted or empty evidence does not forbid a valid UUID, so
historically keyed completion remains valid. The runtime transport validator
remains authoritative, while the emitted schema independently tells 3.1-aware
consumers the same rule.

The verification input and read types are discriminator-based unions.
`CompletionEvidencePayloadRead` is the arrays-only, necessarily non-empty
payload nested under `WorkCompletionRead`. `CompletionEvidenceEpisodeRead` is
the history wrapper with lossless decimal-string `completion_event_id`, one compact
`CheckpointPointer`, and two required arrays that may both be empty. These are
distinct models; neither accepts the other's keys.

Input models never accept server IDs, positions, parent IDs, or `created_at`;
those are allocated by the server. Every child read model requires `id`,
`work_item_id`, `completion_checkpoint_id`, `position`, and `created_at` in
addition to its typed content. Episode/read validators require exact IDs,
contiguous positions, exact child order, parent/checkpoint coherence, and child
time equality with the wrapper checkpoint.

The Python field uses a pre-parse validator that first requires a raw string,
ASCII, and section 3.9's exact length/grammar; generic Pydantic datetime
coercion never sees rejected input. The MCP and TypeScript copies use the same
fixture and explicit civil-date/offset arithmetic. They do not reuse the
current arbitrary-fraction UTC helper unchanged and do not use permissive
`Date.parse`/`Date.UTC`, whose handling of years 1–99 and invalid dates is not
this contract. Read serializers independently enforce the one canonical UTC
spelling.

`WorkCompletionCreate` and `WorkCompletionRead` gain the field-local sparse
`completion_evidence`. Existing completion checkpoint types remain otherwise
unchanged. One exact-key valid non-empty HTTP request shape is:

```json
{
  "expected_version": 7,
  "checkpoint": {
    "prompt": "Implemented the bounded completion-evidence contract.",
    "source_client": "codex",
    "source_session_id": "session-42",
    "source_model": "gpt-5",
    "source_session_url": "https://example.test/sessions/42",
    "repository_branch": "work/example",
    "verified_against": "7ad62e4",
    "affected_paths": ["backend/**"],
    "tags": ["backend"],
    "source_metadata": {}
  },
  "client_operation_id": "11584ccf-c787-4c6a-bb89-a69a02c1554d",
  "completion_evidence": {
    "verification_results": [
      {
        "verification_type": "command",
        "name": "Backend test suite",
        "outcome": "passed",
        "summary": "1,284 tests passed; the PostgreSQL suite ran without skips.",
        "command": "uv run pytest -q",
        "exit_code": 0,
        "observed_at": "2026-09-03T18:01:02Z",
        "observed_at_commit": "7ad62e4"
      }
    ],
    "artifact_references": [
      {
        "artifact_type": "pull_request",
        "label": "Phase 11 pull request",
        "reference": "https://github.com/example/mnemonic/pull/123"
      }
    ]
  }
}
```

Its exact-key response shape is:

```json
{
  "work_item": {
    "id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
    "project_id": "07fd0090-7be9-4bd3-af85-e5759467d44e",
    "title": "Implement structured completion evidence",
    "summary": "Keep completion claims structured and attributable.",
    "status": "done",
    "priority": 1,
    "initial_checkpoint_id": "a66fe2e8-9553-4dce-9962-54db3d398de4",
    "version": 8,
    "created_at": "2026-09-03T15:00:00Z",
    "updated_at": "2026-09-03T18:04:12.123456Z"
  },
  "checkpoint": {
    "id": "82c3c46c-8665-48ad-a0be-13fb196418be",
    "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
    "kind": "completion",
    "prompt": "Implemented the bounded completion-evidence contract.",
    "source_client": "codex",
    "source_session_id": "session-42",
    "source_model": "gpt-5",
    "source_session_url": "https://example.test/sessions/42",
    "repository_branch": "work/example",
    "verified_against": "7ad62e4",
    "affected_paths": ["backend/**"],
    "tags": ["backend"],
    "source_metadata": {},
    "migration_origin": null,
    "legacy_record_id": null,
    "created_at": "2026-09-03T18:04:12.123456Z"
  },
  "completion_evidence": {
    "verification_results": [
      {
        "id": "a96f13a0-b552-451f-970c-90b2387cc518",
        "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
        "completion_checkpoint_id": "82c3c46c-8665-48ad-a0be-13fb196418be",
        "position": 0,
        "verification_type": "command",
        "name": "Backend test suite",
        "outcome": "passed",
        "summary": "1,284 tests passed; the PostgreSQL suite ran without skips.",
        "command": "uv run pytest -q",
        "exit_code": 0,
        "observed_at": "2026-09-03T18:01:02Z",
        "observed_at_commit": "7ad62e4",
        "created_at": "2026-09-03T18:04:12.123456Z"
      }
    ],
    "artifact_references": [
      {
        "id": "515f3427-0fa5-497e-b95a-47417fb0ba28",
        "work_item_id": "8f021f34-b9ca-4bb7-a0ea-26049694c3b9",
        "completion_checkpoint_id": "82c3c46c-8665-48ad-a0be-13fb196418be",
        "position": 0,
        "artifact_type": "pull_request",
        "label": "Phase 11 pull request",
        "reference": "https://github.com/example/mnemonic/pull/123",
        "created_at": "2026-09-03T18:04:12.123456Z"
      }
    ]
  }
}
```

The request example includes no `lease_token`; it is an omitted optional field,
not `null`. Fingerprint canonicalization still uses the existing version-1
operation envelope: it excludes `client_operation_id`, preserves the
Phase-10 treatment of every pre-existing default/null field, and applies only
the new field-local sparse rules from section 3.4 inside evidence. The
completion response deliberately has no
`completion_event_id`: event identity is page-wrapper state, while the
permanent completion receipt contains the full checkpoint plus its arrays-only
evidence payload.

Unknown keys are forbidden. Request and response validators share primitive
helpers and the cross-layer fixture, but response validation is stricter about
canonical empty/null representations and server-owned fields.

### 6.2 Completion transaction order

For a genuinely new protected completion:

1. authenticate and structurally validate the complete request, including the
   conditional operation-UUID rule and full canonical body-size bound;
2. run the evidence-subtree substring secret scan from section 6.5, then the
   existing request-known scan, before reserving/reconciling the
   `complete_work` receipt;
3. lock the exact work item and apply current alias/status/version/blocker/gate/
   lease checks in their existing order;
4. allocate and flush the completion checkpoint so its ID and
   `clock_timestamp()` are fixed;
5. add the ordered verification and artifact rows with that exact timestamp,
   then explicitly flush both child families and requery their aggregate before
   any event is added to the session;
6. change the work item to `done`, increment version, set activity time, and
   explicitly flush that update so the pending-exit guard observes the
   checkpoint and the later event guard observes the post-transition work;
7. stage and flush the publicly unchanged `work_completed` event referencing
   the checkpoint only after the child and work-update flushes succeed; its
   typed work version equals the captured pending-to-done version, and its
   identity is accepted only after the locked Phase 11 exact-current-
   generation, positivity, and same-work-monotonicity guard passes;
8. rehydrate the checkpoint and ordered children from authoritative rows, then
   build and strictly validate `WorkCompletionRead`, including non-empty
   evidence when supplied;
9. complete the existing receipt with the canonical response; and
10. commit once, then emit only the existing data-free live invalidation.

Any validation, constraint, fault-injection, serialization, or response
coherence failure rolls back checkpoint, children, lifecycle, event, lease
consumption, and pending receipt together.

### 6.3 Receipt preservation and response coherence

Before changing schemas, freeze request canonical bytes/fingerprints and
response JSON/digests for all 13 receipt kinds, including completion requests
whose checkpoint has absent, empty, and non-empty Phase 10 scope.

Prove:

- old completion input has the exact old fingerprint;
- explicit empty evidence has the same canonical bytes/fingerprint as omission;
- evidence type, outcome, every string, optional observation-field presence,
  array membership, and array order affect the fingerprint;
- a same-UUID evidence change returns `client_operation_conflict` and performs
  no second completion;
- historical response bodies parse and sparse-reserialize byte for byte;
- a new evidence-bearing response includes exactly the retained ordered rows;
- stored `null`, present-empty, wrong-parent, wrong-position, wrong-matrix, or
  request/response child-count disagreement fails receipt validation closed;
- exact replay after reopen, later completion, merge to alias, soft deletion,
  restart, or restore returns the original checkpoint/evidence IDs and body
  without new rows or events;
- all twelve unrelated receipt kinds remain exact.

`_complete_work_matches` remains a pure request/response function with no
database or session access. It compares the completion checkpoint and every
evidence value while ignoring only server-owned IDs/positions/timestamps where
the request cannot supply them. It also validates those response-owned values
for uniqueness, contiguity, parent ownership, timestamp equality, exact array
cardinality, and read-model coherence.

On initial creation, authoritative rehydration proves physical rows match the
new response before the receipt is completed. On replay, the exact stored
receipt body remains the permanent response source; replay does not add a
database query whose result could override that receipt. Bidirectional
receipt-to-row correspondence belongs to creation tests and the read-only
audit, which fail closed on corruption.

No receipt contract version is bumped and no body is rewritten.

### 6.4 REST read contract

The evidence history endpoint:

- authenticates normally;
- rejects unknown query parameters;
- resolves project/work together without cross-project disclosure;
- permits exact source-alias history reads and labels canonical status through
  the page's own `is_duplicate`, `canonical_work_item_id`, and null alias
  current-pointer fields rather than redirecting;
- excludes soft-deleted work from ordinary reads, while retaining its rows for
  receipt recovery and operator audit;
- returns completion checkpoints even when both evidence arrays are empty;
- uses a bounded page-first query and ordered aggregate subqueries, never one
  row per Cartesian result/reference pair;
- returns no full prompt, source metadata, affected paths, receipt, token, or
  artifact content;
- has no side effect, activity touch, cache write, external request, or receipt.

The API serializes the complete page before sending it and rejects a corrupt
assembly above 3 MiB of UTF-8 JSON with a content-free internal error. Every
legal ten-episode page is proven below that ceiling under maximum JSON
escaping. The controlled HTTP paths send that JSON without content coding;
MCP, proxy, and browser readers independently enforce the same pre-parse
identity-body ceiling.

The endpoint is classified as a safe read by MCP. Ordinary safe-read retry is
allowed after transport failure because no structural outcome is uncertain.

### 6.5 Validation and error precedence

Do not reorder the protected completion pipeline:

1. authentication;
2. structural, cross-field, timestamp, and size validation, including the
   conditional operation UUID and canonical-byte bounds;
3. the evidence-only substring secret scan;
4. the existing request-known secret scan;
5. operation-kind/fingerprint conflict or exact completed/in-progress replay;
6. project/work visibility and canonical ownership;
7. current status/version/blocker/gate/lease checks; and
8. aggregate response coherence and commit.

This preserves recovery of an old success after later state changes.
A request that both omits the evidence-required UUID and embeds a known bearer
therefore receives a sanitized structural `422`; no secret scanner or receipt
lookup runs. OpenAPI emits the exact executable `if`/`then` condition from
section 6.1. Schema tests resolve component references and evaluate it with a
Draft 2020-12 validator plus UUID format checking; runtime tests separately
freeze the same validation locations and precedence.

Use existing public errors wherever their meaning is exact:

- normal FastAPI 422 validation locations for type, field, array index, bounds,
  matrix, URL, path, timestamp, and canonical input errors;
- `client_operation_secret_echo` for a request-known bearer, operation UUID, or
  supplied lease token embedded anywhere in a nested durable evidence string;
- existing `client_operation_conflict` and `client_operation_unavailable` for
  protected retry safety;
- existing work/version/status/blocker/gate/lease/duplicate errors from
  completion.

The new read reuses `invalid_cursor`. No error includes a command, summary,
label, reference, token, key, URL, raw child value, or unauthorized checkpoint
ID. Safe validation-location vocabularies may include field names and zero-based
array indexes only.

The existing generic request-known scanner keeps its semantics for the other
twelve receipt kinds. Phase 11 adds a completion-evidence-only recursive scan
over every caller string before reservation or storage:

- match the raw bearer and supplied lease token as case-sensitive substrings;
- match the operation UUID case-insensitively in canonical hyphenated,
  hyphenless, `urn:uuid:`-prefixed, and brace-delimited common spellings; and
- reject prefix/suffix embedding such as `Bearer <token>`, `token=<lease>`, or
  prose containing a UUID, without reflecting the matched value.

The scan does not claim heuristic detection of unknown credentials and does
not broaden validation behavior for unrelated operations.

### 6.6 Projection exclusions

Structured evidence appears only in:

- a non-empty `complete_work` response;
- exact replay of that response; and
- the dedicated evidence history endpoint/tool/dashboard view.

It does not appear in:

- `CheckpointRead` or ordinary checkpoint history;
- `CheckpointPointer` outside the evidence item wrapper;
- `WorkItemRead`, `WorkItemDetailRead`, `WorkSummary`, ready work, search, or
  hierarchy;
- bounded `WorkContext`, claim-and-recall, resources, or resume prompts;
- `WorkEventRead` or work event metadata/body;
- human gates or revision anchors;
- relationships, merge records, canonical projections, duplicate suggestion
  inputs/results, embeddings, or semantic cache keys.

The new list tool is the explicit bounded retrieval step. This prevents large
commands and summaries from entering routine recall and keeps older compact
contracts stable.

### 6.7 Lifecycle, concurrency, and derived state

Evidence creation inherits completion's exact version and authority checks; it
adds none of its own. Evidence:

- never changes readiness or introduces a stored “verified” state;
- never creates a gate or resolves one;
- never renews, grants, or independently consumes a lease;
- never changes the work version beyond completion's existing single increment;
- never adds another activity timestamp or live-sync frame;
- never invalidates search, embeddings, or duplicate suggestion caches;
- never changes blocker resolution beyond `done`'s existing behavior.

The work-row lock serializes completion against reopen, merge, gate creation,
and competing completion. Evidence rows are invisible without their committed
completion. Concurrent identical protected requests produce one completion;
distinct operation UUIDs race under the existing version/status rules so only
one can complete.

The existing reopen application path must flush its guarded work transition
before staging/flushing `work_reopened`; unit-of-work ordering is not accepted
as proof. The event trigger assigns the private positive binding. Neither the
service nor any public request supplies it.

---

## 7. MCP adapter

### 7.1 One extended write and one new read

Extend the existing `complete_work` tool with optional
`completion_evidence`. It remains one receipt-protected, destructive mutation
and makes exactly one outbound attempt per tool invocation.

Add one tool:

```text
list_completion_evidence
```

It accepts exact `project_id`, `work_item_id`, optional `limit`, and optional
opaque `cursor`, dispatches the REST `GET`, and returns the strict page model.
It is annotated read-only/safe and takes no operation UUID.

Do not add `add_verification_result`, `add_artifact_reference`,
`record_completion_evidence`, or compatibility aliases. Final catalog counts
are exactly 28 tools and 11 protected writes.

### 7.2 Strict transport and response parity

MCP models reproduce the discriminator, matrix, bounds, URL/path grammars,
sparse outer field, child order, IDs, timestamp, pointer, totals, page-level
work/alias identity, current completion, event high-water, and cursor
contracts.

Before FastMCP parsing or dispatch, wrap both exposed server transports with
the shared section-3.8 ingress guard. For Streamable HTTP, an outer ASGI
middleware checks coding and incrementally reads at most 1,048,576 identity
bytes, validates strict UTF-8, the single-message JSON shape, and any request
ID, then replays those exact bytes once to the SDK. It does not trust
`Content-Length`; a declared over-limit length can reject early, while absent,
invalid, chunked, or dishonest lengths still use the counter. For stdio, a
project-owned transport adapter reads the underlying binary stream in bounded
chunks, recognizes LF-delimited records without an unbounded `readline()`,
and applies the same byte/UTF-8/JSON/single-object/ID checks. A rejected record
terminally closes input, discards later records already in the buffer, and
never dispatches either one. For an accepted object, the adapter calls the
installed SDK's exact `types.JSONRPCMessage.model_validate` boundary. A
successful value becomes the SDK `SessionMessage`; an object-shaped JSON-RPC
semantic validation exception is delivered into the SDK read stream exactly
as the locked standard stdio transport does, so the SDK—not the adapter—owns
any resulting protocol response or log behavior. Those object-shaped semantic
failures are not reclassified as terminal transport rejections. The installed
SDK writer retains its canonical serializer and remains the only stdout
writer; a transport-level rejected input closes the transport instead of
inventing a second JSON-RPC error-frame serializer.
`SanitizedFastMCP.run_stdio_async` owns this adapter and calls the inherited
`_mcp_server.run(read_stream, write_stream,
create_initialization_options())`, the same smallest private seam used by the
locked SDK implementation. An exact SDK seam test pins that dependency;
dependency drift must fail the build rather than fall back to the SDK's
unbounded text iterator. There is one guard implementation and fixture corpus
for the ID domain, with thin transport-specific readers.

First-party evidence-history transport from MCP to the backend is separately
identity-coded. The MCP client sends `Accept-Encoding: identity`, opens the
response as a stream, and inspects `Content-Encoding` before acquiring or
pulling a body iterator. It accepts only an absent header or one
case-insensitive `identity` token. `gzip`, `br`, `deflate`, a coding list, an
empty/malformed value, or any other coding closes the response immediately
with a content-free transport error. Neither a success nor an error body is
decoded, buffered, parsed, or reflected before this check.

For an accepted identity response, use the raw byte iterator—not HTTPX's
decoded iterator or eager `.content`/`.json()`—and request iterator chunks no
larger than 64 KiB. Maintain a checked counter and bounded accumulator; inspect
each returned chunk length before copying it, copy no bytes once the total
would exceed 3,145,728, close immediately, fatally decode UTF-8 only after EOF,
and only then parse JSON. This is logically max-plus-one even when a returned
chunk spans the boundary, without retaining the overflow byte.
`Content-Length` may reject a declared oversized identity body early but never
proves acceptance; absent, malformed, negative, chunked, or dishonestly small
lengths still use the stream count. Failure is content-free and returns no
truncated page. This bounds bytes Mnemonic copies, accumulates, and parses. It
does not claim zero allocation inside the operating system, TLS stack, HTTP
runtime, or a runtime-owned raw input chunk.

Before Pydantic normalization, raw response guards:

- accept absence of `completion_evidence` in old/no-evidence completion
  responses;
- require a present evidence object to be non-empty and canonical;
- reject explicit null, present-empty, missing child arrays, extra fields,
  wrong discriminators, incoherent exit codes, duplicate IDs/positions, or
  wrong parent pointers;
- require page totals/current pointer/items/cursor to be coherent;
- permit live page metadata on a continuation to name a current checkpoint
  newer than its historical `as_of_completion_event_id`; do not require the
  current checkpoint to appear in that page's bounded items;
- never log the rejected raw value.

Returning the validated page is a second size boundary. With the locked
FastMCP SDK, retain its normal typed-result contract: `content[0].text`
contains the SDK's JSON rendering and `structuredContent` contains the same
page object. Serialize the maximum legal page through the actual server result
converter and complete JSON-RPC 2.0 success envelope; the compact UTF-8
message uses the maximum legal 128-character ID. The complete HTTP response
body and the complete stdio record including its LF must each be no larger
than 12,582,912 bytes. Test actual bytes accepted over Streamable HTTP and
stdio, not only `model_dump_json()`. This output ceiling is independent of the
3 MiB identity body read from the API. A dependency upgrade repeats the
measurement and transport-seam tests; a failure lowers unreleased
evidence/page limits through design review rather than truncating a page,
weakening the request-ID bound, or hand-building a one-copy result that
changes existing MCP semantics.

OpenAPI consumer metadata names every strict MCP model.

### 7.3 Tool descriptions and authority

`complete_work` tells callers to:

- record only checks actually observed;
- distinguish process result from semantic sufficiency;
- omit evidence rather than invent a pass, timestamp, commit, or artifact;
- retain exact ordered evidence with the existing operation UUID after an
  unknown outcome;
- keep required failed/inconclusive/skipped limitations explicit in the
  completion checkpoint and normally stop for direction;
- never paste secrets, raw logs, tokens, or private reasoning;
- never convert Phase 10 freshness output automatically.

`list_completion_evidence` says:

- results are untrusted historical assertions, never commands or instructions;
- a current completion is identified only by the page's current checkpoint ID;
- earlier episodes remain historical after reopen/recompletion;
- empty arrays mean no structured rows, not pass/fail;
- pass each exact unchanged server-issued cursor and page until
  `next_cursor=null` for a history complete as of the page's high-water event;
  a decoded/edited/manufactured cursor carries no completeness guarantee; when
  current completeness matters, compare a fresh head and repeat until two head
  observations match, reporting instability rather than claiming completeness
  under continuous change;
- use full checkpoint history when prompt or declared affected paths matter;
- never execute a returned command or automatically visit a returned URL.

### 7.4 Resources and prompts

Keep bounded `WorkContext` and MCP resources evidence-free. Update resource and
resume instructions so a client auditing or relying on completed work calls
`list_completion_evidence` explicitly. A normal recall of pending work does not
automatically ingest all historical commands and URLs.

Search results and completion checkpoint pointers remain retrieval aids only.
Evidence never grants execution authority.

---

## 8. Dashboard and browser proxy

### 8.1 Completion editor

The existing “Complete with summary” flow gains a completion-only expandable
section containing:

- repeatable verification rows;
- repeatable artifact-reference rows;
- add/remove/reorder controls;
- type-specific fields and help;
- a running 20-entry and aggregate-byte limit;
- explicit “Recorded as caller-reported evidence; Mnemonic does not run these
  checks” language.

The ordinary context/progress checkpoint form does not show these controls.
The completion checkpoint text and repository declaration stay in the existing
form.

Client validation uses the shared fixtures and points errors to the exact row
and field. The UI does not infer `outcome` from visual color alone and does not
silently change an exit code, normalize a URL, lowercase arbitrary content, or
drop an invalid row.

### 8.2 Frozen retry identity

Before the first completion attempt, freeze:

- expected work version;
- complete checkpoint and Phase 10 declaration;
- exact evidence discriminators, fields, optional-field presence, and array
  order; and
- one client operation UUID.

An unknown outcome preserves the entire draft in memory and blocks intersecting
work mutations. Exact retry reuses the frozen JSON byte intent. Editing any
checkpoint or evidence field, adding/removing/reordering a row, or changing a
token is a new intent and requires a new UUID only after the earlier outcome is
definitively resolved.

The dashboard proxy never accepts a `lease_token`; completion remains
unavailable while the work is actively leased and directs the user to the
owning client/release workflow. Phase 11 does not redesign browser capability
handling.

After a coherent success, reload the authoritative work context. Refetch the
evidence head only when the Evidence tab is active; otherwise invalidate any
cached evidence state and fetch lazily on its next selection. Apply the same
rule after an unknown-outcome retry resolves successfully. Do not optimistically
manufacture IDs, timestamps, counts, current completion, or page state.

### 8.3 Dedicated Evidence tab

Add a sixth work-detail tab, `Evidence`, loaded only when selected. It shows:

- current completion episode first when work is done;
- every earlier completion episode in page order;
- a clear “current completion,” “prior completion,” or “work currently
  reopened” label derived from the page-level current checkpoint ID;
- “No structured completion evidence recorded” for empty episodes;
- ordered verification name/type/reported outcome/summary;
- inert command and exit-code presentation;
- optional observed time and observed-at commit;
- ordered typed artifacts and honest mutability/availability notes;
- completion timestamp and caller provenance from the checkpoint pointer;
- pagination and retry states without losing the current tab.

Exact alias work shows its source-owned evidence read-only, labels the page as
an alias using `canonical_work_item_id`, treats every episode as prior with a
null current pointer, and preserves the existing canonical-direction controls.
It never folds destination evidence into the alias or vice versa.

### 8.4 Safe rendering and links

All caller text is rendered as text nodes with wrapping and bidi isolation. No
HTML, Markdown, ANSI, terminal escape, or linkification is interpreted from
commands, summaries, labels, branches, paths, or commits.

Only artifact types whose validated reference is an absolute HTTPS URL render
an anchor. The anchor:

- uses the exact visible hostname and an accessible type label;
- opens only after an explicit user action;
- uses `target="_blank"` with `rel="noopener noreferrer"`;
- has no preview, prefetch, HEAD request, image load, favicon lookup, or server
  proxy;
- never renders `javascript:`, `data:`, `file:`, credential-bearing, query, or
  fragment locators.

Commit, branch, and repository-path references are inert copyable text. Copying
a stored command does not label it safe or offer a run action.

Set `X-DNS-Prefetch-Control: off` in both `frontend/next.config.ts` and the
mirrored static/browser response path in `deploy/nginx/mnemonic.conf`, with an
exact header-parity test. Do not emit DNS-prefetch resource hints. This
explicit serving policy, plus the absence of preview/prefetch components,
supports the “no contact merely on view” boundary; request interception alone
does not prove that an operating-system DNS lookup was absent.

### 8.5 Proxy and mutation inventory

Allow the new evidence `GET` through the browser proxy with exact path/query
policy. Extend the existing completion body policy for the nested evidence
union and aggregate request-size bounds. Preserve the existing 1 MiB request
ceiling.

The mandatory first browser boundary is
`frontend/app/api/mnemonic/[...path]/route.ts`. Replace its unbounded
`upstream.arrayBuffer()` path for the evidence response with an identity-only
bounded stream. The upstream request sends `Accept-Encoding: identity`.
Before calling `getReader()`, `arrayBuffer()`, a text/JSON helper, or a generic
error-body parser, inspect `Content-Encoding` and accept only absence or one
case-insensitive `identity` token. Any other or malformed value causes
immediate body cancellation and a content-free `502` proxy failure (never a
request `413` and never a truncated success); the proxy does not pull,
decompress, buffer, parse, or forward that body.

For an accepted identity response, keep a checked counter and bounded
accumulator. Inspect each transport-provided chunk's length before copying;
when it crosses 3,145,728, copy none of that crossing suffix, cancel
immediately, release the chunk reference, and never construct or parse the
oversized body. `Content-Length` may reject a declared oversized identity body
early but is never sufficient to accept one; absent, malformed, negative,
chunked, or dishonestly small lengths still use the stream count. The proxy
returns unencoded bytes and removes the upstream `Content-Encoding`; it
recomputes or omits `Content-Length`.

The browser API layer is a second, explicit defense boundary, not an assumption
that every caller used the proxy correctly. Browser JavaScript cannot set the
forbidden `Accept-Encoding` request header, so valid delivery depends on the
controlled Next.js/nginx path disabling content coding for this route. Freeze
that serving configuration: if route-scoped Next.js compression cannot be
proven, set `compress: false`; set native nginx `gzip off` for the dashboard
API path; preserve the Next-owned explicit `Content-Encoding: identity` marker
as the module-portable barrier against google/ngx_brotli; and emit
`Cache-Control: no-store, max-age=0, no-transform` as defense in depth. A
literal `brotli off` is not portable because stock nginx rejects the unknown
directive when that optional module is absent. The deployed-path test must
therefore syntax-check the exact shared policy on stock module-free nginx and
also run it under an enabled inherited ngx_brotli filter, proving a control
response is `br` while evidence responses remain exactly `identity`. Do not
generalize that proof to untested third-party content filters. The deployed-
path test remains authoritative.

The new evidence method in `frontend/lib/api.ts` checks `Content-Encoding`
before calling `response.body.getReader()` or `response.json()` and rejects
anything except absence or one case-insensitive `identity` token. It then uses
a shared identity-byte reader with the same checked-before-copy bounded
accumulator, fatally decodes at most 3,145,728 UTF-8 bytes, and calls
`JSON.parse`; invalid UTF-8 fails content-free. This second boundary covers
mocked identity responses and direct unit use.
A future direct/base-URL transport must re-establish identity delivery; the
plan does not claim browser JavaScript can bound an unsupported origin's
decompressor allocation. Default Fetch controls the allocation size of each
chunk it hands JavaScript; the guarantee is that Mnemonic never copies or
retains more than the ceiling and current runtime-owned chunk, not that the
underlying runtime cannot allocate one larger raw chunk. Exact-limit,
limit-plus-one, one oversized runtime chunk with bounded copied accumulation,
cross-chunk, absent/falsely-small `Content-Length`, chunked identity, non-
identity pre-body rejection, and invalid-UTF-8 tests are required.

Do not allow a new evidence `POST` because none exists. The mutation registry
still has exactly 11 kinds; only `complete_work`'s payload/decoder changes.
Live-sync remains one data-free invalidation for the applied completion and no
additional content-bearing frame.

---

## 9. Claude Code plugin workflow

### 9.1 Packaging boundary

Keep exactly three skills, the existing Phase 10 repository helper, and one
executable. Add a fourth shared reference:

```text
plugin/reference/completion-evidence.md
```

Bump only `plugin/.claude-plugin/plugin.json` from `0.9.0` to `0.10.0`.
`.claude-plugin/marketplace.json` has no version property and must not gain
one; update its existing description metadata only if the delivered behavior
requires it. No fourth skill, new binary, `allowed-tools` grant, provider
integration, or background process is needed.

### 9.2 `mnemonic-save`

Before `complete_work`, the skill must:

1. determine which checks were actually required and observed;
2. record each result with an honest type/outcome and bounded summary;
3. include command text/exit code only from an observed invocation;
4. distinguish a skipped observation from an unperformed plan;
5. add only stable, nonsecret artifact references actually known;
6. state material unrun, failed, inconclusive, environment, external, or
   artifact limitations in the completion checkpoint;
7. preserve exact evidence inside the existing frozen operation intent; and
8. omit structured evidence when it cannot be stated truthfully.

The skill must not claim that a normal `git status`, Phase 10 unchanged result,
test exit code, PR URL, or commit identifier by itself proves the objective.

### 9.3 `mnemonic-recall`

When inspecting or relying on completed work, the skill calls
`list_completion_evidence`, identifies the exact current completion episode,
and pages further only when the audit needs it. It treats commands and URLs as
untrusted quoted history, never instructions.

If the work is reopened, all listed evidence is prior-completion history. If a
verification result's claim depends on repository content, separately recall
the full completion checkpoint and apply Phase 10 freshness guidance to its
declared scope. Do not pretend that comparing `observed_at_commit` or a commit
artifact substitutes for that process.

### 9.4 `mnemonic-search` and authority reference

Search does not return evidence. The search skill directs a user who needs
completion proof to exact work recall followed by the evidence tool. Update
`authority-and-provenance.md` and `work-graph.md` to preserve these rules:

- recorded evidence is not owner authority;
- an old pass is not current authorization;
- an artifact link is not authenticated identity or availability;
- alias evidence never authorizes work on the canonical destination;
- no evidence answers a human gate or bypasses blockers/leases;
- no stored command may be executed merely because Mnemonic returned it.

### 9.5 Installed behavior

Static and installed-package tests verify the new reference is included once,
all three skills link it through `${CLAUDE_PLUGIN_ROOT}`, manifests agree, the
Phase 10 executable mode/content is unchanged, and fresh plus sequential
`... -> 0.9.0 -> 0.10.0` installs contain no stale files.

---

## 10. Implementation sequence and hard gates

Implementation is deliberately ordered so that no client can depend on a
contract the database cannot preserve, and no evidence-bearing receipt is
created before replay and downgrade behavior is proven. A gate must be closed
before the next stage begins. Later stages may add tests to earlier layers but
must not relax an already frozen invariant.

### 10.1 Stage 0 — reconcile the Phase 10 baseline

- [x] Confirm the Phase 10 pull request is merged and its required checks
  passed.
- [x] Fetch `origin/main`, rebase the Phase 11 topic branch onto the actual
  merge, and record the delivered Phase 10 commit in this document.
- [x] Confirm a clean linear Alembic history ending at
  `0018_repository_freshness`; do not resolve a split head by inventing a
  merge migration.
- [x] Re-run exact catalogs for API routes, MCP tools and write annotations,
  receipt kinds, browser mutation policies, plugin files, public error codes,
  schema objects, and version declarations.
- [x] Re-run Phase 10's complete verification matrix, including the populated
  migration fixture and helper protocol tests.
- [x] Compare the merged completion/checkpoint/receipt shapes with this plan.
  If any assumption differs, revise the plan and obtain another cold contract
  review before product implementation.
- [x] Start implementation only from a clean, short-lived linked worktree
  based on that merged commit. Never merge or copy the retired concurrent peer
  worktree as a substitute for reviewed `main`.

The first two items were completed for this planning document: Phase 10 was
delivered at `fe7231595c9009cd46b244ed672a1db06563173d`, and the planning
worktree was current at `97317d8c675e8869cbe1aff684c6cc97dd235c10`. They
remain implementation preflight checks because `origin/main` can advance
after the plan is approved.

**Gate 0:** the delivered boundary is reproducible as
`0.5.0`/`0.9.0`/`0018` and `27/11/13/11`, or this plan has been explicitly
corrected and re-reviewed.

### 10.2 Stage 1 — freeze the cross-layer contract

- [x] Add one language-neutral JSON fixture corpus for valid and invalid
  completion evidence. Give each case a stable ID and an expected canonical
  value, fingerprint relationship, or validation location.
- [x] Freeze all enum spellings, sparse rules, bounds, byte accounting,
  timestamp normalization, commit/path/URL grammars, result matrix, duplicate
  rule, and order semantics from section 3.
- [x] Freeze the 20–32-byte `observed_at` grammar, finite database range,
  microsecond precision, UTC output spelling, and every intentional
  offset/fraction fingerprint equivalence before any generic datetime parser.
- [x] Freeze the distinct arrays-only completion payload and event/checkpoint
  history wrapper, every input/read key, every null/omission rule, and the
  complete canonical examples in sections 3 and 6.
- [x] Freeze the `WorkCompletionCreate` public/
  `WorkCompletionRequest` control-free registry split so stripping the
  operation ID cannot retrigger the conditional transport validator.
- [x] Freeze the evidence-history OpenAPI shapes, page alias/work metadata,
  event-ID ordering, high-water cursor payload/version/bounds, totals,
  current-completion semantics, and error behavior.
- [x] Freeze the conditional client-operation-UUID rule in both runtime
  validation and the exact executable OpenAPI 3.1 `if`/`then`; prove the
  32,768-byte aggregate against the 896 KiB completion and 3 MiB page budgets
  with maximum-escaping cross-product generators.
- [x] Capture pre-change canonical request bytes/fingerprints and stored
  response bodies for every receipt kind from a final 0018 fixture.
- [x] Add negative compile/type fixtures proving extra fields cannot silently
  enter Python, MCP, or TypeScript models.
- [x] Decide all names before DDL. A naming change after migration review is a
  replacement of the unreleased migration, not an alias or compatibility
  column.

The fixture corpus must include Unicode at both character and byte boundaries,
embedded newlines, bidi controls, shell metacharacters, URL percent escapes,
mixed timestamp offsets, lower/upper commit text, dot path components,
duplicates, empty arrays, explicit nulls, and every command/outcome
combination. It contains synthetic values only—never real credentials.

**Gate 1:** backend, MCP, and frontend validators consume the same semantic
evidence cases and agree on every accepted canonical value and every rejection
class. Full-request cases are explicitly surface-tagged: direct REST/OpenAPI
retains the historical optional UUID for absent/empty evidence, while MCP and
browser completion continue requiring it for every request. Draft 2020-12
evaluation of the resolved OpenAPI component agrees on the full REST
empty/non-empty/UUID matrix.

### 10.3 Stage 2 — establish persistence invariants

- [x] Write migration `0019_structured_completion_evidence` only after Gate 0.
- [x] Run completion-checkpoint/event/ID-order preflight before DDL; add the two
  evidence tables, private work/checkpoint/reopen-event generation columns,
  deterministic negative historical/current backfills, checkpoint and reopen
  generation unique indexes, both partial completion-event indexes, symmetric
  work/event reopen guards, locked current-generation completion-event/ID/
  version guard,
  bidirectional checkpoint/done-state guards, the regular pending-exit,
  unsealed-deletion, and sealed-episode departure guards, pending-only child
  guards, immutable row triggers, all four TRUNCATE statement guards, and
  exact downgrade preconditions.
- [x] Extend ORM mappings without adding mutable collection cascades or a
  general-purpose evidence write service.
- [x] Make the insert primitive transaction-private and callable only from
  completion assembly; reads use separate projection functions.
- [x] Extend schema-parity tests and the read-only operational audit.
- [x] Run upgrade/downgrade tests on empty and populated 0018 databases,
  including isolation rejection and a waiting downgrade/concurrent writer,
  then run deliberate corruption/direct-SQL tests under the application role.
- [x] Inspect normalized catalog/function/trigger definitions, not merely
  object existence.

The checkpoint, done-state, child, work-transition, and event-side reopen
deferred guards must be exercised with constraints forced immediate before
commit as well as at ordinary commit. Separately exercise the regular
departure guard after a named constraint was forced immediate and then set
deferred again. Exercise the pending-exit and unsealed-deletion guards
synchronously; they are not constraints and cannot be selectively discharged.
Test an already-done injection; `done -> pending -> checkpoint -> done ->
event` without a retained reopen; an eventless checkpoint followed by each
pending-to-held/terminal exit; a held/terminal-to-done jump; the exact
checkpoint-zero/exit/reopen/checkpoint-one/late-event attack with zero and
non-empty evidence; two completion checkpoints inserted in one pending
generation before either event; missing/wrong/duplicate reopen witnesses;
standalone duplicate/orphan reopen events before and after commit; preinserted
future witnesses; stale-witness reuse after status/version/time resets; manual
counter/binding assignment; a direct insert or transition to done without its
current checkpoint; unchanged, decremented, jumped, or overflowing completion
versions; a second status-preserving version bump between the checked
pending-to-done transition and its event; soft deletion after an eventless
checkpoint followed by clearing `deleted_at`; a retained deletion-event
tombstone whose `deleted_at` value was cleared; aliasing between checkpoint/
children and the done transition, or between done and completion-event
insertion; a child inserted after the done transition but before the event; an
unsupported `done`
departure; out-of-range (including zero, negative, and the reserved terminal
value) or nonmonotonic completion-event identity overrides;
a reset identity sequence; an explicitly high completion ID followed by an
ordinary lower-ID duplicate-merge event; a pending work-version reset followed
by another completion; a done work-version decrement below its sealed event;
an eventless empty completion; both legal child family insert orders; zero
children; one family only; both families; statement and transaction rollback;
row mutation; all four protected table truncates; and failed deferred checks.
There must be no partial checkpoint, evidence, event, receipt, or lease
consumption after failure.

**Gate 2:** persistence alone makes fabricated/duplicate-generation/eventless
episodes, wrong, reset, or event-detached completion-version transitions,
post-state child insertion, unsealed-delete/clear, tombstone-reset, or
alias-before-seal,
orphan/duplicate/reused reopen witnesses,
out-of-range/nonmonotonic completion identities, witness-free reopen
completion, update/delete/TRUNCATE, and append-to-finished-completion
impossible through supported paths, preserves every populated 0018 user-data
byte, and permits a clean READ-COMMITTED pre-use downgrade while refusing every
lossy or indeterminate downgrade before DDL.

### 10.4 Stage 3 — extend the protected completion aggregate

- [x] Add strict domain/API input and read models and the field-local sparse
  serializer.
- [x] Register `complete_work` with the public transport request model and
  separate control-free domain model, following the delivered merge pattern.
- [x] Reject non-empty evidence without `client_operation_id` before receipt
  reservation or domain access.
- [x] Add the evidence-only recursive substring scan for bearer, lease, and
  common operation-UUID spellings before durable receipt reservation, without
  changing the other twelve receipt kinds.
- [x] Extend canonical fingerprinting without changing version 1 behavior for
  an omitted or empty evidence object.
- [x] Enforce the exact aggregate formula and generated compact request,
  fingerprint, response/receipt, and database `jsonb::text` 896 KiB ceilings
  before reservation; test the independent deployed raw-ingress 1 MiB edge.
- [x] Insert and explicitly flush every child row, then explicitly flush the
  pending-to-done work update, before the completion event is added or flushed,
  as required by section 6.2; assert actual SQL order.
- [x] Hydrate the just-created children back from authoritative rows before
  building the receipt response; do not construct a success solely from the
  caller payload.
- [x] Keep completion response matching pure, extend strict stored-response
  validation, and leave physical row correspondence to creation hydration and
  audit.
- [x] Prove rollback with a fault injected after each durable step.
- [x] Prove exact replay under every later lifecycle state before enabling the
  new read endpoint.

No exception handler may translate a failed database invariant into a success
with omitted evidence. Unknown-outcome handling remains the existing durable
receipt response; the application does not retry its own protected write.

**Gate 3:** one new intent yields exactly one checkpoint, ordered evidence
aggregate, state transition, event, receipt, activity change, lease effect,
and live invalidation; exact retry yields none of them again.

### 10.5 Stage 4 — add the bounded history read

- [x] Implement page selection and all page metadata in one SQL statement or
  one explicit read-only repeatable-read transaction so `total`, structured
  total, current pointer, items, and cursor describe one snapshot.
- [x] Select at most `limit + 1` event-backed completion IDs at or below the
  first page's high-water `work_completed.id` before aggregating children.
- [x] Hydrate results and artifacts independently in position order to avoid a
  Cartesian product.
- [x] Reassemble and strictly validate every returned aggregate; corruption
  fails the whole response closed and is reported content-free.
- [x] Bind the bounded versioned cursor to the exact project, work item,
  descending event order, high-water event ID, and last event ID. Cursor
  contents grant no authorization.
- [x] Enforce the 3 MiB serialized API and identity-body reader budget before
  MCP/proxy/browser parsing; request identity and reject non-identity coding
  before any body reader is acquired or pulled.
- [x] Add the REST route, OpenAPI documentation, authorization/visibility
  behavior, cache policy, and safe-read classification.
- [x] Exercise many-page histories across complete/reopen cycles and a
  concurrent new completion.

**Gate 4:** a caller can deterministically enumerate every completion episode,
including empty ones, without unbounded rows, missing child records, duplicated
cross-products, content leakage, or mutation side effects; the API output and
upstream identity-only transport contracts are proven independently.

### 10.6 Stage 5 — update first-party consumers

Implement consumers in this order so the authoritative API contract remains
the source of truth:

1. regenerate or update checked OpenAPI fixtures and contract snapshots;
2. extend MCP strict models and the existing `complete_work` tool;
3. add the single safe `list_completion_evidence` MCP tool;
4. extend the browser proxy policy and completion form;
5. add the lazy Evidence tab, active-only success refetch, leased-work
   boundary, DNS-prefetch header parity, and accessibility/security tests;
6. update the three plugin skills and shared references; and
7. update operator/client notes and version/catalog assertions.

At each consumer boundary, test an old no-evidence response, a new non-empty
response, malformed extra/null/empty forms, maximum-size content, and a page
with mixed empty and populated completion episodes.

**Gate 5:** all shipped first-party clients enforce the same contract and no
client automatically executes, fetches, previews, resolves, or treats evidence
as authority. MCP and Next request identity, every reader rejects non-identity
before body pull, and the built Next/nginx browser path proves identity delivery
under clients advertising compression. Both MCP server transports also apply
the shared 1 MiB pre-dispatch, single-object, and bounded-ID contract. HTTP
returns only its bounded non-echoing rejection, while a rejected stdio record
terminates the real server entrypoint with no response for that record and no
project writer; later buffered records are never dispatched. The maximum typed
MCP page with the maximum permitted ID emits as a complete at-most-12-MiB
JSON-RPC transport payload over both real supported transports without
truncation or representation drift.

### 10.7 Stage 6 — coordinated versioning and release proof

- [x] Set application/API/MCP/dashboard versions to `0.6.0` and the inner
  plugin manifest version to `0.10.0` in one release change; do not invent a
  marketplace version key.
- [x] Assert migration head `0019_structured_completion_evidence` and catalogs
  `28/11/13/11` in executable tests and documentation.
- [x] Run fresh-install, sequential-upgrade, rollback-refusal, restore/replay,
  and full-stack browser/MCP acceptance scenarios.
- [x] Run every repository verification command in section 11.12 with the
  PostgreSQL suites actually enabled.
- [x] Refresh the intentionally local, ignored `CLAUDE.md` operator note when
  present; never add it to version control.
- [x] Review the final diff for accidental compatibility branches, unbounded
  reads, content-bearing logs/events, public evidence writes, and unrelated
  changes.

**Gate 6:** every definition-of-done item is supported by a named automated
test or recorded manual release check and the full required suite is green.

---

## 11. Verification strategy

Phase 11 is complete only when behavior is proven at the schema, service, REST,
receipt, MCP, browser, plugin, migration, and running-stack boundaries. Mocked
unit coverage alone is insufficient, and a PostgreSQL-marked suite that skips
for lack of `TEST_DATABASE_URL` is a failed release rehearsal.

### 11.1 Shared contract fixture

Create a small checked-in JSON corpus consumed directly or mechanically
translated by Python and Node tests. Each fixture states:

```text
case_id
semantic_input
valid | invalid
canonical_output (valid cases)
equivalent_to_case_id (canonical/fingerprint equivalence)
error_path and error_class (invalid cases)
surface_expectations for rest_openapi | mcp | browser (full-request cases)
```

Nested evidence-value cases are language-neutral and must agree everywhere.
Full completion-envelope cases carry the explicit surface expectations above;
the suite must not accidentally treat REST's intentional unkeyed absent/empty
form as valid MCP/browser input. Fingerprint-equivalence assertions apply only
after each surface's own required control fields have been validated and
stripped.

Required valid families include:

- omission, `{}`, and two empty child arrays canonicalizing to omission;
- every valid command/outcome/exit combination;
- every observation outcome with command fields absent;
- every artifact kind at its lower and upper bounds;
- one-family and two-family aggregates with preserved order;
- 20 total entries split `20/0`, `0/20`, `1/19`, and `19/1`;
- multi-byte text exactly below/at character, field-byte, and aggregate-byte
  ceilings;
- exactly 32,768 aggregate bytes combined with the maximum legal
  checkpoint/work envelope and worst-case JSON escaping;
- observation times at the 20/32-byte lexical and year/range boundaries,
  fractions of one and six digits, UTC and `+/-14:00` offsets, and `.1`/
  `.100000`/offset-equivalent pairs canonicalized to the same UTC intent; and
- harmless shell syntax, ANSI-looking text, Markdown-looking text, bidi text,
  and HTTPS paths that remain inert literal content.

Required invalid families include:

- explicit null, present-empty canonical output, 21 entries, and aggregate
  byte overflow at 32,769;
- non-empty evidence without a client operation UUID;
- every missing/extra field and every wrong discriminator;
- passed/nonzero, failed/zero, failed/missing, inconclusive/present exit,
  command/skipped, and observation with either command field;
- blank, NUL, character-overflow, byte-overflow, and signed-32-bit overflow;
- timestamp null/number/coercion, false dates, lowercase `t`/`z`, whitespace,
  leap second, `-00:00`, offset over `14:00`, seven-digit fraction, and UTC
  conversion underflow/overflow;
- uppercase/short/nonhex commits, branch edge whitespace, and malformed path
  components;
- HTTP, relative, credential-bearing, query-bearing, fragment-bearing,
  Unicode-host, whitespace/control-bearing, and overlong external URLs;
- exact duplicate artifact type/reference pairs and noncontiguous positions in
  server responses; and
- operation UUID in each common spelling, bearer value, or supplied lease
  token embedded with prefixes/suffixes at every durable nested string
  location.

The OpenAPI contract suite resolves `WorkCompletionCreate` references and uses
a Draft 2020-12 validator with UUID format checking. Evidence omitted, `{}`,
either omitted/empty array, or both empty arrays is valid with the UUID
omitted, explicitly null under the historical optional control-field contract,
or valid. Results-only, artifacts-only, and mixed non-empty evidence requires
a valid non-null UUID; omission, null, and malformed UUID fail. Explicit
`completion_evidence: null` fails independently. Snapshot and runtime tests
cover every empty spelling with explicit-null UUID so the `then` narrowing is
proven conditional. Snapshot tests require the exact `if`/`then` structure, not
merely descriptive text or examples, and runtime 422 location/precedence tests
must agree.

Errors need not have identical prose across languages, but accepted values,
canonical wire output, and safe field/index locations must agree.

### 11.2 Migration and preservation tests

For both a clean database and the populated final-0018 preservation fixture:

1. corrupt separate disposable 0018 copies with an eventless completion
   checkpoint, a mismatched completion event, a `done` work with no retained
   completion, out-of-range completed/reopened IDs (zero, negative, and the
   reserved terminal value), live completion IDs reversed relative to their
   strictly increasing typed work versions, a live completion ID preceding a
   same-work backfilled completion, and a currently-done work version below
   its selected highest-ID live completion version; prove every upgrade
   preflight refusal occurs before Phase 11 DDL;
2. build the valid fixture both directly from the migration chain and through
   the shipped PostgreSQL 17 custom-format backup/restore path; at 0018, require
   the read-only audit to accept only the two explicit raw whole-survivor
   digests, reject every other change, and reject a same-named Phase 11
   constraint attached to the wrong relation;
3. upgrade both valid 0018 representations to 0019 and compare every
   pre-existing row and exact incoming raw Phase 10 survivor-catalog
   representation, inventorying new custom and RI triggers separately;
4. prove the evidence tables are empty, historical completion generations
   equal the negative exact completion-event IDs, every currently-done work
   has the negative generation of its highest-ID completion, every other work
   generation is zero, other checkpoint generations are null, historical
   reopen bindings equal their negative exact event IDs, other event bindings
   are null, the identity sequence is at least the retained maximum, all four
   temporarily disabled work/checkpoint/event guards are re-enabled with exact
   hashes, and historical completion pages contain empty arrays;
5. inspect all new constraints, indexes, functions, triggers, ownership, and
   privileges;
6. prove downgrade refuses repeatable-read and serializable isolation before
   locks or DDL;
7. downgrade under READ COMMITTED before use and compare the exact restored
   0018 whole catalog;
8. re-upgrade the untouched case, create several evidence-free Phase 11
   reopen/completion cycles, then downgrade again and prove a second
   re-upgrade succeeds, remaps exact negative identities, and preserves every
   ordinary 0018 fact;
9. upgrade again, create evidence through the public completion path, and
   prove downgrade refuses before any DDL;
10. hold a Phase 11 writer before commit, start downgrade so it waits, commit
   the writer, and prove the post-lock check observes the row/receipt and
   refuses;
11. hold the first-party receipt-before-work lock order and, separately, the
    direct child-table-before-work and `work_events`-before-work orders; prove
    the five-second timeout aborts the full downgrade without DDL and a fresh
    quiescent retry is deterministic;
12. prove a completed `complete_work` receipt with any top-level
    `completion_evidence` key independently blocks downgrade, including empty,
    null, and malformed values;
13. prove an indeterminate catalog or receipt state refuses rather than
    guessing;
14. dump and SQL-reparse the Phase 11 vocabulary checks and prove their raw
    definitions and exact constraint digest stay unchanged; and
15. take an ACL-preserving custom archive, restore it under the fixed
    application role, prove every Phase 11 function still denies `PUBLIC
    EXECUTE`, prove both evidence relations have effective owner-only
    privileges whether `relacl` is explicit or default-equivalent null, and
    prove permanent receipt replay remains exact.

Run downgrade/re-upgrade after no-evidence Phase 11 reopen/completion cycles.
The re-upgrade must deterministically remap all then-retained completion and
reopen events into their exact negative namespaces, map each then-`done` work
to the negative generation of its highest-ID completion, map every other work
to zero, and avoid inferring a historical reopen/completion pairing.

Also run upgrade from every repository-supported historical migration path to
head. No migration may read narrative fields to synthesize evidence or print
their values in diagnostics.

### 11.3 Database invariant and direct-SQL tests

Under the same role used by the application, reject:

- a new completion checkpoint on already-`done`, deferred, terminal, deleted,
  or duplicate work, with or without evidence children;
- an eventless completion checkpoint at forced deferred-check time or commit;
- a completion checkpoint whose retained event is missing, wrong-type,
  cross-work, or duplicated; a current-generation checkpoint whose final work
  state is not `done`; and a prior-generation checkpoint that did not pass a
  complete departure validation;
- an eventless generation-zero or later completion checkpoint followed by
  `pending -> deferred`, `pending -> wont-do`, or `pending -> promoted`, with
  zero evidence and with non-empty evidence;
- `deferred|wont-do|promoted -> done`, and a `pending -> done` transition with
  no exact current-generation still-eventless completion checkpoint, an
  unchanged/decremented/jumped version, integer overflow, or a non-live/
  noncanonical `OLD` or `NEW` image;
- the full eventless-C0, pending-exit, valid reopen, C1 completion, late-C0
  completion-event transaction identified in adversarial review; the first
  illegal exit must fail synchronously and roll back every variant;
- `done -> pending -> completion checkpoint -> done -> work_completed` in one
  transaction without the exact retained `work_reopened` witness;
- two completion checkpoints inserted while one work is pending, before either
  event exists, through both separate and multi-row inserts;
- a missing, duplicate, wrong-status, wrong-version, wrong-time, wrong-project,
  non-live, wrong-keyed-generation, or shape-only reopen witness;
- a `work_reopened` event inserted before its future transition, after a valid
  transition has committed, twice for one generation, with a caller-supplied
  binding, or by trying to reuse an old bound event after resetting status,
  version, or timestamp fields;
- direct assignment, decrement, reuse, rollback, or overflow of the database-
  managed work/checkpoint/reopen-event generation;
- a `work_completed` event whose explicit `OVERRIDING SYSTEM VALUE` ID is zero,
  negative, the reserved bigint terminal value, equal to, or lower than the
  highest same-work completion ID, including a positive unused lower gap, or
  whose checkpoint generation is not the work's exact current generation; also
  reject a typed version not strictly above the latest prior live completion
  or its exact positive-generation reopen;
- a status-preserving work-version reset while pending followed by a nominally
  valid next completion, and a decrement below the sealed completion version
  while done; prove event insertion or the queued done-state guard rejects and
  that no eligible downgrade can create a database its next re-upgrade refuses;
- a valid checked `pending(v) -> done(v+1)` transition followed by a
  status-preserving bump to `v+2` before the completion event; prove the
  transition-captured done-state check rejects an event at `v+2`, while a
  separately exercised ordinary done edit after the `v+1` event remains legal;
- an ordinary completion after resetting the owned identity sequence below the
  same-work maximum, followed by operator reseed and a successful fresh retry;
- cross-project and cross-work checkpoint attachment;
- attachment to context, progress, handoff, decision, or legacy checkpoint
  kinds;
- insertion for soft-deleted work or a duplicate alias;
- checkpoint/children while live followed by soft deletion before
  pending-to-done or after done but before `work_completed`, including a later
  same-transaction clear of `deleted_at`; the regular unsealed-deletion guard
  must reject the first delete and roll back the full transaction;
- checkpoint/children followed by authoritative aliasing before
  pending-to-done, and the corresponding transition after done but before
  `work_completed`; the earliest remaining seal guard must fail and roll back
  the full transaction;
- soft deletion with a valid immutable `work_deleted` event followed by a
  direct reset of `deleted_at`, then an attempted checkpoint, child,
  pending-to-done transition, or completion event; the earliest Phase 11 live
  predicate must still reject the tombstoned work;
- insertion before a completion transaction that never reaches done/event;
- insertion after the work has transitioned to done but before its completion
  event is staged, including an otherwise valid same-transaction aggregate;
- insertion after its `work_completed` event is already retained;
- a timestamp different from the checkpoint;
- noncontiguous, duplicate, negative, or out-of-range positions;
- over-count or over-byte aggregates split across the two tables;
- invalid result matrices and artifact grammars;
- duplicate artifact pairs in one completion transaction, including one
  multi-row statement; and
- every update or delete, including changes that would otherwise remain valid;
  and
- `TRUNCATE` of either evidence table, `work_events`, or `client_operations`,
  including a cascading truncate initiated from a referenced parent table.

Prove transactions that construct each legal aggregate commit, and prove the
exact same statements in a later transaction fail. Force deferred checks
early to ensure failures are deterministic and fully rolled back. Catalog tests
also prove the application role sees all row and statement triggers; deliberate
owner DDL remains outside this DML threat model.

Record emitted SQL order and prove checkpoint and both evidence families flush
before the pending-to-done work update, which itself flushes before the
`work_completed` insert, with the event version equal to the captured
transition version. Exercise valid initial/cutover generation zero, valid
reopen/recompletion generations one and two across separate completion
transactions, multiple genuine reopen transitions without an intermediate
completion checkpoint in one direct transaction, and multiple fully sealed
completion/reopen cycles in one direct transaction. Explicitly force
`completion_checkpoint_episode_guard` and `completion_state_episode_guard`
immediate after the first sealed episode, set them deferred, and prove a valid
second cycle commits while any missing event, malformed aggregate, or
unsupported departure fails synchronously and leaves neither episode partial.
Also cover repeated deferred/wont-do/promoted
reopens, ordinary intervening version changes, direct concurrent completion-
event inserts serialized by the work lock, rollback without a partial
aggregate, and downgrade/re-upgrade reconstruction as well as every rejection
above. Assert after each positive retained work generation `N` that positive reopen
bindings are the unique complete prefix `1..N`. After one transaction creates
sealed episodes at generations zero, one, and two, run the page assembler and
global audit before rollback: all three episodes must pass, only generation
two is current while done, and the first two satisfy strict generation/event
ordering.

In a disposable owner-corruption fixture only, disable the pending-exit guard,
construct the late-C0 attack, restore the exact guard catalog, and prove both
public read assembly and the aggregate audit reject the completion-version/
successor-reopen chronology. This detection test does not make trigger-disable
DDL a supported mutation.

### 11.4 Backend model and REST tests

Test strict parsing and canonical serialization for every shared fixture, then
cover:

- completion with omitted, explicit-empty, results-only, artifacts-only, and
  mixed evidence;
- non-empty evidence without `client_operation_id` returns structural 422
  before any receipt or domain write, while absent evidence preserves the
  historical unkeyed direct-REST completion;
- a request that both lacks that UUID and embeds a known bearer returns the
  same sanitized structural 422 without running either secret scanner;
- keyed non-empty evidence survives receipt-layer control stripping and domain
  revalidation; a regression that reuses the conditional transport model as
  the registry domain model fails;
- response IDs, parent IDs, positions, timestamp equality, and exact order;
- strict timestamp pre-parsing/canonicalization plus real PostgreSQL finite
  lower/upper range, microsecond round-trip, infinity rejection, and behavior
  under a non-UTC database session timezone;
- status/version/blocker/gate/lease/alias precedence with nested evidence;
- no additional work-version bump, activity touch, lease operation, event, or
  live-sync frame caused by evidence;
- one normal completion notification only after commit;
- correct concealment for wrong-project and unauthorized reads;
- exact source-alias history with `is_duplicate=true`, canonical destination,
  and null current pointer, without canonical blending;
- soft-delete behavior, operator-audit retention, and a null current pointer
  for a retained deletion-event tombstone even after a disposable direct-SQL
  fixture clears `deleted_at`;
- `limit` defaults/bounds, unknown query rejection, 1/4,096-character cursor
  bounds, decoded 2,048-byte bound, invalid/scope-mismatched cursors,
  final-page null cursor, and zero-completion pages;
- migrated negative current pointer while done, runtime generation-zero/one/
  two pointer movement after each completion, and null after each reopen;
- an ordinary title/summary/priority update while done increments work
  version/time but preserves generation and the exact current pointer;
- newest-first order and current selection by monotonic completion event ID
  despite tied or backward-moving checkpoint timestamps; and
- fail-closed behavior for deliberately malformed stored rows or response
  assemblies without echoing content.

The OpenAPI snapshot must expose the strict discriminated union and read-only
list route plus the executable conditional operation-UUID schema, while
proving there is no standalone evidence POST/PATCH/DELETE.

### 11.5 Receipt and fault-injection matrix

For `complete_work`, inject a recoverable failure:

- after receipt reservation;
- after checkpoint flush;
- after the first result;
- between result and artifact families;
- after the explicit evidence-child flush but before event staging;
- after work-state/version change;
- after lease consumption;
- after completion-event staging;
- during response hydration/validation;
- after receipt completion but before commit; and
- after commit but before the response reaches the caller.

Before commit, assert that every aggregate component rolls back together and
the receipt follows the existing retry/reconciliation rules. After commit,
assert that exact retry returns the stored response and creates no second row,
event, lifecycle change, lease effect, activity change, or notification.

Cross product replay with:

- process restart;
- replica/application restart where supported;
- later context checkpoints;
- reopen;
- a second completion episode;
- authoritative duplicate merge;
- soft deletion/operator recovery; and
- database backup/restore.

For each of the other twelve receipt kinds, freeze and compare old canonical
request fingerprints and response bodies. Phase 11 must not change a byte or
validation outcome outside the extended completion kind.

### 11.6 Concurrency tests

Use real independent PostgreSQL transactions and bounded timeouts:

| Race | Required outcome |
| --- | --- |
| Same operation UUID, same evidence | One commit; every caller receives the same stored response. |
| Same operation UUID, changed evidence/order | One intent wins; the other receives conflict and writes nothing. |
| Different UUIDs, same expected version | One completion wins; the other fails existing status/version checks. |
| Completion versus reopen | Work-row serialization yields one legal lifecycle order; no evidence is attached to the wrong episode. |
| Completion versus duplicate merge | Either completion precedes merge and remains source-owned, or merge wins and completion is rejected. |
| High-ID completion versus later duplicate merge | A valid explicitly high completion ID followed by ordinary lower-ID merge events remains a legal completion-before-merge history; reviewed source counts and guards pass without comparing IDs across event types. |
| Completion versus gate/blocker change | Existing lock/order semantics decide; evidence never bypasses the winning constraint. |
| Completion versus soft delete | Work-row serialization yields either a live completion before a later delete or a delete that makes completion fail; delete/clear between checkpoint, state, and event cannot evade the regular unsealed-deletion guard. |
| Evidence page versus new completion | Every traversal returns exactly its cursor high-water set; a fresh-head comparison either establishes stable current completeness or reports continuing change. |
| Downgrade versus evidence writer | Downgrade waits, observes the committed row/receipt under READ COMMITTED, and refuses before DDL. |
| Direct child-target lock versus downgrade | The bounded lock timeout aborts the whole downgrade before DDL; after the direct transaction ends, a fresh quiescent attempt is deterministic. |
| Direct event-target lock versus downgrade | The same bounded whole-transaction refusal covers the event-before-work lock inversion introduced by the Phase 11 binder. |

Record lock-wait assertions so a test cannot pass merely by hanging until the
suite timeout.

### 11.7 Pagination and query-shape tests

Seed at least 25 completion episodes with alternating empty, result-only,
artifact-only, and mixed evidence. Assert:

- limits 1, default 10, and maximum 10 traverse without gaps or duplicates;
- totals remain totals, not page lengths;
- structured total counts episodes, not child rows;
- child ordering is stable and independent between families;
- page order, boundary, and current selection use `work_completed.id`, never
  wall time or UUID;
- event identities at `2^53-1`, `2^53`, and the valid completion maximum
  `9223372036854775806` round-trip as canonical decimal strings without
  JavaScript precision loss; the terminal bigint value is rejected as the
  explicit fail-closed exhaustion sentinel, and nearing the ceiling raises an
  operator alert requiring fix-forward identity widening/rekeying rather than
  reseeding below retained history;
- every continuation preserves the first page's high-water event ID and
  returns only event IDs at or below it, even when a newer completion commits;
- a cursor cannot be reused for another project/work/order/version and rejects
  extra/missing keys, noncanonical/overflow/nonexistent event strings,
  booleans, overlong text, or oversized decoded envelopes;
- syntactically valid substitution of another retained in-scope event can
  select a valid subset because V1 cursors are unsigned; only the exact
  unchanged server-issued chain supports a completeness claim, and client/
  plugin documentation never blesses a modified cursor;
- item selection is bounded before child hydration;
- query count is constant per page and result/artifact joins do not multiply;
- a ten-episode maximum-escaping page stays at or below 3 MiB UTF-8 JSON and
  one byte over the reader ceiling fails content-free before parsing; and
- EXPLAIN-based regression coverage uses
  `ix_work_events_completion_evidence_history`,
  `ix_work_events_live_completion_version_order`, and the child indexes
  without an unbounded checkpoint/content scan.

### 11.8 MCP tests

Update catalog, schema, dispatcher, transport, and adversarial-response tests
to prove:

- exactly 28 unique tools and exactly 11 protected writes;
- `complete_work` makes one outbound POST and freezes the full nested intent;
- exact retry guidance retains evidence and operation UUID;
- `list_completion_evidence` makes only the expected safe GET and follows the
  limit/cursor contract;
- raw guards reject null/empty/extra/matrix/position/parent/totals/cursor
  incoherence before normalized models can hide it;
- the GET sends `Accept-Encoding: identity`; rejects `gzip`, `br`, `deflate`,
  coding lists, and malformed `Content-Encoding` on success and error statuses
  before pulling a poison body; and accepts absent or mixed-case single
  `identity`;
- the raw identity iterator enforces exact 3 MiB, max-plus-one, oversized
  single-chunk, cross-chunk, absent/malformed/negative/dishonest
  `Content-Length`, chunked, cancellation, and fatal-UTF-8 behavior before
  JSON parsing without reflecting content;
- the server-side Streamable HTTP guard accepts exactly 1 MiB and rejects byte
  1 MiB + 1 before SDK parsing/dispatch across one chunk and many chunks,
  distrusts absent/malformed/dishonest `Content-Length`, rejects non-identity
  coding before its poison body is pulled, and returns only a bounded
  content-free error;
- the project-owned stdio adapter accepts a one-record 1 MiB binary payload
  excluding LF, rejects the next byte without an unbounded `readline()`, and
  closes on an over-limit, invalid-UTF-8, invalid-JSON, scalar/array/batch, or
  invalid-ID record without dispatch, reflection, or a response for that
  record; valid object-shaped messages are converted with the locked
  `JSONRPCMessage.model_validate` boundary, its semantic-validation exception
  is passed through the SDK read stream exactly like the standard transport,
  and multiple entirely valid records preserve installed-SDK output
  serialization;
- shared pre-dispatch tests accept ID-less notifications, signed-64-bit
  endpoints, and 1/128-character allowed ASCII IDs; reject integer overflow,
  booleans, floats, null, empty/129-character strings, and every disallowed
  character before dispatch; and prove HTTP errors never echo the rejected
  value while stdio emits no error record;
- byte-exact adapter tests and a bounded subprocess running the real packaged
  stdio entrypoint prove every rejection class reaches EOF/process termination
  without a response for the rejected record. A first invalid record produces
  zero stdout; an invalid-then-valid pair already present in one input buffer
  never dispatches the later record; and a violation following or racing valid
  requests contributes no output bytes or project-serialized control frame.
  Whole SDK frames completed before shutdown remain parseable; a cut in-flight
  SDK frame is accepted only as EOF/malformed unknown outcome, never as
  success, and a protected write is recovered only through its frozen
  operation UUID;
- the largest generated legal MCP `complete_work` request, including its
  JSON-RPC/tool-call wrapper, remains within the 1 MiB ingress bound;
- the maximum legal page is passed through the locked FastMCP result converter
  using the 128-character maximum request ID, and a complete JSON-RPC success
  response, including both JSON text `content` and object
  `structuredContent`, is at most 12,582,912 UTF-8 bytes; direct invocation
  and real Streamable HTTP preserve the same complete page, the real stdio
  record including LF remains within the same limit, and an SDK-change fixture
  detects representation or private-runner-seam drift;
- hostile strings are returned as quoted data, never executed or followed;
- no evidence enters resources, bounded context, search, or prompts by
  accident; and
- all 27 pre-existing tool schemas and annotations remain unchanged except the
  intentional `complete_work` extension.

### 11.9 Frontend and proxy tests

Component and integration tests cover:

- keyboard-accessible add/remove/reorder controls and exact row errors;
- discriminator-driven fields and exit-code matrix behavior;
- character, UTF-8 byte, aggregate-byte, and total-entry counters;
- raw completion JSON with transport-only padding/escapes at exactly the
  deployed 1 MiB proxy/nginx boundary and rejection of the next byte before
  parsing, including absent or misleading `Content-Length`;
- evidence omission when the editor is empty;
- unknown-outcome freeze/retry and new-intent handling after edits;
- no optimistic IDs, current pointer, evidence page, or extra live frame;
- after completion, an active Evidence tab refetches while an inactive tab
  only invalidates and emits no evidence GET;
- completion remains unavailable for leased work and the proxy continues to
  reject `lease_token`;
- the sixth tab's lazy-load, empty, mixed, error, retry, pagination, reopened,
  current, prior, and alias states;
- text-node rendering of HTML/Markdown/ANSI/bidi/control-looking strings;
- inert command/commit/branch/path presentation;
- HTTPS anchor attributes and the absence of previews, prefetches, proxy
  fetches, favicons, DNS-prefetch hints, or automatic requests;
- exact `X-DNS-Prefetch-Control: off` parity in Next.js and nginx responses;
- outbound `Accept-Encoding: identity` from the Next.js upstream request;
- rejection of `gzip`, `br`, `deflate`, coding lists, and malformed
  `Content-Encoding` on both success and error statuses before acquiring or
  pulling a body reader; a poison first-pull source proves zero body pulls;
- rejection of browser-visible non-identity coding before `getReader()` or
  `response.json()`, plus a deployed-stack request advertising gzip/br that
  still receives absent/identity `Content-Encoding`;
- rejection of identity evidence responses above the 3 MiB ceiling before JSON
  parsing, including exact-limit/max-plus-one, one oversized chunk,
  cross-chunk, absent, malformed, negative, dishonest, and chunked
  `Content-Length` cases at proxy and API-client readers, plus mixed-case
  single-`identity` acceptance, fatal invalid-UTF-8 rejection, and
  cancellation without content reflection; and
- proxy acceptance of only the new GET and nested existing completion body,
  with all unsupported verbs/routes/query/body keys rejected.

Playwright acceptance uses request interception or a controlled test origin to
prove merely opening the Evidence tab makes no HTTP request to a stored
external URL. Header-parity assertions, not interception alone, establish the
DNS-prefetch policy.

### 11.10 Plugin tests

Static and installed tests prove:

- exactly three skills remain;
- the new reference is packaged once and linked with portable root-relative
  paths;
- fresh `0.10.0` and sequential `0.9.0 -> 0.10.0` installs are identical;
- no obsolete file, permission, helper version, or executable mode changes;
- skill examples use the actual 28-tool schemas and distinguish recorded
  evidence from verification authority;
- unknown completion outcomes preserve the exact nested intent;
- recall explicitly lists evidence when needed but normal recall remains
  bounded; and
- no instruction tells an agent to execute a returned command, follow a URL,
  infer evidence from Phase 10, or complete despite an unaccepted material
  failure.

### 11.11 End-to-end acceptance scenarios

Run at least these stories through real REST, MCP, database, and browser layers:

1. **Historical empty episode:** migrate a 0018 completion; REST, MCP, and UI
   show an honest empty episode while its old receipt replays exactly.
2. **Command-backed completion:** complete pending work with one passing
   command and one commit reference; all layers preserve exact order and
   current-episode identity.
3. **Mixed limitation:** with explicit user acceptance, complete with a passed
   check and an inconclusive observation; UI language remains caller-reported
   and no automatic policy change occurs.
4. **Unknown outcome:** drop the response after commit; exact retry returns the
   same checkpoint/evidence IDs and only one event/notification exists.
5. **Reopen and recomplete:** the old evidence becomes prior history, current
   pointer becomes null during pending, and a new completion gets distinct
   evidence without copying rows.
6. **Duplicate merge:** complete a source, merge it authoritatively, and prove
   its evidence stays on the exact alias while destination history is separate.
7. **Malicious content:** submit bounded HTML, shell, bidi, and URL-looking
   strings; all displays are inert, logs remain content-free, and no network
   request occurs.
8. **Maximum aggregate:** combine maximum legal pre-existing checkpoint/work
   fields with exactly 20 entries and 32,768 charged evidence bytes; prove the
   designated compact request serialization, canonical fingerprint envelope,
   exact API/receipt response, and PostgreSQL `jsonb::text` are each at most
   896 KiB. Separately prove raw deployed REST/browser ingress accepts exactly
   1 MiB and rejects byte 1 MiB + 1 before JSON parsing. Reject one evidence
   byte or entry more, and prove ten maximum history episodes fit the separate
   3 MiB budget.
9. **Secret echo:** place each request-known secret in every nested string
   location as an exact value and with prefix/suffix text, including common
   operation-UUID spellings; reject before durable storage without reflecting
   the value.
10. **Recovery boundary:** downgrade succeeds before first use, refuses after
    evidence or an evidence-bearing receipt, and a full snapshot restore
    recovers exact replay.
11. **Identity transport:** controlled success and error upstreams prove MCP
    and Next reject every non-identity coding before first body pull, identity
    max-plus-one readers fail closed, and the deployed browser route stays
    identity-coded even when the browser advertises gzip/br.
12. **MCP result envelope:** the maximum valid ten-episode page travels through
    the installed FastMCP converter and real Streamable HTTP and stdio
    transports with both standard result representations intact and the
    maximum legal 128-character request ID; the full Streamable HTTP body and
    stdio record including LF are each at most 12 MiB and the client receives
    an exact, untruncated page.
13. **MCP ingress and echoed ID:** both real transports accept an exact-1-MiB
    valid record/entity and reject the next byte before SDK dispatch; HTTP
    rejects encoded poison bodies before pulling them and returns its bounded
    non-echoing error; the real stdio process reaches bounded EOF with no
    response for size, UTF-8, JSON, top-level, and ID violations, including an
    invalid-then-valid buffered pair and valid/in-flight races. The validation
    predicate is identical across transports; the HTTP error and terminal
    stdio no-response wire outcomes are intentionally different.

### 11.12 Required verification commands

Run the repository-standard commands from a clean Phase 11 linked worktree:

```sh
docker compose -f compose.test.yaml up -d --wait
export TEST_DATABASE_URL=\
'postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test'

cd backend
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src

cd ../mcp
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp

cd ../frontend
npm ci --no-audit --no-fund
npm test
npm run typecheck
npm run build
npm run test:e2e:stack

cd ..
./scripts/test-nginx-e2e.sh
pre-commit run --all-files
```

Also run the updated read-only aggregate audit from a private environment with
database access at head 0019, following its documented command. Record exact
test counts and confirm PostgreSQL tests did not skip. The dedicated nginx
runner must use `compose.e2e.yaml` plus `compose.nginx-e2e.yaml`, route browser
traffic through a disposable nginx edge that includes the exact production
dashboard-API policy snippet, and exercise normal identity success plus
encoded success and encoded error responses from a controlled test-only
upstream. It must first syntax-check that snippet on stock nginx without the
optional Brotli module, then load and globally enable google/ngx_brotli in the
disposable edge, prove a JSON control response is `br`, and prove the evidence
route remains exactly `identity`. Unit poison-stream tests remain the proof of
no body-reader pull. A green direct-Next-only browser suite is not nginx
release evidence.

---

## 12. Deployment, recovery, and operational audit

### 12.1 Pre-deployment

1. Confirm Phase 10 is already deployed and all running processes report the
   exact `0.5.0`/`0.9.0`/`0018` boundary.
2. Stop or drain older first-party writers and establish a maintenance window
   with no direct work/checkpoint/event DML or event-sequence use before the
   0019 exclusive-lock/generation backfill and identity-sequence normalization.
   Do not run mixed 0.5.x and 0.6.x processes once evidence-bearing
   completions can be accepted.
3. Take and verify a complete database backup with receipt tables, salts,
   digests, events, checkpoints, Phase 10 scopes, migration metadata, and
   archived ACL commands for public-schema application objects. The shipped
   scripts omit/rebind ownership but intentionally retain and replay those
   object ACLs; never add `--no-acl` to a shipped archive.
4. Export only aggregate catalog/count checks to the deployment record; do not
   copy evidence-capable content into logs or tickets.
5. Run the 0018 preflight audit, including exact completion-checkpoint/event
   correspondence, bounded positive completed/reopened event IDs, retained
   live completion work-version/ID compatibility, backfill-before-live order,
   global identity headroom, and a whole-survivor catalog digest matching
   exactly the migration-built or shipped-backup-restored PostgreSQL 17 form;
   resolve any invariant failure before DDL.
6. Inspect the built Next.js and nginx configuration that will serve the
   dashboard API path. Prove content coding is disabled there and that no
   intermediary rule can silently re-enable gzip/brotli for evidence history.

### 12.2 Coordinated rollout

1. Apply migration 0019 and verify its exact catalog/hash inventory, negative
   historical completion/reopen mappings, positive sequence head, and trigger
   enabled state while both evidence tables are empty.
2. Deploy the 0.6.0 backend and MCP adapter, 0.6.0 dashboard, and 0.10.0 plugin
   as one declared compatibility boundary.
3. Verify version, migration-head, OpenAPI, MCP `28/11`, REST receipt `13`, and
   browser mutation `11` assertions from the running artifacts.
4. Verify the 896 KiB completion, 3 MiB identity-coded history, 1 MiB bounded
   MCP ingress/ID contract, 12 MiB maximum-ID MCP response payload,
   `Content-Encoding`/compression policy, and `X-DNS-Prefetch-Control: off`
   serving-header assertions from the built artifacts.
5. Smoke-test a no-evidence completion, an evidence-bearing completion and
   replay, an evidence history read, reopen/recomplete history, and safe UI
   rendering in a disposable project.
6. Run the read-only head-0019 audit and retain only content-free results.
7. Re-enable normal writers only after every first-party component and
   operator/client instruction is at the Phase 11 boundary.

The migration is additive but the runtime contract is coordinated: the first
non-empty completion response is intentionally not promised to strict 0.5.x
clients. This is not mitigated by a server downgrade projection or dual-write
mode.

### 12.3 Recovery matrix

| Point of failure | Permitted recovery |
| --- | --- |
| Before migration | Keep 0.5.x running; make no Phase 11 write. |
| After migration, before any evidence row or `completion_evidence` response key | Stop 0.6.x and direct DML, use a fresh READ COMMITTED transaction, acquire every exact lock and prove absence, downgrade to exact 0018, then restore 0.5.x. |
| During one completion transaction | Let PostgreSQL roll back; use the existing receipt reconciliation and exact-retry procedure. |
| After commit but response unknown | Do not repeat with a new UUID; retry exact frozen intent/UUID until receipt recovery resolves it. |
| Event identity approaches the reserved completion/reopen ceiling | Stop new lifecycle writes before exhaustion and fix forward with a reviewed identity widening/rekeying migration. Do not reseed below retained history or reuse an ID. |
| After any Phase 11 evidence use | Fix forward with 0.6.x-compatible code; do not downgrade or run 0.5.x binaries. |
| Catastrophic corruption | Restore the complete verified pre-use snapshot and matching binaries, accepting loss of every later write, or repair forward under a reviewed procedure. |

Never delete evidence rows, edit receipt JSON, disable immutable triggers, or
manually mark a pending operation completed as a rollback shortcut.

### 12.4 Read-only operational audit

Extend the aggregate audit rather than create a content-dumping diagnostic.
It reports:

- migration/catalog/version/inventory status, including the exact Phase 10
  survivor digest at both supported heads and exact exclusion of all Phase 11
  object identities;
- effective owner-only evidence-relation privileges and exact owner-only
  execution privileges on every Phase 11 function, with no `PUBLIC EXECUTE`;
- completion-episode/result/artifact counts;
- empty versus structured episode counts;
- completion-checkpoint/event disagreement and immutable-merge reviewed
  source-count/evidence-correspondence violations;
- exact migrated done-work negative current generations, other-work zero
  generations, runtime nonnegative checkpoint/work ordering, current/prior
  equality/inequality, generation uniqueness, current-generation
  pending/held-checkpoint violations, late completion-event bindings,
  completion-to-successor-reopen and current-completion/work-version
  inversions, sealed done-state/departure violations, exact negative legacy
  reopen bindings,
  positive reopen-prefix gaps/duplicates, event-side orphan bindings,
  out-of-range/nonmonotonic completion IDs, identity-sequence lag/exhaustion,
  and reopen-witness violation counts;
- global evaluation of the exact legacy-live/backfill and nonnegative-
  generation completion-order predicates from section 5.9;
- exact enabled-state, trigger-definition, and underlying normalized function-
  body hashes for every Phase 11 lifecycle-generation, done-state,
  pending-exit, unsealed-deletion, non-deferrable departure, reopen-binding,
  current-generation completion-event/ID, completion-checkpoint,
  evidence-aggregate, row-immutability, and
  evidence/event/receipt TRUNCATE guard;
- invariant-violation counts by safe category;
- receipt-to-row mismatch counts;
- conditional-downgrade eligibility; and
- IDs only for records requiring private follow-up.

It redacts all prompts, commands, summaries, labels, references, source
sessions/models, tokens, receipt bodies, and URL hosts. Its SQL is read-only,
uses bounded batches, documents its snapshot semantics, and exits nonzero on
any invariant or incomplete scan. Checkpoint-global receipt/row correspondence
uses `ix_verification_results_completion_checkpoint_id_id`,
`ix_artifact_references_completion_checkpoint_id_id`, and the unique partial
expression index `ix_client_operations_completion_receipt_correspondence` on
receipt checkpoint/work IDs. A large unrelated-project fixture plus `EXPLAIN`
must prove those named probes remain in the reverse-correspondence plan; a
textual `LIMIT 2` alone is not bounded-access evidence.

---

## 13. Security, privacy, observability, and resource controls

### 13.1 Trust and injection boundary

Evidence is untrusted data at every read. Stored command text is never passed
to a shell, subprocess, terminal integration, syntax runner, or “rerun” button.
Labels and summaries are never interpreted as Markdown/HTML. Artifact URLs are
never fetched by the backend, MCP adapter, proxy, service worker, preview
component, or plugin; the only navigation is an explicit human click on a
validated external HTTPS anchor.

MCP descriptions and plugin prose must resist indirect prompt injection:
returned evidence is quoted historical content, not instructions. An agent may
choose to run a command only from current user/work authority and its own safe
plan, never because the command appeared in evidence.

### 13.2 Secret and sensitive-data handling

The existing exact-value request-known scanner remains unchanged for prior
receipt kinds. The new completion-evidence-specific traversal from section 6.5
checks every evidence string for embedded current bearer and supplied lease
values and common operation-UUID spellings before receipt reservation or
storage. It is substring-aware rather than whole-scalar-only. Error and
validation paths never echo the matched value.

Phase 11 does not claim general secret detection. UI, MCP, and plugin guidance
therefore prohibit raw logs, environment dumps, credentials, signed URLs, API
keys, cookies, or private reasoning. Summaries should contain the minimum
durable result necessary. If a previously unknown secret is nevertheless
stored, rotate/revoke it immediately and follow the established immutable-data
incident/restore procedure; do not invent a hidden evidence delete path.

### 13.3 Browser and URL safety

Use one audited semantic URL contract across API and frontend fixture
implementations. Reject controls, whitespace, credentials, query strings,
fragments, non-HTTPS schemes, non-ASCII forms, and ambiguous parser results.
Bracketed IPv6 literals must use their exact lowercase canonical compressed
spelling; a non-default port remains outside the brackets. Preserve the
accepted string exactly. Because no server fetch occurs, the locator creates
no SSRF path. Explicit navigation, `noreferrer`, the absence
of preview/prefetch code, and exact `X-DNS-Prefetch-Control: off` parity across
Next.js and nginx ensure that merely viewing evidence initiates no
first-party-controlled contact with the locator.

Text containers use wrapping, directional isolation, and accessible labels so
maliciously long or bidirectional strings cannot visually merge fields or hide
the reported outcome. Outcome meaning is conveyed by text and not color alone.

### 13.4 Logging and metrics

Application, MCP, browser telemetry, audit output, and exception reports must
not log request/response bodies or caller strings. Safe observability may
include:

- operation/route name, status class, latency, and safe error code;
- total result/artifact counts and aggregate bytes;
- enum-only verification type/outcome counts;
- page size, has-next-page boolean, and query timing; and
- migration/audit invariant counts.

Do not label metrics or traces with work/checkpoint/receipt IDs, project names,
commands, summaries, labels, paths, commits, URLs, hostnames, session IDs, or
unbounded error text. Existing data-free live invalidation is the only
post-completion broadcast.

### 13.5 Resource limits and query budgets

The maximum write has 20 child rows and 32,768 bytes of caller strings in
addition to the already bounded completion checkpoint. Enforce the same exact
aggregate formula before receipt storage, at the database, at MCP input, and in
the browser. Do not rely only on overall HTTP body limits.

A generated cross-product fixture fills every existing checkpoint/work field
and every new evidence field with its maximum legal, worst-escaping content,
including the 32-byte maximum accepted timestamp spelling. It records four
distinct measurements: the designated compact UTF-8 request serialization,
the normalized version-1 compact fingerprint envelope, the exact compact API/
durable-receipt response, and PostgreSQL
`octet_length(response_body::text)`. Each measurement must be at most 917,504
bytes (896 KiB). PostgreSQL `jsonb::text` is measured explicitly rather than
called compact.

The 896 KiB budget is a generated-representation invariant, not the raw HTTP
entity limit. Raw deployed REST/browser ingress is independently accepted
through exactly 1,048,576 bytes by the browser proxy/nginx and rejects byte
1,048,577 before JSON parsing; padding or noncanonical JSON escapes can make a
raw request exceed 896 KiB without changing its normalized intent. `/mcp` and
a directly exposed backend have different existing transport boundaries and
must not be cited as proof of that deployed REST limit. If any generated
measurement disproves the 896 KiB bound, stop and lower the unreleased evidence
limit through plan review; a canonical legal request may not fail later as
edge `413` or `client_operation_unavailable`.

The history endpoint returns at most 10 completion episodes. Its implementation
must:

- page event-backed completion identities before fetching checkpoints or
  children;
- issue a constant bounded number of queries;
- never join results directly to artifacts in one multiplying rowset;
- select only projection columns, excluding checkpoint prompts and scopes;
- serialize to at most 3,145,728 UTF-8 JSON bytes at the API; require identity
  coding at every controlled HTTP hop; reject non-identity coding before body
  consumption; and enforce the same pre-parse ceiling in MCP, the Next.js
  proxy, and the browser API reader with raw identity streaming max-plus-one
  algorithms that do not trust `Content-Length`; and
- reject corrupt over-bound data rather than truncate it into a plausible
  response.

A separate maximum page fixture uses ten episodes, 327,680 evidence bytes in
total, worst-case escaping, maximum checkpoint-pointer fields, and maximum
envelopes to prove that every legal page fits the 3 MiB ceiling. The bound
covers bytes Mnemonic copies, accumulates, and parses. It does not claim zero
allocation inside the operating system, TLS stack, HTTP runtime, browser, or
one runtime-owned raw input chunk, and it accepts no compressed history
response. Oversized-chunk tests assert the accumulator bound and immediate
cancellation, not an impossible bound on the Fetch implementation's own
allocation.

The same fixture must then cross the separate MCP egress boundary. Pass the
validated page through the locked FastMCP typed-result converter and serialize
the complete compact JSON-RPC success response using the maximum legal
128-character request ID. Count every UTF-8 byte of the standard JSON text
`content`, duplicate `structuredContent`, ID, and surrounding JSON-RPC fields.
The Streamable HTTP body and stdio record including its terminal LF must each
be at most 12,582,912 bytes. Real transports must deliver that exact
untruncated result. This does not weaken the 3 MiB upstream identity-body limit
or claim a 3 MiB MCP envelope. If the SDK changes its representation or the
fixture exceeds 12 MiB, stop and lower the unreleased evidence/page limit
through plan review.

Independently, bound inbound MCP traffic before the SDK. Streamable HTTP POST
entities and LF-delimited stdio JSON records are each at most 1,048,576 raw
bytes, with the delimiter excluded. The generated maximum legal tool call must
fit. Both transports share the strict signed-64-bit-or-bounded-ASCII request-ID
validator, and maximum output sizing uses its worst-case 130-byte JSON token.
Tests measure bounded reader allocation, dispatch count, and actual output
bytes; an oversized or invalid input cannot be used to elicit an oversized
echo.

Load tests use legal maximum payloads, long histories, and concurrent writers
to establish budgets for completion latency, receipt size, page latency, and
memory. Thresholds should be recorded from the Phase 10 baseline and reviewed
before release, not invented as unactionable production alerts.

---

## 14. Expected implementation surface

The paths below reflect current `origin/main` at `97317d8`, containing merged
Phase 10 from `fe723159`. They must be reconciled again if `origin/main`
advances. The implementation should remain concentrated in these ownership
areas; discovery of a need to change unrelated search, embedding, duplicate,
gate, relationship, or event models is a design-review trigger.

### 14.1 Backend and migration

Expected changes include:

- new
  `backend/alembic/versions/0019_structured_completion_evidence.py`, preserving
  `0018_repository_freshness.py` verbatim;
- `backend/src/mnemonic_api/models.py` and
  `backend/src/mnemonic_api/schemas.py` for the child entities, private work/
  checkpoint/reopen-event generation-column mappings, and strict wire models;
  the generation fields are absent from every public schema;
- the existing completion path in
  `backend/src/mnemonic_api/services/work_items.py`, reusing
  `complete_work_record`, and a focused new
  `backend/src/mnemonic_api/services/completion_evidence.py` for private insert
  assembly plus bounded reads;
- a focused new
  `backend/src/mnemonic_api/application/routes/completion_evidence.py` and
  registration in `backend/src/mnemonic_api/application/routes/__init__.py`
  for the GET, while the existing completion route remains authoritative for
  the write;
- `backend/src/mnemonic_api/services/client_operations.py` for canonical
  completion intent/response matching, the explicit transport/domain registry
  split, and nested secret traversal, without a new `OperationKind`;
- `backend/src/mnemonic_api/application/validation.py` and
  `backend/src/mnemonic_api/errors.py` only where safe public locations or
  existing generic error handling require it;
- version/OpenAPI/schema-parity integration in
  `backend/src/mnemonic_api/application/__init__.py`,
  `backend/pyproject.toml`, `backend/uv.lock`, `backend/alembic/env.py`, and
  `docs/openapi.json`;
- `scripts/audit_duplicate_handling.py` and
  `scripts/check-stack.py` for the head/catalog/invariant boundary; and
- focused unit, PostgreSQL, REST, receipt, migration, concurrency, preservation,
  audit, and live-sync tests under `backend/tests/`.

The migration should follow the existing immutable/source-fact/deferred-guard
patterns in migrations 0005, 0010, 0014, and 0016 while using new,
Phase-11-specific function names. It must leave
`mnemonic_work_event_metadata_v2_is_valid`, `stage_work_completed`,
`WorkEventRead`, every pre-existing event column/value/constraint, and the
event vocabulary unchanged. The `work_events` catalog additions are limited
to the private `reopen_generation` column and its check/partial unique index,
the completion lifecycle insert guard, the deferred event-side reopen guard,
the history and live-completion-version indexes, and the Phase 11 truncate
guard. No private binding enters event metadata or a public serializer.

The `work_items` catalog additions are limited to the private generation
column/check, the database-owned generation guard, the deferred done-state
episode guard, the regular pending-exit, unsealed-deletion, and non-deferrable
departure guards, and the deferred reopen-transition guard. Migration backfill
may temporarily disable only the four exact pre-0019 work/checkpoint/event
guards named in section 5.1, under exclusive locks, with before/after
definition hashes and enabled-state proof.

There is no repository/DAO layer in the current backend; Phase 11 must not
invent one. It also must not add a general evidence CRUD service, event payload
extension, search or embedding mapper, background resolver, external HTTP
client, or compatibility serializer.

Likely focused tests include new
`backend/tests/test_structured_completion_evidence_migration_postgres.py` and
`backend/tests/test_completion_evidence_postgres.py`, plus updates to
`conftest.py`, schema parity, validation, work-item completion, client
operations, idempotent mutations, duplicate invariants, OpenAPI snapshot,
aggregate audit, and live-sync tests. Exact test placement follows existing
ownership rather than duplicating fixtures.

### 14.2 MCP

Expected changes under `mcp/src/mnemonic_mcp/` and `mcp/tests/` are limited to:

- `models.py` for completion evidence and history page models;
- `validation.py` for exact evidence input guards and the shared bounded
  JSON-RPC request-ID validator;
- `api.py` for the bounded GET;
- `server.py` for the extended `complete_work`, one new tool registration,
  annotations, resources/prompts exclusions, raw coherence guards, outer HTTP
  ingress middleware installation, and the bounded stdio runner;
- `transport.py` for the shared bounded raw-frame/ID guard plus thin
  HTTP/stdio adapters; it is not a new application transport or protocol;
- `__init__.py`, `mcp/pyproject.toml`, and `mcp/uv.lock` for version `0.6.0`;
- existing `test_tools.py`, `test_transport.py`,
  `test_openapi_contract.py`, `test_stack_checker.py`, and a focused evidence
  test for catalog, schema, transport, and adversarial behavior.

No new protected-write dispatcher, operation kind, write retry helper, or
compatibility tool is created. The current centralized model/OpenAPI-parity
architecture remains intact unless a separately reviewed refactor is needed.

### 14.3 Frontend

Expected changes under `frontend/` include:

- `lib/types.ts` for wire types and a focused
  `lib/completion-evidence.ts` for strict evidence/page validation;
- `lib/api.ts` for the safe GET;
- `app/api/mnemonic/[...path]/route.ts` for the header-first identity check and
  raw streaming max-plus-one response boundary, replacing the evidence route's
  unbounded `upstream.arrayBuffer()` path;
- `lib/mutation-responses.ts` and `lib/mutation-intent.ts` for the extended
  existing completion response and frozen nested intent;
- `lib/proxy-policy.ts` for the GET allowlist and existing completion body;
- `frontend/next.config.ts` plus `deploy/nginx/mnemonic.conf` for enforced
  identity delivery on the dashboard API path and exact
  `X-DNS-Prefetch-Control: off` response-header parity;
- `deploy/nginx/snippets/mnemonic-dashboard-api-policy.conf`, included by both
  the production nginx configuration and the disposable test edge, so
  compression/header policy cannot drift between handwritten copies;
- `compose.nginx-e2e.yaml`, `scripts/test-nginx-e2e.sh`, the test-only
  ABI-matched ngx_brotli image and stock-nginx syntax configuration under
  `deploy/nginx/test/`, and a bounded encoding-upstream fixture for the
  runnable nginx-inclusive success/error transport matrix;
- `components/dashboard.tsx`, `components/work-detail-pane.tsx`, and
  `lib/work-detail-tabs.ts` for completion editing and the sixth lazy tab;
- a focused `components/completion-evidence-panel.tsx` plus scoped CSS if the
  existing component cannot remain readable;
- `package.json` and `package-lock.json` for version `0.6.0`; and
- existing mutation, proxy, OpenAPI, tab, wire-guard, live-sync, API-error, and
  Playwright suites plus focused evidence tests and the nginx-edge project.

The proposed new focused filenames are not public API and may be adjusted to
fit the delivered component boundaries. Do not add a client-side artifact
fetcher, command runner, Markdown renderer, global evidence cache, or twelfth
protected mutation.

### 14.4 Plugin and documentation

Expected changes are:

- new `plugin/reference/completion-evidence.md`;
- minimal edits to `plugin/skills/mnemonic-save/SKILL.md`,
  `plugin/skills/mnemonic-recall/SKILL.md`, and
  `plugin/skills/mnemonic-search/SKILL.md`;
- updates to `plugin/reference/authority-and-provenance.md`,
  `plugin/reference/work-graph.md`, and references to the unchanged Phase 10
  repository helper contract;
- `plugin/.claude-plugin/plugin.json` at `0.10.0`; the versionless
  `.claude-plugin/marketplace.json` changes only existing descriptive metadata
  if needed and gains no version key;
- `mcp/tests/test_plugin.py` and plugin package/install tests;
- roadmap shipped-state updates, user/API/MCP documentation, operator upgrade
  and audit guidance, examples, and this plan's final status notes; and
- the local ignored `CLAUDE.md`, if present, refreshed but not tracked.

Relevant tracked documentation includes `README.md`, `AGENTS.md`,
`docs/roadmap.md`, `docs/api-contract.md`, `docs/architecture.md`,
`docs/agents.md`, `docs/development.md`, `docs/operations.md`,
`docs/validation.md`, and generated `docs/openapi.json`. Examples must use
synthetic hosts and values and must not accidentally become a promise to
execute or resolve evidence.

---

## 15. Documentation contract

Every public description must use the same vocabulary:

- **completion episode:** one immutable completion checkpoint and any evidence
  recorded atomically with it;
- **verification result:** a caller-reported command result or observation;
- **artifact reference:** a caller-reported locator, not stored artifact
  content;
- **current completion:** the one sealed completion episode whose private
  generation equals a live canonical work's private generation while that work
  is currently done; ordinary done-item metadata edits do not obsolete it; and
- **prior completion:** every earlier episode, plus all episodes while the work
  is reopened.

Documentation must state:

1. evidence is optional, asserted, immutable, and tied to one exact episode;
2. absence is not failure or success;
3. outcomes do not drive lifecycle policy;
4. commands are stored inertly and URLs are not resolved or fetched;
5. asynchronous required evidence should be awaited before completion;
6. correction uses context plus reopen/recomplete, not mutation;
7. exact retry must preserve the full completion intent and operation UUID;
8. source-alias evidence remains source-owned after merge;
9. Phase 10 freshness is a separate local, ephemeral assessment; and
10. direct REST requires that operation UUID whenever evidence is non-empty,
    while absent evidence retains the historical unkeyed form;
11. a paginated history is complete only as of its high-water completion event
    and a current audit requires the fresh-head stability check; and
12. first-party evidence-history transport is identity-only; non-identity
    responses fail before body consumption and clients must not retry through a
    decompression compatibility path; and
13. 0.5.x clients are unsupported after evidence-bearing 0.6.0 use.

Update the roadmap to mark Phase 11 shipped only after all gates pass. Replace
its tentative standalone mutation name with the delivered atomic-completion
shape and record exact versions, migration head, catalogs, receipt behavior,
and deferred work. Reconcile Phase 5's candidate `verification_run` and
“Verification passed” timeline example by stating that Phase 11 adds no event
and renders evidence in its dedicated view, and update Phase 6's stale
“verification submission remains deferred” sentence to the nested
`complete_work` enrollment. Do not rewrite the historical Phase 11 intent
before it is implemented; distinguish planned from shipped text.

API and MCP examples need one omitted-evidence completion, one non-empty
completion, one history page with an empty episode, one exact retry, and one
reopen/recomplete sequence. UI help and plugin guidance must share the same
untrusted-evidence warnings.

---

## 16. Risk register

| Risk | Consequence | Required mitigation/evidence |
| --- | --- | --- |
| Phase 10 or later `main` differs from the inspected baseline | Migration fork or stale contract assumptions | Gate 0 rebase, full inventory, plan correction, and new cold review before code. |
| Optional fields change historical receipt serialization | Old replay conflicts or byte drift | Field-local sparse serializer, frozen 0018 corpus, all-13-kind comparisons. |
| Direct REST stores evidence without an operation UUID | No permanent recovery after an unknown outcome | Structural conditional UUID requirement before reservation or writes; unkeyed path only for absent evidence. |
| Completion payload and history wrapper drift into one permissive model | Ambiguous permanent receipts or malformed reads | Separate exact input/payload/episode/page models, null table, full wire examples, strict raw guards. |
| Evidence commits separately from completion | Done work without its claimed context, or evidence on the wrong episode | Only atomic completion-time write plus database deferred aggregate guard. |
| Direct SQL fabricates or duplicates a completion generation or reuses an event-shaped reopen witness | Audit history can be rewritten, including an empty episode | Database-owned generation counter, one-checkpoint partial unique index, private scalar reopen-generation binding, negative exact legacy mapping, positive generation uniqueness/prefix, symmetric deferred work/event guards, and adversarial direct-SQL tests. |
| A caller discharges a deferred checkpoint/done-state constraint and then changes lifecycle state in the same transaction | An invalid earlier episode can survive because an insert-side constraint event is not queued twice | Regular non-deferrable done-departure guard that revalidates the complete sealed episode before generation advance, bidirectional done-state/checkpoint guards, selective named `IMMEDIATE -> DEFERRED` regressions, and support only for fully sealed multi-cycle transactions. |
| An eventless checkpoint leaves pending through a held/terminal state and is completed only after a later generation | A fabricated lower-generation episode can pass a naive `checkpoint_generation <= work_generation` test without ever having represented done work | Regular pending-exit fence, rejection of non-pending-to-done jumps, exact-current-generation requirement on completion-event insertion, successor-reopen work-version chronology in sealed validation/read/audit, and zero/non-empty two-checkpoint attack tests. |
| Direct SQL completes without exactly one work-version increment, changes canonical/live identity before sealing, clears a deletion timestamp, clears a retained deletion tombstone, or inserts evidence after the done transition | The final event can resemble a valid completion while violating the delivered aggregate and its required write order | The regular pending-exit guard enforces live canonical `OLD`/`NEW` images, absence of any retained deletion event, and the checked `+1` version transition; the transition-captured done-state check binds the event to that exact version; the unsealed-deletion guard rejects delete/clear around an eventless current checkpoint; child and event guards recheck ordering/liveness; exact interleaving regressions fail at the earliest guard or commit. |
| A pending or done work version is reset around otherwise valid completion cycles | A later event can violate migration ordering or an apparently eligible downgrade can produce a database that 0019 refuses to re-upgrade | Require every new live completion version to equal its captured pending-to-done transition and exceed the latest prior live completion and its exact same-generation reopen; make the sealed validator/read/audit check the retained chronology and require a current done event version no greater than the retained work version; apply the same predicates in migration/downgrade preflight and exercise before-event bumps, later legal done edits, and downgrade/re-upgrade after evidence-free cycles. |
| Explicit identity override, terminal bigint use, sequence exhaustion, or reset makes a later completion ID invalid or lower than prior same-work history | Current/history selection can choose the wrong episode, fail lossless serialization, or leave no representable later ID | Locked bounded `work_completed` insertion guard, preflight retained logical-order checks, reserved terminal sentinel, sequence normalization/audit and early alert, direct `OVERRIDING SYSTEM VALUE` and reset tests, fresh retry after a safe ordinary reseed, and reviewed identity widening/rekeying before exhaustion. Never reseed below retained history. |
| A valid explicitly high completion ID is compared with a later ordinary lower ID from a different event type | A legal completion-before-merge history is falsely reported as corrupt | Never infer cross-event-type ordering from identities. Preserve the existing immutable-merge reviewed source counts, work-row serialization, and live-canonical completion guard; regress a high-ID completion followed by the lower-ID merge event. |
| A direct SQL insert appends evidence to an old completion | Audit history can be rewritten | Current-pending-generation and no-event child-insert guards, exact aggregate-at-commit guard, immutability, direct-SQL tests. |
| The application owner truncates evidence, events, or receipts | Immutable evidence disappears, history loses its join spine, or exact replay is destroyed without row triggers firing | `BEFORE TRUNCATE` guards on both evidence tables plus `work_events` and `client_operations`, catalog/direct-SQL tests, prohibited owner DDL, and explicit owner-threat-model language. |
| Reopen/recomplete blurs which evidence is current | Agents or humans rely on stale checks | Episode checkpoint key, page-level current pointer, explicit prior/reopened UI labels. |
| Wall time or UUID is treated as lifecycle order | Wrong current episode or gaps during clock rollback | Monotonic `work_completed.id`, partial index, cursor high-water, and backwards-clock tests. |
| Failed evidence is presented as verified completion | False assurance | Caller-reported language, no aggregate badge/policy state, enum text, guidance to stop absent authority. |
| Stored command becomes an execution instruction | Command/prompt injection | Inert rendering, no runner, MCP/plugin authority language, hostile-content tests. |
| Artifact URL causes SSRF, tracking, DNS leakage, or credential persistence | Data disclosure or external request | No server/client preview fetch, strict durable HTTPS locator, no credentials/query/fragment, explicit click only, DNS-prefetch disabled in both serving paths. |
| Secrets embedded inside larger evidence strings evade the old exact scanner | Long-lived credential exposure | Evidence-only recursive substring rejection for bearer/lease/common UUID spellings, strict bounds, sanitized errors, no raw logs. |
| Result/artifact join multiplies rows | Incorrect order/counts and excessive memory | Page-first selection and independent child hydration with query-shape tests. |
| Cursor/page metadata observe different snapshots | Internally contradictory history | One SQL statement or repeatable-read transaction and concurrent-write tests. |
| A caller edits an unsigned opaque cursor | It can skip a valid in-scope historical prefix and falsely call its own traversal complete | Validate retained scope/event IDs and document that completeness applies only to the exact unchanged server-issued cursor chain; tamper tests freeze this qualification. |
| Permissive or unbounded timestamp parsing accepts huge/equivalent spellings | Memory pressure, budget drift, or unexpected receipt conflicts | Raw-string ASCII 20–32-byte grammar before parsing, enumerated canonical equivalences, fixed aggregate charge, finite `TIMESTAMPTZ(6)` range, shared boundary fixtures. |
| Maximum legal evidence exceeds ingress or permanent-receipt limits | Edge 413 or late unavailable error for valid input | 32,768-byte aggregate; distinct generated compact representations at or below 896 KiB; independent exact deployed REST/browser 1 MiB ingress tests. |
| A peer or intermediary returns compressed or oversized history | Decompression or aggregation can precede a useful application limit | Request identity at controllable clients, reject every non-identity coding before body consumption, stream-count identity bytes to 3 MiB plus one, cancel and fail content-free, and prove identity delivery across the deployed browser hop. |
| FastMCP duplicates a maximum page into text and structured results | The emitted JSON-RPC message can exceed an unstated transport/context budget even though the upstream body is bounded | Separate 12 MiB complete-envelope ceiling, exact locked-SDK byte fixture, real Streamable HTTP/stdio delivery, dependency-drift test, and lower unreleased page/evidence limits rather than truncate or silently change representations. |
| An unbounded/malformed MCP frame or echoed JSON-RPC ID defeats the transport boundary before tool dispatch | Memory pressure, oversized reflection, ambiguous writer ownership, or a response larger than the promised 12 MiB despite bounded evidence | Shared 1 MiB pre-SDK single-object HTTP/stdio reader, identity-only HTTP ingress, signed-64-bit or 1–128-character ASCII IDs, bounded non-echoing HTTP rejection, SDK-writer-only terminal stdio closure, real-process rejection/race tests, maximum-ID response fixture, and exact SDK private-seam drift test. |
| Evidence bloats ordinary recall/search/event traffic | Token, latency, privacy, and compatibility regressions | Dedicated lazy bounded read; explicit projection exclusions and negative tests. |
| Old strict clients see a new response field | Runtime failure | Coordinated prerelease rollout, stop old processes, no compatibility shim. |
| Downgrade takes an old snapshot or deadlocks with reverse-order direct DML | Irrecoverable audit/retry loss or partial migration | Require READ COMMITTED and writer quiescence, exact `ACCESS EXCLUSIVE` locks, five-second fail-closed timeout/full-transaction retry, any response key blocks, and both lock-order tests. |
| Cross-language validators diverge | UI accepts what API rejects or MCP hides malformed data | Shared fixture IDs, byte-boundary corpus, strict raw guards, OpenAPI parity tests. |
| Metrics/logs expose evidence | Sensitive content spreads beyond authoritative store | Content-free telemetry schema and adversarial error/log tests. |
| A future async CI need is forced into context prose | Structured late results unavailable | Explicitly defer and collect product evidence; design a new receipt-protected lifecycle only when justified. |

No risk is mitigated by a silent legacy branch, permissive unknown-field path,
best-effort partial write, evidence inference, or destructive cleanup.

---

## 17. Explicitly deferred follow-up

The following are plausible later phases, not hidden acceptance criteria or
hooks to prebuild now:

- post-completion/asynchronous verification append with its own receipt,
  event/activity/current-episode, correction, and concurrency semantics;
- server-run or scheduled verification and trusted runner identity;
- artifact upload, retention, hashing, signatures, attestations, SBOMs, and
  provenance standards;
- CI/source-host integrations, webhooks, refresh, availability checks, and URL
  previews;
- configurable project completion policy or required verification templates;
- evidence search, filters, aggregation, exports, notifications, or project
  activity stream integration;
- artifact supersession, expiration, revocation, redaction, or legal-retention
  workflows;
- evidence carried automatically in bounded work context or semantic recall;
- richer verification types, structured test counts/durations/environments,
  repository identities, or multi-repository linkage; and
- compatibility negotiation for external stable clients after Mnemonic leaves
  prerelease.

If any deferred capability becomes required during implementation, stop and
amend this plan. Do not smuggle it into nullable columns, free-form metadata,
unused tables, unregistered routes, or dormant feature flags.

---

## 18. Definition of done

Phase 11 is done only when every item below is true:

### Contract and data

- [x] Phase 11 is rebased on the merged Phase 10 commit and migration 0019 has
  exactly one parent, `0018_repository_freshness`.
- [x] Both evidence tables, private work/checkpoint/reopen-event generation
  columns and exact negative historical/current backfills, both generation
  indexes, symmetric work/event reopen guards, locked current-generation
  completion-event/ID/version guard, checkpoint/done-state deferred guards,
  pending-only child guards, regular pending-exit, unsealed-deletion, and
  non-deferrable departure guards, both completion-event indexes, row guards,
  all four
  statement-TRUNCATE guards, ORM mappings, and downgrade refusal match this
  plan.
- [x] A populated 0018 database upgrades without changing any pre-existing
  column value, receipt byte, public event fact, or Phase 10 scope and without
  inferring evidence or historical reopen order; the distinct exact negative
  completion and reopen mappings are proven.
- [x] Results and artifacts are immutable, completion-episode-owned, bounded,
  ordered, and impossible to append after completion.
- [x] Every completion checkpoint, including an empty episode, has exactly one
  retained same-work `work_completed` event; one checkpoint is possible per
  database-owned generation; every done work has one sealed episode at its
  current generation; every nonnegative prior checkpoint generation is lower
  than the work generation; and every positive generation increment has one
  exact uniquely bound retained reopen witness in both directions forming the
  complete prefix.
- [x] The regular departure guard revalidates the complete current aggregate
  before every `done -> pending` transition. Selective named
  `SET CONSTRAINTS IMMEDIATE -> DEFERRED` cannot strand an invalid episode;
  multiple fully sealed cycles may commit in one transaction; and an ordinary
  metadata edit while done keeps the exact current pointer.
- [x] A pending generation with a completion checkpoint can leave only through
  its exact live-canonical, version-incrementing
  `pending -> done -> work_completed` seal; evidence children are accepted only
  before that state transition; deletion, a retained `work_deleted` tombstone
  even if `deleted_at` was cleared, or aliasing before either seal point,
  held/terminal exits, and non-pending-to-done jumps fail synchronously; a new
  completion event can target only the exact current generation and captured
  pending-to-done version; and every prior runtime episode precedes its
  successor reopen by typed work version.
- [x] Every new completion ID is positive, at most
  `9223372036854775806`, and strictly greater than prior same-work completion
  IDs. Its typed work version strictly advances every earlier live completion
  and its exact same-generation reopen, while the current done event version
  never exceeds the retained work version. The direct-SQL generation/witness/
  discharge/identity-reset/version-reset transactions fail at preflight,
  immediate departure, commit, read, or audit as appropriate; identities are
  never compared across different event types.
- [x] Empty evidence remains sparse in completion requests/responses but every
  completion episode appears honestly in the dedicated history page.

### Atomicity and replay

- [x] Completion checkpoint, evidence, done state/version/activity, lease
  effect, existing event, and existing receipt commit or roll back together.
- [x] Every fault injection and concurrency race produces one legal aggregate
  with no orphan, duplicate, partial, or wrong-episode data.
- [x] Actual SQL traces prove checkpoint and both child families flush before
  the pending-to-done work update and that update flushes before event
  insertion; no ORM dependency assumption stands in for database ordering.
- [x] Historical request fingerprints and response bodies for all 13 kinds are
  exact; request/response contract versions remain 1.
- [x] Non-empty evidence without `client_operation_id` fails before any write;
  absent evidence retains exact historical keyed and unkeyed REST behavior.
- [x] Authentication, structural/cross-field/size checks, evidence scan,
  existing scan, and receipt reconciliation run in the frozen order; a request
  missing the required UUID while embedding a bearer returns sanitized 422
  without scanning or reserving.
- [x] Receipt preparation strips the UUID into a distinct control-free
  `WorkCompletionRequest` and successfully executes a keyed evidence request;
  the runtime conditional validator exists only on `WorkCompletionCreate`,
  whose emitted OpenAPI component carries the matching executable condition.
- [x] Completion matching stays pure, initial response creation rehydrates
  rows, and audit proves bidirectional receipt/row correspondence.
- [x] Evidence-bearing exact replay survives restart, reopen, recompletion,
  merge, soft deletion, and restore without a new side effect.

### Read and client surfaces

- [x] The bounded evidence endpoint/tool traverses every event-backed
  completion episode in deterministic high-water pages, self-describes aliases,
  and identifies current versus prior completion correctly.
- [x] New event IDs are database-guarded positive and same-work monotonic and
  round-trip as lossless canonical bigint decimal strings; only an exact
  unchanged chain of server-issued unsigned cursors is documented as complete
  as of its high-water identity.
- [x] The MCP catalog is exactly 28 tools/11 protected writes, with one new
  safe read and no evidence write alias.
- [x] The browser remains at 11 protected mutation kinds and supplies an
  accessible, lazy, safe Evidence tab plus the extended completion editor;
  inactive success does not fetch evidence and leased work stays unavailable.
- [x] MCP and Next send `Accept-Encoding: identity`; MCP, Next, and browser
  reject non-identity `Content-Encoding` before acquiring/pulling a body
  reader; controlled Next/nginx delivery is identity-coded; and every reader
  enforces the 3 MiB identity-body ceiling with max-plus-one cancellation
  before fatal UTF-8/JSON parsing without trusting `Content-Length`.
- [x] Before FastMCP parsing or dispatch, Streamable HTTP and stdio enforce the
  same exact 1 MiB raw entity/record ceiling, single-object top level, and
  bounded JSON-RPC ID domain; HTTP rejects non-identity request coding before
  body pull and returns only its bounded non-echoing error. Stdio never uses an
  unbounded line read and terminally closes without a response for any
  transport-level rejection, discarding later buffered records. The SDK
  serializer remains the only stdout writer; byte-exact adapter and bounded
  real-entrypoint subprocess tests cover every rejection, first-invalid,
  invalid-then-valid, and in-flight-race cases. They prove the rejected record
  contributes no bytes, and any cut SDK frame is treated only as malformed/EOF
  unknown outcome.
- [x] The locked FastMCP typed return keeps its standard text and structured
  representations, and with the maximum permitted request ID the complete
  maximum-page Streamable HTTP body and stdio record including LF are each at
  most 12 MiB and arrive untruncated; any SDK serialization or private-runner
  seam change reruns the exact fixtures.
- [x] The plugin remains three skills/four shared references/one executable,
  bumps only its inner manifest version, and teaches exact retry, provenance,
  freshness separation, and inert handling.
- [x] Evidence stays out of normal context, search, checkpoints, events,
  relationships, embeddings, duplicate projections, caches, and live payloads.

### Security and operations

- [x] Known bearer/lease values and common operation-UUID spellings are
  rejected as embedded substrings at every nested string location before
  durable storage and no error/log/metric/audit output reflects caller content.
- [x] Stored commands cannot execute and stored references cannot trigger an
  automatic first-party network request in adversarial tests; Next.js/nginx
  both emit `X-DNS-Prefetch-Control: off`.
- [x] `observed_at` is a strict 20–32-byte ASCII string before parsing, all
  canonical equivalences are frozen, and real PostgreSQL proves finite
  `TIMESTAMPTZ(6)` range/precision under a non-UTC session timezone.
- [x] Evidence rows, `work_events`, and `client_operations` all reject
  supported `TRUNCATE`; their exact guards are catalog-audited.
- [x] Every maximum legal generated compact request/fingerprint/response/
  receipt representation and measured database `jsonb::text` is at most 896
  KiB; deployed raw REST/browser ingress accepts exactly 1 MiB and rejects the
  next byte before parsing; every maximum legal REST history page is at most
  3 MiB UTF-8 JSON and is delivered as a bounded identity body; and its
  standard duplicate-representation MCP JSON-RPC envelope with the largest
  permitted ID is at most 12 MiB. The largest legal MCP tool call fits its
  separate 1 MiB raw ingress boundary.
- [x] READ-COMMITTED pre-use downgrade works; other isolation and post-use,
  concurrent-use, lock-timeout/deadlock, or indeterminate downgrades refuse
  before DDL; exact `ACCESS EXCLUSIVE` locks, writer/direct-DML quiescence,
  evidence-free-cycle downgrade/re-upgrade, fresh-transaction retry, backup,
  restore, fix-forward, and exact-retry runbooks are exercised.
- [x] The read-only head-0019 audit passes on a representative populated
  database and prints only approved content-free facts.

### Release proof

- [x] Versions are exactly application/API/MCP/dashboard `0.6.0`, plugin
  `0.10.0`, migration `0019_structured_completion_evidence`, and inventories
  `28/11/13/11` plus plugin package `3 skills/4 references/1 executable`.
- [x] Shared backend/MCP/frontend fixtures agree, strict decoders reject every
  adversarial case, Draft 2020-12 evaluation proves the exact conditional UUID
  schema, and OpenAPI/catalog snapshots are current.
- [ ] Backend, MCP, frontend, Playwright, plugin, migration, audit, lint, type,
  build, pre-commit, fresh-install, sequential-upgrade, maximum-envelope
  Streamable HTTP, maximum-envelope stdio, and exact MCP-ingress boundary
  checks all pass with the PostgreSQL suites enabled.

  Every local portion of this aggregate gate is recorded in
  `docs/validation.md`. It remains open until required pull-request CI,
  including the authentic macOS Bash 3.2/Git runtime lane, succeeds on the
  committed tree.
- [x] Public/user/operator documentation and the roadmap describe the shipped
  boundary and limitations without overstating verification.
- [x] The implementation diff contains no compatibility shim, dual write,
  standalone evidence mutation, automatic fetch/execute path, unrelated model
  expansion, or unreviewed deferred feature.

---

## 19. Adversarial review record

### 19.1 Cold review method and initial verdict

The independent `phase11_cold_adversary` subagent received the complete
2,226-line initial draft with no inherited drafting conversation
(`fork_turns=none`). It was instructed to read the Phase 10 plan, roadmap,
merged Phase 10 implementation, and relevant tests/contracts; attack the plan
across product scope, persistence, receipts, concurrency, API/MCP/browser,
security, migration, rollout, and validation; make no edits; and explicitly
assess the nested-write topology. It delegated focused contract and database
attacks, reconciled their results, and returned:

> **ACCEPT WITH REQUIRED CHANGES**

The reviewer endorsed nesting evidence in `complete_work` as the correct
Phase 11 topology: it provides one exact checkpoint identity, work lock,
transaction, operation UUID, ordering boundary, and unknown-outcome recovery
without inventing a mutable late-append lifecycle. Its acceptance was
conditional on the following changes.

### 19.2 Finding dispositions

| # | Initial severity and finding | Disposition in this revision |
| ---: | --- | --- |
| 1 | **Blocking:** direct REST could submit evidence without a durable receipt because the operation UUID remained optional. | Require `client_operation_id` whenever evidence normalizes non-empty; validate before reservation/write; preserve unkeyed completion only for absent evidence; cover OpenAPI, REST, proxy, and replay tests. |
| 2 | **Blocking:** child guards did not prove a genuine completion and never fired for an empty episode. | Add database-owned lifecycle generations, deterministic negative historical mapping, one-checkpoint-per-generation uniqueness, exact reopen and completion deferred witnesses, event-backed reads, and both bypass transactions. |
| 3 | **Blocking:** downgrade could miss a writer under an old repeatable-read snapshot. | Require READ COMMITTED, writer/direct-DML quiescence, exact `ACCESS EXCLUSIVE` locks and five-second fail-closed retry before queries, treat any evidence response key as use, and reproduce snapshot plus both lock-order races. |
| 4 | **Blocking:** the former evidence maximum could exceed the existing 1 MiB ingress/receipt limits. | Fix aggregate evidence at 32,768 charged bytes; prove each generated compact/database representation at or below 896 KiB; keep raw deployed REST/browser ingress separately at 1 MiB; define a 3 MiB decoded history budget. |
| 5 | **Blocking:** one ambiguous read model, incomplete examples, undefined null rules, and unclear replay hydration left the permanent wire contract open. | Separate input, arrays-only completion payload, history episode, and page models; provide full request/response/page examples and field/null/byte rules; keep matching pure while creation hydration and audit prove physical correspondence. |
| 6 | **Major:** timestamp plus UUID was deterministic but not completion order, and one restart could not prove current completeness. | Order/select current by monotonic `work_completed.id`; add a partial event index and high-water cursor; define as-of traversal plus repeat-until-stable fresh-head audit semantics. |
| 7 | **Major:** the existing exact-value secret scanner would miss secrets embedded in prose. | Add an evidence-only recursive substring scan for bearer/lease values and common operation-UUID spellings before reservation, without changing other receipt kinds. |
| 8 | **Major:** row UPDATE/DELETE triggers did not block `TRUNCATE`, and owner-level DDL made the restore-only claim too broad. | Add statement-level guards to both evidence tables, `work_events`, and `client_operations`; test direct/cascading truncate; qualify the supported DML boundary and prohibit/audit deliberate owner DDL. |
| 9 | **Major:** an alias evidence page was not self-describing and could falsely expose a current completion. | Add page-level `is_duplicate` and canonical ID; force alias current pointer null; require direct REST/MCP/UI merge tests. |
| 10 | **Major:** an ordinary anchor could cause speculative DNS contact on view. | Require `X-DNS-Prefetch-Control: off` in both Next.js and nginx, prohibit DNS-prefetch hints, and parity-test the serving paths in addition to HTTP interception. |
| 11 | **Minor:** command outcome prose contradicted the exit-code matrix. | Define command outcomes as conventional process-status categories: zero passed, nonzero failed, indeterminate has no exit; nonconventional domain success uses observation/inconclusive. |
| 12 | **Minor:** branch exact preservation contradicted reuse of the trimming checkpoint validator. | Use a new branch-reference validator that rejects edge whitespace and preserves accepted case/internal spelling; add cross-language edge fixtures. |
| 13 | **Minor:** whole Phase 10 trigger-catalog equality was impossible after new FKs/triggers. | Compare named pre-0019 definitions individually, inventory Phase 11/RI triggers separately, and reserve whole-catalog equality for post-downgrade 0018. |
| 14 | **Minor:** browser guidance both eagerly and lazily loaded evidence and incorrectly contemplated a lease token. | Refetch evidence only for an active tab, otherwise invalidate lazily; keep the proxy token prohibition and make dashboard completion unavailable for leased work. |
| 15 | **Specialist low:** cursor input had no explicit length/decoded bound. | Freeze 1–4,096 ASCII characters and at most 2,048 decoded bytes across REST, MCP, browser, OpenAPI, and adversarial tests. |
| 16 | **Specialist low:** the plan invented a marketplace version property absent from the package contract. | Bump only the inner plugin manifest to 0.10.0; keep marketplace JSON versionless and alter existing description metadata only if needed. |

### 19.3 First closure review and second revision

The same cold reviewer then re-read the frozen first revision and returned:

> **REJECT**

It found 13 of the 16 original dispositions closed, but judged original
findings 2, 3, and 4 only partially closed. Its consolidated closure attack and
the second-revision dispositions are durable here:

| # | Closure finding | Second-revision disposition |
| ---: | --- | --- |
| C1 | **Blocking:** pending-at-insert/final-done checks still allowed a transient `done -> pending -> checkpoint -> done -> event` without reopen and two eventless checkpoints in one pending cycle. | Add private database-owned generations, a unique work/generation checkpoint key, an exact deferred reopen witness, deterministic negative legacy generations, final generation equality, and both adversarial SQL transactions to Gate 2/DoD. |
| C2 | **Blocking:** a generic datetime parser accepted an unbounded `observed_at` lexeme and timestamp normalization created undocumented fingerprint equivalences; the raw/896 KiB claim was also too broad. | Freeze a pre-parse 20–32-byte ASCII RFC 3339 subset, finite `TIMESTAMPTZ(6)` range, exact UTC output/equivalence set, fixed 32-byte aggregate charge, and separate generated-896-KiB versus raw-deployed-1-MiB contracts. |
| C3 | **Major:** `work_events` and `client_operations` remained truncateable, destroying the history join spine and permanent replay. | Add named Phase-11 `BEFORE TRUNCATE` guards to both existing tables as well as both evidence tables, plus catalog, direct, cascading, and downgrade tests. |
| C4 | **Major:** downgrade lock modes were unspecified and the claimed universal order reversed under direct child DML. | Use exact per-table `ACCESS EXCLUSIVE` locks, mandatory writer/direct-DML quiescence, READ COMMITTED, a five-second fail-closed timeout, whole-transaction fresh retry, and both lock-order tests; make no universal deadlock claim. |
| C5 | **Major:** the browser contract omitted the proxy route that eagerly buffered `upstream.arrayBuffer()`. | Name the catch-all proxy and API client explicitly; add decoded streaming max-plus-one/cancellation before fatal UTF-8/JSON parsing; distrust length; cover compressed, chunked, absent, dishonest, exact, and over-limit bodies. |
| C6 | **Minor:** ORM materialization did not ensure children precede the event, and the proposed cross-transaction duplicate-artifact race was impossible. | Require/trace an explicit child flush before event staging/flushing; test artifact uniqueness within one transaction and use concurrent whole completions plus the real direct-DML downgrade race. |
| C7 | **Minor:** the conditional operation-ID and evidence-secret error order was stated two ways. | Freeze authentication, structural/cross-field/size validation, evidence scan, existing scan, then receipt reconciliation; test missing UUID plus embedded bearer. |

During that review, the contract specialist also caught the receipt pipeline's
transport/domain revalidation split before the consolidated verdict. The plan
now follows the delivered merge pattern with `WorkCompletionCreate` as the
public request and control-free `WorkCompletionRequest` as the registry domain
model. Additional specialist hardening makes all new event identities lossless
decimal strings and explicitly limits unsigned-cursor completeness to an exact
unchanged server-issued chain.

### 19.4 Second closure review and third revision

The same cold reviewer attacked the frozen second revision again, with focused
database and contract specialists, and returned:

> **REJECT**

It confirmed the nested completion topology and all previously revised
receipt, timestamp, sizing, cursor, downgrade-locking, browser-lifecycle,
security, and package boundaries. Four remaining findings required this third
revision:

| # | Second-closure finding | Third-revision disposition |
| ---: | --- | --- |
| F1 | **Blocking:** reopen witnesses were matched only from the work-transition side by mutable-looking event shape; a later duplicate/orphan event or a reset-shaped stale event could escape or be reused. | Add private scalar `work_events.reopen_generation`; map legacy reopen events exactly to negative event IDs without inferring pairings; assign positive runtime bindings under the work lock; enforce one event per work/generation plus one scalar binding per event; add symmetric deferred event/work guards, positive-prefix audit, explicit reopen flush ordering, and preinsert/post-commit/stale-reuse tests. |
| F2 | **Blocking:** identity override or sequence reset could insert a nonpositive or lower later `work_completed.id`, invalidating history/current ordering and wire promises. | Extend migration preflight with bounded positive IDs and retained work-version/ID compatibility; reserve the terminal bigint value, normalize and audit the owned sequence, and add a same-work locked `BEFORE INSERT` guard requiring an in-range ID above every prior completion ID; test `OVERRIDING SYSTEM VALUE`, unused lower gaps, terminal use, resets, exhaustion, concurrency, safe reseed, fix-forward, and retry. |
| F3 | **Major:** a decoded max-plus-one iterator bounds retained application bytes only after HTTPX/runtime decompression may already allocate an arbitrarily large expansion. | Make every controlled evidence-history hop identity-only; MCP and Next request identity; reject every non-identity/malformed `Content-Encoding` on success or error before acquiring/pulling the body; raw-stream identity bytes to 3 MiB plus one; enforce identity delivery at Next/nginx; prove zero pulls with poison streams and avoid claims about pre-header runtime allocation. |
| F4 | **Minor:** the conditional operation UUID was documented but absent from the machine-readable OpenAPI 3.1 contract. | Emit the exact JSON Schema 2020-12 `if`/`then` condition on `WorkCompletionCreate`, narrow the non-empty branch to a non-null UUID, retain keyed empty/omitted requests, and evaluate the resolved schema with UUID format checking across the complete matrix. |

The reviewer again endorsed nesting evidence exclusively in `complete_work`.
None of these corrections adds a standalone write, compatibility shim,
inferred evidence, mutable history, or public generation field.

### 19.5 Final consistency attack and hardening revision

Before final acceptance, an independent whole-document consistency pass and
the original cold reviewer attacked the live third revision again. They
confirmed the prior receipt, schema, timestamp, browser, cursor, and nested
write decisions, but found five additional closure defects:

| # | Final-attack finding | Hardening disposition |
| ---: | --- | --- |
| E1 | **Blocking:** FastMCP duplicates a typed page into JSON text and `structuredContent`, but the plan bounded only the 3 MiB upstream REST representation and had no complete MCP result-envelope budget. | Add the separate generated 12 MiB complete JSON-RPC transport-payload ceiling, measure the locked SDK's two representations, include stdio framing, test both real transports, and require limits to be lowered before release if the fixture does not fit. |
| E2 | **Blocking:** an insert-side deferred checkpoint check can be selectively forced, then an invalid episode can be stranded by a later lifecycle transition in the same transaction without queuing that old row again. | Permit truthful multi-cycle transactions, but add the regular non-deferrable `completion_episode_departure_guard` that synchronously seals the exact old generation before every `done -> pending` advance; queue a done-state check on every done update; and test named `IMMEDIATE -> DEFERRED` bypasses plus valid generations zero, one, and two. |
| E3 | **Blocking:** requiring checkpoint/work generation equality for every retained completion contradicted historical and multi-cycle pages, while assigning every migrated work generation zero could not identify a legacy current completion exactly. | Give every legacy completion its exact negative event-ID generation, map each migrated/re-upgraded done work to its highest-ID completion's negative generation, reserve nonnegative generations for runtime, compare `<=` for runtime history and equality only for the sealed current done episode, and preserve current identity across ordinary done-item metadata edits. |
| E4 | **Blocking:** the work-generation backfill would be rejected for a retained duplicate alias by Phase 10's `duplicate_alias_work_mutation_guard`. | Under the locked migration only, hash, disable, backfill through, re-enable, and re-hash exactly that work guard plus the three existing checkpoint/event immutability and alias guards; require all four enabled and byte-equivalent afterward, with no runtime disable path. |
| E5 | **Blocking:** the new 12 MiB response claim still included an unconstrained caller-controlled JSON-RPC ID that FastMCP echoes; an arbitrarily long string or integer could defeat the ceiling, especially over stdio. | Add one 1 MiB pre-SDK entity/record guard to Streamable HTTP and stdio, reject non-identity HTTP coding before body pull, restrict IDs to signed 64-bit integers or 1–128-character safe ASCII strings, size with the maximum ID and stdio LF, reject without reflection, and pin the locked SDK's private stdio runner seam. |

These corrections preserve the chosen product topology: evidence remains an
optional immutable child aggregate of the existing receipt-protected
`complete_work` transaction, with one safe history read and no standalone
write, late append, compatibility shim, inferred evidence, or public
generation field.

### 19.6 Final acceptance review

The same cold reviewer re-read the complete hardening revision, delegated a
focused MCP transport attack, and returned:

> **REJECT**

It explicitly confirmed the nested-only, receipt-protected `complete_work`
topology and all original findings plus F1–F4 and E1/E3/E4 closed. It found E2
and E5 incomplete in two concrete ways:

| # | Final-acceptance finding | Disposition in the next frozen revision |
| ---: | --- | --- |
| G1 | **Blocking:** an eventless generation-zero checkpoint could leave `pending` for `deferred`/`wont-do`/`promoted`, reopen into generation one, and receive a late completion event while the later episode completed normally. The done-departure guard never saw generation zero, so naive `checkpoint_generation <= work_generation` accepted fabricated history. | Add the regular `completion_pending_exit_guard`: pending-to-done requires the exact current still-eventless checkpoint; other pending exits require no current checkpoint; non-pending-to-done jumps fail. Independently require every new completion event to bind the exact current generation. Sealed validation, reads, and audit require each prior runtime completion's work version to precede its exact successor reopen. Add zero/non-empty full-transaction regressions and an owner-corruption detection fixture. |
| G2 | **Major:** the promised stdio `id:null` rejection could not use the locked SDK's non-null `JSONRPCError.id`, `SessionMessage`, or `exclude_none` serializer without adding a competing writer. | Make every pre-dispatch stdio byte/UTF-8/JSON/ID violation terminal with no response bytes for the rejected record. Keep the SDK writer as the only stdout writer; test first-record EOF/no-output and valid/in-flight races for zero rejected-record bytes or interleaved control output. A cut SDK frame is only malformed/EOF unknown outcome, and an interrupted protected write uses its existing frozen operation-UUID recovery. |

Neither correction adds a transaction marker, custom JSON-RPC compatibility
serializer, second stdout writer, standalone evidence mutation, or
compatibility path.

### 19.7 Pre-final traceability and database-consistency audits

Before returning to the original adversary, a second context-free subagent read
the entire current plan, merged Phase 10 plan/source, and roadmap without
editing the workspace. A separate database-consistency specialist then
reviewed the revised lifecycle and migration design. Together they found the
following gaps in the post-G1/G2 revision:

| # | Finding | Frozen-revision disposition |
| ---: | --- | --- |
| H1 | **Major:** pending-to-done required a checkpoint but did not database-enforce the delivered exact work-version increment. | Require the regular pending-exit guard to enforce checked `NEW.version = OLD.version + 1`; add unchanged, decrement, jump, and overflow direct-SQL regressions and gate/risk/DoD coverage. |
| H2 | **Major:** the plan claimed children-before-state database order, but the child guard allowed insertion after done and before the event. | Require every child insert to lock a live canonical work still pending at the parent's exact current nonnegative generation; reject the post-state/pre-event ordering synchronously and test it directly. |
| H3 | **Major:** terminal stdio no-response behavior lacked numbered/gate and real-process coverage, and ownership of valid non-object or semantically invalid JSON-RPC shapes was unclear. | Expand SCE-042 and Gate 5; make scalar/array/batch top levels transport rejections, leave remaining object-level JSON-RPC semantics to the SDK, discard later buffered records after a stdio violation, and test every class through both the adapter and bounded real entrypoint. |
| H4 | **Minor:** version-bearing Python lockfiles were absent from the expected file inventory. | Add both `backend/uv.lock` and `mcp/uv.lock` to the coordinated 0.6.0 surface. |
| H5 | **Minor:** release-time roadmap instructions omitted stale Phase 5 event/timeline and Phase 6 deferred-enrollment text. | Require explicit post-ship reconciliation while preserving historical planned-versus-shipped context. |
| H6 | **Minor:** the numbered requirements had no compact navigation to design, executable proof, gates, and Definition of Done. | Add section 4.1 and require it to change atomically with any implementation-time requirement correction. |
| H7 | **Major:** checkpoint or child insertion followed by soft deletion or aliasing in the same transaction could satisfy row-local checks before the final done/event seal; clearing `deleted_at` after an immutable deletion event could also fake liveness. | Recheck live canonical identity at checkpoint, child, pending-to-done, and event seal points; define Phase 11 liveness as both null `deleted_at` and absence of a retained same-work `work_deleted` event; add interleaving and tombstone-reset regressions. |
| H8 | **Major:** resetting a work version while pending could make a later completion version lower than an earlier live completion and make an eligible evidence-free downgrade produce a database that 0019 then refused to re-upgrade; decrementing a done work below its current completion had a related hole. | Require every new live completion version to exceed the latest prior live completion and its exact same-generation reopen; mirror those predicates in the sealed validator, reads, audit, migration/downgrade preflight, and a bounded partial index; require the current done event version not to exceed the retained work version; test reset and no-evidence downgrade/re-upgrade paths. |
| H9 | **Major:** comparing a source completion-event ID with a later duplicate-merge event ID would falsely reject a legal history when the completion used an explicit high identity and the ordinary merge event later received a lower one. | Remove all cross-event-type ID comparisons. Rely on existing immutable-merge reviewed source counts, work-row serialization, and live-canonical event insertion guards; add the explicit-high-completion/lower-merge regression. |
| H10 | **Major:** a timestamp-only soft-delete followed by a same-transaction clear after checkpoint creation left no tombstone and could evade final liveness checks. | Add the regular `completion_unsealed_deletion_guard`, rejecting null-to-non-null `deleted_at` while an exact current-generation checkpoint remains eventless; retain normal deletion after a sealed completion and test pending and done-before-event intervals. |
| H11 | **Major:** after the checked pending-to-done increment, a status-preserving version bump before event insertion could detach the completion event from the transition while satisfying final-row and monotonic checks. | Make the deferred done-state trigger instance for that transition require the exact completion-event version to equal its captured `NEW.version`; continue allowing ordinary versioned edits after the event is sealed; add both orderings as direct-SQL regressions. |
| H12 | **Major:** the runtime validator required a current done live-completion version not to exceed the retained work version, but the enumerated 0018 upgrade preflight omitted that relation, so pre-existing direct-SQL damage could become an unreadable post-upgrade row. | Add the exact current-done live-completion/work-version predicate to pre-DDL upgrade and next-upgrade downgrade preflight, exclude metadata-free backfill events, and add a disposable 0018 refusal fixture proving no Phase 11 DDL runs. |
| H13 | **Major:** the required backend verification command omitted `TEST_DATABASE_URL`, so PostgreSQL tests could skip while the no-skip release gate appeared to run. | Export the exact disposable `compose.test.yaml` PostgreSQL URL immediately after startup so every subsequent backend test command runs the database suites; retain the explicit no-skip count assertion. |

These changes retain the reviewed nested-only topology and add no application
write, compatibility path, event type, mutable evidence, or inferred history.

### 19.8 Final closure review

A fresh context-free whole-plan reviewer read the complete on-disk revision
from the beginning, checked it against the Phase 10 plan, roadmap, and
delivered source, made no workspace edits, and returned:

> **ACCEPT**

It confirmed H7–H12 were consistently represented in persistence invariants,
upgrade/downgrade preflight, audit, tests, risks, and Definition of Done. It
reported only three nonblocking implementation cautions already enforced by
the plan: size ceilings remain empirical release gates, the MCP stdio path
depends on a pinned FastMCP 1.29.1 private seam and drift test, and equivalent
raw-ingress control for direct backend exposure remains an operator deployment
responsibility outside the controlled proxy.

A separate context-free contract reader then found H13: the required backend
test command could allow PostgreSQL suites to skip. After section 11.12 was
corrected to export the exact disposable `compose.test.yaml` database URL, the
whole-plan reviewer rechecked that delta and returned `ACCEPT`; the contract
reader returned `CLEAN`. The focused final PostgreSQL lifecycle/migration
reader independently returned `CLEAN`.

The final planning verdict is therefore **ACCEPT** with no blocking or major
finding left open. This verdict approves the plan for a later implementation
session; it does not assert that any Phase 11 code, migration, or validation
has run.

### 19.9 Implementation-time adversarial review and closure

Implementation review deliberately reopened the accepted plan against the
running code, generated contracts, PostgreSQL 17, the pinned MCP SDK, the
browser decoder, and a real nginx compression filter. Intermediate revisions
were rejected until each release-significant defect below was closed; no
compatibility branch, inferred backfill, receipt rewrite, or standalone
evidence mutation was added.

| Finding | Delivered disposition |
| --- | --- |
| Direct SQL could preserve `done` while resetting, decrementing, or jumping the public work version and still manufacture a completion-shaped episode. | Tighten the work transition and deferred seal guards to permit only the exact unchanged or checked `+1` paths appropriate to the transition; add unchanged, decrement, jump, overflow, ordering, and post-seal edit regressions. |
| Cursor parsing and event identities could diverge between outer lexical bounds, decoded payload bounds, and JavaScript/Python integer domains. | Enforce both encoded and decoded cursor ceilings, canonical positive decimal event IDs, exact high-water traversal, and negative-zero rejection in every adapter and schema fixture. |
| MCP transport behavior initially left status/exception/schema seams and stdio framing insufficiently pinned. | Put the 1 MiB guards before SDK parsing, keep one SDK stdout writer, make transport rejection terminal and non-echoing, bind the private runner seam to FastMCP 1.29.1, and exercise real HTTP/stdio maximum and rejection paths. |
| Catalog inspection depended on the connected role and could bless ownership/ACL drift or unstable deparser output. | Normalize owner/ACL expectations explicitly, force and restore a catalog-safe search path, hash exact named Phase 11 objects, and prove the Phase 10 survivor digest is role/search-path/index independent. |
| The first operational audit accumulated global receipt state in Python and later used bounded result counts without proving bounded candidate access. | Replace it with typed keyset pages and O(batch-size) state under one repeatable-read/read-only snapshot; add checkpoint-first child indexes and a unique partial checkpoint/work receipt index; prove the reverse plan with 4,096 unrelated rows and JSON `EXPLAIN` assertions. |
| Corrupt same-key receipts could cross size-one pages, while a late unique-index build could fail without a content-free migration diagnosis. | Add a locked, pre-DDL 0018 duplicate-pair refusal, direct unique enforcement, cross-page/cross-owner corruption oracles, transactional corruption fixtures, and exact downgrade cleanup. |
| Catalog regressions leaked index relation columns into the Phase 10 survivor projection, tests leaked sequence state across rollback, and catalog output varied by search path. | Restrict survivor columns to the intended relation kinds, preserve/restore search path and non-MVCC sequence state explicitly, and freeze the corrected survivor and Phase 11 catalog digests. |
| A stock-nginx-only test could not substantiate the planned Brotli barrier. | Share one production policy snippet, syntax-check it on stock nginx, then load an ABI-matched `ngx_brotli`, prove a positive `br` control, and prove exact identity delivery for bounded evidence success and error responses. |
| URL validators disagreed on lowercase percent escapes, IPv4-mapped forms, and noncanonical IPv6 spellings; PostgreSQL's `host(inet)` also rewrites some valid low IPv6 values to dotted notation. | Use one uppercase-percent and exact lowercase `IPv6Address.compressed` contract across REST, OpenAPI, PostgreSQL, MCP, and browser; implement SQL leftmost-longest compression checks without trusting `host(inet)` text; test every 256 zero mask with and without a port and independently fuzz 31,165 spellings with zero mismatches. |
| The frontend history decoder accepted noncanonical server-owned fractional timestamps when matching parent and child strings happened to agree. | Require a valid civil UTC seconds-or-six-microseconds spelling independently for checkpoint, verification, and artifact `created_at`; reject short/long fractions, offsets, invalid dates, and matching noncanonical parent/child values. |
| Migrated empty episodes, alias-owned history, pagination drift, and lazy browser invalidation each exposed opportunities for a strict reader to widen or misidentify state. | Keep pages event-backed and high-water bounded, make alias/current semantics explicit, retain evidence-free episodes with empty families, refetch only the active tab, and add REST/MCP/browser continuation and migrated-history regressions. |
| Negative type fixtures did not initially prove that strict backend, MCP, and TypeScript surfaces rejected widened evidence models. | Add compile/type-check fixtures and resolved OpenAPI/JSON Schema evaluations alongside the shared 56-case semantic corpus and exact JSON examples. |

That reviewer accepted the then-stable implementation without editing it. The
accepted evidence included 225 backend completion/OpenAPI tests, 21 focused
PostgreSQL storage/catalog tests, 48 aggregate-audit tests, 194 MCP
completion/OpenAPI tests, 225 frontend unit tests plus type/build,
repository-wide gitleaks, and the real Brotli/nginx harness. This was an
intermediate pre-rebase verdict: subsequent `origin/main` movement and the
production-shaped backup/restore rehearsal deliberately reopened closure.

### 19.10 Post-rebase production rehearsal and final closure

The post-rebase pass attacked the implementation through a PostgreSQL 17
custom-format archive round trip, both supported migration heads, strict shared
contract consumers, and the latest dashboard surface. The following findings
were fixed before a pull request:

| Finding | Delivered disposition |
| --- | --- |
| A dump/restore reparsed 18 Phase 10 CHECK expressions and two partial-index expressions into a second legitimate raw form, while the initial survivor query admitted four Phase 11 constraint-trigger rows. | Freeze exactly the migration-built and shipped-backup-restored PostgreSQL 17 survivor-projection hashes; exclude every Phase 11 constraint by exact table/constraint identity; accept neither generic normalization nor a third form; audit both forms at 0018 and 0019; add wrong-relation identity and dump/reparse regressions. |
| The former `--no-acl` archive path removed Phase 11 function revocations, and a null relation ACL could be semantically owner-only while differing textually from an explicit ACL. | Retain archived public-schema application-object ACL commands while rebinding ownership; compare effective `acldefault` relation privileges; require exact Phase 11 function ACLs with no `PUBLIC EXECUTE`; prove backup/restore plus downgrade/re-upgrade on PostgreSQL 17. |
| `origin/main` advanced with two keyboard-hint commits after the implementation branch was cut, so the first comparison appeared to delete that UI work. | Rebase cleanly through `9f5e57b`, retain both upstream commits, and rerun the complete frontend and Playwright gates on the integrated tree. |
| REST already bounded completion `expected_version`, but MCP accepted coercible integers without the matching maximum and the browser lacked the same upper bound. | Enforce one strict integer range, `1..2,147,483,646`, in REST/OpenAPI, MCP/runtime schema, and the browser proxy; add shared maximum, max-plus-one, string, and boolean vectors and independent contract review. |
| The evidence-history REST response bounded `work_version` and both counts, while MCP omitted all three maxima and the browser accepted a work version above the database domain. | Match MCP runtime and output-schema maxima to REST exactly—`2,147,483,647` for work version and `9,223,372,036,854,775,806` for both counts—while the browser explicitly enforces the work-version maximum and retains its safe-integer count guard; add exact-max and max-plus-one regressions and obtain an independent `CLEAN` review. |
| The first complete backend rerun exposed a test-only corpus consumer that replaced every case's `expected_version` with `1`. | Pass the shared value through the runtime-model helper, add the regression to the same corpus path, pass the current focused 84-test slice, then rerun all 1,072 backend tests cleanly. |
| Restore documentation spoke about whole physical catalogs and database-wide ACLs more broadly than the implementation proved. | Name the selected raw Phase 10 survivor-catalog projection, scope archive guarantees to public-schema application objects, apply the `--no-acl` warning to every shipped archive, and separate static-script, SQL-reparse, catalog, and real-restore claims; a cold documentation re-review returned `CLEAN`. |
| The live rollout stopped writers and immediately migrated, so evidence-bearing smoke could make downgrade refuse while the only documented recovery point was a stale rehearsal archive. | Make live quiescence contiguous with a fresh named/hashed/independently restored pre-0019 archive, a live 0018 audit, DDL, smoke, and post-0019 restore proof; explicitly forbid substituting the pre-scheduling archive and add an ordering regression. |
| The first runbook correction left the continuously scheduled backup loop active across migration locks, and a later `up -d` restart did not wait through the backup service's health-check start period. | Stop `backup` with application services, use `docker compose run --rm --no-deps backup once` for both quiesced archives, keep the loop stopped through audit/DDL/smoke/post-restore proof, and restart it with `docker compose up -d --wait backup` only before reopening writers; extend the exact ordering regression and obtain independent `CLEAN` review. |

The final local matrix passed: backend 1,072 tests plus Ruff and whole-source
`ty`; MCP 548 tests plus Ruff and `ty`; frontend 252 tests plus TypeScript
and production build; Playwright 99 passing with four intentional skips out of
103 executions in 6.3 minutes; MCP/plugin contracts 51; helper behavior 71; the
authentic Claude CLI smoke; fresh and sequential plugin installation; and the
real stock-nginx/Brotli identity-coding harness.

The production-shaped rehearsal returned `pass` from
`0018_repository_freshness` to `0019_structured_completion_evidence`. It
compared 16 source and 171 restored row/sequence sets, passed audits at source
0018, restored 0018, pre-enablement 0019, populated 0019, and restored 0019,
replayed historical create/completion and new evidence-bearing completion
receipts exactly, removed the whole-schema sentinel, and preserved the reviewed
function/relation privilege boundary. The 205,623-byte pre-upgrade archive had
192 table-of-contents entries and the 320,695-byte post-upgrade archive had 273.
All rehearsal data and infrastructure were synthetic and disposable; no
production content was accessed.

A fresh context-free whole-diff reviewer then found the response-bound and live
recovery-point gaps above. Its focused migration closure attack additionally
found the still-running backup loop; a final documentation adversary then found
that its restart needed an explicit health wait. After all final corrections and
their regressions, independent contract and migration reviewers returned `CLEAN`.
The whole-diff reviewer re-read the combined changes without editing them and
returned **ACCEPT** with its findings closed and no remaining
high-confidence release issue. Repository-wide pre-commit/gitleaks, shell
syntax, Ruff, `ty`, TypeScript, production build, and `git diff --check` also
passed on the corrected tree.
