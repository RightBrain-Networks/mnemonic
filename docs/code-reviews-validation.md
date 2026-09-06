# Code reviews implementation validation

Implementation began at `513f543` (planning PR #49), then rebased onto `c71a0cb`
(work-item moves, PR #50) before cold review. This release is application/API/MCP/
dashboard 0.13.0, Claude plugin 0.14.0 and Alembic `0024_code_reviews`, following
`0023_work_item_moves`. The integrated catalog is 38 MCP tools, 13 protected MCP
writes, 18 REST receipt kinds, 15 protected browser mutations and 24 work events.
The plugin baseline was already 0.13.0. Work with retained review-policy/history
or remediation ancestry cannot move between projects.

The implementation follows [the plan](code-reviews-implementation-plan.md).
[Code reviews](code-reviews.md) documents the shipped workflow and operational
cutover. Validation uses disposable schemas/stacks; it does not deploy the
feature or migrate the operator's running database.

## Persistence and capacity evidence

- Migration/ORM parity, SQL transition/lineage attacks, downgrade guards and
  audit tests: 82 passed in the persistence validation run, plus schema parity
  and a populated move-history upgrade/unused-downgrade round trip. Existing completion
  evidence and report/lease suites are also included in full backend validation.
- Final persistence hardening passed 101 targeted PostgreSQL tests, including
  ten new deferred-event, null-mode and corrupt-data audit regressions, existing
  HTTP lifecycle tests, migration/ORM parity, moves and aggregate audits. The
  frozen 0024 guard catalog contains 2,032 entries across nine categories and
  matches both independently migrated and ACL-preserving restored databases.
- All 16 pre-feature receipt kinds retain frozen request/response digests.
  The two new kinds have their own frozen vectors; sparse historical responses
  do not acquire review fields.
- A PostgreSQL 17 custom archive (692,556 bytes) was restored into a uniquely
  named disposable database. Exact JSON-row digests matched for all 63 rows
  across 31 tables. The 18-category read-only review audit passed with a
  completed review, single remediation, pending optional question and an
  activity stream rotation represented in the rehearsal. This was repeated
  after the final deferred event-witness and nonnull lease-mode corrections.
- A real completion with 100 findings and an exactly 65,536-byte serialized
  result succeeded without truncation or fanout. Completion took 3,678.203 ms;
  the permanent response was 113,891 bytes and review detail 68,665 bytes.
- Four concurrent writers performed 40 real updates to distinct work items
  in one project: p95 168.493 ms. Observed `FOR UPDATE` query p95 was 87.542 ms,
  maximum 172.200 ms; database deadlock count did not increase. This is a
  bounded contention probe, not proof for every mixed-operation workload.

The capacity/contention probes and queue measurements were repeated with the
final database guards. Queues used 20-row pages, five warmups and 30 measured reads:

| History size | Queue | p95 request | Page bytes | Explain execution |
| --- | --- | --- | --- | --- |
| 1,000 | Reviews | 13.607 ms | 13,080 | 0.741 ms |
| 1,000 | Questions | 17.833 ms | 13,394 | 0.142 ms |
| 100,000 | Reviews | 17.772 ms | 13,126 | 0.369 ms |
| 100,000 | Questions | 13.150 ms | 13,439 | 0.174 ms |

Both queues used their project/state/sequence indexes; large-history review
joins used the work/lease primary keys and unique review-remediation index.
These were warmed local reads (zero shared reads in the captured plans), not
cold-storage or production latency claims. The shared test host was running other
validation concurrently. Historical queue shapes were seeded
synthetically in an isolated schema with triggers bypassed during fixture
construction only; lifecycle and direct-SQL suites separately establish
integrity. The benchmark does not use those synthetic rows as proof of valid
review aggregates.

## Regression and browser evidence

- Full backend suite with PostgreSQL after final hardening: 1,560 passed
  (no database-suite skips; 11 existing deprecation/schema-reflection warnings).
- Full MCP suite: 982 passed, including strict wire/contracts, actual stdio/HTTP
  transport and authentic isolated offline Claude plugin fresh/update checks.
- Frontend units: 352 passed; Node 24 typecheck and production build passed.
- Focused review Playwright acceptance: all 14 cases passed across desktop and
  narrow layouts. These cover both mandatory Done paths, cold canary exclusion,
  fixed warm adversarial guidance, single remediation, optional yes/no and
  unknown-outcome exact retries, history recovery, originating-session checks,
  explicit supersession, blocked moves with retained policy, unsent-draft
  preservation/reset and the hard depth-two stop.
- Full Playwright regression: 153 passed, four intentionally viewport-specific
  skips. After that full run, the draft-preservation correction was validated in
  a fresh production build with all 14 review cases, plus unit/type checks.
- Backend/MCP Ruff and ty, OpenAPI snapshot and the required secret scan pass.
- The separate repository-freshness plugin suite passed 71 tests with one
  platform-specific skip on Linux (72 collected). PR CI also runs it on macOS
  with authentic Bash 3.2. The initial PR run exposed stale plugin-version and
  exact-reference-inventory assertions; both were corrected to match the
  coordinated 0.14.0 package, without changing runtime code. An independent
  release-contract sweep found no additional actionable mismatch.

PostgreSQL-marked suites run with `TEST_DATABASE_URL`; skips do not count as
database validation. Authentic plugin checks use isolated offline Claude CLI
state, never operator configuration.

Screenshots from the isolated acceptance stack:
[settings](images/code-reviews/settings-desktop.png),
[narrow settings](images/code-reviews/settings-narrow.png),
[mandatory handoff](images/code-reviews/mandatory-composer-desktop.png),
[requested review](images/code-reviews/requested-review-desktop.png),
[findings](images/code-reviews/findings-desktop.png),
[single remediation](images/code-reviews/remediation-desktop.png), and
[recommendation recovery](images/code-reviews/recommendation-recovery-narrow.png).

## Independent cold adversarial reviews

The implementation is reviewed against pinned base/head commits by fresh
subagents that have not read this plan, author handoffs or previous findings.
They inspect changed source/tests and relevant callers, without reading author
rationale, issue trackers, PR discussion or commit messages. Findings are frozen
before discussion. Review findings, resolutions and revalidation are recorded
here before the pull request is opened.

The first independent cold reviewer froze findings for `c71a0cb..22bc2a5`,
covering backend lifecycle, SQL/migrations, receipt/lease ownership, moves,
relationships and relevant MCP callers. Its isolated reproductions established
two defects; neither was waived:

| Finding | Correction and regression evidence |
| --- | --- |
| Both MCP queue tools sent `cursor` to REST routes that accept `after`. | Map the retained MCP cursor to `after`; test forwarding against generated route query contracts and traverse actual PostgreSQL-backed REST pages. |
| Review projections rejected multiline/tab-containing titles accepted by existing work mutations. | Preserve the stored work-title grammar in API, MCP and browser read projections, without relaxing newly authored finding titles. Test actual post-Done title edits, both queue pages and exact details. |

The focused fix verification passed 49 MCP tests and two PostgreSQL/HTTP
regressions. The first reviewer rechecked `22bc2a5..8271329`, confirmed both
findings resolved, and established no additional actionable defect in the
audit/catalog delta. Its recheck included nine focused frontend tests and
in-memory contract reproductions, not a repeated PostgreSQL restore.

A second independent cold reviewer examined the full client diff at
`c71a0cb..8271329`, including frontend components, proxy/decoder/recovery state,
MCP models/tools/transport, packaged plugin behavior and relevant backend
contracts. It froze one P2 finding: ordinary project activity clears the loaded
question, unmounting the editor and erasing its unsent recommendation/rationale/
handoff. Dispatched unknown-outcome retry tests did not cover that pre-submit
case. The browser regression reproduced Yes changing to an empty choice before
the fix. The corrected panel retains the same question/editor during refresh,
while true identity changes and terminal/new questions clear the draft. All 14
review cases then passed on a rebuilt desktop/narrow stack, including exact
preserved rationale and handoff payloads. No other concrete client finding was
established. The reviewer independently rechecked `8271329..ab478b6`, confirmed
the fix, terminal/new-question resets and independent frozen retry intent, and
found no new actionable defect. That recheck was source-based; the author-side
acceptance run supplies the browser execution evidence.

A third fresh cold reviewer examined persistence at `c71a0cb..8271329`, covering
migration/downgrade, ownership, immutable records, sealing, cardinality, lineage,
lease witnesses and transaction ordering. It froze two P2 findings: lifecycle
events lacked a deferred reverse witness check when inserted without their
resource transition, and a nullable review lease mode could pass SQL CHECK's
three-valued logic. Its supersession finding had an isolated reproduction; the
continued review and null-mode finding were static-only. The temporary probe
schema was removed and the reviewer changed no checkout files.

Commit `73a148a` adds deferred exact event-to-resource witnesses for all six
lifecycle events, explicitly rejects null review modes in migration/ORM, and
extends the read-only audit. Tests demonstrate commit-time rejection after a
successful event insert, duplicate-event rejection despite a valid transition,
valid answer/request/supersession in one transaction, null-mode insert/update
rejection, and detection of deliberately damaged isolated fixture data. The
101-test focused verification passed. The reviewer independently rechecked
`ab478b6..73a148a`, confirmed both findings resolved and the downgrade/catalog
integration coherent, and found no remaining or new actionable defect. This
recheck was static-only; the full 1,560-test backend rerun and repeated restore/
capacity runs supply separate execution evidence. All five findings across the
three independent cold reviews were fixed; none was waived.

## Deployment boundary

Merge is not deployment. Before target deployment: back up and quiesce writers,
migrate, install coordinated API/MCP/dashboard/plugin versions, run the read-only
audit and both review-mode smoke checks, then resume traffic. Never run older
writers against 0024. After policy changes or review facts exist, downgrade is
blocked even when settings are reset; use a forward fix or an explicitly
approved complete restore.
