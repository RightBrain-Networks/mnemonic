# Code reviews implementation validation

Implementation baseline: `513f543` (merged planning PR #49). This release is
application/API/MCP/dashboard 0.12.0, Claude plugin 0.14.0 and Alembic
`0023_code_reviews`. The merged baseline already had plugin 0.13.0; the plugin
bump therefore reconciles the earlier planning catalog rather than reverting it.

The implementation follows [the plan](code-reviews-implementation-plan.md).
[Code reviews](code-reviews.md) documents the shipped workflow and operational
cutover. Validation uses disposable schemas/stacks; it does not deploy the
feature or migrate the operator's running database.

## Persistence and capacity evidence

- Migration/ORM parity, SQL transition/lineage attacks, downgrade guards and
  audit tests: 75 passed in the persistence validation run. Existing completion
  evidence and report/lease suites are also included in full backend validation.
- All 15 pre-feature receipt kinds retain frozen request/response digests.
  The two new kinds have their own frozen vectors; sparse historical responses
  do not acquire review fields.
- A PostgreSQL 17 custom archive (636,767 bytes) was restored into a uniquely
  named disposable database. Exact JSON-row digests matched for all 63 rows
  across 29 tables. The 16-category read-only review audit passed with a
  completed review, single remediation, pending optional question and an
  activity stream rotation represented in the rehearsal.
- A real completion with 100 findings and an exactly 65,536-byte serialized
  result succeeded without truncation or fanout. Completion took 3,654.885 ms;
  the permanent response was 113,891 bytes and review detail 68,665 bytes.
- Four concurrent writers performed 40 real updates to distinct work items
  in one project: p95 128.357 ms. Observed `FOR UPDATE` query p95 was 61.356 ms,
  maximum 121.518 ms; database deadlock count did not increase. This is a
  bounded contention probe, not proof for every mixed-operation workload.

Queue measurements used 20-row pages, five warmups and 30 measured reads:

| History size | Queue | p95 request | Page bytes | Explain execution |
| --- | --- | --- | --- | --- |
| 1,000 | Reviews | 11.491 ms | 13,080 | 0.474 ms |
| 1,000 | Questions | 11.792 ms | 13,394 | 0.161 ms |
| 100,000 | Reviews | 14.827 ms | 13,126 | 0.212 ms |
| 100,000 | Questions | 16.680 ms | 13,439 | 0.280 ms |

Both queues used their project/state/sequence indexes; large-history review
joins used the work/lease primary keys and unique review-remediation index.
These were warmed local reads (zero shared reads in the captured plans), not
cold-storage or production latency claims. Historical queue shapes were seeded
synthetically in an isolated schema with triggers bypassed during fixture
construction only; lifecycle and direct-SQL suites separately establish
integrity. The benchmark does not use those synthetic rows as proof of valid
review aggregates.

## Regression and browser evidence

Final suite results and screenshot links are recorded here after the integration
freeze. PostgreSQL-marked suites must run with `TEST_DATABASE_URL`; skips do not
count as database validation. Authentic plugin checks use isolated offline
Claude CLI state, never operator configuration.

## Independent cold adversarial reviews

The implementation is reviewed against pinned base/head commits by fresh
subagents that have not read this plan, author handoffs or previous findings.
They inspect changed source/tests and relevant callers, without reading author
rationale, issue trackers, PR discussion or commit messages. Findings are frozen
before discussion. Review findings, resolutions and revalidation are recorded
here before the pull request is opened.

## Deployment boundary

Merge is not deployment. Before target deployment: back up and quiesce writers,
migrate, install coordinated API/MCP/dashboard/plugin versions, run the read-only
audit and both review-mode smoke checks, then resume traffic. Never run older
writers against 0023. After policy changes or review facts exist, downgrade is
blocked even when settings are reset; use a forward fix or an explicitly
approved complete restore.
