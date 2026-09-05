# Phase 12 cold backend and MCP code review

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

## Corrections supplied during review and current disposition

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
