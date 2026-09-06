# Development and validation

The Python services are independent packages with separate `pyproject.toml` and
`uv.lock` files. Do not combine their environments: the API and MCP SDK can
require different Starlette versions. Docker builds use frozen lockfiles. The
dashboard uses `package-lock.json` and `npm ci`.

Use Python 3.14, uv, and Node 24 for native development. Docker-only users do
not need these tools to run Mnemonic.

## Contribution workflow and CI

Mnemonic uses trunk-based development: `main` is the only long-lived branch.
Create a short-lived topic branch from current `main`, open a pull request
targeting `main`, update it promptly when the trunk advances, and delete it
after merge. Do not use long-lived development, integration, or release
branches, and do not commit directly to `main`.

Install the repository's local secret-scanning hook from the repository root:

```sh
uv tool install pre-commit
pre-commit install --install-hooks
pre-commit run --all-files
```

The hook runs Gitleaks 8.29.1 against staged changes and blocks the commit when
it detects a secret. Do not bypass it with `--no-verify`. The independent
`.github/workflows/ci.yml` scan checks pull requests and pushes to `main` from a
full-history checkout. Trusted runs use the licensed action and read the license
only from the `GITLEAKS_LICENSE` GitHub Actions secret. Fork and Dependabot pull
requests instead use the digest-pinned Gitleaks container, so untrusted runs
remain blocking without receiving the organization secret. Ensure the
organization secret's repository access policy includes this public repository.

CI also runs Ruff, `ty`, the complete PostgreSQL-backed API tests, MCP tests,
and the dashboard's unit, type, and production-build checks. Ruff applies
McCabe `C901` with a maximum complexity of 10 throughout both Python packages,
with no exclusions. Configure the GitHub ruleset
for `main` to require pull requests and the stable `Required checks` status,
and to reject force pushes and branch deletion. That aggregate status fails
unless Gitleaks, Ruff, `ty`, backend tests, MCP tests, and frontend checks all
succeed.

## Backend verification through Phase 12

Shared-boundary extension rules are documented in the
[public validation vocabulary](validation-vocabulary.md),
[MCP response-validation checklist](mcp-response-validation.md), and
[frontend shared patterns](frontend-shared-patterns.md). Extend these boundaries
before introducing another feature-local copy.

The database suite needs a real PostgreSQL instance because the system depends
on PostgreSQL search, row locking, database time, receipt reservation/waiting,
triggers, and Alembic behavior. Start the isolated
test database from the repository root:

```sh
docker compose -f compose.test.yaml up -d --wait
```

It listens only on `127.0.0.1:55432`, stores its data in a disposable tmpfs, and
does not share the working application's database. Then run from `backend`:

```sh
uv sync --frozen
export TEST_DATABASE_URL=postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test
uv run pytest -q
uv run ruff check .
uv run ty check src
```

Ruff also enforces a McCabe complexity ceiling of 10 (`C901`) over the source
and test trees with no per-file exceptions. `ty` covers the whole backend `src`
tree, which must stay free of diagnostics.

In PowerShell, replace the export with:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://mnemonic_test:mnemonic_test_only@127.0.0.1:55432/mnemonic_test'
```

The retained Phase 5 backend coverage verifies:

The cross-version schema reference is pinned separately by
`backend/tests/test_schema_parity_postgres.py::test_migrated_schema_matches_orm_metadata`,
which compares migrated columns, types, defaults, every ORM table's non-trigger
constraints, and indexes with a scratch ORM schema.

- the `0004` expansion and populated `0005` backfill, including exact legacy
  text/provenance parity, preserved IDs, migration markers, and frozen legacy
  tables;
- the `0006` contract boundary and `0007` lease schema, including model parity,
  constraints, and indexes after physical legacy-table removal;
- the `0008` typed-relationship schema, composite project scoping, normalized
  identity, context ownership, one-parent constraint, and lookup indexes;
- `0009` ready-order/normalized-tag indexes and one-statement ready pages,
  including blocker/lease/filter/order/pagination parity with fresh claims;
- populated `0009 -> 0010` upgrade/backfill/downgrade, schema behavior, lease
  generations/release markers, exact conservative counts/order, actor fallback,
  typed references, metadata checks, source/deferred guards, and immutability;
- atomic work-plus-initial-checkpoint creation, pointer-only grouped search,
  literal and full-text retrieval, optional hybrid search, and cache invalidation;
- identity-only work reads, bounded context assembly, deterministic checkpoint
  pagination, newest-context selection, and terminal clarification checkpoints;
- append-only checkpoint enforcement in both the API and database, concurrent
  appenders, and checkpoint appends that do not consume the work version;
- version-protected identity/lifecycle edits, typed application errors, atomic
  completion checkpoints, default-Pending filtering, human-only deferral, and
  soft deletion;
- exclusive concurrent acquisition, identical-request replay, expiry takeover,
  renewal/release precision, safe public projections, and claim-and-context
  atomicity;
- lease-token enforcement and removal on completion, retirement, promotion,
  and deletion, without changing work version/activity during lease operations;
- five relationship types, idempotent add/remove, project-serialized cycle and
  parent checks, atomic initial links, blocker-driven readiness/claimability,
  active-plus-blocked recovery, and relationship-protected deletion;
- pointer-only immediate adjacency and exact directional counts in bounded
  recall, plus root/child hierarchy filtering and flat-search breadcrumbs;
- atomic event emission for every canonical mutation, replay/no-op suppression,
  rollback fault injection, deterministic endpoint events, and actor provenance;
- progress-only public append, work-before-lease locking, monotonic activity,
  exact text/bounds, request-known secret rejection, and capability-free errors;
- one-statement event pages in both orders with exact filtered totals and the
  unfiltered partial-history flag, plus chronological bounded events in recall;
- event update/delete rejection at PostgreSQL, soft-delete read isolation, and
  direct REST omission recorded honestly as unattributed history;
- cross-project isolation, hostile/unknown input rejection, and
  pagination/filter totals.

The Phase 6 additions verify:

- `0013_idempotent_mutations` chaining from
  `0012_pending_deferred_statuses`, including fresh installs and populated
  upgrades that
  preserve existing work/event content and legacy metadata, model/DDL parity,
  the request-only progress-metadata boundary, and guarded downgrade behavior;
- the private `client_operations` receipt invariants, exact
  `(project_id, client_operation_id)` uniqueness, salted canonical request
  fingerprints, completed-row immutability, permanent response snapshots, and
  absence of resource foreign keys or public receipt lookup;
- reservation/finalization rollback, bounded waiter timeouts, concurrent
  same-key execution, mismatch conflicts, and exact replay before current
  work/relationship/lease guards;
- first execution and historical replay across all ten REST operations registered at that time,
  including natural true/false no-ops, later edit/reopen/delete/remove/lease
  replacement, and no duplicate work, checkpoints, relationships, leases, or
  domain events;
- optional keyed REST versus unprotected keyless REST, keyed actor requirements,
  secret/control echo rejection, sanitized stable errors, response-model and
  size failure rollback, and strict exclusion of project/claim/renew writes;
- outcome-aware live invalidation: applied originals and applied replays publish
  a data-free refresh hint, while no-ops and failures do not.

The combined Phases 7–8 additions verify:

- `0014_human_gates -> 0015_gate_review_fixes` fresh and populated upgrades,
  exact Phase 1–6 row/function preservation, gate/event immutability and
  deferred completeness, fail-closed lease/work triggers, the exact twelve-kind
  receipt check, `clock_timestamp()` creation defaults, acknowledgement and
  persisted resolution-drift column removal, and the explicitly unsupported
  0015 downgrade;
- atomic request/resolution and exact receipt replay under commit/response/
  rendering faults, multiple gates, nested requested/resolved revision anchors,
  backend-computed drift projections, and the required reviewed revision on
  every resolution, sanitized errors, source-event coherence, and no duplicate
  durable effect;
- waiting readiness/lifecycle matrices, active capability replay/renew/release
  after gating, fresh/replacement claim rejection, completion/terminal/delete
  guards, and deterministic request/resolve races;
- cursor-paged attention and gate-history pages, plus point-of-use guidance to
  restart attention traversal from the head before declaring it drained,
  bounded context gate slices with exact omission counts, soft-deleted decision
  audit, and typed event history; and
- one-statement hierarchy pages with exact branch presentation counts,
  subtree-aware filter flags, discovery labels, cycle/depth bounds, pagination,
  current database-time lease facts, statement-count guards, and plan-shape
  assertions. Representative post-correction performance measurement remains a
  release gate.

The Phase 9 Core additions verify:

- fresh and populated `0015_gate_review_fixes -> 0016_duplicate_handling`
  upgrades preserve all Phase 1–8 rows, exact receipt
  JSON, gate projections, event validator vectors, embeddings, IDs, versions,
  timestamps, bodies, hashes, and provenance while creating zero merge or
  witness rows; 0016 has no downgrade;
- ORM/migration parity for the immutable `work_duplicate_merges` ledger,
  relationship/event witnesses, composite keys, identities, indexes,
  deferrability, triggers, and normalized function definitions;
- project-local forest depth/cycle/root/source uniqueness, exact relationship
  and paired-event completeness, immutable merge/evidence facts, and stale
  direct-SQL writers failing closed;
- source/destination context review, structural/gate/lease preconditions,
  source-only lease consumption, version/time increments, historical mark reuse,
  same-transaction mark creation, and exact result ordering;
- the thirteenth receipt kind, mandatory merge UUID, canonical/digest/response
  vectors, unknown-outcome recovery, later-state same-key replay, concurrent
  merges and mutations, and preservation of every old receipt wire shape;
- alias guards across lifecycle, claim/lease, checkpoint, event, gate, and
  relationship operations with no redirect or canonical substitution;
- canonical detail/context/search/hierarchy/readiness projections, grouping
  before pagination, alias audit scopes, matched-member reporting, bounded
  paths/members/relationships, coherent snapshots, and corrupt-graph failure;
  and
- the read-only aggregate audit at head 0016 with zero blocking findings.

The Advisory additions verify:

- fresh and populated `0016_duplicate_handling` to
  `0017_duplicate_suggestion_title_key` upgrades preserve every application row
  and add only the immutable title-key function and matching partial expression
  index, with PostgreSQL-17 normalization and plan-use coverage;
- strict six-field request and purpose-built response validation, global exact
  title groups, lifecycle/project/deleted/excluded-group isolation, canonical
  grouping before every cap, deterministic member/root ordering, contiguous
  ranks, categorical signals, and exact omission counts;
- SQL-bounded initial/later-checkpoint composition, recent-30 distinct tag
  selection that retains a newly relevant tag beyond 30 historical values, the
  200-group lexical lane, full-project versus shortlist semantic scope, the
  10,000-member ceiling, 128-vector fill cap, malformed/stale-vector handling,
  deterministic RRF, and lexical fallback;
- authentication-before-work, direct/chunked 2 MiB body enforcement,
  request/inference acquisition before sessions, the process-wide inference
  gate shared with ordinary semantic search, suggestion lexical fallback versus
  semantic-search 503, bounded saturation waits, typed 413/429/503 behavior,
  and one absolute 60-second safe-read deadline;
- route-relative PostgreSQL-17 transaction/statement timeouts, skip-locked
  cache rows, 50 ms cache lock bounds, ordinary semantic search's SQL-bounded
  legacy text composition and best-effort post-snapshot cache refresh, and
  exact stored candidate title/summary preservation; and
- zero persisted draft/result/query vector and zero work, relationship, event,
  receipt, version, activity, publication, or live-sync effect, with existing
  cache CAS writes isolated after the coherent read snapshot.

The Phase 10 additions verify:

- fresh zero-to-0018 and populated 0017-to-0018 upgrades preserve every prior
  row, digest, timestamp, function/trigger/index definition, receipt fingerprint,
  response body, contract version, and opaque metadata value while assigning an
  empty array to every historical checkpoint;
- PostgreSQL/ORM parity for the one-dimensional `VARCHAR(512)[] NOT NULL`
  column and empty default, the versioned immutable validator, grammar and
  commit-dependency constraints, unchanged checkpoint immutability, and the
  deliberate absence of an index;
- byte-identical ASCII grammar, component/star rules, exact-duplicate rejection,
  64-entry, 512-byte, and 16,384-byte boundaries, preserved order/case, and the
  requirement that every non-empty declaration have a caller-asserted baseline;
- field-local sparse serialization: omitted and explicit-empty request values
  share the historical canonical form, responses omit empty scope, and a stored
  explicit-empty response fails receipt equality rather than silently rewriting
  permanent evidence;
- all three checkpoint mutations include non-empty ordered scope in request
  hashing, exact replay, conflict detection, response coherence, history, and
  bounded full context while compact pointers, events, search, hierarchy,
  readiness, duplicate systems, embeddings, and derived cache identity remain
  unchanged; and
- lock-protected downgrade succeeds before scoped use, refuses before DDL after
  any non-empty declaration, cannot race a concurrent insert, and supports
  re-upgrade without data loss.

The Phase 11 additions verify:

- fresh zero-to-0019 and populated 0018-to-0019 upgrades preserve every prior
  row count, pre-existing column value, identifier, sequence, timestamp, and
  receipt byte while deterministically filling only the new private generation
  bindings and creating no evidence rows; both the migration-built and shipped
  PostgreSQL 17 backup/restored raw Phase 10 survivor catalogs are accepted
  exactly at heads 0018 and 0019, while any other drift or same-named
  Phase 11 constraint on the wrong relation is rejected;
- ORM/schema parity plus every composite ownership key, vocabulary and grammar,
  byte constraint, partial access index, insertion guard, immutability guard,
  truncate guard,
  sealed-episode validator, completion generation, and reopen witness;
- the shared cross-language fixture, conditional operation-UUID requirement,
  sparse empty form, strict union and exit-code matrix, timestamp equivalence,
  artifact identity, order preservation, request fingerprint, response/receipt
  coherence, nested secret scan, and 896 KiB generated-representation budget;
- atomic checkpoint/state/event/evidence/lease/receipt behavior across injected
  failures, exact concurrent replay, UUID conflict, blocker/gate/version/lease/
  alias precedence, and no extra activity, version, event, or invalidation;
- page-first constant-query history, repeatable-read snapshot, bigint string
  identity, high-water cursor stability, exact current pointer, alias ownership,
  soft-delete concealment, 3 MiB serialization ceiling, and fail-closed corrupt
  generation/child/receipt cases; and
- guarded downgrade succeeds only for a valid unused Phase 11 schema, refuses
  before DDL after evidence or a Phase 11-only receipt response, restores that
  database's exact incoming approved raw Phase 10 survivor-catalog
  representation, and supports lossless re-upgrade;
- static script regressions pin omission of `--no-acl`, while dump/SQL-reparse
  regressions freeze the Phase 11 vocabulary checks; and
- the real archive rehearsal and catalog tests prove all 18 Phase 11 functions
  retain their `PUBLIC EXECUTE` revocations and both evidence relations retain
  effective owner-only privileges after restore; and
- `tests/test_legacy_shape_migration_postgres.py` stages the row shapes only
  superseded code could write, at the revision that could write them, and runs
  the whole chain over each. Most migration tests start from an empty schema
  or populate one through current services. This suite covers deployed legacy
  shapes; Phase 12 also uses a deliberately pinned 0019 offline fixture helper
  for historical completion/audit cases, without running current writers on an
  older schema. It also proves each 0019 preflight condition is separately
  named and independently executable, so a refusal identifies the rows to look
  at instead of reporting that something, somewhere, is wrong.

That suite replays the chain in a database of its own, one per xdist worker,
rather than in the shared disposable schema. Catalog scans are per database, and
several suites digest a whole catalog; a schema dropped between such a scan
reading `pg_class` and its `pg_get_*def` call over an OID makes PostgreSQL
report `could not open relation with OID`. Keeping the replay churn in a private
database means no other suite can observe it. Do not move these tests back into
the shared schema to save the database.

Add a shape to `tests/fixtures/legacy-shapes-v1.json` whenever one is
discovered. A migration that cannot accept a shape that corpus stages is a
migration that cannot accept the deployed database, and a route that cannot
serve one has only moved the same assumption upstack.

For a focused Phase 10 backend iteration, run from `backend` with the real test
database configured; the complete suite remains the release gate:

```sh
uv run pytest -q \
  tests/test_repository_freshness_migration_postgres.py \
  tests/test_legacy_shape_migration_postgres.py \
  tests/test_validation.py \
  tests/test_client_operations.py \
  tests/test_work_items_postgres.py \
  tests/test_schema_parity_postgres.py
```

For a focused Core iteration, run:

```sh
uv run pytest -q \
  tests/test_duplicate_handling_postgres.py \
  tests/test_duplicate_merge_catalog_postgres.py \
  tests/test_duplicate_merge_invariants_postgres.py \
  tests/test_duplicate_merge_migration_postgres.py \
  tests/test_client_operations.py
```

For a focused Phases 7–8 iteration, run the real gate/migration/readiness suites;
the complete `pytest` command above remains the release gate:

```sh
uv run pytest -q \
  tests/test_phase78_migration_postgres.py \
  tests/test_schema_parity_postgres.py \
  tests/test_human_gates_postgres.py \
  tests/test_ready_work_postgres.py \
  tests/test_work_items_postgres.py
```

For a focused Phase 6 iteration, run the receipt suites explicitly; the complete
`pytest` command above remains the release gate:

```sh
uv run pytest -q \
  tests/test_client_operations.py \
  tests/test_client_operations_postgres.py \
  tests/test_idempotent_mutations_postgres.py \
  tests/test_phase6_live_sync.py \
  tests/test_phase6_migration_postgres.py
```

Tests create an isolated random schema, apply the real Alembic chain, and remove
the schema afterward. Semantic cases use a deterministic local test embedder and
do not contact a model service.

Without `TEST_DATABASE_URL`, PostgreSQL tests explicitly skip. Such a run still
checks pure validation helpers but is not proof of migration, transaction,
trigger, concurrency, receipt replay, or PostgreSQL retrieval behavior. Any
skipped PostgreSQL-marked test makes the Phase 11 release gate incomplete.

Stop the disposable database afterward from the repository root:

```sh
docker compose -f compose.test.yaml down
```

## MCP verification through Phase 12

Run from `mcp`:

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp
```

The MCP suite verifies the exact 32-tool canonical catalog, strict unknown-field
rejection, nested checkpoint request bodies, canonical/grouped search hits,
compact ready results, bounded recall, deterministic checkpoint/event
pagination, versioned mutation receipts, typed graph and lease behavior, the
`resume_work` prompt, and the work-item resource across direct, Streamable HTTP,
and real stdio transports.

Exactly eleven mutation tools require a canonical `client_operation_id` and
advertise truthful idempotency: `create_work`, `add_checkpoint`, `append_event`,
`add_relationship`, `update_work`, `complete_work`, `delete_work`,
`remove_relationship`, `release_claim`, `request_human_input`, and `merge_work`. Tests prove
exact one-attempt forwarding, strict coherent response decoding, sanitized
same-key recovery guidance, and local rejection on excluded tools. Project
creation, claim, claim-and-recall, and renewal retain separate non-idempotent
contracts.

Merge tests pin its two reviewed
revisions, exact direction, optional source lease token, mandatory operation
UUID, single outbound attempt, strict result/event/relationship coherence,
same-key recovery, and sanitized alias/merge errors. Search/resource/prompt
tests pin canonical grouping, explicit alias audit scopes, matched-member
pointers, exact source-owned alias context, and no automatic canonical
substitution. Advisory tests pin the strict six-field suggestion tool, unique
groups/members, contiguous ranks, signal/count/mode coherence, literal
composition version, 60-second safe-read request, ordinary retry guidance, and
continued exclusion from the eleven protected writes.

Phase 7–8 assertions cover exact request arguments and revision projections,
unknown-outcome guidance, model-valid request coherence injections, reachable
scope guards, ready-page waiting refusal, cursor-paged `list_human_attention`
with pinned first-page restart guidance, `list_work_gates`, waiting readiness,
bounded gate slices in search/ready/context/resource/prompt models, sanitized
gate errors, OpenAPI property/required-set parity, and value-free DEBUG logging
for query and cursor markers. They also pin the tool-description rules to check
open gates and write supporting context first, never withdraw or self-resolve a
gate, and restart the attention traversal. HTTP and stdio expose the same 25
names at that historical Phase 7–8 boundary; Core exposes 26.
At that historical boundary, `get_activity`, `resolve_human_input`, and removed
hand-off surfaces were absent. Phase 12 adds `get_activity`; human resolution
and removed hand-off surfaces remain absent.

Phase 10 MCP assertions cover omitted and explicit-empty checkpoint input,
canonical rejection of an explicit-empty response, exact grammar and bounds,
baseline dependency, order-sensitive request/response coherence, all full
context/resource/prompt projections, and unchanged compact pointers. Static
tests also prove the adapter contains no Git, subprocess, checkout, repository,
or network assessment path. At the Phase 10 boundary, the catalog had exactly
27 tools and eleven protected writes; there is no freshness tool or new receipt kind.

Phase 11 MCP assertions extend only `complete_work` and add the safe
`list_completion_evidence` read. They consume the repository-level shared
fixture for strict unions, nullability, timestamp canonicalization, artifact
grammars, aggregate counts/bytes, request/response coherence, and hostile inert
text. Tests cover one-attempt forwarding, identity-only raw history reads at
exactly 3 MiB and max-plus-one, rejection before a poison stream is pulled,
stable cursor forwarding, and response ownership/order/timestamp checks.
Transport-process tests exercise the shared 1 MiB pre-SDK Streamable HTTP and
newline-delimited stdio guards, the bounded JSON-RPC ID domain, terminal stdio
rejection without a second writer, the locked SDK seam, and a complete
maximum-ID response containing both SDK representations at no more than 12 MiB.
The Phase 11 catalog is exactly 28 tools and eleven protected writes; there is
no standalone evidence mutation, resource/prompt evidence expansion, or
automatic artifact access.

Phase 12 adds `get_activity`, `get_project_settings`,
`list_job_completion_reports`, and `get_job_completion_report`, keeping exactly
32 tools and eleven protected writes. Tests cover strict canonical string
sequences, cursor encoding/scope/order/continuation, per-read byte budgets,
immutable report ownership and prompt hashes, current review envelopes, and
sanitized malformed-wire failures. Closeout tests bind authored report text,
ordered FYIs, revision, version, and provenance for Done/Won’t do/Promoted;
old receipt replay stays sparse. A report’s insertion time is independent of
checkpoint/work timestamps. Reads never call human dismissal/follow-up routes.

The inner plugin manifest is `0.13.0`. Before release, parse the marketplace
and inner plugin manifests, then exercise a disposable fresh `0.13.0` install
plus a `0.12.0 -> 0.13.0` marketplace/plugin update. Use an
isolated `CLAUDE_CONFIG_DIR`; a marketplace refresh alone does not prove that
the cached binary, reference, and skill bytes changed. Confirm the installed
helper retains executable mode, all `${CLAUDE_PLUGIN_ROOT}` links resolve, and
the inventory remains exactly three skills (`mnemonic-save`, `mnemonic-search`,
and `mnemonic-recall`), seven shared references (`authority-and-provenance.md`,
`completion-evidence.md`, `external-records.md`, `job-completion-reports.md`,
`priority.md`, `repository-freshness.md`,
and `work-graph.md`), and
one executable (`mnemonic-repository-freshness`).
A compatibility copy of the old prerelease schema or workflow is not a valid
substitute.

From the repository root, run the MCP scope/plugin contracts and disposable
helper behavior suite with the MCP environment:

```sh
uv run --project mcp pytest -q \
  mcp/tests/test_repository_freshness.py \
  mcp/tests/test_plugin.py
uv run --project mcp python -m unittest discover \
  -s plugin/tests -p 'test_repository_freshness.py'
```

The helper behavior suite uses a trusted version-reporting wrapper around the
host Git so it can exercise repository behavior on older development hosts.
That is not evidence for the required platform lane. Release validation must
also run the packaged helper and cold-session workflow on real Bash 3.2 and
newer hosts with real Git 2.45 or newer, and separately prove a real Git 2.44
executable is rejected after `git --version` and before object or repository
access. The supported matrix covers clean, every changed lane, every
indeterminate blocker, races, hostile environment/config/filter sentinels,
exact ASCII protocol/exit agreement, byte quoting, 100-path and 32-KiB caps,
15-second caller-enforced whole-process-group timeout, no child configured
process, no network, and no repository mutation. A skipped runtime/security
lane is not full validation.

After syncing `backend`, run the repository's available Ruff binary from the
repository root over the MCP and live-check code:

```sh
uv run --project backend ruff check \
  mcp/src/mnemonic_mcp \
  mcp/tests \
  scripts/audit_duplicate_handling.py \
  scripts/check-stack.py
```

## Dashboard verification through Phase 12

Run from `frontend`:

```sh
npm ci --no-audit --no-fund
npm test
npm run typecheck
npm run build
```

The Node tests cover canonical work/root/child query construction, compact
recall pointers, checkpoint normalization, Ready/Active/Blocked formatting,
expiry refresh scheduling, relationship labels/direction previews, graph
conflict/depth helpers, sanitized typed errors, same-origin/host enforcement,
and the retained Phase 5 proxy route boundary. Event tests cover strict runtime
decoding,
deterministic labels and relationship direction, attributed/unattributed/
backfilled states, safe text rendering, newest-page reset after live
invalidation, actor request construction, progress composer errors, pagination/
filtering, and the partial-history notice at desktop and narrow viewports. The
ready endpoint remains intentionally proxy-denied.

Mutation tests cover all thirteen browser writes, one UUID and exact frozen serialized
body per intent, in-flight coalescing, same-document recovery after component or
view unmount, exact manual retry after ambiguous outcomes, and a non-discardable
safety state for key conflicts. Strict per-operation decoders require the
expected status, exact shape, and path/result coherence before clearing
recovery. Proxy-policy tests admit the top-level UUID only on those thirteen
routes, including gate resolution, merge, report dismissal, and report follow-ups, and reject invalid, nested,
query/header/cookie, secret-equal, and excluded-route IDs without echoing them.
Gate creation and token-bearing lease mutations remain denied. The two token-free
dashboard status routes accept only a dashboard actor and exact version/lease
review state, return no capability, and have dedicated proxy-policy tests.

Phase 7–8 Node regressions cover literal gate/event decoding, exact reviewed and
resolved revisions, drift facts, Waiting/readiness and terminal guards,
attention/history query construction, debounced request-driving filters,
omission/history presentation, hierarchy population and discovery labels,
overlap wording, corrupt depth/cycle fallbacks, identifier-free live frames, and
the passive-expiry scheduler. No gate question, answer, UUID, or frozen body
enters browser storage.

Core duplicate tests cover strict detail/canonical/context/search/merge/event
decoding; root/alias/path/member/count coherence; default canonical grouping and
explicit alias/group audit navigation; historical mark display with fresh
generic creation closed; and the distinct `work_merged` timeline. Merge UI
tests must cover separate bidi-isolated source/destination panels, full UUIDs,
permanence acknowledgement, source structural/gate/lease blockers, active-lease
disablement, both frozen review revisions, a two-work-key conflict lock, exact
ambiguous retry, stale-review replacement, and current-root refetch after
success. Since the two-column work library, that merge review is an inline
panel at the top of the detail pane's Graph tab rather than a dialog; the same
destination search, eligibility facts, registry intent, and recovery block apply
there. The browser registry contains exactly thirteen kinds and admits no lease
token.

Advisory Node tests cover exact request construction and strict response
decoding; rank/root/member/signal/count/mode coherence; the sole six-field
safe-POST proxy allowlist; route-specific 2 MiB cap, 60-second timeout, and
busy-only `Retry-After` forwarding; explicit-action loading/empty/lexical/error
states; generation/abort handling; stale marking after every compared draft
field changes; bidi-isolated candidate text; and the guarantee that suggestions
never enter browser storage, the mutation registry, or the valid form's Create
path.

Phase 10 Node contract tests cover the shared ASCII/byte corpus, sparse omitted
or explicit-empty mutation input, explicit-empty response rejection, non-empty
baseline dependency, exact ordered retry identity, full-checkpoint decoding,
and continued absence from compact pointers and derived surfaces. They also pin
the declaration-only trust boundary: browser code accepts and renders stored
scope as untrusted provenance but contains no Git/subprocess/local-filesystem
assessment path and never labels it semantically fresh, current, verified,
correct, or safe.

Phase 11 Node tests consume the same repository-level fixture and pin strict
draft construction, semantic validation, code-point/UTF-8 limits, timestamp UTC
canonicalization, exact artifact URL/path/branch rules, completion-response
ownership/order/time coherence, and all sparse/invalid forms. Mutation tests
prove the full evidence draft is frozen under the existing `complete_work`
intent and survives only an exact ambiguous retry. Proxy tests cover the sole
new safe GET, exact query allowlist, `Accept-Encoding: identity`, header-first
coding rejection, poison streams, exact/max-plus-one 3 MiB bodies, no-store /
no-transform / nosniff headers, and no mutation-inventory or live-sync change.
The lazy Evidence tab covers empty, loading, error, retry, pagination, current
episode, reopened history, evidence-free legacy episodes, hostile bidi/control
text, inert commands, and safe HTTPS links with referrer/opener protection.

Playwright renders hostile literal questions and answers, exact ambiguous retry,
B-to-C drift rejection and fresh intent, attention empty/error recovery,
filtered and unfiltered attention, nonzero omission messages, attention and
53-gate history pagination, current-cursor live refetch, and sibling draft/focus
preservation. Existing browser cases also cover collapsed hierarchy expansion,
queue-card descendant and needs-attention chips with their branch-total titles,
discovery/filter explanations, child paging, ancestry, browser-clock-driven
lease expiry, and targeted ARIA/focus behavior. Gate, event, checkpoint, and
relationship cases reach their panels through the detail pane's Questions,
Activity, History, and Graph tabs. These checks do not claim an exhaustive
keyboard-only traversal audit.

`typecheck` verifies component and API model alignment; the production build
catches server/client boundary and asset issues. Backend and Playwright suites
cover subtree retention, breadcrumbs, lazy expansion, relationship-editor
behavior, and the event UI.

### Automated browser acceptance

Install the pinned Chromium and Firefox builds once on a development or CI host:

```sh
npx playwright install --with-deps chromium firefox
```

From `frontend`, run the complete isolated acceptance path with:

```sh
npm run test:e2e:stack
```

From the repository root, also exercise the deployed identity-coding boundary
with a controlled upstream that emits identity, gzip success, and gzip error
responses:

```sh
./scripts/test-nginx-e2e.sh
```

The wrapper generates a uniquely scoped Compose project, API key, and available
loopback ports. It builds API and dashboard images,
runs PostgreSQL on tmpfs, and exercises the live-motion regression in Firefox
plus the complete desktop and narrow Chromium suite. On success, failure, or
interruption it tears down that exact generated project with volumes and orphan
containers included; it never targets the working application stack. The
scoped teardown shape is:

```sh
docker compose -p "$MNEMONIC_E2E_COMPOSE_PROJECT" -f "$repo_root/compose.e2e.yaml" down -v --remove-orphans
```

`npm run test:e2e` runs Playwright only. Use it when the same disposable stack
is already running and `MNEMONIC_E2E_WEB_URL`, `MNEMONIC_E2E_API_URL`, and
`MNEMONIC_E2E_API_KEY` point to that stack; it does not provision or tear down
services itself.

Phase 9 browser acceptance additionally exercises source/destination direction,
alias audit-to-canonical navigation, canonical/group search, branch merged
counts, active-lease merge refusal, drift rejection, exact lost-response replay,
live convergence of both endpoints, explicit suggestion action, exact-title and
matched-alias candidates, stale draft results, lexical/busy/unavailable states,
and successful Create anyway throughout. This acceptance run is a release gate;
unit/type/build success alone does not substitute for it.

The work library is a two-column surface, not a modal. Below the search and
filter controls, an independently scrolling queue of compact cards fetches the
next `WORK_PAGE_SIZE` page when a sentinel near its end intersects the
scrollport, so the result count reports the total while cards append; there
are no Previous/Next controls. Selecting a card opens the detail pane beside it
with Context, History, Evidence, Graph, Questions, and Activity tabs; edit is inline in
the Context tab, the merge review is an inline panel inside the Graph tab, and
the selection mirrors into `?work=<id>`. A lifecycle filter names a different
queue, so changing it — including the empty state's Clear filters, which returns
the queue to Pending — drops the open selection instead of stranding a record the
queue no longer lists; reselecting the filter already in force changes nothing,
and an unsaved edit or checkpoint draft holds the change behind the same
confirmation that closing the pane uses. Neither column swaps abruptly: the
filter change — from a button, from Clear filters, or from the horizontal-arrow
shortcut — runs inside one view transition that renames the queue, and the
detail pane when it retires the open record, so the browser eases each outgoing
capture out on `easeOutCirc` while the live pane eases in on `easeInCirc` over
the `--pane-crossfade-duration` the stylesheet sets once. The incoming capture
stays live, so a queue page that lands mid-transition arrives inside the fade;
everything the filter did not rename swaps at once, so the clicked button
answers immediately, and a reduced-motion reader gets the plain swap. A divider
between the columns
(`role="separator"`, keyboard-adjustable, double-click resets) sets the
queue's share of the surface and remembers it in `localStorage` under
`mnemonic.work-split`; the stylesheet clamps both columns to readable minimums.
Below 900px the pane becomes a full-height sheet with a Back button.

The surface is keyboard-navigable without leaving the queue. The vertical arrows
walk the queue selection and clamp at both ends; the horizontal arrows walk the
lifecycle filter row in its rendered order, wrapping at both ends because the
eight filters are one small closed ring; Escape drops the open selection through
the same rule the pane's own Back button follows, including the unsaved-draft
confirmation, and stays silent with nothing open; the digits select the workspace
picker's first ten projects — 1 through 9, then 0 for the tenth, which is the
number row's own order; and `c` copies the open record's recall pointer through
the same call, notice, and copied state the record's own button uses. `c` is the
queue's key rather than the pane's: with focus inside the open record it is left
alone, because the pane carries its own copy button, and with nothing open it
does nothing. Caps Lock reports an uppercase letter with no Shift held, so a
letter shortcut compares against a lowered key while a real Shift is still
refused. Every one of these is inert while a `<dialog>` is
open or while focus is in a text field, `<select>`, or `contenteditable` element,
so the keys keep their typing meaning — load-bearing for the digits, which unlike
an arrow are something a person types — and the shared guard behind that lives in
`frontend/lib/keyboard-shortcuts.ts`. The horizontal arrows additionally yield to
a focused surface divider, which steps its own split with them. A project switch
made with a digit routes through the same guard as the picker, so a dispatched
mutation or an unsaved draft still refuses it.

The quiet detail placeholder names the navigation keys in `<kbd>` glyphs, which is
the only place they are written down: the picker's options carry project names
alone. The caps are drawn in the groups a keyboard gives them rather than as flat
pairs: the four arrows in their inverted T, one cap centered above the middle of
three, and the digit pair below it centered on that same axis, with all three
labels left-aligned in one column beside them. Because the T puts the down arrow
in the bottom row with left and right, no label can be read off the row it sits
against, so each names its own directions — "select work item (up/down)" and
"cycle states (left/right)". The cluster is `aria-hidden`, since that copy already
carries it; the digit caps are not, since "select a project" does not name them.
`c` is deliberately absent from that list, because the placeholder shows only when
no record is open, which is exactly when `c` has nothing to copy.
The modified alternatives were considered and rejected. A bare function key loses
F1, F5, F11, and F12 to the browser, and on macOS the function row sends media
keys unless the system setting is changed; Alt+F*n* loses F4 to the window
manager everywhere and F1, F2, F7, F8, and F10 to GNOME and KDE; Ctrl+F*n* loses
F4 and F5 to the browser and F1 through F8 to macOS keyboard navigation. An
unmodified digit is reserved by nothing, at the cost of ten slots rather than
twelve.

Node tests cover the pure queue helpers
(result-count labels, page merging, loaded offsets, arrow-key selection and
list-scroll arithmetic, forced More-filters state, lifecycle-filter transitions,
the filter row's order and its wrapping walk), the project digit helpers (the
bound range and the digit each position takes, key parsing), the split helpers
(bounds, stored-preference parsing, pointer and keyboard steps), the tab-count
helper, and the cross-dissolve's single duration, its two circ easings, and which
panes a given filter transition renames (`tests/pane-crossfade.test.mjs`, which
reads `app/globals.css` so the stylesheet and `lib/pane-crossfade.ts` cannot
drift). `tests/empty-pane-keys.test.mjs` reads the placeholder and the stylesheet
together for the same reason: the arrow caps are meaningless without the grid
areas that seat them in the inverted T, and the labels are ambiguous without
their pairs.
`tests/e2e/work-library-surface.spec.ts` runs in both the desktop and narrow
Chromium projects and covers arrow-key selection that scrolls the list rather
than the window, tab persistence across items, inline edit save and cancel,
merge inside the Graph tab through the real API, lazy append from 20 to 40 to
45 seeded cards with the total shown throughout, the More filters toggle and
provenance auto-open, work-item ID copy, `?work=` restore on reload,
deselection on a lifecycle-filter change and on Clear filters, the
cross-dissolve those changes run inside (both panes captured for the same span
on the two circ curves, the queue alone when nothing is open, no root half, no
name left behind, and no transition at all under reduced motion), deselection
with Escape from both a clicked and an arrow-key selection with the placeholder
it uncovers listing all three hints, holding its four arrow caps in the measured
inverted T, and centering the digit pair on the cluster's own axis beneath it,
the horizontal arrows walking and wrapping the filter row while leaving a focused
search field and divider alone, the digit
keys selecting a project and back again from a picker whose options carry names
alone while the search field keeps every digit typed into it, `c` copying the
open record's real recall pointer off the clipboard while refusing to fire with
nothing open, from inside the pane, or with Shift held, and still firing for the
uppercase letter Caps Lock reports, the draggable
divider (drag, reload, arrow keys, double-click reset, no overflow), and the
narrow sheet with its Back button. The `tests/e2e/surface.ts` helpers
(`workPane`, `workCard`, `selectWork`, `closeDetail`, `openTab`) are how every
other phase spec reaches the pane; the attention view's Open work context
button navigates to `/?work=<id>` instead of opening a dialog.

For native dashboard development, keep the container API running, stop the
`web` container, and create an untracked `frontend/.env.local` containing:

```text
MNEMONIC_API_URL=http://127.0.0.1:8000
MNEMONIC_API_KEY=<the same private key as .env>
```

Run `npm run dev`. The default allowed origins accept localhost and 127.0.0.1
on port 3000; update the configured origins when changing the port. Browser
requests always use `/api/mnemonic`; the upstream API address and bearer key
remain server-only.

## Phase 12 acceptance additions

Current application/API/MCP/dashboard versions are `0.11.0`, plugin is `0.13.0`,
and Alembic head is `0022_external_references`. Validate all surfaces together
with the existing regression suites. Historical Phase 11 downgrade/catalog tests
must seed valid historical shapes with offline SQL at 0019; never run current
application writers against an older schema or strip reports from fresh receipts.

Backend checks cover fresh and populated upgrades through 0020/0021, typed
activity coverage, per-project ordering and stream changes, all three closeout
seals, settings compare-and-set, report cursor snapshots, repeat dismissal,
dual-provenance pending follow-ups, and exact receipt replay. Test direct SQL
invariants, rollback at every aggregate step, races, bounded contention and
privacy, populated audits, and preservation after restore/rebootstrap.

Browser acceptance covers Summaries immediately below Needs Attention, its
count/list/detail, standalone report prose and FYIs, dismissal, independent
manual follow-ups, current prompt editing at `/settings`, and all three
closeout editors. Retain exact report drafts and UUIDs across ambiguous results,
block conflicting actions for their full scope, and recover once in the correct
pane or global area. Prove activity polling/reconnect catches external writes
without resetting in-flight human drafts; count, visibility and focus should
converge. Keep commands, report text, and project prompts inert.

The existing Phase 11 baseline below remains historical evidence. Phase 12
release completion additionally requires the new audit, read/write checker,
full current backend/MCP/frontend suites, and independent cold code reviews.
See [project activity and reports](project-activity-and-reports.md) for the wire
contracts and [operations](operations.md) for target-environment cutover.

## Full running-stack check

After starting current images with:

```sh
docker compose up --build -d --wait
```

Run the read-only live check from the repository root with the MCP environment:

```sh
uv run --project mcp python scripts/check-stack.py
```

Read-only mode verifies REST/MCP health, authentication, the exact 32-tool
catalog, the exact eleven protected schemas and annotations, the absence of an MCP
resolution tool, REST-backed project listing, the dashboard proxy's host/origin
boundary, server-side key isolation, settings/activity/report read contracts, and the
shipped WOFF2 font assets. It does
not create, gate, edit, relate, claim, append events, resolve, complete, or
delete records.

Writes require the explicit `--project-id` opt-in. Run this only against a
disposable stack or an explicitly authorized project. Use a dedicated validation project whose contents
may safely retain synthetic reports, receipts, merge facts, and soft-deleted records:

```sh
uv run --project mcp python scripts/check-stack.py \
  --project-id YOUR_TEST_PROJECT_UUID \
  --verified-against FULL_COMMIT_OID_YOU_INSPECTED \
  --affected-path 'backend/src/**' \
  --affected-path 'mcp/src/**'
```

The full 40- or 64-hex object ID and every repeated path must come from the
repository/dependency scope the operator actually inspected for this synthetic
run. The checker validates and stores that declaration but never selects a
checkout, resolves an abbreviation, or runs Git. Use different patterns when
the inspected test dependency scope differs; do not copy the example values as
fabricated provenance.

The write path performs the combined canonical lifecycle:

1. prepares and retains one UUID plus exact arguments for every protected call,
   deliberately discards the first create result, and recovers the original IDs
   through exact same-key replay without duplicate events;
2. proves the exact-title suggestion group through the safe suggestion tool with
   no version/event effect or prompt exposure, then proves canonical/grouped
   search with exact matched-member identity, bounded recall, resource/prompt
   behavior, and request-known credential/capability rejection;
3. claims one candidate, discards a completed `request_human_input` response,
   and recovers the same unresolved gate through exact MCP replay;
4. proves active-plus-waiting context, text-free attention count, attention and
   gate-history reads, hierarchy gate aggregate, ready/fresh-claim exclusion,
   and preserved active claim replay/renew/release;
5. resolves through the dashboard proxy, repeats the frozen UUID/body, and
   proves byte-equivalent historical result plus attention/history/context/
   event/readiness convergence with no MCP resolution tool;
6. exercises blocker and lease exclusions, exact relationship/release/removal
   replays, natural false no-op replay, and deterministic ready ordering;
7. creates a child/discovered item with both relationships atomically and
   verifies direct-parent ready filtering plus hierarchy branch presentation;
8. completes/reopens work, verifies immutable event history, removes graph
   edges, resolves any interrupted run-owned gate during cleanup, then
   soft-deletes all five synthetic records and confirms ordinary reads return
   `404`.

At Phase 10 the writable checker also freezes valid non-empty ordered scopes on
the synthetic create, add-checkpoint, and completion intents; verifies exact
replay, history, bounded context, resource, and resume-prompt retention; and
confirms search and every compact checkpoint pointer omit the field. It does not
run the local Git helper: helper assessment belongs to the separately selected
workspace/security matrix, not to the repository-blind API/MCP stack.

The Core write check must additionally review two exact contexts, merge a
structurally eligible source through `merge_work`, discard/recover one response
with the same key, verify paired events and canonical/alias projections, reject
an alias claim/mutation without redirect, and clean up only by soft-deleting
other synthetic roots. The merge and alias are immutable audit history and must
not be deleted during cleanup; therefore run this only against a disposable
stack whose database will be discarded.

The checker prints its synthetic run UUID before its first write. Retain that
line until cleanup succeeds. If the process is interrupted, recover only that
run's exact marker data with:

```sh
uv run --project mcp python scripts/check-stack.py --project-id YOUR_TEST_PROJECT_UUID --cleanup-run-id PRINTED_RUN_UUID
```

Cleanup retains each protected body and UUID through one exact retry after an
ambiguous transport or server response. If an interrupted run lost an active
lease token, wait for the lease to expire before running cleanup recovery.

The script registers cleanup as soon as the run marker exists. Its `finally`
path recovers exact synthetic IDs by marker plus exact synthetic session
provenance when a response may have been lost. It removes only relationships
created by that run and then deletes only marker-bearing records. Soft deletion
remains in database history; the script never deletes the project or touches
unrelated work.

Add `--other-project-id` to prove the new ID cannot be read through a second
project. Do not pass either project option without authorization to write in the
named project. Prefer a disposable full stack for automated write-path checks.

The Phase 12 write path also fetches current settings before authored closeouts,
checks the exact stored reports for Done/Won’t do/Promoted, dismisses with
same-key replay and new-key natural no-op, manually creates a pending follow-up
with both provenance links, and verifies exact activity entries through cursor
catch-up. It proves report retrieval after the source work is soft-deleted.

## Historical Phase 11 implementation and deployment gates

The following checklist records the Phase 11 baseline. Its historical version
and catalog values do not supersede the Phase 12 boundary above:

1. `pre-commit run --all-files` and the full backend suite against isolated
   PostgreSQL with no database skips, followed by backend Ruff and `ty`;
2. fresh zero-to-0019 plus populated 0018-to-0019 preservation, all historical
   receipt vectors, deterministic generation backfill, direct-SQL invariants,
   schema/catalog parity, evidence immutability, conditional downgrade/refusal,
   concurrent completion/replay races, and lossless eligible re-upgrade;
3. the full MCP suite in its separate frozen environment, followed by MCP Ruff
   and `ty`, with exact 28-tool/eleven-protected catalogs, strict evidence
   write/read contracts, 1 MiB pre-SDK guards, identity-only 3 MiB history, and
   the complete 12 MiB result-envelope fixture on HTTP and stdio;
4. frontend unit tests, typecheck, production build, and the isolated Playwright
   stack, including evidence editor/history/retry behavior, hostile inert
   rendering, exact proxy query/body policies, and no artifact execution;
5. regenerated OpenAPI plus strict backend, MCP, and frontend snapshot/consumer
   parity, including the executable conditional operation-ID schema and sole
   safe history route;
6. the disposable helper/security matrix, real Git 2.44 rejection before
   repository access, real Git 2.45-or-newer supported lanes on Bash 3.2 and
   newer hosts, packaged mode/inventory, and installed cold-session workflow
   using the new completion-evidence reference;
7. the deployed nginx/browser identity boundary, including a stock module-free
   nginx syntax check and a real inherited google/ngx_brotli filter whose
   control is `br`, against controlled identity, gzip-success, gzip-error,
   exact-limit, and max-plus-one responses, with content-free rejection and no
   cache/transformation ambiguity;
8. the aggregate read-only audit at 0018 and 0019 on disposable populated data,
   with zero blocking findings and no evidence/path-bearing output, plus the
   0019 pre-enablement gate rejecting unexpected Phase 11 state;
9. plugin manifest parsing plus disposable fresh `0.10.0` and sequential
   `0.6.1 -> 0.7.0 -> 0.8.0 -> 0.9.0 -> 0.10.0` installation;
10. read-only and authorized writable stack checks against a disposable project,
   including the exact catalog/protected contract, historical replay, ordered
   scoped completion with structured evidence, evidence-free completion,
   high-water history, scope/evidence-free compact surfaces, duplicate
   suggestion, gate lifecycle, and irreversible merge/alias lifecycle; and
11. generated worst-case proof that compact completion/fingerprint/response/
    receipt/database representations fit 896 KiB, raw REST/browser ingress fits
    1 MiB, a legal ten-episode page fits 3 MiB, and the complete MCP response
    with maximum permitted ID fits 12 MiB; and
12. a rebase onto current `origin/main`, repeated full surface audit,
    `git diff --check`, Markdown link/path validation, schema/tool snapshots,
    and a cold adversarial review whose substantiated findings are fixed and
    reverified before the pull request opens.

Deployment approval is a separate operator-owned gate. It requires named
pre/post-0016, pre/post-0017, pre/post-0018, and pre/post-0019 backups restored in isolation;
ordinary, gate, merge, event, witness, historical receipt, and scoped receipt
parity plus completion generation/evidence parity; populated 0018/0019 target
audits; strict old-client failure; safe pre-use downgrade, post-use refusal, fix-forward, and whole-restore
rehearsals; target Bash/Git capability evidence; and explicit product/operator
approval. Repository tests and disposable-stack evidence do not prove those
target-environment results.

Do not treat a focused suite, PostgreSQL-skipped run, read-only smoke, helper
suite backed only by a version-reporting wrapper, or marketplace refresh by
itself as a Phase 11 implementation result. Repository completion requires the
isolated database/E2E/security lanes, full pre-commit, and cold adversarial
review. It must not be described as deployment approval. At the prior deployment boundary,
application/API/MCP/dashboard `0.7.0`, plugin `0.10.0`, migration
`0019_structured_completion_evidence`, and the operational guidance formed one
compatible boundary. Once Phase 11 state exists, 0.5.x first-party clients are
unsupported; add no projection shim, legacy model union, receipt rewrite,
standalone evidence write, or old-backend bridge.

## Manual browser pass through Phase 12

Exercise project empty state and switching, root browsing, lazy child expansion,
subtree-aware filters, flat-search breadcrumbs, Pending/Active/Dropped/Deferred
lifecycle filters,
lexical search and explicit Semantic opt-in, queue selection by click and by
the arrow keys, the horizontal arrows walking the lifecycle filter row, Escape
dropping the selection, the digit keys switching projects from the workspace
picker, `c` copying the open record's recall pointer, lazy queue paging with the
total in the result count, bounded
context in the detail pane, grouped pointer-only relationships in the Graph
tab, the checkpoint timeline in the History tab, immutable activity timeline
paging in the Activity tab, progress-event creation, prompt copy, work-item ID
copy, inline identity editing in the Context tab, checkpoint creation,
completion, deletion, and stale-version recovery. Confirm the selected tab
persists across items while edit and merge state reset, and that a reload with
`?work=<id>` restores the selection. At a narrow viewport, confirm the queue
scrolls with the page, the pane opens as a full-height sheet with a working
Back button, the tab bar scrolls horizontally, and hierarchy, editors, dialogs,
long IDs, and defensive depth/cycle fallbacks remain usable.

For Phase 10, exercise initial, context/progress, and completion checkpoints
with omitted scope and with a valid ordered non-empty declaration plus an
actually inspected baseline. Confirm an empty declaration remains absent on
read, non-empty order/case survives history and full recall, scope-free compact
pointers stay unchanged, invalid grammar or commitless scope is rejected without
echoing values, and an ambiguous mutation retry retains the exact frozen order.
Confirm the browser presents the data only as caller-declared provenance and
neither runs Git nor claims that a checkpoint is fresh, current, verified,
correct, or safe.

For Phase 11, use the browser to complete an unleased work item with mixed
command, observation, and artifact evidence. While another client holds an
active lease, confirm the browser completion control remains unavailable and
the proxy never accepts a `lease_token`; exercise the leased completion
separately through MCP or direct REST with its exact active token. Exercise
each outcome/exit-code combination,
timestamp canonicalization, preserved order/case/internal whitespace, duplicate
artifact rejection, the 20-entry and aggregate-byte boundaries, and a
validation failure that leaves work Pending. Force an ambiguous completion and
confirm only the frozen same-UUID intent is available; success or replay must
show one exact episode. Reopen and complete again, then page the Evidence tab
and distinguish the current episode from older structured and evidence-free
ones. Verify an alias shows only its source-owned history, a deleted item is
concealed, commands remain inert text, and only accepted HTTPS artifacts are
links. Repeat at narrow width and with hostile bidi/control/very-long text;
there must be no HTML execution, automatic navigation, layout escape, secret or
operation-ID persistence, or evidence in ordinary Context/History/Activity tabs.

Open the valid create dialog and confirm no suggestion request occurs while
typing. Use **Check existing work** once, inspect canonical and matched-member
IDs plus categorical signals and disclosed semantic scope, then change each of
title, summary, prompt, and tags and confirm the result becomes stale. Exercise
empty, lexical-fallback, busy, unavailable, aborted, and delayed-response states
without losing the draft. In every state, confirm **Create work** remains
enabled whenever the ordinary form is valid and creates the unchanged draft.
Inspect browser storage, URLs, logs, and live frames to confirm neither drafts
nor candidates are persisted or published.

Add and remove every relationship type through the editor with exact stored
direction and truthful provenance. Confirm repeated identical non-`duplicate-of`
adds are harmless and the editor prevents self-links or invalid context
selection. Use REST or MCP in the
test project to verify cycle, second-parent, missing-context, and invalid-context
requests return sanitized actionable errors. Confirm work with active
relationships cannot be deleted. A nonmatching ancestor should
remain visible only when a matching descendant needs navigation scaffolding.

Use an API or MCP client to create an agent-owned claim, then add an unresolved
incoming blocker. Confirm lifecycle, `Active`, and `Blocked` stay distinct;
the retained lease remains visible through safe holder/timestamps but no token
appears in browser state or network payloads. Confirm there is no capability
claim, renewal, or force-release UI. Separately exercise the detail split
control: a human may mark eligible work Active through the token-free route and
may choose Pending only against the exact safe lease projection reviewed (or a
Dropped row). Complete the blocker and verify readiness recovers.

Use MCP to request human input on claimed work. Confirm `Active`, `Blocked`, and `Waiting`
remain independent, waiting wins only the display label, the item leaves ready
and rejects a fresh claim, and exact active claim replay/renew/release still
work. Verify Needs Attention count/paging, literal question rendering,
breadcrumbs, detail and event history, and an inclusive hierarchy gate count.
Confirm the browser cannot create a gate and MCP exposes no resolution tool.

Resolve through the dashboard. When context has changed, review the focused
one-snapshot context and submit its exact required reviewed revision; mutate
again before submit and confirm `gate_context_changed` forces a new review and
UUID. Interrupt a resolution response and retry only the frozen body/key.
Confirm one resolution/event, immediate queue removal, ready recovery only when
lease/blocker/lifecycle also permit it, retained paired history, and no gate
text or mutation material in browser storage or data-free live frames.

Expand planned and discovered branches and verify collapsed-by-default display,
direct/descendant and blocked/active/completed/discovered counts, discovery
labels, gate counts, filter-hidden explanations, child paging, defensive
cycle/depth fallbacks, and passive refresh when a descendant lease expires.

In a disposable stack only, review two same-project root contexts and use
**Merge as duplicate…** in the detail pane; the merge panel opens inline at the
top of the Graph tab. Confirm separate bidi-isolated source/destination panels,
full UUIDs, exact direction, both review revisions, rationale, and mandatory
permanence acknowledgement. Exercise source structural and unresolved-gate
blockers; verify an active source lease disables browser merge without exposing
a token. Interrupt the response and retry only the frozen two-work-key intent;
confirm one merge, one supporting mark, the expected relationship-event pair
when new, exactly two `work_merged` events, and one version increment per
endpoint. The source must remain available as an audit alias but absent from
ready/root views; every fresh alias mutation must fail without redirect. Follow
its explicit canonical link, inspect group/alias search, and verify no source or
destination checkpoint, lifecycle, relationship, gate, or lease was coalesced.

With a nonblank search, Semantic starts disabled. Enabling it performs a hybrid
request; disabling it restores lexical retrieval. Repeat the enabled query once
to exercise cache reuse. Never leave synthetic relationships or pending work in a
user's project after manual verification.

For the Phase 6 mutation pass, interrupt one of each browser action after
sending and before accepting its response. Confirm the same-document dashboard
retains the frozen call across pane deselection, dialog closure, and component
unmount, blocks intersecting actions, warns before unloading, and retries the
identical method, path, UUID, and serialized body. Verify a coherent original or
replayed response reconciles current state and heals live invalidation, while
malformed `2xx`, `5xx`, and transport loss remain unresolved and an exact-call
key conflict remains blocked for investigation. Finally, reload an unresolved
document and confirm the UI makes no recovery promise: no UUID or body was
written to local storage, session storage, cookies, a URL, or a header, and a
replacement UUID is not presented as a safe retry.

## D1/D2 coordinated verification

Use `tests/fixtures/external-record-contract-v1.json` across SQL/backend/MCP/browser
for exact URL grammar, label Unicode and timestamp normalization. Focused suites
exercise omitted versus explicit-empty PATCH, ordered receipt intent, sparse old
response replay, all read pointers, large four-kind event metadata and unchanged
progress limits, exact inverse search ownership, and strict request-bound external
suggestions. The offline `examples/external-candidate-frame.py` fixtures check
actual SDK JSON-RPC HTTP and stdio frame sizes, including escaping, multibyte text,
large draft/envelope, count reduction, and an untransportable unchanged draft.

Run the normal full backend PostgreSQL suite (not a skipped DB suite), MCP tests,
Ruff/ty, frontend Node tests/typecheck/build and isolated Playwright stack. Generate
OpenAPI and run consumer correspondence tests. The packaged plugin contains the
same three skills plus shared `external-records.md`; validate exact fresh 0.12.0
installation and 0.11.0-to-0.12.0 update with isolated offline CLI state. The example
is a pure caller-side allocation/frame demonstration, not a provider daemon.

Record migration/content/receipt preservation and restored-catalog audit evidence,
index plus total route costs, worst-case event/receipt/context sizes, and actual
model 1/16/64 candidate cold/warm/contention measurements separately from fake-vector
regressions. Follow the explicit quiescent backup and pre/post/restored audit commands
in [operations](operations.md#external-records-release-0021-to-0022). No test command
here authorizes a live provider read or production cutover.
