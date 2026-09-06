# External records implementation validation

Validated on 2026-09-05 for application/API/MCP/dashboard `0.10.0`, plugin
`0.12.0`, and Alembic `0022_external_references`, rebased onto `3438916`.
This records implementation evidence for the
[plan](external-records-deduplication-implementation-plan.md), together with
the [performance evidence](external-records-performance-and-recovery-evidence.md).
All database changes and browser acceptance runs used disposable environments.
No production cutover, provider access, or credential change was performed.

## Independent adversarial code reviews

Three independent reviewers received the plan and implementation source without
the implementation conversation or authors' reasoning. They reviewed separate
but overlapping boundaries and were asked to find correctness and release
blockers. Their findings were fixed before final validation:

| Review | Finding and resolution | Recheck |
| --- | --- | --- |
| Storage, receipts, migration and audit | A downgrade eligibility check could race a committed external-reference write. The migration now locks work items, work events, and permanent receipts before checking eligibility and holds those locks through DDL. | Both downgrade regressions passed; final source recheck confirmed lock order and transaction lifetime. |
| Storage, receipts, migration and audit | Auditing only reference arrays missed malformed surrounding event metadata. The audit now validates the complete active metadata shape, while retaining all historical head-specific checks. | All 19 audit regressions passed; final source recheck confirmed the complete active validator and excluded event kinds. |
| Resource ownership, ranking and cancellation | A database-pool checkout could exceed the external lane's remaining budget. A per-worker deadline now bounds queue waiting without changing another request's pool timeout; permits remain held until underlying work exits. | Contention regression and an independent concurrent pool probe passed; ordinary checkout behavior remained unchanged. |
| Resource ownership and client contracts | Host Unicode versions could normalize new characters differently from PostgreSQL 17, making valid response correspondence fail. Backend, MCP, and browser title guards now pin the SQL normalizer's Unicode 15.1 assignment boundaries. | An independent 7,545-case normalization comparison against PostgreSQL 17 found no mismatch; cross-runtime fixtures passed. |
| Browser and MCP contracts | Opening a suspended create draft reset authored references. Resetting now occurs only for a fresh draft, and browser acceptance covers suspend/resume. | Focused client tests passed and final source review found no remaining issue. |
| Browser and MCP contracts | JavaScript trimming rejected a valid U+FEFF label accepted by Python and SQL. The reference label guard now uses the contract's exact whitespace definition. | Shared fixtures and strict client regressions passed. |

All three reviewers rechecked their findings and reported no remaining blocker.
The storage reviewer additionally checked that the maximum-context MCP test
uses the real SDK text and structured representations, a maximum-length JSON-RPC
request ID, UTF-8 serialization, and the stdio newline. Reviews supplement the
automated tests; they do not establish production performance or prove the
absence of every defect.

## Populated migration and supported restore rehearsal

The final rehearsal used a newly created database in the isolated PostgreSQL 17
test container. Synthetic data covered a frozen duplicate alias, its canonical
destination, soft deletion, a parked human gate, completion evidence and report,
report dismissal and follow-up, activity, and permanent operation receipts.
Historical feature-empty state was prepared at `0021`; old application processes
were never run against `0022`.

1. Run the head-specific `0021` aggregate audit and take an advance custom-format
   backup. Commit an additional work creation and its permanent receipt, return
   to the predecessor schema, and take the final quiescent recovery backup.
2. Upgrade to `0022`, compare every prior table's count and content digest and
   all sequence values, run the `0022` audit, and replay saved operations with
   exact response equality. Existing work receives only the prescribed empty
   reference list; no historical report or reference is inferred.
3. Simulate a post-upgrade validation failure in the disposable database, then
   restore the final `0021` archive through the actual
   `scripts/database/restore.sh` command. Verify all prior data and the last
   acknowledged creation/receipt survive. Verify the supported activity stream
   rotation, audit `0021`, upgrade again, and replay again.
4. Attach a reference through the API, take a populated `0022` backup, restore
   it through the same script, compare content, replay the permanent operations,
   and audit the restored head. Verify the reference remains readable.

The final integrated run passed with one project, 18 activity rows, one report,
one follow-up, and 13 saved permanent operation replays. The upgrade took
0.033 seconds on this small fixture. Advance, final quiescent, and post-upgrade
archives were respectively 399,046, 399,583, and 409,463 bytes. Each archive was
validated with `pg_restore --list`; actual restoration exercised its contents.
The temporary database and archives were removed afterward. Separate automated
tests cover all receipt kinds, frozen historical bodies, direct SQL constraints,
fresh/migrated schema parity, and downgrade refusal after references are cleared.

These measurements validate the recovery sequence on synthetic data. They do
not replace a live final quiescent backup, its off-machine copy and restore test,
production-sized migration timing, or the separately authorized cutover in
[operations](operations.md#external-records-release-0021-to-0022).

## Validation runs

Python 3.14, Node 24, separate Python environments, and the isolated PostgreSQL
test database were used. Database suites were enabled with no database skips.

| Check | Result |
| --- | --- |
| Backend full suite with PostgreSQL | 1,436 passed; seven framework deprecation/schema warnings |
| MCP full suite, including real SDK maximum context | 929 passed |
| Frontend unit/contract tests | 325 passed |
| Isolated Playwright acceptance stack | 133 passed, four intentional viewport skips |
| Standalone plugin verifier, authentic Bash 5.2.21 and Git 2.55.0 | 72 passed, no skips |
| Backend/MCP frozen lockfiles, Ruff and whole-source type checks | Passed |
| Frontend TypeScript and production build under Node 24 | Passed |
| Generated OpenAPI snapshot and strict consumers | Passed in the full suites |
| Populated migration, backup, supported restoration and aggregate audits | Passed, as detailed above |
| Required gitleaks pre-commit check over all tracked files | Passed |

The separate CI runtime check exercises macOS Bash 3.2. GitHub's aggregate
required checks must also pass on the current up-to-date PR commit before merge.

## Browser captures

The captures use synthetic records in the aligned dashboard:

- [Discovery before selection](images/external-records-discovery.png)
- [Independent comparison results](images/external-records-comparison.png)
- [Narrow dark discovery](images/external-records-discovery-narrow-dark.png)

## Release limitations

Maximum contexts retain all reference and checkpoint data. The measured full SDK
response exceeds the old 12 MiB result ceiling, so only the general MCP result
ceiling increases to 64 MiB. Request frames and permanent receipts stay at 1 MiB;
the previous completion-evidence-specific 12 MiB proof remains explicit. See the
performance evidence for the real frame test, conservative size accounting,
worst-case browser pauses, model deadline behavior, and search contention.

Candidate scope describes only records the caller supplied. Related research can
rank highly, and unavailable provider access is not evidence that no twin exists.
No result automatically links, merges, claims, or closes work. A worker that skips
Mnemonic discovery and claim remains outside its coordination: Path B persists.
