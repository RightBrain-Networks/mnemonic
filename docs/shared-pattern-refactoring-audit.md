# Shared-pattern refactoring audit

Date: 2026-09-05

Audited revision: `90f59435f63584a5c703a46a4a8cd8311c7b71b3` (`main`, “Add structured completion evidence”, PR #32).

## Scope and assessment

Mnemonic has useful shared foundations, but their adoption is uneven across the
features added in successive phases. The most valuable consolidation is at the
boundaries between features: contract validation, browser request state,
work-history projections, and test infrastructure. A large framework rewrite is
unnecessary; most opportunities have a small first extraction with two or more
existing consumers.

The repository's [delivery snapshot](roadmap.md#delivery-snapshot) records
Phases 1–11 as shipped and Phases 12–13 as planned. This audit covers the current
backend, MCP adapter, frontend, representative unit/integration/acceptance tests,
plugin references, and supporting scripts. The roadmap and
[architecture documentation](architecture.md) were used to identify intentional
differences. This source audit does not assess production runtime behavior or
enumerate every textual duplicate.

Evidence links below are relative to this report; source line numbers and symbol
names refer to the audited revision. “Observed” identifies a difference visible
in the implementation. “Risk” describes a possible future consequence, not a
claim that a production failure has occurred. The findings preserve the original
assessment; the follow-up below records which proposals are now implemented.

## P1 implementation follow-up

The five P1 findings are addressed in the accompanying application/API/MCP/
dashboard `0.7.0` changes. The original evidence and line references below
describe the audited revision, not the refactored source. P2 and P3 findings
remain opportunities, not completed work.

| Finding | Implemented convergence |
| --- | --- |
| F01 | One reviewed [validation vocabulary](validation-vocabulary.md), explicit surface subsets, and a shared error-location corpus checked by all three packages. |
| F02 | Typed request-scope and page validators at the existing MCP HTTP boundary, with an explicit [response-shape checklist](mcp-response-validation.md). |
| F03 | One canonical-work search hook for both pickers, with scope-keyed state, cancellation, and browser coverage for clearing a delayed search. |
| F04 | Neutral revision, work, readiness, checkpoint, and relationship codecs; strict evidence rules remain layered on their shared base. |
| F05 | Shared registry/UI scope selection and exclusive recovery ownership, including visible fallback when an unresolved merge's panel is hidden. |

The [frontend extension guide](frontend-shared-patterns.md) describes ownership
and regression coverage for F03–F05. The database schema, plugin version, tool
catalog, receipt catalog, and frozen-request retry contract remain unchanged.
Per-fix checkpoint commits were created before combining the implementation and
this report into one PR.

## Existing foundations to extend

- [Registered mutation lifecycle](../backend/src/mnemonic_api/application/mutations.py)
  already centralizes reservation, replay, execution, receipt completion, commit,
  and mutation tracing. Routes do not need another generic mutation framework.
- [Readiness services](../backend/src/mnemonic_api/services/readiness.py) already
  share claim eligibility; [database helpers](../backend/src/mnemonic_api/database.py)
  provide coherent-read infrastructure. Extend these where appropriate instead
  of adding another stored status or readiness projection.
- [Browser mutation intents](../frontend/lib/mutation-intent.ts),
  [wire guards](../frontend/lib/wire-guards.ts),
  [search parameters](../frontend/lib/work-item-search.ts), and
  [queue paging](../frontend/components/use-work-queue-pages.ts) provide existing
  owners for shared behavior.
- [MCP HTTP handling](../mcp/src/mnemonic_mcp/api.py) already centralizes transport
  and response handling. Its one-attempt protected-write behavior must survive
  any adapter refactor.
- The [OpenAPI snapshot](openapi.json),
  [MCP contract tests](../mcp/tests/test_openapi_contract.py),
  [frontend contract tests](../frontend/tests/openapi-contract.test.mjs), and
  shared [completion-evidence](../tests/fixtures/completion-evidence-v1.json) and
  [repository-scope](../tests/fixtures/repository-freshness-scope-v1.json) corpora
  are a basis for stronger conformance checks. Contract testing is already
  established; its coverage should expand.

## Prioritization

Priority expresses refactoring value, not incident severity. P1 means address
early because drift is visible or the duplication controls correctness. P2 means
consolidate when working in the affected area. P3 means inexpensive cleanup with
lower behavioral impact. Effort is relative: S is a bounded extraction, M spans
several consumers, and L requires staged contract or query work.

| ID | Opportunity | Priority | Effort |
| --- | --- | --- | --- |
| F01 | Approved validation-field vocabulary | P1 | S |
| F02 | MCP response scope and pagination checks | P1 | M |
| F03 | Canonical-work search hook | P1 | S–M |
| F04 | Shared frontend domain codecs | P1 | M |
| F05 | Mutation blocking and recovery selectors | P1 | M |
| F06 | Secret-scanning primitives with explicit policies | P2 | M |
| F07 | Flat-search and hierarchy filter predicates | P2 | M |
| F08 | Public work-event projection | P2 | M |
| F09 | Current-checkpoint and review-revision facts | P2 | M |
| F10 | Scoped browser reads and cursor navigation | P2 | M |
| F11 | Bounded cursor serialization | P2 | S–M |
| F12 | Shared wire declarations and deeper conformance checks | P2 | L |
| F13 | Operation catalog metadata | P2 | M–L |
| F14 | MCP tool policy text | P2 | S |
| F15 | Existing UI primitives | P3 | S |
| F16 | Disposable PostgreSQL migration-test harness | P2 | M |
| F17 | Test builders and acceptance fixtures | P2 | M |

## Findings

### F01 — Maintain one approved validation-field vocabulary

**Evidence.** Field-name allowlists are maintained in
[backend validation](../backend/src/mnemonic_api/application/validation.py#L13)
(`PUBLIC_LOCATION_SEGMENTS`, lines 13–36),
[MCP validation](../mcp/src/mnemonic_mcp/validation.py#L16)
(`VALIDATION_FIELDS`, lines 16–114), and
[browser error formatting](../frontend/lib/api.ts#L23)
(`SAFE_VALIDATION_LOCATION_PARTS`, lines 23–113).

**Observed.** The backend list omits known merge fields including
`destination_work_item_id`, `reviewed_source_revision`, `work_event_count`,
`merged_by_*`, and `rationale`; client lists recognize them. The backend replaces
such names with `field`. Conversely, the browser list omits `cursor`, which the
backend and MCP recognize. Direct calls with synthetic errors confirmed that
`body.destination_work_item_id` becomes `body.field` at the backend and that the
browser drops `query.cursor` from its displayed message. This is degraded
diagnostic information, not evidence of secret leakage.

**Convergence.** Keep one reviewed vocabulary artifact, with explicit surface
subsets, and generate or verify the three constants from it. Keep independently
executed sanitizers and surface-specific formatting. Do not automatically approve
all schema keys: free-form metadata keys and caller-chosen unknown names still
need generic replacement.

**Verification.** Table-driven cases should cover nested merge revisions,
repository settings, cursors, unknown keys, array indices, and secret-bearing
values. Assert useful field locations and absence of caller values.

### F02 — Apply a consistent MCP response-scope validation pattern

**Evidence.** [MCP server helpers](../mcp/src/mnemonic_mcp/server.py#L468)
independently validate completion-evidence pages (lines 468–522), event pages
(634–640), gate history (693–710), and work pages (724–766).
[WorkPage and ReadyWorkPage](../mcp/src/mnemonic_mcp/models.py#L2184)
(lines 2184–2231) also repeat page-bound arithmetic.

**Observed.** Older handlers such as
[`list_checkpoints`](../mcp/src/mnemonic_mcp/server.py#L1020)
(1020–1036) and
[`get_relationship` / `list_relationships`](../mcp/src/mnemonic_mcp/server.py#L1464)
(1464–1502) validate response models without equivalent checks binding returned
IDs or page parameters to the request. Their page models are mostly envelope
declarations. This is uneven defensive validation; it does not establish that
the API currently returns data for the wrong work item or project.

**Convergence.** Compose typed helpers for requested-ID equality, expected
limit/offset, count bounds, and row uniqueness through the existing
`MnemonicAPI.request(..., response_validator=...)` hook. Keep each tool's scope
explicit. Evidence snapshot cursors, ownership checks, pointer-only results, and
mutation-outcome classification remain domain policies.

**Verification.** Feed mocked upstream responses with wrong IDs, incorrect page
parameters, duplicate rows, impossible counts, and valid edge cases through each
affected tool. A common checklist should state which checks each response shape
supports; do not add fields to compact responses merely to fit a helper.

### F03 — Share the canonical-work search lifecycle

**Evidence.** [RelationshipPanel](../frontend/components/relationship-panel.tsx#L100)
(lines 100–143) and
[WorkMergePanel](../frontend/components/work-merge-panel.tsx#L106)
(106–150) each implement trimmed 250 ms debounce, request cancellation, canonical
search decoding, self-exclusion, and loading/error state. Both already use shared
query-building and decoding helpers; the repeated part is React orchestration.

**Observed.** The relationship picker clears `searching` for an empty query; the
merge picker does not. If the query is cleared while a request is pending, effect
cleanup aborts that request and its guarded `finally` skips clearing loading.
The merge panel renders its loading message whenever `searching` is true
(line 277). Static control flow therefore exposes a path to a stuck loading
indicator. This sequence was not reproduced in a browser during this audit.

**Convergence.** Extract a `useCanonicalWorkSearch` hook with explicit project,
excluded work ID, query, enabled flag, and result limit. Leave relationship
direction, destination eligibility, merge review, and mutation controls in their
panels. A common picker component can follow if its presentation also converges.

**Verification.** Delay a response, clear the query, and assert loading stops.
Also cover late responses after project/work changes, invalid results,
case-insensitive self-exclusion, and canonical-only results. Preserve explicit
merge review in the existing duplicate-handling acceptance suite.

### F04 — Give common frontend domain codecs neutral ownership

**Evidence.** The exact merge-review shape is independently checked in
[`decodeMergeReviewRevision`](../frontend/lib/duplicate-handling.ts#L106)
(lines 106–125),
[`mergeRevision` / `sameMergeRevision`](../frontend/lib/mutation-responses.ts#L493)
(493–524), and
[`validMergeReviewRevision`](../frontend/lib/proxy-policy.ts#L308)
(308–319). Human-gate revision checks likewise recur in
[human-gates.ts](../frontend/lib/human-gates.ts#L88) and
[proxy-policy.ts](../frontend/lib/proxy-policy.ts#L294).

**Risk and cause.** Shared codecs live in feature modules: general summary,
readiness, and checkpoint-pointer decoding is in `human-gates.ts`, while
checkpoint and relationship codecs are in `mutation-responses.ts`.
`duplicate-handling.ts` already imports from the latter, so importing its revision
decoder back would create a cycle. Feature ownership encourages reimplementation.

**Convergence.** Start with a leaf revision-codec module depending only on types
and `wire-guards`. Share structural predicates and equality functions, with
caller-specific error messages. Then move genuinely general work/checkpoint/
relationship codecs into domain modules as consumers change.

**Verification and limits.** Exercise missing/extra fields, integer bounds, UUID
case/equality, and request/response acceptance parity. Keep OpenAPI field catalogs
accurate. Completion-evidence pointers deliberately require completion kind,
canonical timestamps, and stricter tags; compose those rules over a shared base
instead of replacing them with the looser general pointer decoder.

### F05 — Reuse the mutation registry's blocking and recovery rules

**Evidence.** The registry already exposes `hasDispatched`,
`hasDispatchedForProject`, and `blocks` in
[mutation-intent.ts](../frontend/lib/mutation-intent.ts#L160) (lines 160–175).
Components independently filter for non-prepared intents and intersect conflict
keys in [work-event-timeline.tsx](../frontend/components/work-event-timeline.tsx#L65)
(65–70), [relationship-panel.tsx](../frontend/components/relationship-panel.tsx#L148)
(148–156), and
[human-gate-resolution.tsx](../frontend/components/human-gate-resolution.tsx#L77)
(77–81). [Dashboard recovery placement](../frontend/components/dashboard.tsx#L1650)
(1650–1705) and the merge panel add further intent classification.

**Risk.** A new intent state or conflict rule requires matching changes in the
registry, disabled controls, retry UI, and local/global recovery placement.
The registry can remain correct while the interface gives inconsistent feedback.

**Convergence.** Have registry methods and subscribed React selectors use the
same pure predicates. A `useMutationScope` hook can return blocked state and
relevant intents. Promote the existing private dashboard recovery component
(lines 145–181), allowing domain-specific wording and detail.

**Verification and limits.** Cover every intent state, own-slot exclusion, both
merge endpoint keys, gate/work conflicts, and one recovery presentation per
intent. Preserve exact in-memory bodies and UUIDs, non-retryable safety conflicts,
navigation restrictions, and byte-identical retries. This is not a replacement
for the established receipt executor.

### F06 — Share secret-scanning mechanics, retaining named endpoint policies

**Evidence.** Durable-text traversal and control-value checks recur in
[gate guards](../backend/src/mnemonic_api/services/gates.py#L146) (lines 146–182),
[progress-event guards](../backend/src/mnemonic_api/services/work_events.py#L427)
(427–471), [merge guards](../backend/src/mnemonic_api/services/duplicates.py#L392)
(392–426), and
[receipt/evidence guards](../backend/src/mnemonic_api/services/client_operations.py#L448)
(448–565). Merge and evidence guards also separately enumerate UUID spellings.

**Risk.** Fixes to nested traversal or UUID recognition require multiple edits.
However, the existing scanners intentionally differ: progress examines metadata
keys and carries locations; some guards inspect values; receipt checks recognize
reserved control fields; exact matching and substring matching serve different
contracts. Their differences are not themselves defects.

**Convergence.** Extract a typed JSON walker yielding paths, keys, and values,
plus UUID spelling/matching helpers. Keep endpoint wrappers responsible for
selected fields, exemptions, matching policy, error codes, and diagnostic safety.
Characterize validation order before considering a common pre-reservation hook.

**Verification and limits.** Preserve checks that must precede reservation and
receipt replay, general receipt exact-only behavior, and public historical IDs.
Test nested lists/dictionaries/keys, UUID variants, empty controls, designated
fields, and errors that never reproduce secret values. Retain the existing
PostgreSQL rejection and replay tests; broadening every scan to substring
matching would change accepted inputs.

### F07 — Share work-list filter predicates across flat search and hierarchy

**Evidence.** [Flat-search helpers](../backend/src/mnemonic_api/application/routes/work_search.py#L170)
(lines 170–217) implement status, lease, tag, and checkpoint-provenance predicates
using SQLAlchemy. [Hierarchy matching](../backend/src/mnemonic_api/services/hierarchy.py#L33)
(33–89) expresses the same rules in manually assembled SQL. Parallel query fields
and tag normalization appear in [schemas.py](../backend/src/mnemonic_api/schemas.py#L3337)
(3337–3356 and 3387–3401).

**Risk.** A status or provenance change can make the flat view and hierarchy
disagree. Multiple provenance constraints must match one checkpoint; separate
existence tests could accidentally satisfy them from different checkpoints.

**Convergence.** Move composable status/provenance predicates below the route
layer. Accept explicit work/checkpoint aliases and a captured database-time
expression. Hierarchy already embeds compiled shared readiness expressions;
extend that approach. Share query-field declarations only where semantics match.

**Verification and limits.** Build a flat/root/children parity matrix for absent,
active, and expired leases; lifecycle states; tag casing; and split provenance.
Retain hierarchy query-count bounds. `pending` filtering is not ready-work
eligibility. Ready-work delegates tag normalization to PostgreSQL, while
search/hierarchy normalize requests in Python. Preserve Unicode and validation
behavior; [ready-work tests](../backend/tests/test_ready_work_postgres.py#L150)
explicitly cover mixed-case and Unicode inputs.

### F08 — Define the public work-event projection once

**Evidence.** [`work_event_read`](../backend/src/mnemonic_api/services/work_events.py#L524)
(lines 524–567) maps ORM fields and derives relationship direction/counterpart.
The [paged history query](../backend/src/mnemonic_api/services/work_events.py#L624)
(approximately 624–711) repeats field lists and SQL derivation.
[Recall SQL](../backend/src/mnemonic_api/services/work_context.py#L215)
(215–280 and 551–585) repeats the projection again.

**Risk.** A public event field or relationship semantic must change in three
serialization paths. The same stored event can acquire inconsistent presentation
depending on whether it is returned by a mutation, history, or recall.

**Convergence.** Introduce an explicit public field definition and assembler for
ORM records/row mappings, with shared SQL expression builders where aggregation
needs database-side projection. Retain an allowlist; serializing every model
column would weaken the public/private boundary.

**Verification and limits.** Compare progress append responses with `/events`
and `/context`; compare other event kinds between history and recall. Cover
directed and `related` edges, removed relationships, progress, completion, merge,
and backfilled origins.
Preserve metadata validation, historical relationship facts, incomplete-history
flags, ordering, and the bounded coherent recall query. Avoid per-event queries.

### F09 — Share current-checkpoint and revision facts

**Evidence.** [Gate revisions](../backend/src/mnemonic_api/services/gates.py#L192)
(lines 192–240), [merge revisions](../backend/src/mnemonic_api/services/duplicates.py#L291)
(291–332), and [recall queries](../backend/src/mnemonic_api/services/work_context.py#L174)
(174–198, 392–399, 720–725) independently select the latest context checkpoint
and relevant event counts. Summary queries in `work_context.py` (85–100) repeat
the latest-context ordering. Gate relationship-event membership also appears as
both Python constants and SQL literals.

**Risk.** These facts control stale-review rejection. Different checkpoint
tie-breakers or event membership could make a revision returned by a read differ
from the revision accepted by a subsequent mutation.

**Convergence.** Extract alias-aware latest-context and event-count expressions,
then compose separate `HumanGateContextRevision` and `MergeReviewRevision`
constructors. Keep queries batched within their existing snapshots.

**Verification and limits.** Compare read and mutation revisions after checkpoint,
progress, lifecycle, and relationship changes, including tied timestamps. Gates
count relationship events; merge review counts all work events. Share those
underlying facts without making one generic revision token. Preserve project
scope, work-row lock order, recomputation after locking, and fail-closed handling
of missing state. Do not infer historical revisions.

### F10 — Share scoped browser-read and cursor-navigation mechanics

**Evidence.** [Human attention](../frontend/components/human-attention-list.tsx#L48)
(lines 48–125), [gate history](../frontend/components/human-gate-panel.tsx#L37)
(37–103), [event history](../frontend/components/work-event-timeline.tsx#L109)
(109–147), and [hierarchy](../frontend/components/work-hierarchy.tsx#L112)
(112–151) each own loading/error state, cancellation, request identity, decoding,
and refresh behavior. Attention and gate history additionally duplicate a
`[null]` cursor stack, index, back navigation, forward-history truncation, and
head reset.

**Risk.** Every new panel must correctly suppress late responses and decide when
to clear data, errors, loading, and pagination. These decisions are hard to review
when embedded in unrelated components.

**Convergence.** Start with a cursor-navigation reducer for the two gate views.
Then extract a scoped async-read hook with explicit request identity, enabled
state, cancellation, and stale-data policy. Leave decoding and reconciliation
to callers. Preserve the stronger existing queue-paging hook rather than
replacing it wholesale.

**Verification and limits.** Background/live attention refresh and resolution
preserve a deep cursor and sibling answer drafts; the explicit Refresh queue
action resets to the head. Gate history refreshes to its head; events return to
newest; hierarchy clamps vanished offsets. Make those named policies. Evidence snapshot
append checks and advisory draft invalidation remain specialized. Test late
success/error, unmount, retry, next/back branching, page shrinkage, and the
existing human-gate focus/draft preservation acceptance cases.

### F11 — Share bounded cursor encoding, not pagination semantics

**Evidence.** [Gate cursor helpers](../backend/src/mnemonic_api/services/gates.py#L53)
(lines 53–143) and
[evidence cursor helpers](../backend/src/mnemonic_api/services/completion_evidence.py#L41)
(41–169) each implement versioned JSON/Base64URL envelopes, decoded-size bounds,
error translation, and scope validation.

**Observed.** Both emit sorted ASCII JSON in unpadded Base64URL and limit decoded
payloads to 2,048 bytes. Evidence additionally rejects padding and requires exact
canonical re-encoding; gate decoding does not. This is a current acceptance
difference, not an established fault.

**Convergence.** Extract bounded serialization/decoding primitives with explicit
canonicality policy. Keep separate typed gate and evidence cursor payloads,
endpoint scope checks, and data lookups.

**Verification and limits.** Share malformed encoding, oversized payload, and
invalid JSON vectors. Retain gate sequence integers versus evidence decimal-string
event IDs, filter/direction binding, evidence high-water checks, and event
ownership verification. Preserve currently accepted gate inputs during a pure
refactor; any stricter acceptance rule needs a separate behavior change.

### F12 — Reduce manual wire-contract translation and strengthen conformance

**Evidence.** Affected-path constraints recur in
[backend schemas](../backend/src/mnemonic_api/schemas.py#L84) (lines 84–127),
[MCP models](../mcp/src/mnemonic_mcp/models.py#L805) (805–851),
[browser validation](../frontend/lib/affected-paths.ts), and the
[Bash verifier](../plugin/bin/mnemonic-repository-freshness#L19) (19–21).
Evidence enums, limits, and response properties follow the same cross-language
pattern. Existing [MCP checks](../mcp/tests/test_openapi_contract.py#L12)
(12–40) generally compare property and required-field names; the
[frontend checks](../frontend/tests/openapi-contract.test.mjs#L50) likewise verify
field catalogs rather than every semantic constraint.

**Risk.** Type, enum, bound, nested-reference, or nullability changes can escape a
field-name comparison. Existing shared corpora substantially reduce this risk;
no acceptance drift in affected paths or evidence was established here.

**Convergence.** Generate elementary declarations/constants/field sets where their
meaning is exact, and compare normalized schema constraints for manually owned
definitions. Extend the existing semantic corpora. The stronger duplicate-
suggestion request comparison in `mcp/tests/test_openapi_contract.py` (43–53)
provides a small starting example.

**Verification and limits.** Deliberate enum, bound, or nullability changes should
fail consumer conformance tests. Keep validators independently executed at each
trust boundary, separate backend/MCP environments, and the Bash helper's runtime
independence. Preserve omission-versus-null rules, strict evidence semantics,
and deliberate compact-pointer exclusions. This does not require a shared
runtime package across all languages.

### F13 — Describe operation metadata once, with explicit surface subsets

**Evidence.** The backend already has
[operation kinds and a registry](../backend/src/mnemonic_api/services/client_operations.py#L63)
(lines 63–93 and 174–244). Browser mutation kinds are separately listed in
[mutation-responses.ts](../frontend/lib/mutation-responses.ts#L46) (46–60);
transport effects recur in [MCP API code](../mcp/src/mnemonic_mcp/api.py#L28)
(28–33) and [proxy policy](../frontend/lib/proxy-policy.ts#L616) (616–629).
Tool registrations independently declare methods, statuses, effects, and
annotations. Some API routes already publish `x-mnemonic-effect` metadata.

**Risk.** Adding an operation requires coordinated edits to disconnected
catalogs. No current catalog mismatch was established in this audit.

**Convergence.** Extend explicit contract metadata to describe operation ID,
method/path, expected status, effect, and allowed surfaces. Use it to generate
declarations or verify independently maintained registries. Begin with parity
checks; avoid a generic dispatcher that obscures route authorization.

**Verification and limits.** Pin 28 MCP tools, 11 receipt-protected MCP writes,
13 REST receipt kinds, and 11 protected browser mutations. Suggestion POST stays
a safe read; human resolution has no MCP tool; lease-capability routes stay
browser-forbidden; evidence remains nested in `complete_work`. An operation's
presence in REST must never implicitly expose it on another surface.

### F14 — Compose repeated MCP policy text at tool registration

**Evidence.** The protected-write retry paragraph appears in eleven
[MCP tool descriptions](../mcp/src/mnemonic_mcp/server.py#L864), at lines 864,
998, 1094, 1200, 1387, 1424, 1513, 1618, 1671, 1715, and 1757. Repository-scope
instructions also recur in create/checkpoint/completion descriptions. The plugin
already has useful shared policy in
[authority-and-provenance.md](../plugin/reference/authority-and-provenance.md#L139),
[repository-freshness.md](../plugin/reference/repository-freshness.md), and
[completion-evidence.md](../plugin/reference/completion-evidence.md).

**Observed.** Retry wording has local variations, including “backend failure,”
“inspect safely,” and domain-specific frozen-argument requirements. Those
variations are not proven errors, but future policy changes require reviewing
eleven embedded paragraphs.

**Convergence.** Compose shared retry, historical-result, and provenance text with
explicit tool-specific descriptions. Keep the full resulting guidance in each
exported description because a client may see only one tool. Do not replace it
with a link to another document.

**Verification and limits.** Enumerate the 28 exported tools and check that all
11 protected writes carry the common guidance and intended annotations. Preserve
merge reviewed revisions, completion evidence ordering, and other specific
requirements. Extend existing plugin semantic tests instead of asserting
incidental source-string formatting.

### F15 — Promote the UI primitives already copied across features

**Evidence.** Local SVG `Icon` implementations appear in
[dashboard.tsx](../frontend/components/dashboard.tsx#L109) (lines 109–125),
[work-item-list.tsx](../frontend/components/work-item-list.tsx#L22) (22–30),
[work-queue.tsx](../frontend/components/work-queue.tsx#L30) (30–37),
[work-detail-pane.tsx](../frontend/components/work-detail-pane.tsx#L51) (51–60),
and [work-merge-panel.tsx](../frontend/components/work-merge-panel.tsx#L30)
(30–35). The private dashboard `ErrorNotice` (222–223) has analogous error/retry
markup in checkpoint history, attention, gates, event history, and settings.

**Risk.** Icon stroke/path corrections, button semantics, and accessible error
announcements can drift across otherwise similar controls.

**Convergence.** Promote a typed `Icon` using the existing path definitions and
an `ErrorNotice` with optional retry content. A small loading-status primitive
is another candidate where markup actually matches. Extend existing shared
badges, lease summaries, time formatting, and repository-declaration components;
a new design-system dependency is unnecessary.

**Verification and limits.** Preserve per-feature text, classes, accessible names,
and retry behavior. Use typecheck/build and relevant visual/keyboard acceptance
coverage. Keep the fixed-color brand logo separate from monochrome UI icons;
avoid tests that merely mirror the new component's source.

### F16 — Consolidate disposable PostgreSQL migration-test setup

**Evidence.** The shared [conftest.py](../backend/tests/conftest.py#L23)
(lines 23–78) creates isolated schemas and migration engines. Similar setup and
teardown is repeated in
[Phase 6 migration tests](../backend/tests/test_phase6_migration_postgres.py#L26)
(26–55), [duplicate-merge migration tests](../backend/tests/test_duplicate_merge_migration_postgres.py#L37)
(37–56, with cleanup at the end), and
[freshness migration tests](../backend/tests/test_repository_freshness_migration_postgres.py#L830)
(830–845). Additional phase tests repeat the same engine, search-path, Alembic
connection, disposal, and schema-drop lifecycle.

**Risk.** Isolation and cleanup rules are reimplemented whenever a phase needs
an older starting revision. Some helpers return raw admin/engine/schema tuples,
making each caller responsible for cleanup. A fixture targeting only `head`
cannot directly serve historical migration scenarios, which explains the copies.

**Convergence.** Create a test-support context manager/fixture factory accepting
an explicit starting revision and yielding an engine plus migration helper.
Own schema creation, validated disposable naming, restricted search path,
timeouts, connection handoff, and exception-safe cleanup in one place. Leave
historical row seeding and migration assertions in their test modules.

**Verification and limits.** Run real PostgreSQL tests for setup failure,
migration failure, and successful cleanup. Never broaden teardown to an entire
database or disable immutable-history guards for convenience. Preserve separate
admin and scoped connections and exclusion of the application's tables. A
skipped database suite would not validate this extraction.

### F17 — Move repeated test builders and clients into explicit support modules

**Evidence.** Backend work factories recur in
[work-item tests](../backend/tests/test_work_items_postgres.py#L25),
[lease tests](../backend/tests/test_leases_postgres.py#L23),
[gate tests](../backend/tests/test_human_gates_postgres.py#L24), and
[duplicate tests](../backend/tests/test_duplicate_handling_postgres.py#L19).
Reuse also crosses test modules: [freshness migration tests](../backend/tests/test_repository_freshness_migration_postgres.py#L42)
(lines 42–49) import builders and lock helpers from several other test files;
[completion tests](../backend/tests/test_completion_evidence_postgres.py#L42)
(42–44) do the same.

Browser acceptance suites independently load `E2EState`, create authenticated
clients, and build work/checkpoint requests in
[duplicate handling](../frontend/tests/e2e/phase9-duplicate-handling.spec.ts#L125)
(125–175), [advisory suggestions](../frontend/tests/e2e/phase9-advisory.spec.ts#L35)
(35–81), and [freshness](../frontend/tests/e2e/phase10-repository-freshness.spec.ts#L14)
(14–28), among others.

**Risk.** Contract additions require editing many fixture copies; private imports
from phase-named tests make later suites depend on earlier test organization.
Different defaults and return shapes hide setup differences from test readers.

**Convergence.** Add small test-support modules for typed work/checkpoint/actor
builders and database lock coordination. Add Playwright fixtures for state and
an authenticated disposable-stack client with automatic disposal. Extend the
existing shared `surface.ts` and `database.ts` pattern. Keep scenario-specific
fields and assertions at the call site.

**Verification and limits.** Migrate two representative consumers first and run
their existing tests. Keep raw invalid payload construction available, stable
operation UUIDs where replay is tested, exact source provenance, and explicitly
versioned historical fixture shapes. Do not derive expected values from the
production serializer being tested or make setup silently repair invalid data.

## Recommended implementation sequence

1. **Establish evidence and close the smallest gaps.** Characterize F01's error
   locations and F03's query-clearing sequence, then implement their narrow
   extractions. F15 can be a separate small UI change. None depends on a broad
   contract-generation project.
2. **Consolidate client correctness boundaries.** Extract F04's leaf revision
   codecs, then F02's typed response validators and F05's registry selectors.
   Keep each change reviewable with a few migrated consumers and existing
   domain tests. F10 can follow with the gate cursor reducer first.
3. **Consolidate backend facts and mechanics.** Address F07–F09 with parity and
   query-bound tests. Extract F06 and F11 only with characterized acceptance and
   validation-order behavior. Do not combine query, authorization, and cursor
   behavior changes in one refactor.
4. **Reduce the next phase's maintenance cost.** Build on the existing schema
   snapshot/corpora for F12–F13, compose F14's description policies, and extract
   F16–F17's test support as those suites are touched. Prefer checked metadata
   and small generators over a shared runtime dependency across all packages.

A refactor is complete when its intended consumers use the common owner, their
local copies are removed, and tests preserve the meaningful differences. Moving
functions into a new file without migrating consumers does not resolve the debt.

## Differences to preserve and lower-confidence follow-ups

- **Database enforcement and service validation are independent defenses.**
  Preserve immutable-history triggers, deferred consistency checks, service
  errors, and client validation. Repeated enforcement across trust boundaries is
  not equivalent to duplicated presentation logic.
- **Historical migrations are versioned snapshots.** Do not deduplicate old
  migration SQL by importing mutable current application helpers. Refactor test
  harnesses and future construction patterns while preserving historical upgrade
  behavior and direct-SQL invariant coverage.
- **Readiness, display state, gate review, and merge review answer different
  questions.** Share their proven common facts without merging their authority
  or sensitivity. Evidence remains caller-asserted and scoped to its exact
  completion episode; it must not enter ordinary pointers or inferred truth.
- **Offset and cursor paging have different contracts.** Evidence history's
  pinned snapshot is stricter than an ordinary offset list. Neither a generic
  page model nor a common fetch hook should erase that difference.
- **Detail-session reset logic merits a subsequent focused review.**
  [Dashboard selection transitions](../frontend/components/dashboard.tsx#L882)
  (`openWork`, `openExactWork`, `clearSelection`, lines 882–984) repeat clusters of
  checkpoint/evidence/draft setters. A reducer could make reset-versus-preserve
  policy explicit. For example, `openWork` resets `checkpointKind`, while
  `openExactWork` does not; product intent must determine whether that is wrong.
  Do not unify resets until direct-ID navigation and draft-preservation behavior
  are characterized.
- **Older count-plus-page reads merit a concurrency check.**
  [Project listing](../backend/src/mnemonic_api/application/routes/projects.py#L35)
  (35–50) and [checkpoint listing](../backend/src/mnemonic_api/application/routes/history.py#L47)
  (47–74) use separate count/item queries, whereas newer composite readers use
  `begin_coherent_read`. Under the default READ COMMITTED isolation, concurrent
  writes could make the count and items reflect different snapshots. This was
  not reproduced and a stronger endpoint contract was not established. Verify
  expectations first; if needed, reuse the existing coherent-read helper.

## Validation performed and limits

The audit combined independent backend, frontend, and MCP/contract source reviews
with cross-checking of representative findings against the audited files and
existing tests. Two pure-function probes with synthetic errors confirmed F01's
backend field replacement and browser cursor-location omission. F03 is based on
the actual effect/cleanup control flow and remains unverified in a browser.

Only this Markdown report was added. No application, schema, migration, runtime
configuration, or plugin behavior changed. The full Python, PostgreSQL, frontend,
Playwright, and deployment suites were not run for this report; their references
above describe verification needed for future implementation, not tests claimed
to have passed. Report integrity checks cover source references and whitespace.
