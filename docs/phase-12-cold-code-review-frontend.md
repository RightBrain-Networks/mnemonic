# Phase 12 cold code review: frontend and cross-surface behavior

**Verdict: ACCEPT WITH REQUIRED CHANGES.** Four P2 correctness findings require
resolution before shipping. These are failures of report context freshness,
draft preservation, and durable refresh recovery; no application changes were
made by this reviewer.

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
This document records the reviewed snapshot and distinguishes an observed
amendment from a verified closure. A separate closure pass is required after
all fixes settle.

## Required changes

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
