# Phase 12 cold backend and MCP code review

**Final closure verdict: ACCEPT.** A second independent reviewer closed B1, B2,
B3, and the recorded performance/restore evidence gate after inspecting the final
corrections and running the bounded checks documented at the end. The initial
findings and intermediate dispositions below are preserved as review history.
Full release validation and required CI remain separate checks.

Reviewed on 2026-09-05 against `origin/main` at
`413155947a4953499d4f868f552e0b0ce493f8c5`. The implementation worktree HEAD was
`559564faa3f117060f8984fb1da86e035d3c006d`; the Phase 12 implementation was
uncommitted and changed concurrently during this review.

The reviewer previously inspected the preimplementation architecture for the
planning task, but did not participate in implementation. This review used the
user requirements, the Phase 12 plan, the diff, and source/tests. It did not
read builder discussion or solicit implementation rationale. Frontend behavior
is reviewed separately. No application code was changed by this reviewer.

**Initial verdict: changes required before shipping.** Corrections supplied during
review are recorded below; outstanding validation prevents final approval.
The source supports the basic
transactional ordering, exact closeout reports, sparse historical receipts,
revisioned project settings, and separate human review/provenance design.
The supported restore path still violates the stream-incarnation contract,
and the operational audit can declare a missing required report healthy.
The central ordering test and performance release evidence also need the
specific additions below.

## Findings

### B1 — High: the supported restore command never rotates activity streams

Locations: `scripts/database/restore.sh:57–61`,
`backend/alembic/versions/0020_project_activity.py:170–181`,
`backend/src/mnemonic_api/services/activity_cursors.py:48–53`, and the restore
sequence in `docs/operations.md:1073–1090`.

The migration defines `mnemonic_rotate_activity_streams_after_restore()`, but
the actual maintenance restore script loads the archived schema/data and
returns without invoking it. The documented next command starts the complete
application. A restored archive already at 0021 runs no new migration that
could replace its archived `stream_id`. The only explicit rotation invocation
found in the reviewed implementation was its isolated database test.

Reproduction:

1. Back up a project at stream S, sequence 1.
2. Commit changes through sequence 3 and let a consumer retain cursor `(S,3)`.
3. Use the documented restore operation on that backup, then restart service.
4. The restored project still has stream S. Two new changes now receive
   sequences 2 and 3 in the restored history.
5. `get_activity` accepts the old `(S,3)` cursor and returns no entries, although
   the consumer never received those two different changes.

An independent call to the actual cursor decoder confirmed the distinction:
the old cursor initially produces `invalid_activity_cursor` while the restored
head is 1, then is accepted again after the same stream reaches 3. This was a
decoder reproduction plus inspection of the real restore path; it was not a
production restore experiment.

Integrate rotation into the supported offline restore procedure before any
traffic can resume, and test that procedure with an already-0021 archive.
Keep pre-Phase-12 archives supported: their upgrade creates fresh stream IDs.
An isolated test manually calling the SQL helper does not validate the
maintenance restore path. This is the required recovery behavior in plan
sections 4.6 and 12.2, not a request for a new recovery feature.

### B2 — Medium: the integrity audit misses closeouts with both report and witness absent

Location: `scripts/audit_project_activity.py:135–157`.

`unsealed_closeout_witnesses` only considers work with a nonnull witness, and
`invalid_report_event_binding` starts from existing reports. Neither checks
every post-import reportable closeout event for a required report and the
corresponding database transition witness. Consequently, simultaneous absence
of those two facts is invisible.

An independent disposable-schema probe produced this exact state after 0021:

```text
work_items: status=wont-do, version=2, last_reportable_closeout_version=NULL
work_events: live work_status_changed, pending -> wont-do, work_version=2,
             job_completion_report_id=NULL
job_completion_reports: empty
```

The probe temporarily disabled the three new report transition/event guards
only to construct corrupt data, then restored all three guards and flushed
deferred checks before auditing. The resulting installed catalog was intact.
The read-only audit returned:

```text
result=pass
blocking_findings={}
```

This is a missing corruption detector, **not** a claim that normal application
DML bypasses active guards. Such data is exactly what the plan's operational
audit is required to reject after migration or recovery. Scan qualifying
post-boundary events as well as existing reports/witnesses; preserve the
honest exemption for imported historical closeouts. Add regression fixtures
for missing report, missing witness, and both absent, with guards restored
before the audit runs.

### B3 — Medium: the ordering race test does not exercise the new allocator lock

Location: `backend/tests/test_phase12_database_postgres.py:366–411`.

The two writers both update the same `projects` row. The second writer must
wait for the first writer because of that row's ordinary PostgreSQL UPDATE
lock, independently of `project_activity_heads` and the new allocator. The
test therefore does not prove the plan's key scenario: independent source
mutations on different work rows cannot publish a later project prefix while
the earlier allocator transaction is uncommitted.

Keep this test, but add the planned distinct-work race using separate
connections and source-event insertion, including a reader while the first
transaction is paused. Assert the second writer is blocked by the head
allocation, then test commit and rollback outcomes. Also demonstrate that a
writer in a different project proceeds. The required implementation already
appears to hold the head row lock correctly; this finding concerns the missing
regression proof for its central durability guarantee.

## Required release evidence not present in the reviewed snapshot

Plan section 11.6 requires measurements at 1,000 and 100,000 activity entries,
10 and 100 concurrent writers, and large inboxes with mostly dismissed
history. No Phase 12 benchmark implementation/results or Phase 12 query-plan
record was found in the inspected tests and validation documentation. The
review therefore cannot accept the release gate for universal project
serialization, lease-renewal contention, or indexed sparse-inbox reads.

Record actual latency/lock waits/deadlocks, indexed query plans, response bytes,
dataset and environment details. This is an outstanding validation gate, not
an assertion that the observed code is already too slow. The implementation
was still in progress, so this section does not imply that its authors claimed
all validation had finished.

## Issue corrected while review was in progress

The initially inspected cursor decoder called `UUID(value["stream_id"])`
without first verifying a string. Integer, boolean, list, and object JSON
values could raise an uncaught `AttributeError`. This was reported to the
coordinating agent; the implementation then added a string type check.
Independent execution of the updated decoder confirmed sanitized 422 errors
for all four values in both activity and report cursors. This issue is not
outstanding.

## Corrections supplied during review and intermediate disposition

The coordinating agent supplied changes after receiving these findings. The
reviewer inspected their code independently without obtaining builder rationale.

- **B1:** `scripts/database/restore.sh` now appends an offline rotation block to
  the generated restore SQL, before the single-transaction `psql` invocation.
  It refuses an activity-bearing archive that lacks the rotation function and
  leaves pre-0020 archives to acquire new stream IDs during migration. This
  addresses the inspected integration defect. `sh -n` passes. A test of the
  actual maintenance restore procedure with an already-0021 dump, followed by
  old-cursor rejection, is still required before recovery approval.
- **B2:** the audit now scans post-import qualifying closeout events for absent
  reports/witnesses. The independent corruption probe was rerun against the
  correction and returns `blocked` with `missing_live_closeout_reports: 1`.
  Thus the observed audit defect is corrected. Its newly added committed test
  needs the fixture correction described below.
- **B3:** a new distinct-work source-event race now avoids the project row lock
  and includes a separate-project writer. The scenario addresses the requested
  proof, but its initial execution failed before reaching the race.

The first independent run of the supplied audit/ordering regressions produced
**6 passed, 3 failed**. Both added fixtures put `"work_version":2` directly
inside `sqlalchemy.text`; SQLAlchemy treats `:2` as a bind placeholder and
raises `A value is required for bind parameter '2'`. Bind the JSON as a value
rather than embedding that literal. The corruption fixture should also flush
pending deferred constraints before reenabling the work-events trigger; the
independent probe required `SET CONSTRAINTS ALL IMMEDIATE` there. Rerun these
regressions after correcting the fixtures. These are observed incomplete test
repairs, not new application defects.

The performance and actual restore-procedure validation gates above remain
open in this review. Once they and the corrected regression tests pass, a
closure review should reassess the final diff and update this disposition.

## Independent validation performed

- Backend contracts, report REST behavior, Phase 12 database invariants, and
  mutation deadlines: **71 passed** against PostgreSQL 17 using the disposable
  test database and a randomly named schema.
- MCP Phase 12 behavior, OpenAPI, plugin guidance, and transport tests:
  **164 passed**.
- Custom isolated corruption probe reproduced B2 with all relevant guards
  reenabled. Its random schema was removed afterward.
- Actual cursor-decoder probes confirmed the restored-incarnation risk in B1
  and the malformed-value fix described above.

The backend test command was:

```sh
uv run pytest -q tests/test_phase12_contracts.py tests/test_project_reports_postgres.py \
  tests/test_phase12_database_postgres.py tests/test_project_mutation_deadlines_postgres.py
```

The MCP test command was:

```sh
uv run pytest -q tests/test_phase12.py tests/test_openapi_contract.py \
  tests/test_plugin.py tests/test_transport.py
```

These focused results do not substitute for the complete backend suite,
Playwright stack, installed plugin smoke, populated backup/restore rehearsal,
performance gates, or the independent frontend review. Historical fixture and
audit work was being finalized concurrently; recheck the final diff and rerun
the corrected-case tests before closing this review.

## Final closure by a second independent reviewer

The frontend cold reviewer performed this additional backend closure pass at
worktree HEAD `3bd7491a1a7b37d6d1e2dfdda3ff44c396367b86`, after the rebase onto
`413155947a4953499d4f868f552e0b0ce493f8c5`. This reviewer had no backend
implementation role, independently read the original B1/B2/B3 findings,
inspected the final source and tests, and reviewed the measurement/rehearsal
artifacts rather than obtaining builder rationale. This is closure of the
specified findings and evidence gates, not a claim that a second complete
backend/MCP cold review was performed. No application code was edited.

### B1 closed — supported restore and old-cursor rejection verified

`scripts/database/restore.sh:58–75` appends the rotation block to the SQL executed
by the actual single-transaction restore, refuses an activity-bearing archive
without the helper, and leaves pre-0020 archives to receive their first stream
incarnation during migration. `sh -n scripts/database/restore.sh` passes.

The second reviewer independently reran the supported script in a newly created
disposable test database. The scenario migrated to 0021, created a report,
archived the database with ACLs, committed another activity fact, and restored
that archive through the real script. Assertions verified the archived sequence
and exact report ID, changed stream UUID, and a passing read-only aggregate
audit. The reviewer additionally decoded old activity and report cursors against
the restored head; both returned `activity_stream_changed`, including an activity
cursor whose sequence was ahead of the restored head. The temporary database and
archive were removed afterward. Observed result:

```json
{
  "supported_restore": "pass",
  "stream_rotated_automatically": true,
  "rewind_preserved_report_count": 1,
  "audit": "pass",
  "findings": {},
  "independent_old_cursor_checks": {
    "activity": "activity_stream_changed",
    "reports": "activity_stream_changed"
  }
}
```

### B2 closed — source-event audit catches missing report/witness facts

`scripts/audit_project_activity.py:183–195` now starts from qualifying events
after the preserved import boundary and checks their report binding and retained
work witness. Thus an absent report and absent witness cannot make the closeout
invisible to both checks. Existing reports and nonnull witnesses retain their
separate binding/sealing checks, and imported history remains exempt from newly
required report authorship.

The corrected corruption fixture uses SQL text that no longer creates the
accidental `:2` bind, flushes deferred constraints before reenabling the guards,
and verifies `missing_live_closeout_reports == 1` while catalog trigger drift is
absent. The independent bounded suite passed this scenario and valid populated
history/all-three-outcome receipt cases.

The final catalog normalization was also inspected. It checks current-user
ownership and effective owner/PUBLIC/other privilege classes for functions and
relations; column ACLs and internal FK trigger state are included. Independent
regressions confirmed that PUBLIC function execution, PUBLIC report selection,
PUBLIC column selection, and disabled FK triggers each block the audit. The
supported ACL-preserving restore still passes, so normalization does not accept
those tested privilege/guard changes as benign restore representation drift.

### B3 closed — distinct-work committed-prefix race passes

`backend/tests/test_phase12_database_postgres.py:510–576` inserts source events
for different work IDs through separate connections, without updating a shared
project source row. While the first transaction remains open, the second
same-project writer is observed blocked and an independent reader sees only the
committed head. Another project's writer completes within its separate lock
budget. Both commit and rollback branches pass, and their final activity
sequences are contiguous, including sequence reuse after rollback. This exercises
the allocator's project-head row lock rather than the original test's incidental
project metadata row lock.

### Performance and operational evidence gate closed

The second reviewer read
[the final performance and restore evidence](phase-12-performance-and-restore-evidence.md),
inspected the measurement scripts, and compared the recorded tables/plans with
the saved measurement JSON. The evidence covers 1,000/100,000-entry activity
histories, 10/100 concurrent project mutation requests, bounded page and empty
poll reads, 100,000-report undismissed/sparse/fully-dismissed distributions,
exact-work filters, maintained counts, response sizes, and query plans. The
recorded maximum domain duration is 338.824 ms and maximum lock-statement
duration is 277.983 ms, with no observed errors/deadlocks in those batches. The
sparse and fully dismissed queries use the intended partial inbox index.

The benchmark measures local authenticated FastAPI TestClient reads and actual
project-mutation contexts on PostgreSQL 17 with tmpfs, not deployed network or
browser latency. It correctly identifies those limits. The large cardinality
benchmarks were reviewed, not independently rerun by this second reviewer.
Maximum escaping report/evidence payload coverage was independently rerun and
passed. This closes the missing recorded-evidence finding without asserting a
production performance guarantee or completion of the separate full release
suite.

### Independent closure checks

```sh
uv run pytest -q tests/test_project_activity_audit_postgres.py \
  tests/test_phase12_database_postgres.py::test_allocator_serializes_distinct_work_facts_without_project_row_lock
uv run pytest -q tests/test_completion_evidence_postgres.py::test_maximum_escaping_completion_representations_fit_896_kib
sh -n scripts/database/restore.sh
```

Both pytest commands used the isolated test PostgreSQL instance with the
repository's randomly generated disposable schemas: **13 passed** and **1
passed**, respectively, with no skipped tests. The actual supported restore
probe used a separate disposable database, as described above. These runs did
not touch production data or replace the coordinator's complete backend,
frontend stack, MCP/plugin, or required CI validation.

**No B1/B2/B3 finding or performance/restore evidence gate remains open in this
review.**

## Incremental review: scope index OIDs before resolving definitions

**Disposition: ACCEPT.** The second reviewer independently inspected the small
read-only audit/test delta at worktree HEAD
`1b46ddd5f909bc9ae1ffc731632b3e88909469aa` plus its uncommitted changes.

`scripts/audit_duplicate_handling.py:3155–3166` first materializes index OIDs
whose owning relation is `checkpoints` in the explicitly requested audit schema.
Only the outer query calls `pg_catalog.pg_get_indexdef` and applies the unchanged
case-insensitive `%affected_paths%` match. The materialized CTE prevents that
function call from being evaluated against unrelated schemas before filtering.
For the audited relation, index membership, definition matching, and the count's
meaning are unchanged. Catalog relations, equality/matching operators, and the
index-definition function retain explicit `pg_catalog` qualification; the
surrounding catalog audit also retains its protected search path.

The matching change in
`backend/tests/test_repository_freshness_migration_postgres.py:1121–1129`
preserves that test's original wider scope: all indexes in its current disposable
schema. It similarly filters raw OIDs before resolving their definitions. Neither
change weakens the production audit's target-schema/table boundary or accepts an
unexpected affected-paths index.

No additional required finding was identified. This disposition is based on
source-level semantic and namespace review; it does not claim completion of the
coordinator's ongoing full backend or E2E reruns.
