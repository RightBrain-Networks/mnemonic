# Mnemonic Phase 10 — Repository Freshness Verification Implementation Plan

This began as the implementation contract for Phase 10 and now also records
implementation-time feasibility corrections and release evidence. The gates in
sections 10 and 18 are checked only where the completed implementation,
adversarial review, and disposable release rehearsal supplied direct evidence.
As of 2026-09-03, the implementation is integrated through `origin/main` at
`a0cc7fc`, the complete local release matrix passes, and cold review accepts
the result. Production-target preflight, approval, live-fleet quiescence, and
deployment remain operator work and are not claimed here.

It was prepared against `origin/main` at
`63227294e989a11e8ab914feace3652849b0ea88`, after Phase 9, the theme-selector
change, the dark-theme contrast adjustment, and the subsequent README update
landed. That baseline
has application/API/MCP/dashboard version `0.4.0`, plugin version `0.8.0`,
Alembic head `0017_duplicate_suggestion_title_key`, 13 durable REST mutation
receipt kinds, 27 MCP tools, 11 protected MCP writes, and 11 browser mutation
kinds.

Phase 10 is one coordinated user-facing release:

- application, REST API, MCP adapter, and dashboard: `0.5.0`;
- Claude Code plugin: `0.9.0`;
- database migration: `0018_repository_freshness`;
- receipt request and response contract versions: remain `1`;
- tool and protected-mutation counts: unchanged.

The roadmap's essential boundary is preserved: Mnemonic stores
caller-declared checkpoint provenance, while a repository-aware client performs
a read-only assessment against a local Git checkout. The server never mounts,
fetches, or claims to have verified a repository.

---

## 1. Outcome

Phase 10 makes existing checkpoint provenance actionable without overstating
what Git can prove.

After the release:

1. Every full checkpoint input and read can carry an ordered
   `affected_paths` list alongside `repository_branch` and
   `verified_against`.
2. An affected path describes source on which the checkpoint's assertions
   depend. It is not merely a list of files changed by the checkpoint author.
3. Missing or empty scope means “no dependency scope was declared.” It never
   means “the checkpoint covers the whole repository” or “nothing can have
   changed.”
4. The backend persists the declaration on the existing immutable checkpoint
   row. It does not inspect Git, derive paths, or persist a freshness result.
5. The MCP adapter transports the declaration but remains repository-blind.
6. The installed plugin contains one read-only Git helper with capped input,
   retained evidence, and output. The caller applies a whole-process-group
   deadline, and the recall workflow invokes the helper only for a full
   checkpoint the agent is about to rely on.
7. The helper emits exactly one assessment state:

   - `unchanged`: no relevant Git change was observed and every required
     precondition was established;
   - `changed`: one or more relevant committed, staged, unstaged, unmerged, or
     nonignored-untracked changes were observed;
   - `indeterminate`: the comparison could not safely establish either of the
     first two outcomes.

8. User-facing language says “no relevant change observed,” “relevant change
   observed,” or “could not assess.” It never says that a checkpoint, branch,
   repository, or assertion is semantically correct, current, safe, or
   independently verified.
9. A `changed` or `indeterminate` outcome makes the agent warn and reinspect
   current sources before relying on the checkpoint. It does not create a
   human gate, revoke a claim, mutate a work item, or block an otherwise
   authorized operation automatically.
10. The dashboard accepts and displays declarations, but browser code never
    claims to have assessed a local repository.
11. Every existing database fact and permanent receipt remains intact.
    Historical request fingerprints and stored response bodies remain
    byte-for-byte stable.

Representative client output is deliberately evidence-oriented:

```text
Repository freshness: RELEVANT CHANGE OBSERVED
Checkpoint baseline:  a832bc1… (resolved locally)
Current HEAD:          d7be142…
Declared scope:        3 patterns
Relevant paths shown:  2

  app/services/foo.py
  tests/test_foo.py

Reinspect current source before relying on this checkpoint.
This is a Git-state comparison, not a semantic-correctness result.
```

An `unchanged` result says “No relevant Git change was observed in the
declared scope.” An `indeterminate` result gives one stable reason code and a
safe next action; it must not silently collapse into `unchanged`.

### 1.1 Non-goals

Phase 10 does not:

- prove that a checkpoint was true when written;
- prove that unchanged bytes preserve meaning;
- verify builds, tests, generated artifacts, deployments, databases, external
  services, or runtime configuration;
- add Git blob IDs or content manifests;
- make Mnemonic clone, mount, fetch, pull, or trust a repository;
- persist the result of a local assessment;
- introduce an API freshness endpoint or an MCP freshness tool;
- add a new skill;
- add a gate, readiness rule, lease rule, merge rule, event type, work version
  change, activity update, receipt kind, or audit authority;
- copy path declarations into event metadata;
- put path declarations into checkpoint search text, embeddings, duplicate
  suggestions, or derived-cache identity;
- recursively assess submodule contents;
- inspect ignored untracked files as if they were versioned evidence;
- silently infer historical path scopes from checkpoint prompts, tags, source
  metadata, work relationships, or repository contents;
- maintain parallel legacy and current checkpoint models.

---

## 2. Shipped baseline and constraints

### 2.1 Checkpoints are immutable provenance records

`Checkpoint` already stores caller-asserted `repository_branch` and
`verified_against`. Create, add-checkpoint, and completion inputs share the
same checkpoint payload shape. The update/delete trigger introduced with the
work graph makes checkpoint rows append-only.

Phase 10 extends that record rather than creating a second provenance table.
The path list belongs to the checkpoint because:

- it qualifies the assertions in that exact immutable packet;
- different checkpoints on one work item can depend on different paths;
- a merged duplicate source retains its own history and must not inherit the
  canonical root's path scope;
- later context may supersede an earlier context without rewriting it.

### 2.2 Permanent receipts constrain serialization

`create_work`, `add_checkpoint`, and `complete_work` have receipt responses
that contain a full checkpoint. Request fingerprints serialize explicit,
defaulted, and unset fields. Receipt replay validates a stored body, serializes
it again, and requires exact equality. Completed receipt rows are guarded
against mutation.

A naive defaulted `affected_paths: []` property would therefore:

- alter historical request fingerprints;
- alter reserialized historical responses;
- break exact same-key recovery; and
- tempt a destructive rewrite of permanent response evidence.

Phase 10 instead adopts one sparse rule for all records and callers:

> Missing `affected_paths` and explicit `affected_paths: []` are the same
> semantic value, and their canonical wire representation omits the property.

Non-empty lists serialize normally and become part of new fingerprints and
responses. This is the current contract, not a time-limited compatibility
branch.

### 2.3 Full checkpoints and compact pointers have different jobs

Full checkpoint reads appear in checkpoint history and bounded recall context.
Compact `CheckpointPointer` projections appear in search, hierarchy, gates,
relationships, and other high-fan-out surfaces.

`affected_paths` is added only to full checkpoint inputs and reads. It is not
added to `CheckpointPointer`. Search or hierarchy selection must be followed
by a full recall before assessment, just as compact pointers are already
insufficient execution authority.

### 2.4 The server and browser lack a trustworthy checkout

The API can authorize and persist project-scoped data, but it has no trusted
mapping from a project row to a mounted Git repository. The browser cannot
safely inspect an arbitrary local checkout. Moving Git execution into either
surface would violate the roadmap boundary and create filesystem, credential,
and network risk.

Only the local plugin helper evaluates Git. Its result is ephemeral client
evidence. Neither the backend nor MCP adapter accepts an assessment result.

### 2.5 Existing provenance remains caller-asserted

`repository_branch`, `verified_against`, and the new `affected_paths` are
untrusted author declarations:

- `repository_branch` is display and mismatch evidence, never a revision to
  execute or resolve;
- `verified_against` names a commit the author says was actually inspected;
- `affected_paths` names dependencies the author says qualify the checkpoint.

Reading `HEAD` is not sufficient to claim inspection. If important
uncommitted, ignored, generated, or external state cannot be represented by a
commit and scope, the author must say so in checkpoint text or source metadata
and must not imply that Phase 10 can later establish it.

### 2.6 Prerelease evolution without shims

Mnemonic is prerelease. Phase 10 may update all first-party clients in one
release and does not preserve acceptance by an older strict client when a new
non-empty property is present. It does preserve existing data and permanent
receipt semantics.

There will be one checkpoint model with one empty-value rule: no v1/v2 model
union, response-body receipt rewrite, per-client downgrade projection,
old-field alias, or dual database write.

---

## 3. Decisions fixed by this plan

### 3.1 One coordinated prerelease release

Phase 10 ships as application `0.5.0` and plugin `0.9.0`. Migration `0018`
lands before the new backend serves. Backend, OpenAPI, MCP, dashboard, helper,
plugin guidance, and documentation form one reviewed contract.

The catalog stays at 27 MCP tools, 11 protected MCP writes, 13 REST receipt
kinds, and 11 browser mutation kinds. A new server tool or receipt kind crosses
the phase boundary and requires a new design.

All first-party `0.4.x` clients are unsupported once a non-empty scope exists.
That is an intentional prerelease break, not something hidden behind a
compatibility branch.

### 3.2 Exact `affected_paths` meaning

The public field is `affected_paths: list[string]`.

Rules:

- omitted and explicit `[]` inputs normalize to internal `[]`;
- canonical output omits the property whenever it is empty;
- non-empty scope requires non-null `verified_against`;
- `repository_branch` remains optional;
- at most 64 entries;
- at most 512 ASCII/UTF-8 bytes per entry;
- at most 16,384 bytes over all entries;
- order, spelling, and case are preserved;
- no trimming, normalization, sorting, expansion, or deduplication;
- exact duplicates are rejected;
- `[]` means no scope declared, never no changes;
- the literal `**` is the explicit whole-eligible-repository scope.

The entries name version-control paths on which checkpoint assertions depend,
not merely files the author changed. “Eligible repository paths” means tracked
entries in the baseline/current/index plus nonignored untracked worktree files.
Ignored untracked content, submodule interiors, generated external artifacts,
and symlink targets outside the repository are outside this phase.

### 3.3 Shell-transport-safe scope grammar

Phase 10 intentionally uses a narrow ASCII grammar so untrusted stored scope
can cross the plugin's fixed shell invocation without a general-purpose
encoder.

Each slash-separated component contains only:

```text
A-Z a-z 0-9 . _ @ + = , ~ - *
```

Additional rules:

- `/` is the only separator;
- one `*` matches zero or more bytes within one component;
- `**` is allowed only as a complete component and spans complete components;
- no other run of consecutive stars is valid;
- a no-wildcard entry names one file or gitlink;
- a directory dependency is written explicitly as `directory/**`;
- `**` alone covers all eligible top-level and descendant entries.

Reject empty entries/components, leading/trailing slash, `.`/`..` components,
absolute/drive/UNC forms, backslash, whitespace, non-ASCII, controls, `?`,
brackets, braces, quotes, dollar/backtick, colon, bang, caret, raw pathspec
magic/exclusions/attributes, shell operators, duplicate entries, and all
count/byte excesses.

This grammar is deliberately less expressive than Git. It makes Python,
PostgreSQL, TypeScript, and Bash validation byte-identical. A repository with
an unsupported exact filename can declare a safe ancestor glob or `**`.
Unicode, spaces, `?`, character classes, escaping, and arbitrary Git pathspecs
are deferred rather than approximated.

The helper compiles exact entries to `:(top,literal)<value>` and wildcard
entries to `:(top,glob)<value>`. Because brackets/backslashes and every
caller-supplied magic prefix are invalid, only the two helper-owned prefixes
have Git semantics.

### 3.4 Persistence and historical rows

Migration `0018_repository_freshness` adds:

```text
checkpoints.affected_paths VARCHAR(512)[]
    NOT NULL
    DEFAULT '{}'::VARCHAR[]
```

It adds one versioned immutable SQL grammar/bounds function, a constraint using
it, and a constraint requiring `verified_against` for non-empty arrays. There
is no index.

Every historical row receives `{}`. No prompt, tag, metadata key, kind,
branch, commit, relationship, event, duplicate history, or checkout is used to
guess a scope.

### 3.5 Permanent sparse serialization

Backend and MCP full-checkpoint models use a field-local empty exclusion:

```python
affected_paths: list[AffectedPath] = Field(
    default_factory=list,
    max_length=64,
    exclude_if=lambda value: not value,
)
```

Observable requirements:

- omitted and explicit-empty requests have identical internal/canonical form;
- the property stays absent under existing receipt dump flags and nesting;
- non-empty lists serialize in original order;
- no global dump option changes;
- historical response-v1 bodies reserialize exactly;
- request/response receipt versions remain `1`;
- the coherence tuple includes the new field.

Backend and MCP require at least the already-locked Pydantic `2.13.5`.
Reordering or changing a non-empty list under one operation UUID conflicts.

This is one current model, not a legacy shim. Old strict clients may reject
non-empty new responses and must be upgraded.

### 3.6 Canonical response strictness

Request contracts accept omission or explicit `[]`. Response contracts accept:

- absent property, interpreted internally as `[]`; or
- present, valid, non-empty property.

A server response that explicitly contains `affected_paths: []` is
noncanonical and must be rejected by MCP/frontend raw-response guards. The
backend receipt equality check likewise rejects a stored explicit-empty body
after sparse reserialization.

### 3.7 Two-stage assessment lattice

The assessment has an anchor stage and evidence stage.

Anchor failure is always `indeterminate`: no declaration, unbound workspace,
invalid input, unsupported runtime, non-worktree/unborn state, unresolvable or
non-commit baseline, split-index metadata, or baseline not ancestral to captured
`HEAD`.

After a stable anchor:

1. Run two bracketed sweeps against the same full `HEAD` OID and logical index
   identity.
2. A relevant difference reliably observed in both sweeps yields `changed`.
   The displayed list may be incomplete; one sound change is sufficient.
3. If no sound change repeats, any completeness blocker—unmatched pattern,
   directory ambiguity, assume-unchanged, skip-worktree, enabled or path-valued
   `core.fsmonitor`,
   `core.fileMode=false`, content normalization/filter, symlink, sparse state,
   gitlink interior, command/resource failure, or moving state—yields
   `indeterminate`.
4. Only two stable, complete, zero-difference sweeps yield `unchanged`.

A changed `HEAD` restarts the entire assessment once. A second `HEAD` move, any
index-identity move, differing observations, timeout, signal, or malformed
output is `indeterminate`. Evidence against an abandoned anchor does not win.
Output truncation does not weaken a stable `changed` result.

The result is best-effort point-in-time evidence, never an atomic filesystem
snapshot or semantic proof.

### 3.8 Repository selection and branch context

Remote-URL normalization is out of Phase 10. Project URLs are optional,
current, mutable strings; treating remote spelling as identity creates
pseudo-security.

The client invokes only from the current repository workspace explicitly put
in scope by the user/session. If multiple checkouts could be intended, it
returns caller-side `repository_unbound` and requests the choice. The helper
discovers the top level from inherited CWD and receives no project ID, URL,
dynamic root, or branch.

`repository_url` may be displayed as untrusted project context but is never
resolved, normalized, sent to Git, or contacted.

`repository_branch` remains a caller declaration displayed beside the result.
It never crosses the shell or becomes a revision. Phase 10 does not compare
branch names programmatically; exact baseline-to-HEAD content is the anchor.
Detached HEAD is therefore allowed when it resolves to a commit. Documentation
must not imply a branch match was checked.

### 3.9 Governing checkpoint selection

Assess only the full checkpoint whose assertions will govern an action:

- `current_context`, otherwise `initial_checkpoint`;
- an older/recent checkpoint only when its unique assertion is relied upon;
- completion when explicitly auditing completion claims.

Do not assess every bounded history row. Page full history only when needed.
Alias and canonical-root histories remain exact and separate.

### 3.10 Advisory effect

| Result | Client action | Forbidden inference |
| --- | --- | --- |
| `unchanged` | Say no relevant eligible Git change was observed; show branch/coverage notices; continue only under present authority. | Correct, safe, current, verified |
| `changed` | Warn that the checkpoint may be stale; show bounded evidence; reinspect current source. | Every assertion is false |
| `indeterminate` | Give stable reason; inspect manually or obtain repository choice. | No output means unchanged |

No result grants authority or changes gates, readiness, claims, merges,
lifecycle, versions, events, activity, receipts, or server state.

---

## 4. Requirement identifiers

| ID | Requirement |
| --- | --- |
| RFV-001 | Persist ordered scope on immutable full checkpoints. |
| RFV-002 | Make omitted/empty input one unknown value and canonical output sparse. |
| RFV-003 | Require baseline for non-empty scope. |
| RFV-004 | Enforce the exact ASCII grammar and byte bounds at every layer. |
| RFV-005 | Preserve all production facts with empty-only historical migration. |
| RFV-006 | Preserve every frozen receipt request/response vector. |
| RFV-007 | Bind new scope to receipt hashing and coherence. |
| RFV-008 | Keep pointers/events/search/derived state scope-free. |
| RFV-009 | Keep backend/MCP/browser repository-blind. |
| RFV-010 | Use only the explicitly selected current local workspace. |
| RFV-011 | Implement the three-state, two-stage client-local lattice. |
| RFV-012 | Cover committed, staged, unmerged, unstaged, and nonignored-untracked evidence. |
| RFV-013 | Prove each pattern matches independently before `unchanged`. |
| RFV-014 | Fail closed on relevant index flags, enabled fsmonitor configuration, filters, gitlinks, sparse/split state, errors, and races. |
| RFV-015 | Prevent stored content from becoming shell/Git syntax. |
| RFV-016 | Guarantee no Git-triggered process, repository write, or network access. |
| RFV-017 | Freeze protocol, reason ownership, exit codes, output order, and caps. |
| RFV-018 | Warn/reinspect without server effects or authority inflation. |
| RFV-019 | Keep browser declaration-only and accessible. |
| RFV-020 | Preserve Phase 9 alias/root ownership and invariants. |
| RFV-021 | Ship coordinated `0.5.0`/`0.9.0`/`0018`. |
| RFV-022 | Refuse data-losing downgrade and post-scope old binaries. |
| RFV-023 | Document exclusions, privacy sinks, and operational incompatibility. |
| RFV-024 | Prove packaged behavior in disposable repos and cold sessions. |

---

## 5. Persistence and database invariants

### 5.1 Migration `0018_repository_freshness`

Create `backend/alembic/versions/0018_repository_freshness.py` with
`down_revision = "0017_duplicate_suggestion_title_key"`.

Upgrade:

1. Create `mnemonic_affected_paths_valid_v1(VARCHAR[])` or equivalently unique
   versioned immutable validator.
2. Add the not-null array with empty server default.
3. Add grammar/bounds/duplicate constraint.
4. Add non-empty-requires-commit constraint.
5. Assert validity without changing prior functions/triggers.

The validator handles both PostgreSQL empty-array representations; rejects
multidimensional/non-1-bound arrays and null elements; applies count,
per-entry byte, aggregate byte, component, star-run, and duplicate rules; and
uses UTF-8 byte comparison or deterministic `C` collation for exact duplicates.
Database encoding must be UTF-8.

ORM/Alembic metadata agrees on element type, nullability, default, and checks.
No index, generated column, side table, event, or inferred backfill exists.

### 5.2 Preservation fixture

Populate `0017` with every checkpoint kind, legacy/native history,
branch/commit variant, lifecycle/alias state, relationships, gates, leases,
events, embeddings, suggestion cache, merges, and all 13 receipts.

After upgrade:

- all scope arrays are empty;
- all pre-0018 checkpoint columns are byte-identical;
- all other row counts/digests are exact;
- receipt fingerprints, salts, request/response versions, bodies, kinds, and
  completion states are exact;
- opaque metadata keys are untouched and not interpreted;
- Phase 9 function/trigger/index hashes and behavior remain exact.

### 5.3 Ownership and immutability

Existing checkpoint UPDATE/DELETE guard protects the new column. Later
checkpoints and duplicate merges never copy/coalesce scope. Alias history and
separately recalled root history retain their own declarations. Events retain
only existing checkpoint references/metadata.

### 5.4 Conditional downgrade

Under `ACCESS EXCLUSIVE`, downgrade checks for any non-empty array. If one
exists it raises before DDL, with no force option. If none exists it drops
constraints, column, then function.

Two-connection tests prove no insert race. After scoped use only fix-forward or
whole-database restore is allowed; no shadow copy or destructive downgrade.

### 5.5 Parity and audits

Test migration head, array element/shape/default, constraints, deterministic
function signature/volatility/search path/body hash, unchanged immutability
trigger, and no index. Operations gets read-only invalid-array,
commit-dependency, trigger/function, and receipt-drift audits.

---

## 6. Backend, receipt, and REST design

### 6.1 Shared validation and writes

One table-driven backend validator implements the ASCII byte grammar for every
checkpoint kind. Validation order is list/type, entry bytes, grammar,
duplicates, aggregate bytes, then commit dependency. It never rewrites input
or accesses Git/filesystem.

Add the ORM column and use existing create/add/complete transactions. Scope
causes no extra event, version, activity touch, cache invalidation, embedding,
or suggestion.

Add the field/index to safe validation-location vocabulary without reflecting
raw values.

### 6.2 Full versus compact reads

Non-empty scope flows through:

- create's `initial_checkpoint`;
- add-checkpoint response;
- completion's `checkpoint`;
- checkpoint history;
- full initial/current/recent WorkContext slots;
- MCP resources/resume prompt built from full context.

Empty stays absent. Audit explicit SQL/JSON projections.
`CheckpointPointer`, search, hierarchy, gates, relationships, readiness,
events, embeddings, suggestions, and cache keys remain unchanged.

### 6.3 Receipt requests and responses

Freeze all 13 existing request fingerprints and response digests first.

Prove omission and explicit-empty request retain old bytes/hash; non-empty and
ordering affect hash; changed same-UUID scope conflicts; all unrelated kinds
remain exact.

Historical responses validate to internal empty, sparse-reserialize exactly,
and replay original JSON. New non-empty responses include scope and coherence
checks it. Never rewrite bodies, bump contract versions, bypass guards, or
create old/new unions.

### 6.4 Exact error precedence

Do not reorder the shipped protected-mutation pipeline:

1. authentication and structural request validation;
2. operation-kind/fingerprint conflict or exact completed/in-progress replay;
3. project/work visibility and authorization;
4. response/request coherence and current-state domain guards in their
   existing operation-specific order.

Historical exact replay remains recoverable after later alias, gate, lease,
version, lifecycle, or blocker changes. Add those regression cases. Path
validation cannot leak unauthorized resource existence.

### 6.5 REST/OpenAPI

Expose optional scope only on full checkpoint input/read schemas. Document
ASCII grammar, byte limits, commit dependency, sparse response, and example
`src/**`. Add no route/query/filter/envelope/error/effect.

Regenerate `docs/openapi.json`; do not hand-edit it.

---

## 7. MCP adapter

### 7.1 Strict parity

Use the same input validation and Pydantic floor. Inputs accept omission/empty.
Before Pydantic normalization, raw response guards accept absence or a valid
non-empty list and reject explicit empty. Preserve order/case and strict
unknown-key behavior.

Include non-empty scope in request matching/coherence and redact invalid
values. Audit direct WorkContext decoding.

### 7.2 Existing surfaces only

Update `create_work`, `add_checkpoint`, `complete_work`, full recall/
claim-and-recall/history, resources, and resume prompt. No tool executes Git or
accepts repo root/status/changed files. Counts stay 27/11.

Descriptions define dependency scope, unknown empty, actually inspected
commit, declaration-only server role, unsupported state, and full-recall
requirement.

### 7.3 Tests

Cover sparse input/output, explicit-empty response rejection, grammar/bounds,
coherence, replay/conflict, redaction, full context, unchanged pointers,
catalog/resource/prompt parity, and no subprocess/Git/filesystem/network code.

---

## 8. Local plugin verifier

### 8.1 Packaged boundary and runtime floor

Add:

- `plugin/bin/mnemonic-repository-freshness`;
- `plugin/reference/repository-freshness.md`.

Keep three skills and no `allowed-tools`. Invoke the executable through quoted
`${CLAUDE_PLUGIN_ROOT}`.

Runtime requires Bash `>=3.2` and Git `>=2.45.0`. The helper checks Bash using
built-ins, then runs only trusted-host `git --version` before any repository or
object command. Failure emits `unsupported_bash_version` or
`unsupported_git_version` and performs no repository read. Git 2.45 is required
for enforced no-lazy-fetch behavior; an older Git is never used optimistically.

Runtime dependencies are Bash and Git only. The client execution facility
enforces the 15-second whole-process-group deadline; timeout/signal/partial
output is caller-side `timed_out` or `malformed_helper_result`. Test harnesses
may use host utilities for measurement, but the installed helper does not.

### 8.2 Injection-safe fixed invocation

The helper inherits the current directory and takes:

```text
--baseline <7-to-64 lowercase-or-uppercase hexadecimal bytes>
--path '<validated ASCII scope>'  # repeated 1..64
```

It receives no dynamic repository root, project URL, branch, refname, command,
config, or output destination. The plugin invokes from the explicitly selected
workspace with this fixed template:

```text
"${CLAUDE_PLUGIN_ROOT}/bin/mnemonic-repository-freshness" \
  --baseline a832bc1 \
  --path 'src/**' \
  --path 'tests/test_api.py'
```

Baseline is hex-only. Each path is single-quoted; the persisted grammar cannot
contain a quote, whitespace, newline, expansion, operator, or option boundary.
`--path` consumes its next argv as data even if it begins `-`. The helper
revalidates everything before its first repository-aware Git command. No
`eval`, dynamic shell source, command substitution from data, or shell glob
occurs.

### 8.3 Exact environment and Git hardening

At startup, retain only ordinary host execution variables required to locate
Bash/Git. Using Bash built-ins, unset every inherited `GIT_*` variable, every
`GCM_*` variable, `GLOBIGNORE`, and pager/editor/askpass/SSH/trace shell
steering before resolving the absolute trusted-host Git executable.

Then set:

```text
LC_ALL=C
LANG=C
GIT_OPTIONAL_LOCKS=0
GIT_NO_LAZY_FETCH=1
GIT_NO_REPLACE_OBJECTS=1
GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_SYSTEM=/dev/null
GIT_CONFIG_GLOBAL=/dev/null
GIT_ATTR_NOSYSTEM=1
GIT_TERMINAL_PROMPT=0
GCM_INTERACTIVE=never
GIT_PAGER=cat
```

Every repository-aware call uses an absolute resolved Git executable,
`git --no-replace-objects --no-pager -C "$top"`, explicit `--`, and applicable:

```text
-c core.fsmonitor=false
-c core.ignoreStat=false
-c core.checkStat=default
-c core.trustctime=true
-c core.quotePath=true
-c core.attributesFile=/dev/null
-c core.excludesFile=/dev/null
-c advice.graftFileDeprecated=false
--no-ext-diff
--no-textconv
--no-renames
--ita-visible-in-index
--ignore-submodules=none
```

Repository `.gitignore` and `.git/info/exclude` still define nonignored
untracked content. User-global ignore/attribute files are disabled for
determinism. Only the known non-error graft deprecation advisory is suppressed;
every other Git stderr byte remains a fail-closed error. Do not force
`core.fileMode`: read it safely, use it when true, and apply
`core_filemode_scope_unsupported` before a zero result when false.


Raw Git stderr is captured/suppressed and mapped to a reason; it never reaches
the protocol, model context, terminal, logs, or checkpoint.

### 8.4 No-process worktree boundary

The helper never fetches/clones/deepens; mutates refs/index/worktree/config/
objects; initializes; changes `safe.directory`; invokes hooks, aliases,
external diffs, textconv, fsmonitor, pager, editor, credentials, SSH, filters,
or a remote; or creates a file.

Attribute preflight is not an execution barrier. The helper never calls
`git diff-files`, `git status`, patch-producing `git diff`, or another
index-to-worktree conversion path. Its sole `git diff` use is a per-file
`--no-index --raw` comparison between `/dev/null` and an already classified
regular path. Git's no-index queue derives the mode with `lstat`; raw output
bypasses patch generation, content hashing, attributes, user diff drivers, and
clean/process filters. The call also forces `--no-ext-diff`, `--no-textconv`,
`--no-renames`, `--no-relative`, and `-O/dev/null`.

For each scoped stage-zero index entry, the worktree lane:

1. classifies index mode and refuses symlink/gitlink/special entries as
   completeness blockers for zero;
2. checks existence/type with quoted Bash file tests immediately before and
   after reading;
3. reads a regular file through quoted stdin into
   `git hash-object --no-filters --stdin` without `-w`;
4. compares that raw OID with the index OID;
5. reads Git's canonical `100644` or `100755` owner-execute mode through the
   no-index raw metadata call and compares it only when `core.fileMode=true`;
6. rejects malformed mode output or a path/type race rather than using Bash
   process-access tests as a proxy for the owner-execute bit;
7. repeats the same observation in the second bracketed sweep.

`--no-filters` is mandatory and no `--path` hint is supplied. Thus repository
clean/process filters cannot execute even if attributes/config change during
the check.

Any scoped content-conversion condition—`filter`, `text`, `eol`, `ident`,
`working-tree-encoding`, or non-false `core.autocrlf`—is an unconditional
zero-result completeness blocker, even when raw bytes equal the index blob:
raw equality does not prove the converted canonical result. `filter` yields
`external_filter_scope_unsupported`; the built-in conditions yield
`normalization_scope_unsupported`. If raw bytes differ under one of these
conditions, the raw lane also remains indeterminate unless a different stable,
filter-free committed/index/untracked observation independently establishes
`changed`. `git check-attr -z --all` is used only to classify this safe raw
comparison and is repeated; every explicit relevant declaration, including a
negative one, blocks zero because named porcelain output cannot distinguish
literal `unset`/`unspecified` values from sentinel states. It is never followed
by a filter-capable content command.

If `core.fileMode=false`, content changes remain observable through raw hashes,
but a zero result is `core_filemode_scope_unsupported`. A true setting permits
comparison of the no-index raw Git mode against index mode. Bash `-x` is never
used because process access is not equivalent to Git's owner-execute bit under
different ownership, ACLs, `noexec` mounts, or root. Any mode/type race is
`state_changed_during_check` or a fail-closed worktree-lane error.

No temp file/directory is used. Any design needing configured conversion, an
extra runtime, process sandbox, or temporary persistence returns to review.

### 8.5 Exact object, pattern, and index preflight

1. Discover top level from CWD; reject non-worktree, bare, unsafe ownership,
   and unborn HEAD without changing configuration. Require the Git directory
   to be listable and reject any `sharedindex.*` artifact before the first
   index-aware command. Git 2.45 refreshes a live shared index's mtime on every
   read, so split-index support is incompatible with RFV-016 without a temp
   shadow, native helper, or read-only filesystem sandbox.
2. Resolve the hex name without peeling. Stream and drain disambiguation
   output, retaining only the first candidate and a count capped at two.
   Require the candidate's own `cat-file -t` type to be exactly `commit`;
   reject tag/tree/blob/missing/ambiguous IDs.
3. Capture full current `HEAD`. Require baseline equal/ancestor; parse
   merge-base `0` ancestor, `1` non-ancestor, `>1` failure.
4. Compile helper-owned top literal/glob pathspecs.
5. For each declaration independently, prove an eligible match across
   baseline, captured HEAD, index, or nonignored untracked worktree. Report
   only zero-based unmatched indexes.
6. For each exact entry, inspect modes and descendant prefixes. A directory
   requires `directory/**`.
7. Independently detect gitlink prefix intersections at baseline, HEAD, and
   index. Stable gitlink OID cannot prove interior.
8. Globally enabled sparse checkout/index and a scoped persisted
   sparse-directory index entry are zero-result blockers. If sparse settings
   are manually disabled while a sparse index remains on disk, a supported Git
   version may instead diagnose its required expansion; strict stderr handling
   returns `git_failed` on the index lane without asserting zero or writing the
   repository.
9. Inspect scoped `git ls-files -v --sparse -z`: lowercase assume-unchanged and
   uppercase `S` skip-worktree are separate zero-result blockers even when
   sparse config is off. Classify an `S` sparse-directory record ending in `/`
   as sparse state rather than a symlink or ordinary skip-worktree entry. Never
   clear either bit.
10. Read effective repository/worktree `core.fsmonitor` through read-only,
    source-selected config queries while every repository-aware Git process
    still receives `-c core.fsmonitor=false`. Any enabled or path-valued setting
    is a scoped zero-result blocker. Never run an unforced `git ls-files -f` or
    `git ls-files --debug`: on supported Git, suppressing the monitor masks its
    per-entry valid bit from both views. Dormant persisted valid bits cannot
    suppress the helper's raw hash of every scoped stage-zero regular file.
    Override `core.ignoreStat=false`.
11. Read `core.fileMode` through hardened Git; false is the zero-result blocker
    described above.

Safe committed/index evidence may still produce `changed` under the two-stage
lattice. These blockers prevent only an assertion of complete zero.

### 8.6 Logical index identity and repeated sweeps

Before and after each sweep, pipe these NUL streams to
`git hash-object --no-filters --stdin` without `-w`:

- `git ls-files --stage --sparse -z`;
- `git ls-files -v --sparse -z`.

Check all `PIPESTATUS` members. These logical hashes cover index stages,
assume-unchanged, and skip-worktree in ordinary indexes and linked worktrees.
Split indexes are rejected before these streams. Each sweep separately rereads
effective fsmonitor configuration through bounded NUL records and includes its
blocker state in the repeated observation.

Each sweep, using captured OIDs rather than symbolic `HEAD`, records:

1. baseline-to-HEAD committed changes with renames off;
2. HEAD-to-index staged/type changes;
3. unmerged stages through `git ls-files --unmerged --sparse -z`, which avoids
   expanding and writing an active sparse index;
4. the raw filter-free content comparison and fixed no-index raw mode
   observation in section 8.4;
5. nonignored untracked paths.

Two sweeps must repeat at least one sound observation for `changed`, or both
must be complete zero for `unchanged`. Other disagreement is indeterminate.
One HEAD move restarts; a second or any index-identity move does not.

Command/status tables are fixtures. Tree/index diff status `0` is zero, `1`
is difference, and `>1` is failure. The fixed no-index mode call instead
requires exit `1`, exactly two NUL records, an add record with two all-zero
full OIDs, canonical regular mode, and the exact absolute input path. Never map
generic nonzero to clean or changed.

### 8.7 Versioned ASCII protocol

Valid helper stdout is ASCII with one terminal newline and keys in this order:

```text
protocol=mnemonic-repository-freshness-v1
state=unchanged|changed|indeterminate
reason=<registered_reason>
baseline_oid=<full_hex_or_dash>
head_oid=<full_hex_or_dash>
pattern_count=<unsigned_decimal>
matched_pattern_count=<unsigned_decimal>
displayed_path_count=<0_to_100>
paths_truncated=0|1
detail=<registered_reason_detail>    # reason-defined count
path_byte_q=<ascii_byte_escaped>     # displayed_path_count entries
disclaimer=git-state-only-not-semantic-proof
```

All `detail` lines precede all path lines. A byte encoder emits bytes from
`A-Z a-z 0-9 . _ / @ + = , ~ -` unchanged and every other filename byte as
uppercase `\xHH`. It operates under `LC_ALL=C`, is used for actual Git
filenames only, and is tested for every byte `01` through `FF`; Git filenames
cannot contain NUL. Filesystems that reject the exhaustive non-UTF-8 filename
with `EILSEQ` retain coverage through a valid UTF-8 corpus containing controls,
punctuation, DEL, and stable multibyte characters; the Linux lane retains the
exhaustive raw-byte corpus. Encoded values are display-only and never decoded,
evaluated, or passed back to a command. Branch is intentionally absent from
the protocol.

Path order is first observation by lane (committed, staged, unmerged,
unstaged, untracked) and Git byte order within a lane, deduplicated by first
occurrence. Retain at most 100. Additional observations set
`paths_truncated=1`; stdout never exceeds 32 KiB. Do not retain a raw path
candidate longer than 8,192 bytes. Repeated anchored committed, staged, or
unmerged difference status can remain sound `changed` evidence after such a
name is dropped; an over-cap worktree or untracked observation cannot alone
become stable `changed` because its identity was not retained.

Exit mapping:

| Exit | Meaning |
| --- | --- |
| `0` | Valid `unchanged` body |
| `10` | Valid `changed` body |
| `20` | Valid `indeterminate` body |
| `64` | Invalid invocation, no trustworthy body |
| `70` | Internal failure, no trustworthy body |

Client parser requires exact order/counts, ASCII, size, terminal newline, known
keys/reason, and body/exit agreement. Exit 64/70, stderr, partial/extra output,
timeout, or signal becomes caller-side failure.

### 8.8 Exhaustive reason and detail contract

Caller reasons never appear in helper protocol:

| Caller reason | State | Detail |
| --- | --- | --- |
| `no_scope` | indeterminate | none |
| `no_baseline` | indeterminate | none |
| `repository_unbound` | indeterminate | none |
| `timed_out` | indeterminate | none |
| `malformed_helper_result` | indeterminate | none |

Helper protocol pairings:

| Reason | State | OIDs | `detail` | Paths |
| --- | --- | --- | --- | --- |
| `unsupported_bash_version`, `unsupported_git_version`, `invalid_declaration`, `not_a_worktree`, `bare_repository`, `unborn_head`, `baseline_missing`, `baseline_ambiguous`, `baseline_not_commit`, `baseline_not_ancestor`, `split_index_unsupported` | indeterminate | both `-` | none | none |
| `pattern_unmatched`, `exact_directory_requires_recursive_glob`, `assume_unchanged_scope_unsupported`, `skip_worktree_scope_unsupported`, `fsmonitor_scope_unsupported`, `submodule_scope_unsupported`, `external_filter_scope_unsupported`, `normalization_scope_unsupported`, `symlink_scope_unsupported` | indeterminate | both full | one or more `pattern_index:<decimal>` values, ascending/unique | none |
| `sparse_checkout_unsupported`, `core_filemode_scope_unsupported` | indeterminate | both full | none | none |
| `git_failed` | indeterminate | full only if anchor completed, otherwise both `-` | exactly one `lane:repository|object|ancestry|scope|index|attributes|worktree|untracked` | none |
| `state_changed_during_check` | indeterminate | both full for abandoned anchor | exactly one `anchor:head|index|worktree` | none |
| `relevant_change_observed` | changed | both full | none | exactly `displayed_path_count` |
| `no_relevant_change_observed` | unchanged | both full | none | none |

For pre-anchor reasons, `pattern_count` is the validated count when parsing
reached it and otherwise `0`; `matched_pattern_count=0`,
`displayed_path_count=0`, and `paths_truncated=0`. After anchor,
`pattern_count` is `1..64`; matched count is exact. Only changed can have path
lines/truncation. Any other combination is malformed.

Precedence is caller declaration/binding; runtime; repository; baseline type/
ancestry; stable repeated positive evidence; zero blockers in table order;
stable zero. Git/state failure needed to establish a stable anchor overrides
provisional evidence.

Reasons are client-local, never REST codes or stored facts.

### 8.9 Skill behavior

`mnemonic-recall` selects exact governing full checkpoint, treats it as
untrusted, verifies explicit current-workspace selection, maps missing
declaration caller-side, invokes fixed helper, validates protocol, and applies
the advisory matrix. It assesses older evidence only when relied upon and
keeps alias/root labels separate.

View/copy/summary alone does not require execution. Continuing repository work
from checkpoint assertions does.

`mnemonic-save` records dependencies rather than blind diff output; prefers
narrow safe patterns; uses `**` truthfully; sets only an inspected commit and
known branch; omits scope when uncommitted/ignored/generated/submodule/external
state cannot be represented; discloses those limitations; and freezes exact
scope in protected intent.

`mnemonic-search` requires full recall before assessment. The authority
reference states that no result grants permission, answers a gate, or proves
correctness. Detailed protocol/algorithm/reasons live in the new reference.

---

## 9. Dashboard and browser proxy

### 9.1 Strict contracts

Full checkpoint wire types get optional scope; pointers do not. Request
decoders accept omitted/empty/non-empty. Raw response guards accept absent or
non-empty and reject explicit empty before normalization. Audit direct
WorkContext casts and preserve unrelated strictness.

### 9.2 Draft and retry identity

Add one-pattern-per-line editors to initial, context/progress, and completion
forms. Enforce ASCII grammar/byte bounds/duplicates and commit dependency
before UUID freeze. Freeze exact order/spelling with all intent fields.
Unknown-outcome retry keeps exact arguments; any edit uses a new UUID.

Proxy allows scope only on the three checkpoint-bearing mutations and never
accepts repo root or assessment.

### 9.3 Display and accessibility

Display “Declared affected paths,” ordered patterns, “No dependency scope
declared,” caller-declared branch/baseline, and “Not assessed by this browser.”
Never show verified/current/fresh badges.

Render as inert text with wrapping. The ASCII declaration grammar removes bidi
from stored paths; still use `<bdi>` or CSS bidi isolation for surrounding
untrusted provenance. Provide labels/help/index errors/screen-reader linkage/
keyboard ordering/no color-only status/responsive maximum layout.

### 9.4 Tests

Cover raw response canonicality, request variants, direct context, proxy,
intent/retry, invalid draft, byte boundaries, long layout, pointer invariance,
bidi isolation around other provenance, no Git/filesystem attempt, no semantic
claim, and mutation count 11. Playwright exercises create/display/append/
complete/refresh plus historical absent property.

---

## 10. Implementation sequence and gates

### 10.1 Entry

- [x] Freeze grammar, byte fixtures, sparse vectors, state lattice, reason
      ownership, protocol, commands/statuses, runtime floors, and inventories.
- [x] Freeze all 13 request/response receipt vectors before model changes.
- [x] Add historical nested response fixtures.
- [x] Prove Pydantic exclusion under exact dump/nesting.
- [x] Review DB/backend/MCP/frontend/helper/security/operations contract.

### 10.2 A — database and backend model

- [x] Add `0018`, validator, constraints, guarded downgrade.
- [x] Add ORM/shared validation and Pydantic floor/lock.
- [x] Add populated preservation, SQL parity, race, immutability tests.
- [x] Keep all receipt vectors exact.

### 10.3 B — projections and receipts

- [x] Add scope to full writes/reads only.
- [x] Keep compact/derived systems unchanged.
- [x] Add canonical response and coherence/idempotency tests.
- [x] Regenerate OpenAPI.
- [x] Add authorization, precedence, alias/root, concurrency regressions.

### 10.4 C — MCP

- [x] Add strict input/raw-output handling and dependency floor.
- [x] Update existing descriptions/resources/prompts.
- [x] Prove catalogs and repository-blind adapter.

### 10.5 D — helper and plugin

- [x] Implement runtime/env/process/protocol contract.
- [x] Build disposable matrix before skill wiring.
- [x] Add reference and update three skills/authority.
- [x] Test source and installed packaging/mode.
- [x] Pass cold-session workflow smoke.

### 10.6 E — dashboard and release

- [x] Add strict frontend/editor/display/proxy and tests.
- [x] Update versions, locks, manifests, docs/examples.
- [x] Run all standard/DB/E2E/helper/security suites.
- [x] Rehearse quiesced upgrade, fix-forward, pre-use downgrade, restore.

Each increment stops on a red prior gate. There is no compatibility subrelease.

---

## 11. Verification plan

### 11.1 Cross-layer corpus

Valid: exact file, `src/**`, `tests/test_*.py`, component `*`, whole `**`,
leading hyphen after `--path`, 64 entries, 512 bytes, and 16,384 aggregate
bytes.

Invalid: empty/component, whitespace/non-ASCII, absolute/drive/UNC/backslash,
`.`/`..`, repeated/trailing slash, controls, `?`, quotes/shell syntax,
bracket/brace/pathspec magic, malformed stars, duplicates, null/multidimensional
SQL arrays, 65/513/16,385 bounds, and scope without commit.

One fixture corpus and fuzz/property generator proves SQL/Python/MCP/TypeScript/
Bash agreement byte for byte.

### 11.2 Migration and receipts

Test fresh zero-to-0018 and populated 0017-to-0018; exact prior rows/digests/
receipt bodies/fingerprints/versions; historical empty scope; Phase 9 function/
trigger/index hashes; direct SQL boundaries; ORM parity; immutability;
empty-only downgrade; non-empty refusal; two-connection race; re-upgrade.

For all three checkpoint mutations prove omitted/empty old hash, non-empty/order
hash, conflict, exact replay without duplicate effect, later-state replay, and
explicit-empty response rejection. Freeze all ten unrelated receipt kinds.

### 11.3 Backend/MCP/read matrix

Cover every checkpoint kind and full projection, history pagination, source
alias versus root, authorization/isolation, mutation guards, concurrent
appends, no extra effects, sparse JSON, unchanged pointers/events/search/
semantic/cache/rank behavior, strict MCP/resource/prompt/catalog parity, and
absence of repository code in server/adapter.

### 11.4 Disposable Git matrix

Topology/object:

- same HEAD, clean descendant, and relevant committed add/modify/delete/type/
  rename/merge;
- missing/ambiguous ID and blob/tree/annotated-tag object ID;
- divergent baseline, unavailable partial/shallow object, no-network sentinel,
  disabled replacement refs;
- unborn/bare/non-repo/linked worktree/nested CWD/unsafe ownership.

Patterns:

- every valid/invalid grammar byte;
- every pattern matched independently;
- valid-plus-typo, overlaps, baseline-only delete, new-untracked-only;
- exact directory versus recursive glob;
- gitlink prefix/whole-scope intersection;
- maximum argv and rejection before Git.

Index/worktree:

- staged/unstaged add/modify/delete/type, unmerged, intent-to-add;
- nonignored untracked, ignored-only unmatched, unrelated dirt;
- manually set skip-worktree with sparse config off and modified/deleted bytes;
- assume-unchanged modified bytes, `core.ignoreStat`, enabled/path-valued
  `core.fsmonitor`, and dormant valid bits with fsmonitor disabled;
- `core.fileMode=false` plus executable-bit-only drift;
- active and config-disabled on-disk sparse indexes with fail-closed,
  zero-write snapshots, plus zero-write split-index rejection;
- regular raw hash equality/difference;
- CRLF/text/eol/ident/working-tree-encoding normalization;
- external clean/process filter attempts to write/connect, proving no filter
  process launches;
- symlink and special-file scope.

Races:

- one/two HEAD moves;
- index stage/flag changes between every lane;
- split-index artifacts and shared-index mtime preservation;
- attribute/config changes around raw comparison;
- worktree change/revert and inconsistent observations;
- timeout, signal, permission, malformed Git output.

Hardening/protocol:

- every named environment/config/pathspec/trace/SSH family;
- diff/textconv/filter/fsmonitor/pager/editor/alias/hook/credential sentinels;
- no network, child filter process, repository mutation, or raw stderr;
- every byte encoder case;
- exact reason/state/OID/detail/path cardinality, key order, exits, lane order,
  dedupe, limits, and malformed combinations;
- Git 2.44 rejection before object access and Git 2.45+ no-lazy behavior.

### 11.5 Frontend and plugin workflow

Frontend unit/E2E covers strict response forms, editor, proxy, retry identity,
display/a11y/bidi isolation, historical absence, and no browser verifier.

Static plugin tests cover exactly three skills, new reference/binary inventory,
`${CLAUDE_PLUGIN_ROOT}` links, installed executable, fixed invocation,
governing-only recall, dependency-oriented save, search-to-recall, and
authority language.

Cold sessions cover unchanged, each changed lane, caller/executable
indeterminate, branch provenance display, alias/root separation, view-only behavior, and
stored prompt/path injection.

### 11.6 Privacy and sink tests

Treat declarations, branch, unresolved SHA, project URL, Git config, filenames,
helper output, shell transcript, tool transcript, conversation/model context,
logs, and telemetry as sinks.

Assert raw paths/remotes/roots/errors do not enter routine logs/telemetry;
credential URLs never print; actual filenames are quoted/capped before model
context; stored ASCII paths remain unexecuted; sensitive output is not copied
to checkpoints/events automatically.

### 11.7 Required commands

```sh
pre-commit run --all-files
docker compose -f compose.test.yaml up -d --wait

cd backend
uv sync --frozen
TEST_DATABASE_URL=... uv run pytest -q
uv run ruff check .
uv run ty check src

cd ../mcp
uv sync --frozen
TEST_DATABASE_URL=... uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp

cd ../frontend
npm ci
npm test
npm run typecheck
npm run build
npm run test:e2e:stack
```

Also run populated migration/replay, OpenAPI regeneration, Git 2.44/2.45
capability fixtures, helper security matrix on supported Bash hosts, and
installed cold-session smoke. A skipped DB/E2E/security lane is not full
validation.

Performance tests report tracked/untracked counts, patterns, history distance,
Git/Bash/OS/filesystem/runner hardware, cache state, repetitions, median/max
duration, peak RSS, and output bytes. Functional release gates are the
15-second caller-enforced process-group timeout, capped helper-retained data,
and 32-KiB output—not a hardware-agnostic RSS number. Git and Bash enumeration
cost remains proportional to the trusted local repository.

---

## 12. Deployment, recovery, and audits

### 12.1 Preflight and rebase

Before implementation/release, fetch current `origin/main`, rebase the topic
branch, rerun the full surface audit, and update the recorded baseline if it
changed.

Production preflight confirms `0017`/Phase 9 versions, release source/OpenAPI,
integrity and guard hashes, counts/digests, lock estimate, restorable backup,
and every deployed first-party client/plugin version. It is read-only and
creates no scope.

### 12.2 Rehearsal

Restore production-shaped backup; rehearse populated migration/digests, old
receipt replay, scoped writes/reads, strict old-client failure, pre-use
downgrade, post-use refusal, fix-forward, whole restore, dashboard, and
installed helper. Record locks/timings/counts/host capabilities.

### 12.3 Quiesced rollout

1. Announce maintenance and required client/plugin minimums.
2. Quiesce reads and writes that could cross first-party versions.
3. Apply `0018`; verify schema/constraints/functions/trigger/empty history.
4. Deploy backend/MCP/dashboard `0.5.0` and plugin `0.9.0`.
5. Verify no `0.4.x` first-party service is serving.
6. Run old-receipt, new-scope, dashboard, and three-state helper smoke.
7. Resume traffic.

There is no claim of an atomic update for independently installed plugins.
Older plugins/strict clients are explicitly unsupported and may reject a new
non-empty checkpoint response; users must upgrade before reconnecting. No
server projection shim or feature-flag dual contract is added.

### 12.4 Rollback

Before any non-empty scope, components can roll back together and guarded
downgrade can remove the empty-only column.

After scoped use, only `0.5`-compatible binaries may serve. Fix forward, or
restore the whole database and matching binaries with explicit acceptance of
all post-backup data loss. Remove the older-backend bridge entirely: it hides
scope and cannot replay new scoped receipts.

Never copy scope into metadata or rewrite receipts.

### 12.5 Continuous audit

Detect schema/function/trigger drift, invalid/commitless arrays, unexpected
pre-enablement population, receipt mutation/version drift, catalog drift,
plugin version/mode/package drift, and unsupported first-party versions.

Do not collect paths, branches, SHAs, roots, remotes, Git stderr, or changed
filenames as routine server telemetry.

---

## 13. Security, privacy, observability, and resource bounds

### 13.1 Trust and sinks

| Data | Trust | Allowed sinks |
| --- | --- | --- |
| Scope | Untrusted caller declaration | Immutable row, authorized full reads, non-empty receipt body/hash, MCP/dashboard |
| Branch/baseline | Untrusted; baseline locally resolved | Existing authorized surfaces and bounded helper input/output |
| Current CWD/top | User-selected sensitive state | Helper process only |
| Project/remote URL | Mutable, possibly sensitive | Display context only; never helper/Git |
| Actual changed names | Untrusted local bytes | Git-quoted/capped tool and conversation/model context |
| State/reason | Ephemeral advisory | Current client output only |

Tool output sent to a hosted model is not “local only”; it is an explicit
privacy sink. Show only necessary quoted evidence and never automatic full
lists.

### 13.2 No-process/no-network invariant

Security review traces every environment/config/attribute/path from entry to
Git. The minimum-version gate and no-lazy-fetch setting precede object access.
Attribute/config reads only classify the filter-free raw comparison and never
authorize worktree conversion. Any path that could execute configured code,
prompt, fetch, or mutate fails closed.

Host `PATH`, the installed plugin directory, Bash executable, Git executable,
and client process launcher are trusted computing-base inputs, not checkpoint
data. Compromise there is outside the feature threat model and is documented.

### 13.3 Observability

Optional local metrics may contain state, reason, duration bucket, pattern/
display counts, truncation, and versions. They contain no declarations, names,
root, branch, SHA, URL, stderr, text, credential, or command string. The server
has no freshness metric because it performs no assessment.

### 13.4 Retained-data bounds and local scale assumption

- 64 patterns;
- 512 ASCII bytes each;
- 16 KiB aggregate input;
- 100 displayed paths;
- 8,192 raw bytes per retained path candidate;
- 32 KiB protocol stdout;
- two sweeps and at most one whole retry after HEAD movement;
- 15-second client-enforced whole-process-group wall clock.

Stream Git output and retain at most capped display values; abbreviated-object
resolution retains only its first candidate and a count capped at two; logical
identities are hashes, not full captured indexes. These are bounds on data the
helper intentionally retains and emits, not an absolute bound on Git/Bash
internals. Git must enumerate repository entries, and Bash 3.2's
`compgen -G` materializes split-index artifact matches before discarding them.
Repository entry count, config size, and stable local metadata therefore remain
trusted local scale inputs constrained by the caller's process-group deadline.
Timeout or retained-data bound failure is `indeterminate`, never partial
`unchanged`.

---

## 14. Expected implementation surface

### 14.1 Backend

- new `backend/alembic/versions/0018_repository_freshness.py`;
- backend models, schemas, validation locations, checkpoint writes/history/
  context projections, receipt coherence;
- backend dependency/lock;
- new migration test plus schema parity, validation, work-item, receipt,
  idempotency, context/history, hierarchy/search/duplicate, OpenAPI suites.

### 14.2 MCP and plugin

- MCP models/server/validation, dependency/lock, contract/catalog/resource/
  prompt tests;
- plugin inventory and dedicated disposable helper/security tests;
- new plugin binary and repository-freshness reference;
- authority reference and all three named skill documents;
- plugin/marketplace manifests.

### 14.3 Frontend

- full checkpoint wire/domain types;
- mutation response/raw guards, proxy policy, mutation intent as needed;
- dashboard and checkpoint timeline/focused editor;
- unit tests and `frontend/tests/e2e/phase10-repository-freshness.spec.ts`;
- package/lock/version.

### 14.4 Docs/artifacts

- generated `docs/openapi.json`;
- README, architecture, agents, operations, validation, roadmap, examples;
- ignored local `CLAUDE.md` only after shipped phase/migration/catalog/retry/
  error facts change.

There is no changelog file. This map is not permission for unrelated edits.

---
## 15. Documentation contract

Use “declared scope,” “caller-asserted baseline,” “no relevant eligible Git
change observed,” “relevant change observed,” and “indeterminate.” Never use
unqualified fresh/current/verified/correct/safe.

Docs state:

- empty is unknown; `**` means all eligible repository paths;
- v1 scope is narrow ASCII; each pattern must match;
- ignored untracked state, submodule interiors, sparse/index flags,
  `core.fileMode=false`, normalization/filter state, symlink targets,
  external/generated/runtime state are not proven;
- worktree content is compared raw and filter-free; normalization ambiguity is
  indeterminate;
- server/MCP/browser never inspect Git;
- user explicitly selects workspace; repository URL is not identity;
- Git 2.45/Bash 3.2 minimum and exact protocol;
- no fetch, configured process, or mutation;
- branch is displayed provenance only and not compared;
- unchanged is not semantic authority;
- pointers require full recall;
- output enters tool/model context and is privacy-sensitive;
- old clients are unsupported after non-empty scope;
- sparse serialization preserves historical DB/receipts.

Roadmap becomes Shipped only after definition of done.

---

## 16. Risk register

| Risk | Prevention | Proof |
| --- | --- | --- |
| Receipt drift | Field-local sparse canonical form | Frozen 13-kind vectors/replay |
| Explicit empty response | Raw strict response guard | API/MCP/frontend negative tests |
| History inferred/lost | Empty migration; downgrade refusal | Populated digest/race |
| Validator disagreement | ASCII/byte corpus | Cross-layer fuzz |
| One glob hides typo | Per-pattern matching | Mixed-scope tests |
| Directory literal recurses | Mode/prefix preflight | Directory fixture |
| Shell/pathspec injection | Narrow grammar/fixed quoted argv/helper prefix | Reject before Git |
| Wrong repo guessed | Explicit workspace choice | Ambiguity workflow |
| Tag object accepted | No peel; own type commit | Object matrix |
| Lazy fetch/network | Git 2.45/no-lazy/sentinels | Version fixtures |
| Git filter executes | Never use conversion-capable worktree command | Filter sentinel |
| Normalization false change | Raw hash plus normalization blocker | CRLF/attribute matrix |
| Environment redirects | Exact unset/set | Steering matrix |
| Assume/skip flag hides bytes | Scoped flags block zero | Modified/deleted fixtures |
| File-mode drift hidden | `core.fileMode=false` blocks zero | Chmod fixture |
| Split/sparse/index race | Reject shared indexes; logical ordinary-index identities/two sweeps | Race matrix |
| Git error means empty | Command status table | Fault injection |
| Submodule appears clean | Prefix intersection | Gitlink tests |
| Unstable observation | repeated evidence/anchor retry | Race tests |
| Protocol disagreement/injection | Exhaustive ASCII table/byte encoder | Parser fuzz |
| Browser/server claims verify | Declaration-only boundary | Static/E2E |
| Paths leak to model/logs | Encode/cap/sink inventory | Privacy audit |
| Alias scope crosses root | Exact recalls | Phase 9 regressions |
| Search/rank changes | Explicit exclusion | Cache/rank digests |
| Old backend breaks replay | Prohibit after scope | Version audit |
| Flaky perf threshold | Functional bounds/context benchmarks | Benchmark metadata |
| Unchanged grants authority | Vocabulary/skills | Cold sessions |

## 17. Explicitly deferred

- Unicode, spaces, `?`, brackets, escaping, arbitrary Git pathspecs;
- structured non-shell launcher protocol;
- blob/content/working-tree manifests and attestations;
- server mirrors/provider APIs/webhooks/clone/fetch;
- persisted/cached assessments;
- multi-repository and recursive submodule scopes;
- sparse-checkout semantics;
- ignored/generated artifact manifests;
- LFS content beyond pointer bytes and external symlink targets;
- semantic/symbol/build dependency graphs and inferred scopes;
- freshness-driven gates/readiness/leases/merge/completion;
- browser filesystem access and freshness dashboards/search;
- historical scope inference;
- non-Git repositories.

---

## 18. Definition of done

### 18.1 Contract and storage

- [x] RFV-001 through RFV-024 trace to implementation/tests.
- [x] Fresh/populated migration preserves every prior fact/receipt.
- [x] All validators agree on ASCII bytes.
- [x] Sparse canonical response is enforced, not merely emitted.
- [x] Downgrade cannot lose scope.

### 18.2 Product surfaces

- [x] Full checkpoints carry scope; compact/events/derived do not.
- [x] All old receipt vectors stay exact; new scope binds idempotency.
- [x] MCP remains 27 tools/11 writes; browser 11 mutations.
- [x] Browser is accessible/declaration-only.
- [x] OpenAPI and strict clients agree.

### 18.3 Helper and workflow

- [x] Runtime floor, environment, filter, index, object, pattern, sweep, and
      protocol contracts pass adversarial tests.
- [x] Every pattern independently matches before unchanged.
- [x] No process/network/repository mutation occurs.
- [x] All output is fixed ASCII/quoted/capped and privacy-reviewed.
- [x] Skill guidance preserves authority and exact history.
- [x] Installed cold-session smoke passes.

### 18.4 Release

- [x] Branch rebased onto latest `origin/main` and surface audit rerun.
- [x] Versions/locks/manifests/OpenAPI/docs/examples/roadmap agree.
- [x] Standard/DB/E2E/helper/security suites pass without material skips.
- [x] Quiesced rollout, old-client failure, backup, fix-forward, safe pre-use
      downgrade, and restore are rehearsed.
- [x] No shim, receipt rewrite, inferred backfill, old-backend bridge, or
      hidden repository authority exists.

---

## 19. Cold adversarial review record

### 19.1 Method and initial verdict

After the 1,346-line initial draft was frozen, a new subagent with no drafting
context read it, the full Phase 9 plan, roadmap, and repository surfaces. It
made no edits and returned **REJECT**: persistence/sparse serialization was
directionally sound, but the verifier allowed false `unchanged`, had
unreachable reasons, and did not substantiate no-network/no-process claims.

A separate read-only feasibility probe reproduced the global-match and
assume-unchanged failures in disposable Git 2.43 repositories.

### 19.2 Initial required findings and disposition

| Finding | Disposition |
| --- | --- |
| One pattern could hide another typo | Require a separate match per index; report index only. |
| Helper reasons crossed layers | Split caller/executable registries. |
| Remote URL “binding” was vague | Removed; explicit current workspace only. |
| Assume-unchanged hid bytes | Scoped flag blocks zero. |
| No-lazy unsupported | Require Git 2.45 before repository access. |
| Filters could execute | Replaced Git worktree diff with raw `--no-filters` hash lane. |
| Git steering was vague | Exact unset/set/per-call contract. |
| `^{commit}` peeled tags | Resolve without peel; require own type commit. |
| Protocol/exit vague | Ordered ASCII v1 protocol and exact exits. |
| Clients accepted explicit-empty response | Empty accepted only as input; response rejects. |
| Error precedence contradicted replay | Restore shipped receipt-first ordering. |
| Unicode/`?` diverged | Removed; ASCII byte grammar. |
| Data crossed shell unsafely | Fixed CWD/hex/single-quoted safe invocation. |
| State precedence contradicted changed | Stable two-stage lattice. |
| Older backend bridge broke replay | Removed after scoped use. |

### 19.3 First closure findings and disposition

The reviewer found the major redesign sound but returned **ACCEPT WITH REQUIRED
CHANGES** for four residual blockers:

| Finding | Disposition |
| --- | --- |
| Standalone skip-worktree was not blocked | Detect scoped `S` regardless of sparse config; add reason and modified/deleted tests. |
| Attribute preflight could race before filter-capable diff | Remove filter-capable worktree commands; hash raw regular-file bytes with `--no-filters`; use attributes only to classify normalization. |
| `core.fileMode=false` could hide chmod | Make false a zero blocker and add executable-bit regression. |
| Protocol details/branch encoding incomplete | Remove branch from protocol; add byte encoder and exhaustive reason/state/detail/OID/path table. |

### 19.4 Additional improvements

The revisions also define gitlink/sparse boundaries; logical split-index
identities; raw byte, symlink, and normalization limits; exact shell and Git
environment; tool/conversation/model context as a privacy sink; quiesced
rollout; explicit old-client incompatibility; and contextual rather than
hardware-agnostic benchmarks.

The topic branch was initially rebased from reviewed commit `11457fb` onto
`origin/main` at `7e35646`, then onto `6322729`, `07d365f`, and finally
`a0cc7fc`. The intervening upstream history added the Phase 10 plan, replaced
the work-context modal with the two-column work library, refined README prose,
expanded the wide layout, and made the queue/detail split adjustable. Repeated
non-UI surface audits found no change to a Phase 10 contract, inventory,
migration baseline, or implementation target.

### 19.5 Second closure finding and disposition

The reviewer confirmed the skip-worktree, `core.fileMode`, protocol, and
filter-execution-race blockers were closed, but retained **ACCEPT WITH REQUIRED
CHANGES** for one false-unchanged case and stale prose. The final revision makes
every scoped content-conversion condition an unconditional zero-result blocker
and replaces the stale conversion-preflight wording.

### 19.6 Planning closure verdict

The cold reviewer returned **ACCEPT** for the pre-implementation plan after
confirming that every planning blocker was resolved. Implementation review can
and did reopen feasibility and correctness decisions below.

### 19.7 Implementation feasibility correction

An implementation-time probe against authentic Git 2.45.4 demonstrated that
both `git ls-files -f` and `git ls-files --debug` invoke a configured fsmonitor
when it is enabled, while `-c core.fsmonitor=false` prevents that process but
masks the persisted per-entry valid bit from both commands. The original demand
to inspect that bit while also guaranteeing no configured process was therefore
not implementable through the supported Git interface.

The delivered contract preserves the security and no-false-`unchanged`
properties: every repository-aware Git invocation forces fsmonitor off;
effective enabled or path-valued repository/worktree configuration is the
`fsmonitor_scope_unsupported` zero blocker; dormant bits are not claimed to be
observable; and every scoped stage-zero regular file is compared by a raw,
filter-free hash regardless of Git stat-cache flags. Logical index identity
retains the stage and assume-unchanged/skip-worktree streams. Disposable helper
tests pin the configured-process sentinel, conservative blocker, and dormant-bit
raw comparison; the required platform matrix must separately verify authentic
Git 2.45 behavior. This is a correction to an impossible mechanism, not a
relaxation of RFV-014 or RFV-016.

### 19.8 Implementation-time helper corrections

Cold implementation review found that Git 2.45 refreshes shared-index metadata
on an index read, so the no-write contract requires pre-index rejection of any
`sharedindex.*` artifact. It also found that Bash `-x` reports process access,
not Git's owner-execute bit, and can therefore hide or invent a mode change.

An attempted `git diff-files --raw` replacement was rejected immediately:
authentic Git 2.45 source and a racy-index sentinel proved that it can enter
clean/process conversion even with raw output and `--no-textconv`. The retained
implementation instead uses `git diff --no-index --raw` only as a metadata
`lstat` primitive, with external/text conversion and rename processing
disabled and ordering pinned to `/dev/null`; raw no-index dispatch never enters
the index conversion path. Direct filter sentinels, owner/group-execute cases,
malformed output, and authentic Bash 3.2/Git 2.45 runs cover this distinction.

Review also narrowed “bounded” to capped helper-retained data and protocol
output. Git/Bash enumeration, including Bash 3.2 split-artifact glob expansion,
remains proportional to a trusted, stable local repository and is contained by
the caller's 15-second whole-process-group deadline.

### 19.9 Implementation closure verdict

Independent implementation review reopened the plan's assumptions rather than
treating planning acceptance as code acceptance. It found and closed four
release-significant issues before the final matrix:

| Finding | Disposition |
| --- | --- |
| Git index reads can update shared-index metadata | Reject every split-index artifact before an index read; cover ordinary and linked-worktree gitdirs and prove metadata does not move. |
| Bash process executability is not Git owner-execute mode | Replace the test with raw no-index metadata parsing; reject malformed or widened records and cover owner/group execute combinations. |
| The executable helper was initially staged without its required mode | Record it as Git mode `100755`; verify source, installed payload, manifest inventory, and digest. |
| The affected-path hint polluted the textarea's accessible name after dashboard integration | Use a dedicated `htmlFor` label with descriptive siblings; add a structural regression and rerun both Playwright viewports. |

Review also added a required authentic `macos-15` Bash 3.2/Git 2.45-or-newer
job, enrolled it in the aggregate CI gate, and widened operational Ruff coverage
to both audit/check scripts. A final cold pass over the rebased database,
receipt, MCP, browser, helper, packaging, CI, and documentation surfaces returned
**ACCEPT** with no unresolved high-, medium-, or release-blocking finding.

The complete observed matrix and the production-shaped disposable
upgrade/backup/restore rehearsal are recorded in `docs/validation.md`. They do
not claim a production backup, deployment approval, or live service-fleet
cutover.

### 19.10 Hosted CI compatibility and final adversarial closure

The first pull-request CI run exposed two supported-platform defects, and a
renewed cold review deliberately reopened the prior acceptance:

| Finding | Disposition |
| --- | --- |
| Git 2.55 diagnosed expansion during `ls-files --unmerged`; follow-up isolation proved the command also wrote a loose tree object | Use the command's native `--sparse` mode. Keep arbitrary stderr fatal and prove the entire active sparse repository remains byte-identical. |
| The original sparse fixture wrote repository-local false values underneath higher-precedence worktree configuration | Split active and genuinely config-disabled on-disk sparse fixtures, assert effective configuration, accept only their bounded fail-closed taxonomies, and snapshot both repositories. |
| macOS rejected the exhaustive invalid-UTF-8 filename with `EILSEQ` before helper execution | Retain exhaustive `01`-through-`FF` coverage where supported and use a valid UTF-8 control, punctuation, DEL, and multibyte corpus only on `EILSEQ`. |
| A test-only file descriptor was not protected if its write raised | Close it in `finally` and assert the complete fixture write. |

An intermediate proposal to suppress `advice.sparseIndexExpanded` was rejected:
it would have hidden the only diagnostic for the unsafe expansion. The final
tree instead prevents that expansion. Default discovery passed 71 tests with
the opt-in authentic case skipped; exact discovery passed all 72 tests on Bash
3.2.57 and Git 2.55.0, and focused sparse/conflict plus installed-payload tests
passed on Git 2.45.4. The renewed cold review returned **ACCEPT** with no
unresolved blocker, high-, or medium-severity finding. Hosted required checks
remain the merge gate.
