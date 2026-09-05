# Phase 12 cold code review: frontend and cross-surface behavior

**Final frontend verdict: ACCEPT.** All four original P2 findings are closed
by independent source inspection and isolated mounted-browser probes. The
original findings and their initial evidence remain below as review history;
they are not outstanding defects. Full configured-stack acceptance and required
CI remain separate release checks. No application changes were made by this
reviewer.

## Scope and evidence

This review independently read the Phase 12 implementation plan, inspected the
implementation against `origin/main`, and examined frontend report/settings,
activity, mutation receipt, proxy, lifecycle, and provenance behavior, with
backend/MCP contract checks where relevant. The reviewer did not participate in
implementation or consult builder discussions or rationales. The inspected
working tree was based on commit `559564faa3f117060f8984fb1da86e035d3c006d`;
Phase 12 implementation changes were still in progress and uncommitted.

All four findings below were reproduced with Playwright against a separate
copied frontend in `/tmp`, with every Mnemonic HTTP request intercepted by
synthetic API responses and WebSocket hints suppressed. This did not contact
or mutate production data. The copied application source was not patched to
make the scenarios fail. The reviewer also ran:

```sh
node --test tests/project-activity.test.mjs tests/job-completion-reports.test.mjs tests/mutation-intent.test.mjs tests/mutation-responses.test.mjs tests/proxy-policy.test.mjs
```

Result: **67 passed, 0 failed, 0 skipped**. These bounded checks used the
available Node `v22.22.3`; they are not the repository's full Node 24 validation,
configured-stack acceptance suite, or required CI. Passing contract tests did
not exercise the failing mounted-component interactions described below.

The live implementation began changing after preliminary findings were sent.
This document records the original reviewed snapshot. The final closure pass
is recorded at the end, distinguishing independently executed checks from the
full-stack release validation performed separately.

## Original required changes — all closed

### F1 — P2: Source lifecycle changes do not refresh report context through the durable feed

**Locations:** `frontend/lib/project-activity.ts:70`,
`frontend/components/dashboard.tsx:339`,
`frontend/components/job-report-content.tsx:12`, and
`frontend/components/job-report-list.tsx:22`.

In the reviewed snapshot, `activityInvalidations` marked work events only as
`work`, while the dashboard incremented `reportRefresh` only for `reports`.
However, a report envelope includes *current* source status, deletion, and
canonical identity. Reopen, merge, and deletion are work events. When a socket
hint is lost, the durable poll consumes those events without rereading report
envelopes. The ordinary socket callback happens to refresh reports and masks
the missing dependency.

**Reproduction:** Open Summaries with an undismissed Done report. Suppress the
WebSocket hint, make the source Pending, and return a `work_reopened` activity
entry at sequence 10. Wake the foreground poll. The browser accepts the entry
and advances its cursor, but report-list reads remain **2 before / 2 after**.
The card still shows its Done outcome without the required notice that the
source is now Pending. Subsequent empty polls cannot supply that missing
current-state context.

This violates the plan at lines 1055–1059 and 1124–1141. The immutable Done
outcome must remain, accompanied by the changed current source state.

**Required fix:** Invalidate report envelopes for source-affecting work events.
Refresh current source context in an already-open follow-up form without
replacing its human-entered fields: `followUpReport` is a separately retained
envelope, so updating only `items` does not refresh that copy. Apply the same
principle to an already-open originating report in
`frontend/components/work-report-provenance.tsx:42`, whose detail-fetch effect
currently omits `refreshSignal`. Add dropped-socket reopen/merge/delete
regressions for both a normal card and an open form/report detail.

**Observed amendment, not yet verified:** At final citation collection, the
live `project-activity.ts:71–72` had been changed to mark `work_event` as
`reports`. The isolated reproduction used the earlier snapshot. This amendment
addresses the initial mapping gap; the full affected-view behavior still needs
closure verification.

### F2 — P2: Recovering one report's dismissal destroys another report's follow-up draft

**Locations:** `frontend/components/job-report-list.tsx:64–68` and
`frontend/components/job-report-list.tsx:108–116`.

The recovered-mutation subscription closes `followUpReport` for *any* dismissal
or follow-up creation recovered in the selected project. It does not compare
the recovered report with the report owning the open form. The per-report
conflict keys correctly allow independent report A to remain usable while
report B has an uncertain dismissal, making this interaction reachable.

**Reproduction:** Dismiss report B and return a 502, retaining the exact request
as an unknown outcome. Open Create Follow-up on report A and enter a work
summary and standalone instructions. Retry B using the global **Retry exact
request** action and return a valid successful receipt with the original actor.
B recovers successfully, but A's form disappears. The reproduction recorded
**two dismissal attempts, A's nonempty draft before retry, and form count 0
after retry**. There is no discard prompt or saved copy of A's uncontrolled
input fields.

This violates the independent-action ownership and dirty-draft preservation
requirements in plan lines 1073–1077 and 1081–1099.

**Required fix:** Scope recovered-action cleanup to the form/report that owns
the recovered intent. A dismissal must not clear an unrelated follow-up form.
A recovered follow-up may clear its own frozen submission, while another
report's unsaved draft must survive the resulting list refresh. Add this
cross-report unknown-outcome recovery scenario as a mounted UI regression.

### F3 — P2: Report-only closeout drafts bypass the work pane's discard guard

**Locations:** `frontend/components/dashboard.tsx:375`,
`frontend/components/dashboard.tsx:1013–1034`, and
`frontend/components/work-detail-pane.tsx:250`.

The closeout editor adds `jobReportDraft.summary` and `fyiItems` to the existing
pending-work form, but `leavingOpenedWorkAllowed` checks only checkpoint,
evidence, and work-item edit state. Changing the selected work then resets the
report draft through the `openedId` effect. A human who writes the summary
before the technical checkpoint can therefore lose all report prose without
the existing unsaved-work protection.

**Reproduction:** Open a Pending work item, enter only Human summary and one
FYI, leaving checkpoint text and evidence empty. Move focus out of the editor
and press Escape. The pane closes, the work selection disappears from the URL,
and both report fields disappear. The reproduction observed **0 confirmation
dialogs**. Escape uses the same close guard as the pane's Back action.

**Required fix:** Include meaningful report summary/FYI edits in the work
pane's dirty-state check. Preserve them when the human refuses to discard,
and reset them only after explicit discard, confirmed closeout success, or
another intentional lifecycle action. Loading a prompt revision alone should
not count as a human edit. Cover report-only summary and FYI drafts when
closing, selecting other work, and switching projects.

### F4 — P2: A failed secondary-view read is forgotten after its activity cursor advances

**Locations:** `frontend/components/use-project-activity.ts:48–57`,
`frontend/components/dashboard.tsx:345–351`,
`frontend/components/human-attention-list.tsx:111–117`, and
`frontend/components/work-event-timeline.tsx:123–145`.

The poll queues invalidations and advances its cursor before the asynchronous
view reads finish, relying on `onRetryDirty` for recovery. That callback sees
queue/settings/context/project failures and sidebar counts, but it cannot see
the Needs Attention list's own `loadError`. A successful badge count therefore
hides a failed list fetch. The list has no independent retry timer or durable
dirty registration. The event timeline uses the same uncovered failure pattern.

**Reproduction:** Open Needs Attention with valid initial list and count reads.
Suppress socket hints. Deliver a work event through activity and fail the
resulting `limit=30` attention-list GET once with 503, while its separate
`limit=0` count GET succeeds. Restore healthy reads and trigger three further
foreground polls, each returning an empty activity page after the accepted
sequence. The reproduction observed **five activity reads total, attention
list reads fixed at 2, and the original error still visible**. Manual refresh
can recover, but durable polling has lost the pending view refresh.

This directly contradicts plan lines 1136–1139: failed view fetches must remain
dirty and retry independently after cursor advancement. An activity transport
success alone is insufficient to establish that the visible queue is current.

**Required fix:** Retain dirty state for each affected view until its own read
succeeds, either through registered per-view retry callbacks or bounded local
retry behavior. Cover the attention list and all other activity-driven detail
views, including event history, checkpoint history, evidence, and provenance.
Do not use successful badge counts as evidence that their associated lists
loaded. Add a regression with a successful activity read, one failed dependent
read, then only empty activity pages; the dependent view must recover without
new activity or manual intervention.

## Optional observations and limits

No additional stylistic changes are requested. The findings above are required
correctness fixes; this review does not claim that every backend database or
migration invariant has been independently proven. In-progress legacy fixture
adaptation was not counted as a product defect.

## Final independent frontend closure

Closure source was inspected at worktree HEAD
`3bd7491a1a7b37d6d1e2dfdda3ff44c396367b86`, following the rebase onto
`413155947a4953499d4f868f552e0b0ce493f8c5`. The reviewer independently read the
corrections and new browser regressions, then refreshed the separate frontend
copy and reran the failing interactions with synthetic APIs and no socket
hints. Application source was not changed by the reviewer.

- **F1 closed:** `project-activity.ts:72` now invalidates reports for work events.
  `job-report-list.tsx:65–81` refreshes the retained envelope by report identity,
  including independent recovery of a failed detail read; the form keeps a
  stable report key. `work-report-provenance.tsx:47–58` refreshes the selected
  originating report on the parent refresh signal and independently retries its
  own failed read. The copied-browser probe delivered reopen, merge, and deletion
  entries: list reads advanced 2→3→4→5, both the card and retained draft displayed
  each changed source state, and both entered text fields remained exact. A
  synthetic first detail-read 503 recovered automatically. Browser regressions
  cover retained originating report deletion and source merge across both views.
- **F2 closed:** `job-report-list.tsx:82–90` clears a recovered follow-up form
  only for its own report conflict key and a follow-up creation intent. Recovering
  a dismissal no longer clears any independent form. The original two-report
  probe now yields two dismissal attempts, no remaining recovery notice, one
  still-open form, and the exact original human-entered summary after recovery.
- **F3 closed:** `job-completion-reports.ts:214–216` detects actual summary/FYI
  edits independently of loaded prompt revisions, and
  `dashboard.tsx:1037–1046` includes them in its discard guard. The original
  Escape probe now produces one dialog; refusing it preserves the selected work
  URL, summary, and FYI. Source inspection confirms work selection, project
  changes, pane close, and ordinary sidebar navigation use the guard. New
  browser regressions additionally cover revision-only cleanliness, summary-only
  and FYI-only drafts, rejected work/project changes, activity refresh, and
  explicit discard. Cross-page persistence of an unsubmitted follow-up form is
  not added as a requirement: the plan explicitly requires protection against
  background refresh and retention of uncertain submitted mutations.
- **F4 closed:** `use-failed-read-retry.ts` gives each failed view a bounded,
  visibility-aware retry schedule, cancels timers/listeners on cleanup, and
  prevents a busy view from retrying concurrently. The attention list, event and
  gate history, checkpoint reads, evidence, report/provenance reads, and other
  affected readers register their own failures. In the original activity probe,
  the count still succeeds independently, while list reads now increase 2→3
  during subsequent empty activity polls and the error clears. New browser
  regressions also cover failed history/evidence/event/gate/provenance reads
  without new activity.

The reviewer reran the five targeted frontend test files listed above under
**Node 24: 70 passed, 0 failed, 0 skipped**. The synthetic browser was served
from the copied frontend using the available Node 22 development runtime; this
is supporting component-interaction evidence, not Node 24 production-build or
full-stack acceptance evidence.

Inspection and independent browser execution also found that the new report
editor regression's exact-label selectors matched zero elements because the
labels include explanatory text. This was reported before the full stack run;
the coordinating agent changed those test selectors to prefix regular
expressions. It did not require changing application behavior. The reviewer
has not claimed an independently completed full-stack E2E run.

There are **no remaining required frontend changes from this review**.

## Incremental review: wait for the first report prompt revision

**Disposition: ACCEPT.** The reviewer independently inspected the subsequent
small source/test delta at worktree HEAD
`3bd7491a1a7b37d6d1e2dfdda3ff44c396367b86` plus its uncommitted changes.

`work-detail-pane.tsx:261` now disables Complete work while the report draft's
prompt revision is null. `work-item-editor.tsx:54–55` uses the same terminal
transition predicate for displaying the report editor and, at line 72, disabling
Save changes until the revision is available. It covers both pending-to-Won’t do
and pending-to-Promoted; ordinary work edits remain available. The existing
loading/error display and automatic settings-read retry explain and recover the
unavailable state. The successful settings read copies the current draft before
setting its first revision, preserving prose entered while the request was in
flight. Existing validation still precedes mutation-intent creation and dispatch.
These guards therefore address the early-click validation error without changing
receipt or stale-revision semantics.

The new regression at `tests/e2e/phase12-project-activity.spec.ts:477` holds the
settings response, checks that Done and Won’t do submission controls remain
disabled with no closeout writes, releases the response, verifies retained human
prose, and checks one write for each successful action. The Promoted control uses
the same inspected predicate. No additional required finding was identified.

This incremental disposition is based on independent source and regression
inspection. The coordinator's fresh full E2E run was still in progress; this
review does not claim its result or an independent execution of that new test.
