# Phase 12 database performance and restore evidence

Measured on 2026-09-05 in the Phase 12 implementation worktree. The final database
catalog and supported restore proof were repeated after rebasing onto upstream
`4131559`. All fixtures used the isolated test PostgreSQL container and random
`mnemonic_test_*` schemas or a separately created disposable database. They were
removed afterward. Production data was never read or changed.

## Environment and method

The host exposed 16 logical CPUs and 66,955,493,376 bytes of RAM (about 62.4 GiB),
running Linux 6.8.0-138-generic on x86-64. PostgreSQL was 17.10, Alpine, in the
repository's `compose.test.yaml` container with its disposable data directory on
tmpfs. Python was 3.14. API route measurements used authenticated FastAPI
`TestClient` requests, including authorization, query validation, repeatable-read
transactions, ORM hydration and JSON response serialization. These are local API
measurements; they exclude TCP, reverse-proxy and browser rendering costs.

Each read row below records 100 sequential requests, including the first request.
Percentiles use sorted samples with the nearest lower sample for p95. Tests used
ordinary PostgreSQL planning; no scan type was forced. `ANALYZE` followed fixture
creation. The final sparse-inbox query plans followed `VACUUM ANALYZE` after the
large dismissal batches, so they measure retained history rather than dead tuple
cleanup. Concurrent test/setup activity means the results are an engineering
baseline, not a production latency guarantee.

Activity datasets started as genuine offline 0019 work/event fixtures and were
migrated through 0020/0021. Each had one project and one work item, one initial
`work_created` fact and enough valid `work_updated` facts to reach exactly 1,000
or 100,000 imported activity entries. A separate authenticated route run used the
same distributions. The 0019-to-head migration took 0.250 seconds for 1,000
entries and 3.738 seconds for 100,000 entries in the initial run.

The report dataset contained 100,000 separate work items, initial checkpoints,
recorded creation events, real pending-to-wont-do transitions and immutable
completion reports. Every source, transition, deferred seal, text, review, count
and journal guard remained enabled. Reports were created in batches of 1,000 in
disposable fixture transactions. Each used a short summary, zero FYIs, and a valid
project-configured prompt at revision 2. No historical report backfill or disabled
constraint path was used. This isolates inbox cardinality from maximum-size text
validation, which is covered separately below. Creation took 226.478 seconds;
dismissing 99,990 reports took 31.483 seconds. These bulk fixture setup times are
not API mutation timings and do not bypass application deadlines in any serving
process.

## Activity requests

| History entries | Request | p50 ms | p95 ms | Max ms | Response bytes |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1,000 | 100-entry page | 11.557 | 13.108 | 50.804 | 34,534 |
| 1,000 | Empty start=now poll | 7.281 | 8.482 | 8.774 | 453 |
| 100,000 | 100-entry page | 11.213 | 12.487 | 41.469 | 34,538 |
| 100,000 | Empty start=now poll | 6.601 | 7.916 | 8.632 | 460 |

The first bounded page selects at most 101 rows to emit 100. The initial database
query plans were:

1,000-entry project:

```text
Limit  (cost=0.28..9.05 rows=101 width=154) (actual time=0.042..0.060 rows=101 loops=1)
  Buffers: shared hit=7
  ->  Index Scan using pk_project_activity on project_activity  (cost=0.28..87.15 rows=1000 width=154) (actual time=0.041..0.052 rows=101 loops=1)
        Index Cond: ((project_id = '<fixture UUID>'::uuid) AND (sequence > 0) AND (sequence <= '1000'::smallint))
        Buffers: shared hit=7
Planning:
  Buffers: shared hit=7
Planning Time: 0.101 ms
Execution Time: 0.078 ms
```

100,000-entry project:

```text
Limit  (cost=0.42..8.04 rows=101 width=154) (actual time=0.033..0.047 rows=101 loops=1)
  Buffers: shared hit=8
  ->  Index Scan using pk_project_activity on project_activity  (cost=0.42..7548.22 rows=99980 width=154) (actual time=0.032..0.041 rows=101 loops=1)
        Index Cond: ((project_id = '<fixture UUID>'::uuid) AND (sequence > 0) AND (sequence <= 100000))
        Buffers: shared hit=8
Planning:
  Buffers: shared hit=7
Planning Time: 0.090 ms
Execution Time: 0.063 ms
```

Both plans use the `(project_id, sequence)` primary key and stop at the page
boundary. The 100,000-entry scan touched eight buffers and returned 101 rows;
its measured database execution time was 0.063 ms.

## Same-project writer contention

Each batch released 10 or 100 threads together. Every request entered the actual
`project_mutation` context, obtained the project lock, changed project metadata,
produced its activity fact and committed. The pool matched the serving capacity
of five retained connections plus ten overflow connections. Domain timing starts
before pool checkout and ends after commit. Lock-statement timing covers the
actual project `SELECT ... FOR UPDATE`, including PostgreSQL waiting and
execution; it excludes time waiting for a pool connection. No network service or
LLM call occurred while the mutation lock was held.

| History entries | Concurrent requests | Domain p50 ms | Domain p95 ms | Domain max ms | Lock statement p50 ms | Lock statement p95 ms | Lock statement max ms | Errors/deadlocks |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 10 | 60.675 | 98.872 | 108.358 | 28.986 | 68.774 | 77.067 | 0 / 0 |
| 1,000 | 100 | 145.176 | 327.100 | 338.824 | 7.510 | 224.791 | 277.983 | 0 / 0 |
| 100,000 | 10 | 53.626 | 88.415 | 101.162 | 22.267 | 53.445 | 60.422 | 0 / 0 |
| 100,000 | 100 | 228.947 | 311.345 | 317.811 | 9.562 | 157.249 | 174.676 | 0 / 0 |

The largest observed domain duration was 338.824 ms and the largest observed lock
statement duration was 277.983 ms. These are below the enforced two-second lock
ceiling and ten-second fresh-domain deadline. The independent PostgreSQL tests
also pause a writer, verify a second writer on a different work item is blocked
by the allocator, verify a reader sees only the committed prefix, and verify a
third writer in a different project remains independent. Both commit and rollback
branches are covered, including sequence reuse after rollback.

## Summary inbox requests

All cases retain 100,000 immutable reports. The sparse case leaves the ten oldest
reports undismissed underneath 99,990 newer dismissed reports. It has 399,992
activity entries: project creation, one prompt update, 300,000 creation/closeout/
report facts, and 99,990 dismissal facts. The fully dismissed case adds ten more
dismissal facts and leaves the maintained count at zero.

| Distribution and request | p50 ms | p95 ms | Max ms | Response bytes |
| --- | ---: | ---: | ---: | ---: |
| 100,000 undismissed; newest 50 reports | 18.731 | 23.578 | 72.905 | 44,294 |
| Ten old undismissed; up to 50 reports | 11.081 | 16.466 | 18.791 | 8,942 |
| Exact source work; one undismissed report | 11.660 | 18.894 | 23.355 | 1,128 |
| Sparse queue count | 7.783 | 12.743 | 15.606 | 104 |
| Fully dismissed; empty inbox | 9.416 | 11.491 | 21.409 | 223 |
| Fully dismissed count | 8.426 | 13.405 | 14.655 | 103 |
| Empty activity poll at about 400,000 entries | 8.756 | 16.331 | 25.047 | 455 |

The maintained badge query reads one count row and the activity head; it performs
no `COUNT(*)` over reports. Inbox hydration uses bounded joins and a batched
canonical-source lookup rather than one report query per item.

Ten old undismissed reports:

```sql
SELECT report_id, created_sequence
FROM job_completion_report_reviews
WHERE project_id = :project_id AND dismissal_id IS NULL
ORDER BY created_sequence DESC
LIMIT 21;
```

```text
Limit  (cost=0.14..3612.43 rows=17 width=24) (actual time=0.026..0.029 rows=10 loops=1)
  Buffers: shared hit=2
  ->  Index Scan using ix_job_report_reviews_undismissed on job_completion_report_reviews  (cost=0.14..3612.43 rows=17 width=24) (actual time=0.025..0.027 rows=10 loops=1)
        Index Cond: (project_id = '<fixture UUID>'::uuid)
        Buffers: shared hit=2
Planning Time: 0.103 ms
Execution Time: 0.048 ms
```

Exact work filter:

```text
Limit  (cost=0.42..8.44 rows=1 width=24) (actual time=0.029..0.029 rows=1 loops=1)
  Buffers: shared hit=4
  ->  Index Scan using ix_job_report_reviews_work_all on job_completion_report_reviews  (cost=0.42..8.44 rows=1 width=24) (actual time=0.028..0.029 rows=1 loops=1)
        Index Cond: ((project_id = '<fixture UUID>'::uuid) AND (work_item_id = '<fixture UUID>'::uuid))
        Filter: (dismissal_id IS NULL)
        Buffers: shared hit=4
Planning Time: 0.083 ms
Execution Time: 0.047 ms
```

PostgreSQL chose the exact-work all-history keyset index because this fixture
has one report per work. The predicate examines one indexed row; its cost does
not depend on the project's other 99,999 reports. Dedicated exact-work partial
indexes also exist for works with larger report histories.

Maintained count:

```text
Index Scan using pk_project_job_completion_report_counts on project_job_completion_report_counts  (cost=0.12..8.14 rows=1 width=8) (actual time=0.024..0.024 rows=1 loops=1)
  Index Cond: (project_id = '<fixture UUID>'::uuid)
  Buffers: shared hit=2
Planning Time: 0.027 ms
Execution Time: 0.039 ms
```

Fully dismissed history:

```text
Limit  (cost=0.12..5.89 rows=1 width=24) (actual time=0.020..0.020 rows=0 loops=1)
  Buffers: shared hit=1
  ->  Index Scan using ix_job_report_reviews_undismissed on job_completion_report_reviews  (cost=0.12..5.89 rows=1 width=24) (actual time=0.020..0.020 rows=0 loops=1)
        Index Cond: (project_id = '<fixture UUID>'::uuid)
        Buffers: shared hit=1
Planning Time: 0.082 ms
Execution Time: 0.036 ms
```

An initial run immediately after mass dismissal selected a bitmap lookup on the
nullable global dismissal-ID unique index, touching 596 buffers before sorting
ten surviving rows. The final schema makes dismissal-ID uniqueness a partial
unique index on non-null IDs. Its uniqueness timing remains immediate, and the
composite dismissal-owner key used by foreign keys is unchanged. The measured
final sparse plan uses `ix_job_report_reviews_undismissed` directly, touches two
buffers and returns ten rows in 0.048 ms. Empty history uses one buffer and
returns zero rows in 0.036 ms. PostgreSQL vacuum remains responsible for removing
dead index entries after large mutation batches.

## Payload bounds and functional gates

The measured pages are below their serving limits: activity items/page are
bounded to 4 KiB/512 KiB, report pages to 50 items/2 MiB, report detail to 256 KiB,
provenance pages to 50 items/256 KiB, and the count response to 1 KiB. Cursors are
bounded to 512 ASCII bytes. Limits include JSON escaping and response envelopes.

Maximum-size completion/report/evidence coverage is kept in
`backend/tests/test_completion_evidence_postgres.py`:
`test_maximum_escaping_completion_representations_fit_896_kib`. It combines a
16,384-byte report text budget, the maximum ten FYIs and 2,000-character summary,
32,768 bytes of structured evidence and escaping-heavy checkpoint content. This
is separate from the short-text cardinality benchmark. The final full-suite
result, including that test, is recorded in the release validation report.

The targeted PostgreSQL tests cover exact report ownership, all three reportable
terminal outcomes, required closeout witnesses, immutable content, default
settings migration, sparse historical receipt replay, prompt revisions,
monotonic dismissal, follow-up provenance, direct allocator attacks, lossless
downgrades, lock deadlines and ORM/migration parity. The aggregate audit's catalog
covers source functions, normal and internal FK triggers, constraints, indexes,
columns, ownership, effective privileges and relation security state. Catalog
variants are frozen from freshly migrated disposable schemas and actual
ACL-preserving `pg_dump`/restore representations; omitting ACLs is not an accepted
restore variant.

## Supported backup/restore rehearsal

The final rehearsal executed the actual `scripts/database/restore.sh`, not a
manual approximation. It created a separate disposable database with only the
application's public schema, upgraded to 0021, and committed one completed work
item and its human report. A custom-format `pg_dump --no-owner` archive preserved
application ACLs. A further project update committed after the backup, advancing
the activity head beyond the archived head.

With no application ingress attached to that disposable database, the rehearsal
ran the supported script with its required explicit restore confirmation and
archive filename. The script rebuilt and restored the public schema and rotated
all restored activity stream IDs in one transaction. Afterward, assertions
verified that the archived sequence was restored, the acknowledged newer
sequence was absent, the stream UUID differed from the backup, and the exact
immutable report ID survived. The private repeatable-read, read-only aggregate
audit passed with no blocking findings. The disposable database and archive were
then deleted.

Final observed result:

```json
{
  "supported_restore": "pass",
  "script": "scripts/database/restore.sh",
  "separate_disposable_database": true,
  "rewind_preserved_report_count": 1,
  "stream_rotated_automatically": true,
  "audit": "pass",
  "findings": {}
}
```

The operator sequence remains: close ingress, restore, apply newer migrations,
run the private aggregate audit and readiness checks, then reopen ingress. A
restore failure rolls back both the restored data and incarnation rotation.
