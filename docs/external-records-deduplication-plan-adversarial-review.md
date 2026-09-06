# External records implementation plan — independent adversarial review

Reviewed 2026-09-05 against the initially completed implementation plan and
repository `0ea0cc46a336c9a53c8f14182a0d589d281180a4`. This is a planning-only
review. No application code, migration, test data, receipt, credential, or
external system was changed; no migration or implementation test was run.

The reviewed initial plan has Git blob ID
`10f68c32ec1d3e7cef2e5852c932393fe914e8e8`.

The reviewer read `AGENTS.md`, the full plan, the local source evaluation
`.untracked/EXT_RECORDS_DEDUPE.md` including its recorded owner decisions, and
the relevant current backend, MCP, browser, migration, test, and operator code.
The review received no drafting discussion or reconnaissance notes. A separate
read-only source cross-check covered the proposed comparator and resource
ownership contract. Findings below refer to line numbers in the initial plan;
subsequent resolution edits can move them.

## Assessment

The architecture is sound in its main choices: mutable links stay on work,
external candidates remain ephemeral, permanent receipt replay stays intact,
and no provider credential, foreign lifecycle, automatic linking, compatibility
execution path, or new write catalog is introduced. The plan correctly fixes
the evaluation's dangerous PATCH omission/clear conflation, explicitly handles
the existing ledger validators, and explains Path B's limit.

It is **not ready to implement exactly as written**. Five material corrections
are needed. None requires reopening the central D1/D2 architecture or adding
a new product subsystem. P1 denotes a potential production data-loss path; P2
denotes a correctness, contract, or required-validation defect to settle before
implementation proceeds through the affected package.

## R1 — P1: Take the final recovery backup after quiescing writers

**Plan evidence:** [§12](external-records-deduplication-implementation-plan.md#12-release-and-operational-handoff),
initial lines 993–1006, takes and restore-tests the backup in step 4, then stops
old processes and quiesces writes in step 5. Step 7 permits restoring that
backup on failure. There is no second backup after quiescence and no declared
mechanism to retain intervening commits.

**Current source:** [docs/operations.md](operations.md), lines 111–116, requires
closing ingress and quiescing every writer **before** taking the fresh backup.
Lines 169–174 explicitly recognize that whole-archive restoration loses later
writes. The detailed Phase 11 sequence at lines 550–575 also forbids resuming a
writer between the final backup and migration.

**Failure scenario:** A user edits work or closes out an item after the step-4
dump snapshot but before step 5 stops writers. The upgrade then fails a
post-migration check and the operator follows step 7. The restored database
silently loses the accepted edit, report, or permanent receipt even though the
plan promises production-content preservation. Restore testing the earlier
archive does not close this window.

**Correction:** Separate advance rehearsal from the live recovery point. For
the live cutover, close ingress, stop/drain all application and direct writers,
then take and validate the final named backup while quiescence remains in
force. Keep writers stopped through migration, aligned-process startup, replay
and integrity checks, and post-upgrade backup/restore validation. Explicitly
state when traffic reopens. If any later recovery can discard accepted writes,
it requires a separately stated recovery decision; do not imply losslessness.

**Validation:** The release checklist must prove that the recorded recovery
archive and preservation baseline correspond to the final quiescence point.
Exercise the failed-upgrade recovery path on a clone with a last-minute
committed work mutation and receipt and confirm that both survive.

## R2 — P2: Reuse the shipped OR lexical query, not full-draft AND matching

**Plan evidence:** [§6.3](external-records-deduplication-implementation-plan.md#63-comparison-stages),
initial lines 537–539, specifies English `plainto_tsquery` over the current
`_draft_text` composition. This requires all surviving title, summary, tag, and
prompt lexemes to match the external document.

**Current source:** [duplicate_suggestions.py](../backend/src/mnemonic_api/services/duplicate_suggestions.py),
lines 254–268, extracts normalized draft lexemes and joins them with ` | ` to
construct an OR query. The deliberate regression
`test_partial_title_overlap_survives_disjoint_draft_fields_and_seeds_semantic`
in [test_duplicate_suggestions_postgres.py](../backend/tests/test_duplicate_suggestions_postgres.py),
lines 576–607, protects a candidate titled “Repair cache invalidation” when
the draft title is “repair cache” and its other fields contain unrelated
astronomy/geology terms.

**Failure scenario:** An actual external twin shares the objective's title
terms but lacks words from Mnemonic's longer summary or original prompt. Its
title is similar rather than exactly equal, so the exact lane does not rescue
it. The proposed AND query omits it. With the shared inference permit busy,
the promised lexical fallback returns no evidence for the twin despite the
shipped internal comparator retaining equivalent evidence.

**Correction:** Specify the shipped OR-of-normalized-lexemes query construction
for the external lane, with its empty-lexeme behavior. Reuse a narrowly scoped
SQL helper or equivalent pinned expression; retain the proposed external
title/body weights, normalization, positive-match rule, and separate ranking.

**Validation:** Adapt the existing partial-title/disjoint-context regression
to an external candidate and force inference unavailable. Assert its retention
with a lexical signal, not merely that the request succeeds. Cover an all-stopword
draft and an unrelated lexical nonmatch as controls.

## R3 — P2: The plugin's payload budget must include the stricter MCP envelope

**Plan evidence:** [§6.1](external-records-deduplication-implementation-plan.md#61-caller-supplied-population),
initial lines 449–452, identifies a 2,097,152-byte raw request cap. The client
gathering policy in §9.2, lines 802–806, says to reduce bodies/count to fit
“the entire UTF-8 request cap.” The MCP package and validation inventory never
identify a different limit or the JSON-RPC envelope overhead.

**Current source:** [mcp/transport.py](../mcp/src/mnemonic_mcp/transport.py),
line 22, sets `MCP_REQUEST_MAX_BYTES = 1_048_576`. HTTP enforcement at lines
102–113 and 148–157 rejects an oversized complete JSON-RPC request before the
tool runs. The stdio reader at lines 197–219 stops processing an oversized
frame. By contrast, [frontend/proxy-policy.ts](../frontend/lib/proxy-policy.ts),
lines 709–710, does allow 2,097,152 bytes on the suggestion route.

**Failure scenario:** Sixty-four valid ASCII candidate bodies of 20,000
characters occupy about 1.28 MB before adding titles, URLs, the draft, or the
MCP wrapper. This satisfies the plan's API count/field limits and fits its
stated 2 MiB budget, but fails over either shipped MCP transport. Less extreme
Unicode or escaped input can cross the same boundary. The primary plugin
workflow therefore cannot reliably submit a population that its guidance
declares acceptable.

**Correction:** State separate REST/browser and MCP wire limits. Prefer
preserving the existing MCP cap and have client guidance/examples fit the
actual serialized JSON-RPC frame, including identifiers and envelope overhead.
Client-side body reduction to the comparator's useful 1,500-character prefix
is an available first step, followed by count reduction if needed. Keep this
explicit and disclosed. If a larger MCP frame is instead intended, list that
transport contract change and its tests deliberately rather than changing a
global cap incidentally.

**Validation:** Add boundary tests through both HTTP MCP and stdio, including
large valid candidate lists, multibyte text, JSON escaping, and envelope
overhead. Test that the collection example's final frame fits its selected
transport and that over-limit input receives the documented behavior.

## R4 — P2: Resolve the stronger-than-existing SQL change-authenticity promise

**Plan evidence:** [§3.2](external-records-deduplication-implementation-plan.md#32-event-shapes-and-required-database-work),
initial lines 237–243, plans a new creation snapshot-to-row comparison, then
requires verification that direct SQL cannot insert “fake reference changes.”
Only shape validation and the creation source check are specified. No mechanism
binds an update event's reference `before` and `after` values to a real row
transition.

**Current source:** [0010_work_events.py](../backend/alembic/versions/0010_work_events.py),
lines 813–850, verifies creation snapshots against the retained work row. Its
source-fact guard has no ordinary `work_updated` branch and returns directly
for unmatched event kinds at lines 1023–1025. The metadata validator at lines
215–305 validates the update envelope and scalar shapes, not their historical
truth. [work_items.py](../backend/src/mnemonic_api/services/work_items.py),
lines 260–315, and [work_events.py](../backend/src/mnemonic_api/services/work_events.py),
lines 230–266, capture the before value and construct the diff in application
code. Phase 12's event source at
[0020_project_activity.py](../backend/alembic/versions/0020_project_activity.py),
lines 116–128, emits an activity pointer; it does not authenticate the diff.

**Failure scenario:** A direct SQL insert supplies a well-shaped reference
diff whose `before` or `after` never occurred on the work row. A JSONB CHECK
accepts the shape, and extending the creation guard has no effect on an update
event. Thus the stated direct-SQL guarantee cannot be established by the
enumerated migration work. Comparing only `after` to the current row would
still not prove `before` or the existence of a corresponding transition.

**Correction:** Make the boundary explicit before package B. The minimal
scope-consistent choice is to preserve the existing guarantee: database
reference shape/cap/alias enforcement and creation source correspondence,
with update-diff correctness enforced by the transactional mutation service
and its rollback/concurrency tests. Replace “fake reference changes” with the
specific malformed shapes/direct-SQL violations that will be rejected. If
transition-authenticated update events are actually required, specify the
row-transition/event binding mechanism, same-transaction multiple updates,
lock ordering, and historical baseline; that is additional ledger work and
must not be smuggled in as a CHECK-validation task.

**Validation:** Distinguish shape/source checks from application diff tests.
Do not mark an authenticity claim tested merely because invalid URL or invalid
array inserts fail. This finding concerns truthful Mnemonic change history,
not verification of the caller's assertion about provider state.

## R5 — P2: Add the new schema to the required integrity audit and its fixtures

**Plan evidence:** Packages B/G, the verification matrix, and [§12](external-records-deduplication-implementation-plan.md#12-release-and-operational-handoff),
initial lines 999–1003, require existing Phase 12 integrity/activity validation
after migration. The file/work inventory contains no update to the shipped
aggregate audit or its frozen catalog. The new migration changes work columns,
CHECKs, functions, and indexes that the audit intentionally verifies exactly.

**Current source:** [audit_project_activity.py](../scripts/audit_project_activity.py),
lines 22–27 and 333–335, supports only heads 0019, 0020, and 0021. Lines
274–285 fail on a head mismatch. Lines 145–153 compare live catalog definitions
with [project-activity-catalog-v1.json](../tests/fixtures/project-activity-catalog-v1.json).
[docs/operations.md](operations.md), lines 142–148, makes this exact catalog
validation part of the required operational audit, including supported
dump/restore representations.

**Failure scenario:** The migration and application tests pass at 0022, but
the documented production audit cannot select that head and rejects the live
database under its current default. Changing only the default also fails the
frozen catalog check. Operators are left with an unexecutable release gate or
pressure to skip the very checks intended to protect existing production data.

**Correction:** Add explicit work for the aggregate audit, new-head catalog
fixtures, audit tests, and the corresponding operator commands. Preserve 0021
as the pre-upgrade audit target; do not overwrite its frozen expected catalog.
Ensure report checks remain enabled for both 0021 and 0022 rather than simply
retargeting current `expected_head == HEAD` branches. Add the new reference
invariants while retaining prior-phase checks. Freeze supported migrated and
backup-restored catalog forms for 0022.

**Validation:** Run the audit on populated 0021 before migration, on populated
0022 after migration, and on a supported restored 0022 backup. Add negative
cases for missing/altered new reference guards and existing report/activity
guards. All must fail closed with aggregate, content-free findings.

## Limits and non-findings

The independently owned worker/completion-handle proposal in §7 addresses
the current synchronous route's inability to return external fallback while
native inference is still running. No additional high-confidence material
resource-ownership defect was found in that proposed contract. Its real-host
measurements remain future release evidence, as the plan already states.

No automatic production-data backfill is recommended. The deliberate empty
default and refusal to infer links from historical prose preserve authorship.
Preserving historical read/replay shapes is an existing integrity obligation;
it is not a reason to add old-client execution adapters. The review does not
request provider infrastructure, URL normalization, new lifecycle semantics,
or an expanded mutation catalog.

## Resolution record

Initial status: R1–R5 open. The plan author should append the chosen correction
and affected sections for each finding after revising the implementation plan.
This review artifact alone does not claim that a finding is fixed or authorize
implementation, migration execution, deployment, or provider access.

### Targeted disposition recheck — 2026-09-05

The cold reviewer rechecked the revisions against R1–R5 in plan §§3.2,
6.1/6.3, 9.2, package B in §10, §11, §12, and §14. The revised plan reviewed
has Git blob ID `ffdba77000e17faf776a928fa6606811051ee064`. This was a targeted
planning recheck, not another exhaustive implementation audit. The initial
findings above remain the record of the original draft.

| Finding | Disposition | Evidence in the revised plan |
| --- | --- | --- |
| R1 — Final recovery backup | Resolved in planning. | §12 now separates advance rehearsal from the live recovery point, closes ingress and drains all writers before the final backup, preserves quiescence through migration and validation, and explicitly gates reopening traffic. Recovery after reopening no longer claims preservation of later writes. §11 includes the last-minute mutation/receipt recovery case. |
| R2 — Lexical query construction | Resolved in planning. | §6.3 now specifies the shipped OR-of-normalized-lexemes query, empty-lexeme behavior, and the partial-title/disjoint-context regression under unavailable inference. §11 retains stopword and unrelated controls. |
| R3 — MCP request envelope | Resolved in planning. | §6.1 distinguishes the 1 MiB complete HTTP MCP/stdio frame from the 2 MiB REST/browser body. §§8.1 and 9.2 preserve the transport cap, reduce bodies/count deliberately, account for actual serialized envelope/escaping overhead, and require transport-specific collection fixtures. §11 adds the boundary cases. |
| R4 — SQL diff-authenticity claim | Resolved in planning. | §3.2 explicitly retains database shape/cap/alias and creation-correspondence checks while assigning truthful update snapshots to the transactional mutation service. It excludes independent SQL transition authentication from this increment. §11 tests these separate boundaries without claiming more. |
| R5 — Integrity audit support | Resolved in planning. | Package B explicitly updates the audit script, catalog fixture, and audit tests for 0022 while preserving the 0021 pre-upgrade target and report/activity checks at both heads. §§11–12 require pre-upgrade, post-upgrade, restored-database, and negative guard-drift validation and give the selected audit heads. |

The related size clarifications distinguish conditional system-event metadata
caps, the unchanged 1 MiB permanent-receipt response bound, and nonreceipt
context fanout. They do not claim those future maximum-size or ordinary-client
usability checks have passed; the plan explicitly requires revising the
contract before release if supported clients cannot consume its chosen bounds.

**Final assessment:** R1–R5 are resolved as planning corrections. The revised
plan provides a coherent basis for future authorized implementation, with its
contract fixtures, database/replay validation, transport checks, host
measurements, and quiescent release gates still to be performed. No application
implementation, migration, benchmark, production rehearsal, or deployment has
been performed or authorized by this review. No material residual issue was
found within the targeted recheck.
