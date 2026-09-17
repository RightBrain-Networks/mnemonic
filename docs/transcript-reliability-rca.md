# Transcript reliability RCA and remediation

Application/API/MCP/dashboard **0.62.0**, plugin **0.40.0**, migration
`0043_transcript_source_identity` (includes the 0.61.0 access-diagnostics repair).

## Production evidence

The September 16–17, 2026 audit inspected database metadata, container identities,
approved source mounts, and file metadata. No transcript bodies were displayed.
At the initial observation, 1,341 transcripts had retained copies, 41 were queued
with `transcript_io_error`, four had failed with that code, one had failed with
`transcript_path_not_allowed`, and one was waiting on an Active lease.

Both API and worker already ran as UID 1026 / GID 1000 and mounted the configured
Claude, Codex session, and archived-session directories. Opening affected paths
as the actual worker showed these causes:

- Forty queued records had a unique native session filename in a worktree-specific
  Claude project directory, while their recorded paths used the main checkout's
  encoded project directory. The mismatch alone does not establish whether the
  original assertion was wrong or the client subsequently moved the file. One
  missing filename had no verified match in the approved roots. A separate Active
  enrollment had the same main/worktree mismatch.
- Four assertions named `subagents/workflows/wf_*` directories. Each held several
  native agent files; there is no sound one-to-one replacement for a directory.
- The rejected `/tmp/claude-1026/.../tasks/*.output` assertion was a temporary
  symlink. Host `readlink -e` identified its actual native subagent JSONL inside
  the existing approved Claude root. Allowing `/tmp` would have treated the
  symptom by expanding filesystem access unnecessarily.

The initial production evidence did **not** show a general UID/GID mismatch or
missing source mount. Those remain credible failures on other installations and
are covered by the new diagnostics and tests.

## Why the problem recurred

Earlier releases repaired deployment identity, moved source mounts into base
Compose, retried corrected allowlists, and added an audited historical recovery
workflow. They also instructed agents to verify native paths. However, fresh
claims and closeouts still accepted syntactically valid absolute paths without
verifying them. New guessed paths could therefore recreate the same backlog after
an earlier repair (59 recovery approvals already existed before this audit).

The live recheck after deploying 0.61.0 found a newly enrolled path missing, despite
server-side validation at claim time. The same native session filename existed in
a worktree-specific directory. The initial RCA had overattributed these mismatches
to guessed assertions. [Claude's documented behavior](https://code.claude.com/docs/en/worktrees#resume-a-worktree-session)
explains that transcripts follow the working directory when entering or exiting
Git-created worktrees. Validation proves accessibility at enrollment, not continued
location. The new observation is consistent with that move; it does not retroactively
prove how each older missing path arose. Version 0.62.0 adds durable enrollment
evidence and verified relocation instead of relying on the original path staying put.

Several independent failures were reduced to `transcript_io_error`. In particular,
Python's `fdopen` rejected a directory before the intended regular-file check,
turning an invalid directory assertion into an I/O failure. Missing files and
permission errors used the same copy status and spent a short retry budget before
becoming terminal. Restoring permissions or a mount did not automatically revive
these failures. Only changed allowlists had automatic recovery.

The reader also opened every ancestor directory with `O_RDONLY`, requiring read
permission where filesystem traversal only requires search/execute permission.
The API checked source roots at startup, but that could take the dashboard down
instead of explaining the failure. API access did not prove worker access. No
project-wide page warning described the failing path, actual service identity,
required permission, storage failure, or worker observation freshness.

## Corrective changes

1. **Verify fresh assertions.** Claims and closeouts verify native `.jsonl`/`.json`
   paths are readable regular files under operator-approved roots. Directory,
   temporary-output, missing, permission, and symlink failures reject the fresh
   transaction with a specific path and repair instructions. The API hashes a bounded
   prefix for relocation evidence; it returns no transcript body to the agent.
   Explicit null remains available when no verified transcript exists.
   Successful permanent receipt replays bypass this check even if the original
   file or mount later disappears; uncertain retries remain byte-for-byte frozen.
2. **Use least-required filesystem access.** Descriptor-based no-follow traversal
   uses `O_PATH` on Linux (with a portable read-descriptor fallback). Every source
   component still refuses symlinks. File type is checked before `fdopen`, and
   permission diagnostics identify the blocking parent or leaf.
3. **Separate environmental failures from transient retries.** Copy diagnostics
   persist code, exact path, operation, service UID/GID, and available owner/mode
   metadata. Missing source, denied access, full/read-only storage, unavailable
   storage and historical generic I/O failures are rechecked every five minutes.
   Transient I/O/content-change retries remain bounded. Restored access resumes
   normal durable jobs without rebuilding ready text. Lease/pause guards, recovery
   byte pins, immutable captures, crash fencing and legacy ready text remain intact.
   Queue reconciliation orders by due time so old work does not monopolize dispatch.
4. **Observe both services.** The actual worker reports root accessibility and a
   private storage create/write/fsync/delete probe every 30 seconds. Reports older
   than two minutes are visibly stale. API/worker identity and root mismatches are
   reported. Worker observations are deployment state and excluded from project
   backups; per-transcript diagnostics remain with transcript records.
5. **Make failures actionable.** `/transcripts` requests project-scoped health
   independently of search and pagination and shows warnings above the search box.
   Warnings include the specific path, needed permissions, service and owner IDs,
   observed modes, recovery guidance and automatic retry behavior. They are bounded,
   expandable, rendered as text, and do not silently imply health if loading fails.
   Unavailable source roots or search-index storage no longer prevent the API
   dashboard from starting. A repaired index can reopen on health refresh; retained
   transcript copies and normalized database text remain intact.
6. **Check new installations explicitly.** Setup can accept dedicated source roots
   and select the source owner's numeric UID/GID. Different owners require an
   explicit service identity and access arrangement. A preflight checks both real
   Compose services and a bounded sample of native files without reading bodies.
   It supports built one-off containers before application startup.
7. **Recognize verified moves.** New assertions retain a versioned filename/prefix
   hash (256–65,536 bytes). A missing or changed source can be found only by a complete,
   bounded scan inside current approved roots yielding one matching regular file.
   The copier rechecks the evidence on the same descriptor used to stream bytes.
   No symlinks, first-match guesses, legacy backfills or root expansion are allowed.
   Original assertions stay immutable; actual capture locations are recorded when
   known. Ambiguity, limits and changed identity are actionable warnings with slow
   retries. Existing private copies and full-file operator recovery pins take priority.

8. **Separate search limits from format coverage.** The previous indexer folded
   every unsupported native record or unresolved relationship into `truncated`.
   The live audit found 1,334 flagged rows, but only 11 at the two-million-character
   search limit. Version 0.62.0 reserves that flag for the indexed text budget and
   labels structural issues **Normalization warnings**. Metadata limits have their
   own property. Retained raw copies and full stored segments are preserved. The
   migration queues only idle ready copies, excludes Active work and never clears
   lease fields; normal jobs refresh the derived flags from persisted segments.

## Instance repair and remaining historical gaps

Forty unique exact session filenames and the verified native target of the one
`.output` symlink were prepared and applied through
`scripts/recover_transcript_paths.py`. The separate Active mapping was recovered
after its lease ended; all **42** resulting records reached both copy **ready**
and index **ready**. The original assertions and work linkage were
preserved; approved replacement paths, SHA-256/size pins and operation UUIDs are
retained in the existing append-only journal. No roots or permissions were widened.

The four multi-agent workflow directories and the one unmatched source require
operator evidence identifying the actual native files, or restoration from an
external backup. They must remain visible gaps rather than being filled with a
plausible but unverified session. These are historical source-identification gaps,
not permission repairs. The post-deployment moved source is handled through a new
audited recovery because that enrollment predates persistent identity evidence.
Its first approved hash failed when the session continued writing after work
completion; that explicit failure prevented silently substituting unapproved bytes.
A new verified approval subsequently completed copying and indexing, bringing the
confirmed repairs to **43**. Both recovery intents remain in the append-only journal.
Private recovery manifests are kept outside Git and contain paths/operation IDs,
not transcript bodies or credentials.

## Deployment and validation

Stop old API, MCP, dashboard and worker processes before upgrading the schema.
Build the coordinated images, take a database backup, migrate, then start the new
services together. Migration 0042 adds `transcripts.copy_error_details` and the
worker-health observation table. Migration 0043 adds nullable `source_identity`
and `copy_source_path`, with shape and evidence immutability guards. Existing rows
remain unproven; enrolled evidence cannot be added retrospectively or discarded
by downgrade. No new MCP tool, agent write, receipt kind or
protected browser mutation is added.

Regression coverage exercises real execute-only ancestors, denied parent/leaf
permissions, symlink refusal, directory rejection, storage-full diagnostics,
fresh-assertion rollback, immutable receipt replay, automatic access recovery,
worker staleness/configuration mismatches and project isolation. Existing copy
crash, recovery, migration, backup and RabbitMQ suites protect the established
safety guarantees. Playwright verifies the warning above search at desktop and
narrow widths and observes it disappear after the same source's permissions are
restored and the normal retry runs. Relocation regressions cover rename/append after
claim, restart, frozen receipt replay, Active lease fencing, backup persistence,
ambiguity, symlinks, outside-root targets, inaccessible and budget-limited scans,
source substitution between discovery and capture, and unchanged full-file pins.
Browser acceptance moves a real shared file after claim and observes normal
RabbitMQ copying/indexing after release.

The residual dependency is explicit: Mnemonic cannot recreate deleted external
files, choose among several agent transcripts, grant itself host permissions,
change Docker Desktop file sharing, or infer a remote client's local filesystem.
Docker bind mounts refer to the **daemon host**, so a remote Docker engine needs
an explicitly shared filesystem. See [Docker bind mounts](https://docs.docker.com/engine/storage/bind-mounts/)
and Linux's [open/search permission semantics](https://man7.org/linux/man-pages/man2/open.2.html).
