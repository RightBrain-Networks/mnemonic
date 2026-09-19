# Shared native transcript storage

Release 0.65.2 / schema `0046_shared_transcript_copies` fixes native storage
multiplication across lease generations. It changes no MCP claim/closeout fields,
transcript IDs, work links, reporting sessions, source assertions, native hashes,
normalization revisions, searchable text or download bytes. Plugin remains 0.42.1.

The production audit verified 15 retained enrollments of the reported session,
including older captures and a relocated assertion. They contained 2,531,543,794
bytes in total; the largest was 343,655,474 bytes. Every full SHA-256 matched, and
every smaller capture was a complete byte prefix of the largest. Six captures
were identical at 250,381,385 bytes. They belonged to different primary lease
generations on the same work item; queued captures became eligible together after
the earlier capacity repair. Across the store, 1,409 ready copies had 1,388 distinct
content hashes and 12 exact-duplicate groups at the audit observation.

## Identity and preservation

Enrollment identity remains separate from native content identity. Claim receipt
replay, work navigation, normalized segment revisions, and historical reads depend
on the existing work/generation/snapshot associations. Replacing all rows with the
latest session would change old evidence. Instead, equal content shares one object
key derived from its full SHA-256; every enrollment remains readable.

A growing source shares its complete previous byte prefix. Its next immutable
object contains a versioned header identifying that base and only the new suffix.
The prefix is compared in full, not inferred from filename, session ID, mtime, size,
a short hash or a sampled prefix. Changed or shortened sources get independent
objects unless their exact complete content already exists. Source heads are
performance hints, never authority or proof of content identity.

For an append-only history, retained native payload bytes equal the largest
snapshot, plus small object headers, per-enrollment crash receipts and filesystem
metadata. Identical enrollments add no native payload. Per-source filesystem locks
also prevent simultaneous enrollments from staging multiple full copies or forking
the shared prefix. Copying still verifies source identity and stability and obeys
capture limits, active-generation guards and project pause settings.

Objects use two UUID-shaped directories derived from the SHA-256 and a
`snapshot.bin` filename. This preserves private descriptor-relative, no-symlink
storage primitives. The new database constraint accepts this deterministic layout
and the historical `{transcript_id}/{snapshot_id}/transcript.jsonl` layout. Indexes
support content/reference inventory. A downgrade is refused while shared objects
are referenced; never run an older worker against the new store/schema.

Readers reconstruct exact native bytes into an unlinked private temporary file,
using bounded buffers. They verify every intermediate and final hash/length,
strictly decreasing base sizes, and all underlying file identities through
normalization. Missing or modified dependencies fail explicitly. Temporary native
views consume the process temporary filesystem, separately from retained storage;
in Compose this is the container/Docker filesystem. Memory is not proportional to
conversation size. JSONL/native parsing and canonical text publication are unchanged.

## Reclaim existing copies

Upgrade API and worker together, with old workers stopped. Preserve a matching
verified database and complete native-store backup before migration/reclamation.
Run inside the new worker, which already has its private storage and database access:

```sh
docker compose exec -T worker python /app/scripts/reclaim_transcript_copies.py --dry-run
docker compose exec -T worker python /app/scripts/reclaim_transcript_copies.py --apply
```

Omitting both flags means dry-run. It verifies every ready snapshot and reports
exact-duplicate bytes, reusable prefix bytes and estimated reclaimed file bytes.
The estimate includes object headers, crash receipts, source hints and replacement references but excludes
filesystem directory/block rounding. It prints aggregate counts, never transcript
bodies or source paths. Dry-run does not alter retained files or their permissions.

Apply holds the exclusive storage maintenance lock; new copy publications wait,
while normal dashboard/search/text reads remain available. A copy worker holds
the corresponding shared lock through its database commit. Each conversion:

1. Verifies the old snapshot and publishes its complete shared object and receipt.
2. Commits only its storage pointer, fenced by row, snapshot, status, size, hash and
   original pointer; all other metadata and canonical text remain unchanged.
3. Confirms no row still references the old full file, then atomically replaces it
   with a small reference to the same verified bytes. Open native readers finish
   before that replacement, and readers with older database pointers still work.
4. Verifies the shared read again. A final full pass verifies every current row.

A crash before pointer commit leaves the original full file intact. A crash after
commit leaves a discoverable redundant file, reclaimed on the next apply. Apply is
idempotent and needs no original client source. Concurrent source recovery fails
the row fence rather than deleting its old capture. Never manually delete native
files to reclaim space; the command does not delete shared objects or base objects.
Their references include dependent snapshot objects, not only database rows.
Historical small references intentionally remain for readers and retained pointers.
Empty historical files stay empty: they contain no redundant payload to reclaim.

Back up the **complete native store**, including objects, base objects, receipts and
references. A project-only directory selection is not a native backup: shared
content can serve several projects. PostgreSQL archives still include normalized
text and metadata but do not contain the original native file store.

## Regression coverage

Tests cover nine concurrent unchanged captures sharing one object; sixteen growing
snapshots with payload storage equal to the largest source and allocated storage
below twice that size; cross-path identical content; rewritten sources with a long
matching initial prefix; native/dependency corruption; pinned recovery; actual
process death; retained reads after lowering limits; dry-run nonmutation; interrupted
apply before/after commit; stale-row conflict; active-lease deferral; and concurrent
readers retaining old storage pointers. PostgreSQL tests compare complete metadata,
text download bytes/hashes and search results before and after reclamation. The
shipped CLI is exercised in default, explicit dry-run and apply modes. Existing
large-transcript, migration, normalization, RabbitMQ and backup/restore suites also
exercise the new storage path.
