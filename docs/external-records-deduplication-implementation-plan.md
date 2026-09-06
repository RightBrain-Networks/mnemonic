# External records and duplicate discovery — implementation plan

Status: planning only. Prepared 2026-09-05 against `origin/main` at `0ea0cc4`
in `work/external-records-plan-current`. All implementation, validation, and
release tasks below are future work. This document authorizes no application
code, migration execution, provider access, credential changes, or deployment.

The input is the local evaluation `.untracked/EXT_RECORDS_DEDUPE.md`, including
its owner decisions in §10. This document is self-contained: that untracked
evaluation is historical evidence, not a required runtime or implementation
dependency. Its September 3 incident measurements have not been rerun here.

Implementation amendments (2026-09-05): application release 0.9.0 landed on
`main` during implementation, so the coordinated application release is 0.10.0.
The plugin and migration reservations are unchanged. The original planning
scope below is preserved; implementation was subsequently requested by the owner.

Maximum-context rehearsal measured a
full SDK JSON-RPC result above 48 MiB because it includes both text and structured
copies. The original 12 MiB stdio result ceiling cannot carry this supported
context. The implementation raises only the general result ceiling to 64 MiB;
MCP request frames and permanent receipts remain 1 MiB, and the existing evidence
response proof remains 12 MiB. No fields or histories are truncated. See
[performance evidence](external-records-performance-and-recovery-evidence.md) and
[implementation validation](external-records-implementation-validation.md).

## 1. Outcome, scope, and baseline

Implement the evaluation's Branch A in dependency order:

1. **D1: structured external references.** Store a small ordered list on each
   work item; edit it through existing work creation/update operations; expose
   it before a worker selects or claims work; support an exact inverse URL
   lookup; record its authorship and changes in the existing event ledger.
2. **D2: caller-supplied external comparison.** Extend the existing explicit
   duplicate suggestion safe read to compare a bounded set of external record
   texts. Return a separately ranked external result list. Caller credentials
   and provider reads remain on the caller's side.
3. Update the dashboard, MCP contract, three existing plugin skills and shared
   references, examples, operator documentation, and validation together.

An external record is a link target. It has no Mnemonic revision, checkpoint,
lease, or lifecycle. Do not fabricate any of those or pass it to `merge_work`.
An actor may separately retire work as Promoted or Won't do using the existing
closeout contract, including the mandatory job completion report and operation
UUID. A link alone never makes that decision.

The observed failure this addresses is **Path A**: filing was recorded in event
prose that ready-work discovery did not show. D1 makes the reference visible on
the discovery row even if the summary remains stale. D2 supports a fresh
comparison after parking as well as before creation. **Path B**, a worker who
never consults Mnemonic, remains outside the reach of these changes. An exact
lookup gives such a consumer an inexpensive check if it chooses to use it;
it does not provide cross-system mutual exclusion.

### 1.1 Current and proposed release coordinates

| Coordinate | Inspected baseline | Proposed implementation release |
| --- | --- | --- |
| Application, API, MCP, dashboard | `0.8.0` | `0.10.0` |
| Claude plugin | `0.11.0` | `0.12.0` |
| Alembic head | `0021_job_completion_reports` | `0022_external_references` |
| MCP tools / receipt-protected MCP writes | 32 / 11 | 32 / 11 |
| REST receipt kinds / protected browser mutations | 15 / 13 | 15 / 13 |
| Work-event types / plugin skills | 17 / 3 | 17 / 3 |

These are reservations, not version edits in this planning session. Recheck
remote `main` before implementation and renumber if another release lands.
D1 and D2 are implementation milestones within one coordinated release;
shipping either separately requires revisiting this table and its release gates.

The evaluation's proposed `0019`, `0.6.0`, plugin `0.10.0`, and 27-tool catalog
are obsolete. Phase 11 already shipped immutable completion evidence; Phase 12
already shipped activity and reports. Reuse their current boundaries, without
changing their meaning or modifying old migrations.

### 1.2 Owner decisions carried forward

- No third-party credential in Mnemonic; no server provider fetch, webhook,
  background refresher, external index, or credential configuration in D1/D2.
  D3 is deferred, with revisit criteria in §13.
- References are available to every project. No new project setting or switch.
  Automatic candidate gathering in the plugin occurs only during an explicit
  compare step, with a repository URL and existing provider access.
- Keep park-then-file. Use `request_human_input` for a proposal that awaits the
  owner's decision. Attach the reference after the external record exists.
- Preserve production content and permanent receipts through migration. Do not
  infer references from `#123`, summaries, events, tags, completion artifacts,
  or reports. Existing rows start with an empty reference list.
- The application is prerelease. Change the canonical schema and ship aligned
  clients; do not create dual execution paths, redirects, field aliases, or old
  API adapters. Reading preserved history and replaying permanent receipts are
  existing integrity obligations, not an old-client execution mode.

### 1.3 Explicit scope decisions resolving the evaluation's options

| Question | Decision for this implementation |
| --- | --- |
| JSONB list or separate record entity? | One bounded JSONB list on `work_items`; no new entity or write operation. |
| Full or compact reference pointer? | The same full reference shape on every included pointer. Observation time must remain visible with state. Bound list size rather than inventing a second wire shape. |
| URL identity normalization? | Exact accepted URL spelling. No provider-specific equivalence, fetch, redirect resolution, or implicit normalization. |
| Empty PATCH vs omission? | Different operations: explicit `[]` clears; omission preserves. They must have different fingerprints. |
| Optional gate-resolution URL? | Deferred. Resolve the existing gate and then use versioned `update_work`; document the non-atomic interval. |
| Optional `work_item_id` draft source? | Deferred. Document the existing text plus `exclude_work_item_id` call. |
| Mutable reference vs completion artifact? | Separate claims and storage. Share only validation primitives where semantics actually match. |
| Exact URL filtering in hierarchy roots? | Not supported in this increment; reject `external_url` with `view=roots`. Flat full view provides exact inverse lookup. Hierarchy rows still display their own references. |
| Dashboard candidate acquisition? | Manual candidate fields in the explicit compare panel; no provider integration or raw JSON upload requirement. |

## 2. Domain invariants and reference contract

### 2.1 Reference shape

```json
{
  "url": "https://github.com/example/project/issues/2188",
  "kind": "tracked-by",
  "label": "project#2188",
  "state": "closed",
  "state_observed_at": "2026-09-05T14:20:00Z"
}
```

| Field | Contract |
| --- | --- |
| `url` | Required strict string, 1–2,000 ASCII bytes, valid absolute HTTP or HTTPS URL with a host and valid port. |
| `kind` | Required literal `tracked-by` or `references`. |
| `label` | Optional, omitted when absent; if present, a nonblank single-line plain-text string, at most 120 Unicode scalar values and 480 UTF-8 bytes. Explicit null is invalid. |
| `state` | Required literal `open`, `closed`, `merged`, or `unknown`. Caller-asserted observation only. |
| `state_observed_at` | Optional timezone-aware RFC 3339 timestamp; omit when absent, reject explicit null or a timezone-less timestamp. Normalize to UTC `Z` on the wire and in storage. |

Reject unknown fields, coercion of scalar types, invalid Unicode, NUL, control
characters, and explicit bidi formatting controls in display labels. Render
ordinary international text with bidi isolation. URL validation additionally
rejects credentials, whitespace, backslashes, malformed percent escapes, and
characters outside the accepted URI grammar. Use a shared validation fixture
table to pin its grammar across backend, SQL, MCP, and browser. Do not simply
reuse a browser URL parser that silently repairs the input.

URLs retain accepted spelling, including path case, port spelling, query, and
fragment. Query-based trackers are allowed. Scheme/host case variants, trailing
slashes, fragments, and different query strings are different stored identities.
The caller should use a stable provider permalink. An inverse lookup must send
that exact spelling using proper query-parameter encoding. No fragment stripping,
tracking-parameter stripping, hostname lowercasing, or issue/PR equivalence is
performed. Links must not contain credentials or signed access tokens; extend
the existing known-secret protections to durable reference strings (§4.3).

An item has zero to ten references. Reject duplicate exact URLs within a list,
including the same URL with different kinds or states; changing that URL's kind
is an edit of its entry. The same URL may occur on many work items. Many
`tracked-by` entries per item are valid, though one primary tracker is preferred.
List order is authored presentation order and is preserved. Do not sort,
coalesce, or silently deduplicate it. A reordered list is a different update.

Bound the canonical serialized UTF-8 list to 32,768 bytes as well as the field
and count limits. Pin canonical JSON as UTF-8, non-ASCII preserved, no NaN,
sorted object keys, compact separators; list order remains unchanged. JSONB's
printed representation has different whitespace, so SQL storage/event bounds
must explicitly account for that difference rather than equating byte counts.

### 2.2 Meaning and timestamps

`tracked-by` asserts that the external record tracks this objective.
`references` asserts supporting context only. Neither asserts that another
worker is implementing it, that a proposed duplicate is confirmed, or that any
work is complete. An issue and a PR are distinct URLs; both may be retained.

A known state without an observation timestamp is accepted and displayed as
“observation time unknown.” Unknown state with a timestamp means a caller
observed the record but could not map its state. Do not substitute the server's
write time for a missing observation time. Timestamp validation is structural,
not dependent on the current clock, so a permanent retry does not change validity.
Render future observations as an absolute caller-supplied time rather than a
misleading “just now.” No stale hint suppresses readiness or authorizes a closeout.

### 2.3 Ownership, aliases, and completion history

References belong only to the exact work row that stores them. The existing
blanket duplicate-alias mutation guard protects the new column automatically.
Editing a frozen alias fails with the existing duplicate error; it is never
redirected to its canonical root. Merging freezes the source list and leaves
the destination list as authored. There is no union, inheritance, or silent
copy of references. A separately authorized destination edit uses its current
version and its own operation UUID.

Soft deletion preserves references with the rest of the exact row. Normal
discovery excludes deleted rows under existing rules. Mutable references do
not modify sealed artifact references, completion checkpoints, reports,
follow-up provenance, or historical event bodies. The same URL may appear in
both mutable tracking context and immutable completion evidence if the actor
explicitly authored both claims.

## 3. Persistence, migration, and event ledger

### 3.1 Schema changes

Create `backend/alembic/versions/0022_external_references.py`, with
`down_revision = "0021_job_completion_reports"`, and update ORM metadata:

- `work_items.external_references JSONB NOT NULL DEFAULT '[]'::jsonb`.
- A schema-qualified immutable validator and CHECK enforcing array type,
  cardinality, exact permitted keys, required fields, string/enumeration/URL
  rules, unique URLs, canonical optional-field/timestamp representation, and
  the declared list-byte bound. Invalid JSON shapes must return false without
  accidentally throwing on a scalar passed to an array/object operation.
- A GIN index with `jsonb_path_ops` for containment, preferably partial on
  `deleted_at IS NULL` to match normal lookup. Use the existing project index
  alongside it. Verify usefulness with realistic data; the index does not by
  itself make the current canonical-aware route a single cheap SQL statement.
- Assign a fresh JSON-compatible list when replacing it. Do not rely on in-place
  mutation tracking of nested ORM dictionaries, and do not retain a mutable
  reference to the list used for the event's `before` snapshot.

The constant default initializes existing live, terminal, deleted, and alias
rows to empty without a semantic data backfill. Do not UPDATE each historical
row to manufacture timestamps, versions, events, or activity. Retain every
existing user column and every receipt byte. Use bounded migration lock and
statement timeouts and a maintenance window; an ordinary transactional index
build is sufficient for this release if restore rehearsal validates its cost.

### 3.2 Event shapes and required database work

Creation with nonempty references records them in `work_created.metadata.initial`
alongside the existing identity snapshot. Empty references omit that optional
snapshot field. Historical creation events remain exactly as stored.

A replacement records `metadata.changes.external_references` as full ordered
`before` and `after` arrays. Empty arrays MUST remain explicit inside a diff:
an empty `after` is the evidence of clearing. Extend backend and MCP
`WorkSnapshot`, `WorkChangeSet`, and an explicit `ExternalReferencesChange`
model, plus browser event guards and rendering. Do not treat an array diff as
one of the existing scalar-change models.

The current SQL ledger rejects extra creation keys, fifth change keys, and
metadata over 16,384 bytes. Adding an application field alone will therefore
fail at insertion. Migration work must include:

1. Replace the relevant active metadata validation body in place with direct
   validation of the expanded canonical shapes and the already-preserved
   historical shapes. The current normal-event chain is
   `mnemonic_work_event_metadata_v2_is_valid` →
   `mnemonic_work_event_metadata_v1_is_valid`; do not add a wrapper that strips
   references or supplies fake changes to obtain acceptance from an old shape.
   Preserve all existing gates, merges, origins, statuses, and provenance guards.
2. Replace `ck_work_events_metadata_envelope_valid` and matching validator/ORM
   bounds so `work_created`, `work_updated`, `work_status_changed`, and
   `work_reopened` allow at most 131,072 bytes of `metadata::text`. Every other
   event, including caller-authored progress, retains 16,384 bytes. Reopening
   belongs here because the same PATCH can replace references while reopening.
3. Extend the live creation source-fact check in the existing event insertion
   guard to compare the optional snapshot list with the row list, treating
   absence as empty. A supplied nonempty snapshot must match exactly.
4. Verify direct SQL rejects malformed reference arrays/diffs, incorrect
   creation snapshots, alias writes, and violations of existing closeout
   constraints. Preserve the existing enforcement boundary: the transactional
   mutation service captures truthful before/after update values; the database
   validates their shape but does not independently authenticate an ordinary
   update diff against an actual row transition. Do not claim that a CHECK
   proves either that transition's history or the external observation true.
   Transition-authenticated update events would be separate ledger work and
   are not added here.

The higher cap is deliberate: two maximum reference arrays plus escaped
title/summary diffs and JSONB formatting can exceed 16 KiB and even 64 KiB.
Calculate and test the maximum accepted payload with all editable fields,
multibyte labels, and escaped text. Keep this bounded system-event extension
consistent in SQL, Python, MCP, browser, and response-size checks.

Specifically, `frontend/lib/work-events.ts:validEventMetadata` currently uses
the generic 16 KiB helper for every event. Pass the conditional event-specific
limit there. MCP's generic 16 KiB helpers apply to progress/checkpoint metadata;
keep those limits and add the appropriate explicit bound to typed system-event
validation. Do not globally enlarge an unrelated metadata helper.

No new event or activity kind is added. Existing `work_events` insertion feeds
Phase 12 activity; a changed reference produces the ordinary update activity
pointer. Do not add a second activity write or copy URLs into activity rows or
WebSocket invalidations. An identical-list replacement retains current PATCH
behavior: it increments version and records the requested before/after values
in an event. It is not collapsed into a read or silently discarded.

### 3.3 Preservation and downgrade policy

Rehearse fresh migration and upgrade of a populated `0021` backup containing
aliases, soft-deleted work, gates, receipts, completion evidence, reports,
reviews, follow-ups, and activity. Compare old row values, counts, content
digests, relationships, receipts, and sequences before/after; only the new
default column and intentional schema objects may differ.

A production downgrade must never drop populated references or leave new
event shapes unreadable. Prefer forward repair. The migration's downgrade
must fail closed if any row has nonempty references, any event contains the
new shape, or any permanent receipt contains a populated new response field.
Only an empty-feature disposable database may remove the new objects and
restore exact predecessor validators/bounds. Test both the allowed empty case
and refusal after a list was created and later cleared; history still matters.
Disaster recovery restores the verified backup as a whole under the existing
activity stream rotation procedure; it is not an in-place lossy downgrade.

## 4. Creation, updates, and permanent receipt semantics

### 4.1 Request behavior

| Input | `create_work` | `update_work` |
| --- | --- | --- |
| Field omitted | Create with no references; omit from canonical fingerprint. | Leave existing list untouched; omit from canonical fingerprint. |
| Explicit `[]` | Same value and fingerprint as omission. | Clear the list; MUST remain in fingerprint and domain payload. |
| Nonempty list | Store as authored after validation. | Replace the complete list under `expected_version`. |
| Explicit null | Validation error. | Validation error. |

Use an omission-only sentinel/default for PATCH, following the current
omission-only report pattern, with serialization excluding only the sentinel.
Do NOT use `exclude_if=lambda value: not value` on a PATCH list. The source
evaluation's blanket affected-path precedent would erase a clear during
`model_dump(exclude_unset=True)` and conflate different receipt intents.

Create defaults and read models may sparsely omit empty lists; that lets old
stored response bodies round-trip without fabricated properties. All included
read pointer models use the same omission rule. An empty reference list on a
new response is represented by an absent property, not null or `[]`.

`services/work_items.py` must explicitly pass the create list to `WorkItem`,
include a deep JSON snapshot in the update `before` map, and assign the
validated replacement list. Expand `stage_work_changed` types beyond scalar
values while keeping ordinary field comparison and event selection intact.

Reference-only edits keep the current lease policy: a lease is not required
for an identity edit; if a token is supplied, it is validated. An incorrect
token, stale version, frozen alias, deleted row, wrong project, or forbidden
lifecycle transition fails under the existing guards. References neither
acquire nor release a claim and do not change readiness.

A replacement list is atomic, not an add/remove patch. On definitive version
conflict, reread the exact item, reconcile the intended change with the current
list, and submit the revised full replacement as a new operation. Never
blindly retry an old full list with a newer version. State/label edits and list
reordering count as identity edits.

### 4.2 Receipt matching and closeouts

Extend `services/client_operations.py` at the existing canonical payload and
create/update response-correspondence boundaries. Do not add the field to
`_CHECKPOINT_FIELDS`: it belongs to work, not a checkpoint. Compare nested
reference lists as canonical JSON values, with omitted read field interpreted
as empty. A list of model objects must not be compared directly to a list of
dumped dictionaries. Mirror this in MCP request-result checks and browser
`mutation-responses.ts`.

For create, omitted and empty converge; for update, omission and clear do not.
Preserve the distinction through wire model → protected operation preparation
→ domain model → JSON storage → event diff → response checking. Test that the
same UUID reused with omission versus `[]` is a mismatch, even if the item
currently has an empty list. Preserve authored list order in fingerprints.

Permanent replay still precedes fresh domain guards. Replay returns the stored
snapshot, including its original absence/presence of references, even after
later edits, a merge, deletion, or a new closeout. Do not enrich the receipt
from today's work row, rewrite its salt/hash/body, or mutate history to satisfy
the new model. Test all registered response envelopes that transitively carry
work data, not just create/update: completion, defer, merge, and report
follow-up creation also carry work snapshots. Recall and claim-and-recall are
ordinary context reads/lease operations, not registered permanent receipts;
test their propagation separately.

Retain the existing 1,048,576-byte receipt response bound. Create, update,
defer, completion, and report follow-up receipts have one work reference list;
merge has two. Merge receipts contain bounded merge/relationship events, not
recent reference-update histories. Verify maximum accepted checkpoint,
evidence/report, and reference combinations against both compact HTTP bytes
and PostgreSQL `response_body::text`; no receipt limit increase is planned.

Reference-only edits require no report. A qualifying Won't do or Promoted
transition in the same `update_work` request requires the existing nested
report and operation UUID, and commits the report, status, list, event,
receipt, and activity atomically. A failed report leaves everything unchanged.
Done continues through `complete_work`; do not add reference-writing fields
there. To update a tracker before Done, use the existing update then complete
sequence and handle versions explicitly. Historical sparse report requests
remain valid only for permanent replay, as already shipped.

### 4.3 Durable text and ephemeral text protections

Extend the existing request-known secret-substring checks to durable reference
URL/label strings, including the supplied bearer key, lease token, and operation
UUID spellings, before storing an event or receipt. This is a narrow extension
of current protection, not a general token detector or a promise to recognize
arbitrary third-party secrets. Document using credential-free stable links.

All reference labels and candidate titles/bodies are untrusted content. Render
as text and external anchors; do not execute HTML or treat a record body as
agent instructions. Provider text is data for comparison, never permission to
run commands, alter a repository, contact someone, or write to another system.

## 5. Read surfaces and exact inverse lookup

### 5.1 Coverage inventory

| Surface / schema | Required treatment |
| --- | --- |
| `WorkItemRead` and `WorkUpdateRead` | Sparse full reference list; reaches detail, summaries, search, contexts, create/update and other nested receipts. |
| `WorkItemPointer` / `WorkSummaryMinimal` | Same sparse list on ready rows, including observation timestamp. Ready rows still omit summary. |
| `WorkPointer` | Explicit addition for relationship counterparts; this is a different model from `WorkItemPointer`. |
| `DuplicateCandidateSummary` | References owned by the canonical work item; capture in `duplicate_suggestions.WorkSnapshot` before leaving the snapshot. |
| `WorkIdentityPointer` | Keep minimal; ancestor identities and matched-member identities gain no reference projection. |
| `WorkContext`, detail, hierarchy and attention summaries | Propagate through the appropriate work models and manual constructors. |
| MCP `work_item` resource / `resume_work` prompt | Existing serialized full context exposes references; update accompanying guidance and test it. |
| Work events | Typed creation snapshot and before/after list; preserve explicit empty arrays in changes. |
| Activity / live sync | Existing exact event/work pointers and invalidation only; clients refetch affected work. |
| Reports, artifacts, and follow-up provenance | Preserve immutable content; no reference hydration into historical claims. |

Audit manual constructors in `services/work_context.py`, `services/readiness.py`,
`services/relationships.py`, hierarchy helpers, and duplicate snapshot/ranking
code; adding a Pydantic field does not make a hand-built pointer populate it.
Use the existing coherent read transaction for composite reads and snapshot
the list by value before ranking outside it. No per-reference joins or fetches.

Measure high-fanout context responses separately from receipts: context can
include up to 100 counterparts in each of three directions and 20 recent
events. At the conservative list/event bounds this permits roughly 9.375 MiB
of counterpart references plus 2.5 MiB of event metadata, before existing
checkpoint text. The current context path has no corresponding 1 MiB response
cap. Record actual serialized sizes, database time, decoding/rendering time,
and ordinary-client usability with representative and maximum fixtures before
release. Do not introduce silent truncation, reference projection, or a global
1 MiB cap as a presumed consequence of the receipt limit. If measured supported
clients cannot consume the chosen bounds, revise this contract before shipping.

Reference content is not added to work full-text vectors, duplicate embedding
composition, or checkpoint search. Exact URL lookup is explicit. Updating a
reference can still change `updated_at` and therefore existing tie ordering;
the guarantee is unchanged ranking algorithms, not identical ordering across
different work revisions.

### 5.2 `search_work(external_url=...)`

Add an optional exact URL filter to `WorkItemListQuery`, the REST query, MCP
`search_work` signature, and browser proxy query allowlist. Use the same URL
validation as storage. The indexed predicate is JSONB containment against the
actual owning row: `external_references @> [{"url": supplied_url}]`, with a
bound SQL parameter and existing project/deleted predicates.

Both kinds match. The returned reference supplies the distinction; this release
does not add an `external_kind` parameter. Combine the filter with existing
status, provenance, text, and semantic filters using AND, before result count
and pagination. It is not an alternative lexical match and contributes no
ranking score. Preserve existing text/group matching after restricting which
returned work rows own the URL.

- `duplicate_scope=canonical`: only canonical rows that themselves own the URL.
- `duplicate_scope=aliases`: only frozen aliases that themselves own it.
- `duplicate_scope=all`: each matching owning row under the current audit scope.
- An alias-only URL never satisfies its root's ownership predicate. The caller
  can inspect that alias's canonical identity and then explicitly read the root.
- `view=roots` with `external_url` is a validation error. Ready-work and child
  queries do not gain this filter; their rows still expose references.

The documented inverse lookup uses `view=full`, `status=all`,
`duplicate_scope=all`, no text query, and paginates all matches. Existing defaults
(`status=pending`, canonical only) are intentionally narrower and must not be
used to claim that a record has no matching Mnemonic work. Results may be many;
never pick one automatically. Use its readiness/lease context and explicit
canonical follow-up reads where needed before acting.

Verify the index with `EXPLAIN (ANALYZE, BUFFERS)` on a disposable representative
population. Report total route cost as well as the indexed predicate: current
search also captures visible rows and canonical projections. Do not promise
constant-time or single-query inverse lookup based only on the GIN index.

## 6. D2 request, response, and ranking contract

### 6.1 Caller-supplied population

Extend `DuplicateSuggestionRequest` and the existing tool/POST with optional
`external_candidates`. Each entry has exactly:

```json
{
  "url": "https://gitlab.com/example/project/-/issues/12",
  "title": "Show tracker links before selecting work",
  "body": "Expose the external tracker on the ready-work row.",
  "state": "open"
}
```

- Zero to 64 entries; unique exact URLs using §2's identity and URL grammar.
  Duplicate URLs are a validation error, not server-side deduplication.
- Required nonblank plain-text `title`, at most 500 Unicode scalar values;
  preserve accepted spelling. Required `body`, at most 20,000 scalar values;
  empty is valid. Reject invalid Unicode/NUL and invalid scalar types. Bodies
  may contain ordinary multiline prose; they remain untrusted data.
- Required `state`, with the same four literals as a stored reference. No
  label, kind, observation timestamp, provider enum, repository override,
  credential, work ID, lease, or operation UUID is accepted in an entry.
- Entire request remains subject to the existing 2,097,152-byte raw body cap,
  including draft, candidates, and JSON escaping. Individual maxima do not
  guarantee that 64 maximum Unicode bodies fit. Reject oversize with the
  existing body-size error; the server never silently truncates input.
- MCP has a separate stricter transport limit: 1,048,576 bytes for the entire
  serialized JSON-RPC request/frame, including tool name, IDs, arguments, and
  envelope/escaping overhead, for both HTTP and stdio. Preserve this cap.
  REST/browser-valid requests above it are not necessarily MCP-transportable.
  Client gathering must fit its actual transport, not just candidate fields.
- Omitted and explicit empty candidate lists both mean no external comparison;
  omit all external response fields. Explicit null and unknown fields fail
  normal validation. A supplied nonempty list exists only within that request:
  no external row, event, activity, receipt, or embedding cache is persisted.

The API accepts explicit candidates for any project, including one without a
repository URL. That URL gates the plugin's automatic gathering policy, not
storage or manual API comparison. A record may be from another repository:
it is caller-supplied text, not a server credential-routing decision. Normal
project existence/auth checks apply before returning external results.

### 6.2 Response extension

Keep existing `items`, `mode`, `semantic_available`, `semantic_scope`,
`composition_version`, and internal exact-group counters unchanged in meaning.
For a nonempty candidate request, add these three fields together:

```json
{
  "external_items": [
    {
      "rank": 1,
      "signals": ["exact_title", "lexical", "semantic"],
      "reference": {
        "url": "https://gitlab.com/example/project/-/issues/12",
        "title": "Show tracker links before selecting work",
        "state": "open"
      }
    }
  ],
  "external_candidate_count": 12,
  "external_scope": "hybrid"
}
```

This illustrates the extension, not a complete internal page. Define strict
external candidate/suggestion models with names distinct from persisted
references: the suggestion's URL/title/state shape is not the stored
URL/kind/label/state/time shape. Linking requires a separate decision.

`external_candidate_count` counts valid supplied records, 1–64, not provider
search results or successfully completed inference. The UI says “N supplied
records; [scope].” On unavailable, do not claim successful comparison.

| Scope | Meaning |
| --- | --- |
| `hybrid` | Exact/lexical stages completed and valid semantic vectors covered every supplied external record. |
| `lexical` | Exact/lexical stages completed; no external semantic evidence is used, including busy inference, semantic failure, or semantic subdeadline. |
| `unavailable` | The exact/lexical baseline could not complete within its bounded execution or failed; `external_items` is explicitly empty. |

The external list has at most the existing `limit` (1–10), independently of
the internal limit. Ranks are contiguous from one; URLs are unique. Every
URL/title/state triple equals one submitted candidate after declared
validation. Never echo bodies. Signals are unique, nonempty, and ordered
`exact_title`, `lexical`, `semantic`; lexical/unavailable scope cannot carry
semantic signals. Exact matches form the prefix and reserve slots first.
If more exact candidates exist than `limit`, deterministic ordering selects
the prefix. This is a bounded list, not exhaustive matching; request-aware
clients can identify exact-title truncation from their supplied titles.

MCP, browser, and backend request-bound construction validate all-or-none
extension presence, candidate identity, count, ranks, signals, exact prefix,
and scope. Reject unsolicited external data and forged candidates. Keep the
existing internal response validators strict.

Without candidates, the response is byte-identical to D1 for identical stored
data, snapshot, and inference outcome. D1 itself adds populated references.
With candidates, internal ranking, signals, counters, and serialized fields
are retained independently; optional external failure must not rebuild the
internal result in lexical mode. This does not promise identical timing or
inference availability between concurrent requests sharing one process.

### 6.3 Comparison stages

Keep internal canonical grouping, cache, composition version, and tie rules.
Add a focused `services/external_duplicate_suggestions.py`, orchestrated from
the existing suggestion route/service. Reuse narrow mathematical/text helpers;
do not build a generalized provider framework or external pseudo-work items.

1. **Exact title:** invoke the shipped PostgreSQL
   `mnemonic_duplicate_title_key_v1` on draft and candidate titles over bound
   `VALUES`/array input. Its actual SQL collation/whitespace behavior, not the
   evaluation's abbreviated description, defines equality. Include existing
   Unicode/title-key regression fixtures.
2. **Lexical:** reuse the shipped OR-of-normalized-lexemes construction for
   `_draft_text`: English `to_tsvector`, `tsvector_to_array`, quote each lexeme,
   join deterministically with ` | `, and cast to `tsquery`. Do not replace it
   with `plainto_tsquery`'s all-term AND requirement. An empty lexeme set yields
   no lexical matches while exact-title reservation remains available.
   Candidate title is A, first 1,500 body characters C; use `ts_rank_cd`
   normalization 32 and positive matches only. URLs, state, kind, and labels
   contribute no rank evidence. Pin partial-title overlap with disjoint
   summary/prompt/tags under unavailable inference as an external regression.
3. **Semantic:** reuse the same valid draft query vector and current local
   BGE embedder. Candidate document is `title + newline + body[:1500]`.
   Embed all supplied candidates in batches of at most the current 16, without
   caching. At most 64 external document vectors are additional to the
   unchanged internal limit of 128 fresh vectors. Validate cardinality,
   dimensions, finite numbers, and usable nonzero norms. A bad external batch
   discards that semantic stage as a whole.
4. **Fusion:** reserve exact candidates first, sorted by exact URL. Exclude
   them before numbering lexical/semantic nonexact fusion ranks. Use current
   RRF `k=60`, lexical weight 3.0, semantic weight 1.0. Break lexical, cosine,
   and fused ties by accepted ASCII URL ascending, independent of request
   order. Return the exact prefix then fused nonexact candidates up to limit.

Lexical fallback retains exact and positive lexical matches without padding.
Hybrid comparison follows the existing threshold-free advisory discipline:
a no-twin draft can still return a full list. External and internal ranks are
not globally comparable. Signals are categorical evidence, never confidence.

The source measured external text used as a draft against internal work,
not the proposed external-candidate direction. That is directional evidence,
not proof of symmetric ranking. Test the actual direction with representative
twins, related-but-different records, and no-twin controls. Never derive an
automatic linking threshold from three successful examples.

## 7. Resource ownership and failure isolation

### 7.1 Existing controls

`application/suggestion_resources.py` owns request/shared inference permits,
bounded body pre-parsing, and response buffering under one 60-second deadline.
Defaults are four request slots, one shared inference slot, and a 50 ms
inference wait. Ordinary semantic search shares inference. Keep safe-read
annotations, no-store, authentication, duplicate-JSON-key rejection, and
current busy/timeout errors and retention for still-running timed-out work.

The current synchronous route cannot guarantee optional fallback with only
`try/except`: native inference can outlive the whole request, causing the
outer middleware to discard a completed internal page. D2 therefore includes
a small explicit orchestration change, not just another comparator loop.

### 7.2 Staged execution contract

1. Compute the existing internal page first, including coherent snapshot,
   inference/fallback, and disposable cache handling. Retain its completed
   page and reusable valid query vector as detached values. Existing fatal
   project/graph/resource errors remain errors, never external-only success.
2. Reserve one second of the remaining global deadline for serialization/send.
   External work gets at most five seconds total, clipped to the remaining
   deadline minus that margin. Use named internal constants and deterministic
   clock tests, without new public settings. No remaining budget means an
   immediately unavailable external result.
3. Compute exact/lexical results in a separately owned short read-only session
   with SQL and pool checkout deadlines within that external budget. Inputs
   are request constants and need no original work snapshot transaction.
   This intentionally differs from the evaluation's same-transaction sketch;
   an external SQL error cannot poison the completed internal transaction.
   Close/rollback the external session before inference.
4. Retain the completed external lexical baseline before semantic work.
   Without the already-acquired inference permission or query vector, return
   lexical immediately. Do not reacquire the gate or load a second model.
   Check deadlines before/after each batch. Error or semantic expiry returns
   the lexical baseline; a failed baseline returns unavailable.
5. Use asynchronous route orchestration where needed and bounded workers for
   synchronous SQL/model execution. Each worker owns its session; never share
   a SQLAlchemy session concurrently. Register actual worker completion with
   the request resource owner, await only the budget, and discard late results.
6. Extend the existing retention/finalization mechanism with explicit owned-work
   completion handles. A fallback response can finish while an uninterruptible
   worker runs, but request/inference permits remain held until it actually
   exits. Route completion alone must not release them. Do not launch untracked
   background tasks or an unbounded executor. Release exactly once across
   success, exception, timeout, and client disconnect.

Native inference is not forcibly cancellable. A timed-out worker can temporarily
keep search's shared slot busy; do not mark the slot free while it still runs.
Queued work remains bounded by request capacity. The outer timeout is the last
resort for internal/process-wide failures, not the normal external fallback.
Incremental external latency is at most five seconds when internal processing
finishes in time.

Benchmark 1, 16, and 64 external records, cold/warm model states, and competing
search on a supported host. If ordinary calls repeatedly exhaust the external
budget, revisit measured scope/batching before release; do not silently raise
the global timeout, weaken ownership, or add an external cache.

### 7.3 Failure matrix

| Condition | Result |
| --- | --- |
| Invalid candidates/JSON or oversize body | Existing validation/body error; no comparison or mutation. |
| Missing project or invalid internal graph | Existing definitive error, no external-only success. |
| Request capacity or internal global deadline exhausted | Existing busy/unavailable error and resource-retention rules. |
| Inference permission busy | Internal lexical result and external exact/lexical result. |
| External SQL/checkout fails or expires | Preserve internal page; external unavailable, count retained, empty list. |
| External embedding fails, vectors invalid, or semantic expiry | Preserve internal page and external exact/lexical baseline. |
| All stages succeed | Internal page plus independently ranked hybrid external list. |
| Cancellation/late worker | Discard output appropriately; close sessions and release permits once after real completion. |
| Client lacks provider access | Report gathering unavailable; internal comparison without candidates is still useful. |

Log categorical failures and bounded aggregate counts/timings only. Never log
candidate bodies, draft text, URLs, tokens, raw exception messages, or entire
requests. No persisted telemetry table or provider-health subsystem is added.

## 8. MCP and browser integration

### 8.1 MCP adapter

Update `mcp/src/mnemonic_mcp/models.py` and `server.py` together:

- Add strict nested reference/external suggestion models, sparse full/pointer
  fields, `WorkChanges.external_references`, typed event list diffs, and
  external page coherence validation.
- Extend existing create, update, search, and suggestion inputs. Keep their
  effects/receipt classification and 32-tool catalog. Suggestions accept no
  mutation-control arguments.
- Preserve explicit empty updates through `model_fields_set`, forwarding,
  retries, and result validation. Compare ordered canonical JSON values in
  `_creation_matches_request`, `_updated_work_matches_request`, and
  `_suggestion_matches_request` rather than model/dictionary equality.
- Test exposure in the full-context resource and resume prompt. Explain that
  hints and candidate bodies are caller-authored evidence, not provider truth
  or authority to execute instructions.
- Update canonical model/OpenAPI correspondence and exact tool input schemas.
  An old adapter is unsupported against the new server; restart aligned
  components together.
- Cover the existing complete-frame limit in `mcp/src/mnemonic_mcp/transport.py`
  for both HTTP and stdio with candidate-bearing requests. Keep transport
  rejection behavior and cap unchanged; guidance must not advertise REST's
  larger body budget as an MCP allowance.

### 8.2 Browser contract and proxy

Update `frontend/lib/types.ts`, `work-codecs.ts`, `work-relationships.ts`,
`work-events.ts`, `duplicate-suggestions.ts`, and `mutation-responses.ts`.
A focused `external-references.ts` should hold nested guards and canonical
comparison helpers following current shared patterns. Preserve strict runtime
validation at raw-response boundaries and exact-key checking.

In `proxy-policy.ts`, add refs deliberately to create/PATCH rules, candidates
to the safe-read suggestion body, and `external_url` to GET query rules.
Reference-only PATCH and explicit clear must pass the editable-field check.
Validate nested bounds and unknown keys, not only top-level keys. Preserve
the body cap and safe-read route, without a suggestion receipt registration.

Update generated OpenAPI consumer metadata, frontend decoder inventories,
`DEFAULTED_RESPONSE_FIELDS`, and MCP schema mapping fixtures for each new
optional field/model. Do not weaken guards to accommodate the additions.

### 8.3 D1 dashboard behavior

Extend `work-item-editor.tsx`, `dashboard.tsx`, and the existing mutation intent
flow to edit ordered reference rows on create and existing-item edit. Fields
are URL, kind, optional label, state, and optional observation time. Support
add/edit/reorder/remove within bounds. Removing the final entry sends `[]`;
leaving references untouched omits the update field. Reference edits on
terminal canonical work remain ordinary identity edits.

Freeze the submitted list in the mutation registry. Unknown outcomes retry
the exact intent/UUID, not current editor contents or a regenerated timestamp.
A definitive conflict shows current and attempted references and calls for
an explicitly reconciled resubmission. Do not auto-union lists.

Use one reference presentation across:

- `work-queue-card.tsx`, which renders flat queue and hierarchy rows;
  `work-item-card.tsx` currently provides shared badges/helpers.
- `work-detail-pane.tsx`, relationship counterparts, and attention summaries.
- Canonical suggestions in `duplicate-suggestion-panel.tsx`.
- Event detail: humanize “External references” and make complete before/after
  arrays inspectable without raw `[object Object]` output.

Show “Tracked by” versus “Reference,” label or URL fallback, visible host,
and observed state/time. A known closed/merged hint must be visible on a queue
row before selection even when the summary disagrees. Compact presentation
may collapse extra links, but must expose tracker presence and terminal hints.
Full URLs and absolute times need an accessible expansion, not hover alone.

Use validated outgoing anchors, `rel="noopener noreferrer"` for new tabs,
escaped text and bidi isolation. Link clicks/keyboard activation must not
trigger the parent card's selection or claim. No HTML rendering, automatic
previews, image fetches, or provider reads. Verify small screens, light/dark
themes, keyboard focus, and screen-reader labels. Capture screenshots for the
eventual implementation PR.

### 8.4 D2 dashboard behavior

Add optional manual “External records” rows to the current create-draft
compare panel: URL/title/body/state, up to 64, with visible field limits and
aggregate request-size feedback. This is supplied context, not a promise to
gather repository records. Typing/pasting/loading a project sends nothing;
the existing explicit check button sends a frozen draft/candidate snapshot.

Show internal and external results as separate lists with independent ranks.
Display candidate count and scope. External-only results must produce a
results state rather than the current internal-only “No possible existing
work” message. Empty, omitted, and unavailable external comparisons need
distinct copy. Keep Create available regardless of advisory outcome.

Candidate add/edit/remove/reorder invalidates and aborts old comparisons using
the existing draft/project generation mechanism. Late results for another
population or project cannot replace current state. Candidate links support
inspection; no selection auto-adds a stored link or changes lifecycle.
Reference editing remains separate because relationship kind and observation
time need their own decision. A dedicated existing-item dashboard comparator
is deferred; post-create comparison is delivered through §9.3's MCP/REST path.

## 9. Plugin and consumer workflows

Update the three existing `plugin/skills/*/SKILL.md` files and shared
references without adding a skill/tool. A focused
`plugin/reference/external-records.md` can hold the detailed contract and
examples; if added, update the exact packaged-reference inventory in
`mcp/tests/test_plugin.py`. Link critical guidance from each relevant skill.

### 9.1 Filing and recalling work

1. Park new work as pending. If it awaits an owner decision, request the
   existing human gate, which removes it from ready work. Do not restore the
   obsolete gate-disable setting described in the incident.
2. Gate resolution remains the existing separate human action. Filing an
   external issue requires the actor's existing provider-write authority; an
   answer does not authorize unrelated external actions.
3. Once the provider confirms creation, reread the exact work row and use
   versioned `update_work` to attach the stable URL with `tracked-by`, state,
   and the actual observation time. Use `references` for related research or
   evidence. A progress-only event does not satisfy this workflow. Preserve
   other links when replacing the list.
4. On an unknown write outcome, retry the frozen UUID/arguments. On a
   definitive version conflict, reread/reconcile and start a new operation.
   Never file another issue just because storing its existing URL failed.
5. Recall reads references before selecting work. Hints invite inspection
   when provider access exists; they do not silently close/decline work.
   Refreshing a hint is an explicit versioned write, never a side effect of
   recall, search, or comparison.

An interval exists between gate resolution, provider creation, and link
storage. These separate operations do not atomically acquire a worker's lease.
The deferred gate-link option would not eliminate the provider-creation
interval or Path B. Consumer-specific FishFood files, hooks, environment
cleanup, and GitHub writes remain outside this Mnemonic implementation.

### 9.2 Bounded explicit gathering

Gather only during an explicit comparison, with a project repository URL
mapped to a supported provider and existing client read access. Manual
candidates do not require this automatic path. Missing access/tooling skips
gathering with an honest scope statement; do not provision credentials.

Document a concrete initial client collection policy:

- Gather title-search hits, open records, and records closed/merged in the
  last 30 days. Include issues and PRs/merge requests intentionally; a mixed
  provider issue endpoint must not misclassify PRs.
- Bound each bucket to 32 issues and 32 PRs, 192 raw records maximum. Use
  explicit page limits, no unbounded pagination, and a ten-second total
  collection budget. Each provider read must fit the remaining time; bounded
  parallel reads through existing clients are permitted.
- Deduplicate by the chosen exact URL before allocation. Reserve up to 32
  unique title-search, 16 open, and 16 recent-closed records. Fill unused slots
  from remaining title/open/recent buckets in that order, up to 64. Order each
  bucket by provider relevance where available, then update time descending,
  then URL. Provider relevance itself is not promised stable over time.
- Map provider states to the four literals; unrecognized means unknown.
  GitLab/Jira-shaped candidates fit without server schema changes. Providers
  without PRs allocate only from their available record classes.
- Strip authentication material before building requests. Explicitly bound
  overlong bodies on the client and disclose truncation; never truncate a
  title/URL into a different identity. First reduce bodies to the useful
  1,500-character comparison prefix, then reduce count if needed. Calculate
  the complete serialized frame/body, including draft, JSON escaping and
  envelope; preserve priority and respect §6.1's selected transport limit.
  In particular the plugin must fit the 1 MiB MCP frame, not just the 2 MiB
  REST body. Disclose reductions. Example collection fixtures must prove their
  actual final HTTP MCP and stdio frames fit, including multibyte/escaped text.
- Disclose submitted count, skipped/failed buckets, truncation, and window.
  Missing candidates cannot be found; failed reads are not zero-result proof.
  Respect rate limits and the total budget instead of retrying indefinitely.

This is client-side guidance/examples using existing provider tools, not a new
server script, daemon, or provider framework. Query arguments must not become
shell code; use structured arguments and never interpolate untrusted titles
into commands. No provider credential or environment dump is sent to Mnemonic.

### 9.3 Comparing existing work and inverse lookup

Read the exact item's title, summary, initial checkpoint prompt, and supplied
tags, then call suggestions with those fields and `exclude_work_item_id`.
Use the initial checkpoint, not whichever context is newest. Existing
canonical-group exclusion remains unchanged and does not remove external
candidates. Gather candidates during this explicit step, not every recall.

Inspect candidate text and actual provider state before choosing tracked-by,
references, or no link. Rank/signals authorize no linking, merging, closeout,
or provider creation. A later authorized Promoted/Won't do action uses its
existing nested report and operation UUID; a Done closeout remains completion.

For a session starting from an external issue, document §5.2's paginated
`status=all, duplicate_scope=all` inverse lookup. Follow aliases explicitly,
inspect current canonical readiness/lease, and use normal claim operations.
This is an opt-in consumer practice, not coordination of workers that skip it.

## 10. Work packages and dependency order

All packages below are future implementation. Use linked topic worktrees
from current remote main and the repository's PR/required-check workflow.

| Package | Concrete work | Exit evidence |
| --- | --- | --- |
| A — Contract fixtures | URL/text/time corpus, list bounds, PATCH clear vs omission, ordered equality, external presence/scope, maximum event sizing, OpenAPI consumers. | Reviewed fixtures with no unresolved semantic choice. |
| B — Storage/ledger | `0022`, ORM, active event validators/source checks, conditional metadata caps, GIN, history-aware downgrade guard, explicit audit head/catalog support. | Fresh/populated upgrades, direct SQL tests, schema parity, content/receipt preservation, pre/post/restored audits. |
| C — D1 backend | Work/schema/event/receipt services, explicit readiness/context/relationship/hierarchy projections, duplicate snapshots, full-view exact search. | Lifecycle, replay, concurrent writers, read/search matrix and snapshot tests. |
| D — D1 clients | MCP models/signatures, proxy/guards, response correspondence, frozen mutation intents, editor/presentation/events. | Contract tests and edit/clear/conflict/unknown-outcome acceptance. |
| E — D2 backend/resources | Focused comparator, existing route/service orchestration, detached internal result/vector, owned workers/deadlines/retention. | Ranking, failure isolation/cancellation/contention tests and real-host measurements. |
| F — D2 clients | Candidate inputs, request-bound guards, manual compare rows, separate results/staleness. | External-only/empty/unavailable/forged cases and Create-anyway acceptance. |
| G — Workflow/release docs | Three skills/shared references, examples, contracts/operations, OpenAPI and versions. | Packaged plugin tests, aligned catalog and release checklist. |

Package B must extend `scripts/audit_project_activity.py`,
`tests/fixtures/project-activity-catalog-v1.json`, and
`backend/tests/test_project_activity_audit_postgres.py` for explicit
`0022_external_references` support. Preserve the frozen `0021` catalog as the
pre-upgrade target; do not overwrite it. Keep report/activity checks enabled
for both `0021` and `0022`, rather than blindly changing `HEAD` in existing
`expected_head == HEAD` branches. Add reference column/index/validator/cap and
data-invariant checks, freeze supported migrated/restored `0022` catalog forms,
and add negative guard-drift fixtures. Findings remain aggregate and free of
content/credentials. Package G publishes the exact pre/post-upgrade commands.

A precedes B/C/E. Establish D1 through B/C/D before declaring D2 deliverable.
D can proceed beside E after contract stabilization; F follows D2 contract
and execution work. G reflects final implemented behavior and current counts.
Avoid deploying intermediate states with old strict clients. A documentation
PR has no application version bump.

Update `docs/api-contract.md`, `architecture.md`, `agents.md`, `development.md`,
`operations.md`, `roadmap.md`, relevant validation documentation,
`AGENT-README.md`, applicable README/examples, and generated `docs/openapi.json`.
Explain exact URLs, mutable/immutable references, clearing, bounded comparison,
manual review, report-required closeouts, and unsupported roots filtering.
Update backend/MCP/frontend versions and locks, plugin/marketplace manifests,
and release assertions together. Refresh local ignored `CLAUDE.md` only during
the later release; never track it or alter unrelated consumer files. Add no
dependency solely for chips, formatting, or RRF when current primitives suffice.

## 11. Verification contract

Planning validation is source inspection, document checks, and §14's cold
critique. Writing this plan performs none of the implementation tests,
benchmarks, migration rehearsals, or rollout steps below.

### 11.1 Required behavior matrix

| Area | Required cases |
| --- | --- |
| Validation | 0/10/11 refs, 64/65 candidates, unknown keys/null/coercion, duplicate URLs, case/query/fragment/percent grammar, credentials/controls/backslash, Unicode labels, UTC normalization, omitted observation, JSON/UTF-8 limits. |
| Ledger | Direct SQL shape/cap/creation correspondence, application-authored before/after correctness, all four expanded events, explicit empty diffs, unchanged progress cap, JSONB escaping, alias immutability; no unsupported SQL transition-authenticity claim. |
| Writes | Create/replace/edit/reorder/clear, omitted preservation, identical-list update retains existing version/event behavior, stale/concurrent writers, absent/valid/invalid token, atomic rollback. |
| Closeouts | Ref-only pending/terminal edits; refs plus Promoted/Won't do require report/UUID; failed report rollback; reopen with refs; unchanged Done/evidence/report/gate/lease guards. |
| Receipts | Assess all 15 kinds; historical fingerprints/bodies unchanged; create empty equals omission, PATCH empty differs; ordered intent; replay after edit/merge/deletion/closeout/restore; no hydration from current rows. |
| Discovery | Ready before selection; full search/detail/recall/claim contexts, counterparts, hierarchy/attention, canonical suggestions, MCP resources/prompts; no alias inheritance. |
| Snapshot | Concurrent ref edit during inference retains captured refs; refs absent from embedding composition/digest; same snapshot/capacity yields unchanged internal ranking. |
| Search | Shared URL across items/projects, soft deletion, duplicate/status scopes, q/semantic AND, paging/totals, encoded punctuation, roots rejection, realistic index and route plans. |
| Ranking | SQL title-key parity, exact-prefix overflow, URL ties/permutation, partial-title overlap surviving disjoint draft fields with no inference, stopword-only and unrelated controls, RRF order, 1/16/64 candidates, state not rank input. |
| External response | Omitted/empty request omits extension; either lane independently empty/populated; count/identity/presence; no body echo; malformed responses fail; external scope independent of internal mode. |
| Resources | Slow SQL/pool/model, invalid vectors, external expiry, shared search, late results/disconnect before/after baseline; permits released once after actual completion, internal page preserved. |
| Safe read | No durable external rows/cache/receipt/event/activity, no provider network, no sensitive logs, create available, effect/catalog counts unchanged. |
| Transport/size | REST/browser 2 MiB vs complete HTTP MCP/stdio 1 MiB boundaries, JSON/envelope overhead, client reduction fixtures, maximum 1 MiB receipts, conditional browser/MCP event bounds, high-fanout context sizes/usability. |
| Browser | Add/edit/remove/reorder/clear, frozen retry/conflict, safe links/keyboard, state/time accessibility, external-only/stale/project-switch behavior, Create anyway. |
| Upgrade/recovery | Fresh/populated/restored upgrades, preserved digests/replay, final quiescent recovery backup, last-minute committed mutation/receipt survives failed-upgrade restoration, pre-0021/post-0022/restored-0022 audits, negative guard drift, downgrade refusal after clear, activity stream rotation. |

### 11.2 Test locations and evidence

Extend backend work-item/event/receipt suites, including
`test_work_items_postgres.py`, `test_work_events_postgres.py`,
`test_work_event_semantics_postgres.py`, `test_client_operations.py`,
`test_idempotent_mutations_postgres.py`, `test_duplicate_suggestions.py`,
`test_duplicate_suggestions_postgres.py`, `test_schema_parity_postgres.py`,
and Phase 11/12 regressions. Add focused external reference/comparison and
populated `0022` tests rather than burying every case in unrelated fixtures.
Include `test_project_activity_audit_postgres.py` and lint the changed audit
script under the existing operational-script checks.

MCP: `test_tools.py`, `test_duplicate_suggestions.py`,
`test_response_validation.py`, `test_openapi_contract.py`, `test_plugin.py`.
Exercise request/result correspondence and malformed nested responses, not
only successful serialization.

Frontend: `proxy-policy.test.mjs`, `duplicate-suggestion-proxy.test.mjs`,
`duplicate-suggestions.test.mjs`, `mutation-responses.test.mjs`,
`mutation-intent.test.mjs`, `work-events.test.mjs`,
`work-relationships.test.mjs`, `openapi-contract.test.mjs`. Add a Playwright
feature spec for discovery/editing and manual comparison, reusing fixtures
for reports, unknown outcomes, and aliases.

Use deterministic fake vectors/clocks for ranking/deadline assertions and
separate actual-model host measurements for latency/quality. Record elapsed
time, mode/scope, candidate count, cold/warm status, and competing request
behavior. Do not commit private incident issue bodies, backups, credentials,
or test output; use sanitized representative records.

### 11.3 Full implementation checks

Use Python 3.14, Node 24, separate backend/MCP environments, and an isolated
PostgreSQL test database. The implementation worktree runs:

```sh
pre-commit run --all-files
docker compose -f compose.test.yaml up -d --wait
```

With `TEST_DATABASE_URL` set to that test database, from `backend/`:

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src
```

From `mcp/`:

```sh
uv sync --frozen
uv run pytest -q
uv run ruff check .
uv run ty check src/mnemonic_mcp
```

From `frontend/`:

```sh
npm ci --no-audit --no-fund
npm test
npm run typecheck
npm run build
npm run test:e2e:stack
```

Regenerate OpenAPI with the documented repository generator and verify its
snapshot/consumer tests. Run actual PostgreSQL migration/parity/replay tests;
a skipped database suite is incomplete. None of these commands authorizes
starting or migrating the live stack during planning.

### 11.4 Incident and product acceptance

- Park an item with a stale “not filed” summary, then store a tracked-by URL.
  Ready/search must show it before selection. Updating an observed closed
  state changes the hint, never readiness.
- Give related research a references link. It must not be shown as the tracked
  objective or auto-promoted.
- Compare representative external twins in the actual candidate direction
  after creation/self-exclusion. Record rank/signals/scope and include related
  research/no-twin controls to show why auto-action is unjustified.
- Under contention/failure, retain useful internal results and honest external
  scope; Create remains available.
- Explicitly demonstrate that a worker skipping lookup/claim is not
  coordinated. Do not claim elimination of Path B.

## 12. Release and operational handoff

Readiness requires the behavior matrix, coordinated contracts, populated
migration/restore rehearsal, adversarial implementation review, and host
measurements. Record results later in a validation artifact, distinguishing
automated checks from operator-run production checks.

For the later authorized release:

1. Fetch main, reconcile versions/revision IDs, and work only on linked topic
   branches. Commit through gitleaks; push/open PRs targeting main.
2. Wait for `Required checks` on the current up-to-date head. If main advances,
   rebase/retest and push only with `--force-with-lease`. Never bypass branch
   protection or use administrator overrides.
3. Merge through GitHub's allowed squash/rebase path. Confirm clean worktree
   before cleanup, then fast-forward primary main to the already-merged
   remote main. No implementation commit goes directly to main.
4. Complete advance migration/restore rehearsal and pin coordinated artifacts.
   At the separately authorized live cutover, close ingress and stop/drain
   every application and direct writer, then run the pre-upgrade audit with
   `--expected-head 0021_job_completion_reports` and take the final named
   custom-format recovery backup. Validate the archive, copy it off-machine,
   and restore-test it on isolated PostgreSQL 17. Record versions/head,
   catalog, counts/digests and permanent receipts at this final quiescence
   point. An earlier rehearsal backup is not the live recovery point.
5. Keep writers stopped, migrate, and start the aligned processes behind
   closed ingress for checks. Older processes must not use the new schema
   even before refs are populated. Verify health and actual versions/catalog.
6. Verify old data/replay; run controlled create/update/clear/read/search/
   comparison smoke tests in staging or an approved disposable project.
   Run the updated audit with `--expected-head 0022_external_references` and
   verify activity/report integrity. Validate a post-upgrade backup/restore
   while public writes remain closed; reopen ingress only after these gates
   pass. The historical Phase 11 preflight is not a `0022` preflight.
7. On failure, keep writers stopped and use forward repair or restore the
   complete verified backup, honoring downgrade refusal and activity stream
   rotation. Never erase references or rewrite receipts to start old clients.
   Once traffic has reopened, restoring the cutover backup can discard later
   accepted writes: it is not lossless rollback. That situation requires a
   separately decided recovery approach preserving those writes or an explicit
   owner decision about loss; do not execute the pre-open restore procedure
   under an assumption that later data will survive.

D1/D2 need no new environment variable, project source setting, browser
secret, provider credential, or external network permission. Their impact is
a column/index, expanded system-event validation, and coordinated canonical
client schemas. No live cutover is authorized by this planning document.

## 13. Deferred work and revisit criteria

Outside scope: server provider reads, external entities/tools, source settings,
webhooks, background synchronization, external caches, automatic links/merges/
closeouts, prose parsing, gate-link writes, server draft-source shorthand, and
a dedicated post-create dashboard comparator. Do not build unused hooks now.

At least 30 days after D1/plugin release, an explicitly scoped read-only
adoption study may sample items independently known to have corresponding
external records. Do not select the denominator only from already-linked
items. Record sampling, missing links, observation age, provider coverage,
and uncertainty. If over half lack tracked-by, investigate adoption and
reconsider D3; this finding is not permission to start indexing.

Other owner-recorded triggers: a requested dashboard provider-check control
or predominance of dashboard comparison, a client without provider access,
repeated hints over a day stale, tracking outside the repository provider,
persistent provider latency/rate limiting, or a new person/tenant requiring
access boundaries. Comparing dashboard/tool usage needs an agreed measurement
method; this plan adds no usage telemetry. Revisit gates if owners repeatedly
answer in chat while dashboard questions remain unresolved for over a day.

D3 requires a new approved credential/security design. The evaluation's claim
that deriving a target from `repository_url` eliminates SSRF/ensures tenancy
is too strong: that field is editable. A future design must validate configured
origins, redirects, resolved addresses, repository/credential binding, and
authority to edit that binding. The historical auth sketch is not an approved
implementation contract, and D1/D2 add no server provider infrastructure.

## 14. Independent cold review and resolution record

The completed initial draft (Git blob
`10f68c32ec1d3e7cef2e5852c932393fe914e8e8`) received an independent cold critique
on 2026-09-05. The reviewer had the proposal/current source, without drafting
discussion or reconnaissance notes. It found five material corrections:
quiescent final backup ordering, OR lexical matching, MCP frame sizing,
the SQL update-diff enforcement boundary, and new-head integrity audit support.

All five corrections are incorporated above. The original findings and their
dispositions are preserved in the
[adversarial review](external-records-deduplication-plan-adversarial-review.md).
Additional source checks made event-size/client boundaries and nonreceipt
context fanout explicit. A targeted reviewer recheck is recorded there.

Owner-gated work remains deferred. Review completion and a documentation PR
do not authorize application code, migration execution, or live cutover.
